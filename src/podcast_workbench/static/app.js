"use strict";
const $ = id => document.getElementById(id);
const storageKey = "podcast-workbench-session";
let health = null, session = null, pending = false, jobId = null, timer = null, traceId = "";
let polling = false;
const stages = ["researcher", "writer", "reviewer", "tts", "merge"];
const stageNames = ["正在整理材料", "正在编写对话", "正在审校内容", "正在分角色配音", "正在合成音频"];
const example = "阅读可以发生在通勤、散步等零碎时间。将文章改编为主持人与嘉宾的对话，可以用提问和解释帮助理解。改编时应保留原文事实，区分观点与结论，不补充没有依据的信息。";

function remember(id) {
  try { id ? localStorage.setItem(storageKey, id) : localStorage.removeItem(storageKey); } catch { /* 浏览器禁用存储时仍可制作 */ }
}
function recalled() { try { return localStorage.getItem(storageKey); } catch { return null; } }
function message(text) { $("design-message").textContent = text; $("design-message").hidden = !text; }
async function api(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      method: body ? "POST" : "GET", signal: controller.signal,
      headers: body ? {"Content-Type": "application/json"} : {},
      ...(body ? {body: JSON.stringify(body)} : {}),
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error?.message || `请求失败（${response.status}）`);
      error.trace = data.trace_id;
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (!error.status) throw new Error("暂时无法连接后台，请检查本地服务后重新查询。提交请求超时后，请先刷新恢复会话，避免重复提交。");
    throw error;
  } finally { clearTimeout(timeout); }
}
function showError(error) {
  if (error.trace) traceId = error.trace;
  message(error.message);
  identifiers();
}
function identifiers() {
  $("trace-details").hidden = !session && !traceId;
  const codes = $("trace-details").querySelectorAll("code");
  codes[0].textContent = session?.id || "尚未创建";
  codes[1].textContent = traceId || "尚未提交";
}
function settings() {
  return {audience: $("audience").value, style: $("style").value,
    target_minutes: Number(document.querySelector('input[name="minutes"]:checked').value),
    host_voice: $("host-voice").value, guest_voice: $("guest-voice").value};
}
function voiceValid() {
  const valid = Boolean($("host-voice").value && $("guest-voice").value && $("host-voice").value !== $("guest-voice").value);
  $("voice-error").hidden = valid;
  return valid;
}
function controls() {
  const busy = pending || Boolean(jobId);
  const length = [...$("source-text").value.trim()].length;
  const validSource = length > 0 && length <= (health?.max_source_chars || 20000);
  $("character-count").textContent = length.toLocaleString();
  $("create-button").disabled = busy || !health?.llm_configured || !validSource;
  $("create-label").textContent = busy ? "正在制作……" : session ? "按当前设置重新制作" : $("preview-script").checked ? "生成对话脚本" : "生成双人播客";
  $("revision-submit").disabled = busy || !health?.llm_configured || !$("revision-text").value.trim();
  $("new-program").disabled = busy;
  $("source-text").readOnly = busy || Boolean(session);
  $("use-example").disabled = busy || Boolean(session);
  $("empty-example").disabled = busy || Boolean(session);
  document.querySelectorAll("#material-form select,#material-form input").forEach(el => { el.disabled = busy; });
  ["render-audio", "render-new-audio", "retry-audio", "change-voices"].forEach(id => {
    $(id).disabled = busy || !session?.current_revision || !health?.tts_configured || !health?.ffmpeg_available;
  });
  $("resume-job").hidden = !jobId || polling;
  identifiers();
}
function fillSettings(value) {
  $("audience").value = value.audience;
  $("style").value = value.style;
  document.querySelector(`input[name="minutes"][value="${value.target_minutes}"]`).checked = true;
  for (const field of ["host_voice", "guest_voice"]) {
    const select = $(field.replace("_", "-"));
    if (![...select.options].some(option => option.value === value[field])) {
      select.add(new Option(`${value[field]}（原音色，请检查当前服务是否支持）`, value[field]));
    }
    select.value = value[field];
  }
}
function artifactUrl(item) {
  return item?.available && /^\/artifacts\/[0-9a-f-]{36}$/.test(item.url || "") ? item.url : null;
}
function node(tag, text, className) {
  const el = document.createElement(tag); el.textContent = text;
  if (className) el.className = className;
  return el;
}
async function refreshSession(id, fill = false) {
  session = await api(`/sessions/${id}`);
  remember(session.id);
  $("source-text").value = session.source_text;
  if (fill) fillSettings(session.settings);
  const revision = session.current_revision;
  $("result-output").hidden = !revision;
  $("empty-output").hidden = Boolean(revision) || Boolean(jobId);
  if (revision) {
    const script = revision.script;
    document.querySelector(".episode-heading h3").textContent = script.title;
    $("episode-style").textContent = revision.settings.style === "casual" ? "轻松访谈" : "知识讲解";
    $("episode-audience").textContent = {general:"普通大众", beginner:"初学者", professional:"专业听众"}[revision.settings.audience];
    $("revision-label").textContent = `第 ${revision.number} 版`;
    $("panel-script").replaceChildren(...script.turns.map(turn => {
      const row = node("div", "", `script-turn${turn.role === "guest" ? " guest-turn" : ""}`);
      row.append(node("span", turn.role === "host" ? "主持人" : "嘉宾", "speaker-label"), node("p", turn.text));
      return row;
    }));
    const list = node("ol", "", "outline-list");
    list.append(...script.outline.map(text => node("li", text)));
    $("panel-outline").replaceChildren(list);
    $("panel-review").replaceChildren(node("p", script.review_summary || "本次未返回审校摘要。", "review-copy"));
    const audio = session.published_audio, url = artifactUrl(audio);
    $("audio-panel").hidden = !url;
    $("script-ready-callout").hidden = Boolean(url);
    $("stale-banner").hidden = !session.audio_is_stale;
    if (url) {
      if ($("program-audio").getAttribute("src") !== url) $("program-audio").src = url;
      $("audio-download").href = `${url}?download=true`;
      $("audio-duration").textContent = `实际时长 ${Math.round(audio.duration_seconds)} 秒`;
      $("audio-engine").textContent = audio.tts_protocol === "edge_tts" ? "Edge TTS" : "Qwen TTS";
    } else { $("program-audio").removeAttribute("src"); }
    $("script-download").hidden = true;
    const data = await api(`/sessions/${session.id}/artifacts`);
    const scriptFile = data.artifacts.find(item => item.kind === "script" && item.revision_id === revision.id && artifactUrl(item));
    if (scriptFile) {
      $("script-download").href = `${artifactUrl(scriptFile)}?download=true`;
      $("script-download").hidden = false;
    }
    $("state-badge").textContent = session.audio_is_stale ? "脚本已更新" : url ? "音频就绪" : "脚本就绪";
  }
  controls();
  return session;
}
function progress(job) {
  $("running-output").hidden = false;
  $("empty-output").hidden = true;
  $("state-badge").textContent = "制作中";
  const index = stages.indexOf(job.stage);
  $("running-title").textContent = stageNames[index] || "任务已排队";
  document.querySelectorAll(".stage-list li").forEach((li, n) => {
    const skipped = job.action === "render_audio" && n < 3;
    li.className = skipped || n < index ? "complete" : n === index ? "current" : "";
    li.querySelector(".stage-word").textContent = skipped ? "沿用脚本" : n < index ? "已完成" : n === index ? "进行中" : "等待";
  });
}
async function poll() {
  if (!jobId || polling) return;
  polling = true; controls();
  try {
    const job = await api(`/jobs/${jobId}`);
    traceId = job.trace_id;
    if (["succeeded", "failed"].includes(job.status)) {
      // 先保留任务标识，结果同步失败时仍可重新查询。
      await refreshSession(job.session_id);
      jobId = null;
      $("running-output").hidden = true;
      $("empty-output").hidden = Boolean(session.current_revision);
      $("failure-banner").hidden = job.status !== "failed" || !session.current_revision;
      if (job.status === "failed") {
        $("state-badge").textContent = "制作未完成";
        document.querySelector("#failure-banner b").textContent = "本次制作未完成，已有内容已保留";
        message(job.error_message || "制作失败，请重试。");
      } else { message("制作完成，可查看脚本或试听音频。"); }
    } else {
      progress(job);
      timer = setTimeout(poll, 1000);
    }
  } catch (error) {
    showError(error);
    $("running-output").hidden = true;
    $("empty-output").hidden = Boolean(session?.current_revision);
    $("resume-job").hidden = false;
    // 已有脚本时也提供查询按钮。
    if (session?.current_revision) $("empty-output").hidden = false;
  } finally { polling = false; controls(); }
}
async function submit(body) {
  if (pending || jobId || !voiceValid()) return;
  pending = true; message(""); $("failure-banner").hidden = true; controls();
  try {
    const accepted = await api("/chat", body);
    traceId = accepted.trace_id;
    jobId = accepted.job_id;
    remember(accepted.session_id);
    await refreshSession(accepted.session_id);
    $("revision-text").value = "";
    void poll();
  } catch (error) { showError(error); }
  finally { pending = false; controls(); }
}
function renderAudio() {
  if (!session?.current_revision) return;
  void submit({action:"render_audio", session_id:session.id,
    settings:{host_voice:$("host-voice").value, guest_voice:$("guest-voice").value}});
}
$("material-form").addEventListener("submit", event => {
  event.preventDefault();
  const source = $("source-text").value.trim();
  const valid = source && [...source].length <= (health?.max_source_chars || 20000);
  $("source-error").hidden = Boolean(valid);
  $("source-error").textContent = `请输入 1 至 ${health?.max_source_chars || 20000} 字材料。`;
  $("source-text").setAttribute("aria-invalid", String(!valid));
  if (!valid) return;
  const body = {settings:settings(), preview_only:$("preview-script").checked};
  void submit(session ? {...body, action:"revise", session_id:session.id, message:"按照当前节目设置重新制作，保持原文事实。"} : {...body, action:"create", source_text:source});
});
$("revision-form").addEventListener("submit", event => {
  event.preventDefault();
  if (!session || !$("revision-text").value.trim()) return;
  void submit({action:"revise", session_id:session.id, message:$("revision-text").value.trim(),
    settings:settings(), preview_only:$("preview-script").checked});
});
for (const id of ["render-audio", "render-new-audio", "retry-audio", "change-voices"]) $(id).addEventListener("click", renderAudio);
$("resume-job").addEventListener("click", () => { message(""); void poll(); });
for (const id of ["use-example", "empty-example"]) $(id).addEventListener("click", () => { if (!session && !jobId) { $("source-text").value = example; controls(); $("source-text").focus(); } });
$("new-program").addEventListener("click", () => {
  if (pending || jobId) return;
  clearTimeout(timer); remember(null); session = null; traceId = "";
  $("material-form").reset(); $("revision-form").reset();
  $("source-text").value = ""; $("result-output").hidden = true;
  $("running-output").hidden = true; $("empty-output").hidden = false;
  $("failure-banner").hidden = true; $("source-error").hidden = true;
  $("source-text").removeAttribute("aria-invalid");
  $("program-audio").pause(); $("program-audio").removeAttribute("src");
  $("state-badge").textContent = "等待创作"; message(""); controls();
});
document.querySelectorAll("[data-edit]").forEach(button => button.addEventListener("click", () => {
  $("revision-text").value = button.dataset.edit; controls(); $("revision-text").focus();
}));
document.querySelectorAll("[data-tab]").forEach((button, index, tabs) => {
  button.addEventListener("click", () => tabs.forEach(tab => {
    const active = tab === button; tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    $(tab.getAttribute("aria-controls")).hidden = !active;
  }));
  button.addEventListener("keydown", event => {
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
    if (next !== null) { event.preventDefault(); tabs[next].click(); tabs[next].focus(); }
  });
});
$("help-open").addEventListener("click", () => $("help-dialog").showModal());
$("help-close").addEventListener("click", () => $("help-dialog").close());
$("material-form").addEventListener("input", controls);
$("revision-text").addEventListener("input", controls);
async function init() {
  try {
    health = await api("/health");
    const voices = health.tts_protocol === "edge_tts" ? [["zh-CN-XiaoxiaoNeural", "晓晓 · 女声"], ["zh-CN-YunxiNeural", "云希 · 男声"]] : [["Cherry", "芊悦 Cherry"], ["Ethan", "晨煦 Ethan"], ["Serena", "苏瑶 Serena"]];
    ["host-voice", "guest-voice"].forEach((id, index) => {
      $(id).replaceChildren(...voices.map(([value, label]) => new Option(label, value)));
      $(id).value = health.default_voices[index];
      for (const option of $(id).options) option.defaultSelected = option.selected;
    });
    const issues = [];
    if (!health.llm_configured) issues.push("请在本地 .env 填写 DEEPSEEK_API_KEY 并重启后台，再生成脚本");
    if (!health.ffmpeg_available) issues.push("音频合成组件不可用，请检查 FFmpeg 配置");
    if (!health.tts_configured) issues.push("语音服务尚未配置");
    $("connection-status").textContent = `后台已连接 · ${health.tts_protocol === "edge_tts" ? "Edge TTS" : "Qwen TTS"}${issues.length ? "。" + issues.join("；") + "。" : " · 可以开始制作"}`;
    const id = recalled();
    if (id && /^[0-9a-f-]{36}$/.test(id)) {
      try {
        await refreshSession(id, true);
        if (session.active_job) { jobId = session.active_job.id; void poll(); }
      } catch (error) { if (error.status === 404) remember(null); showError(error); }
    }
  } catch (error) { $("connection-status").textContent = error.message; }
  controls();
}
void init();
