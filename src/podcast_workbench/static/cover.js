"use strict";
const coverUI = (() => {
  const el = id => document.getElementById(id);
  let current = null, config = null, locked = false, revision = null, planJob = null;
  let style = "illustration";
  const tabs = [...document.querySelectorAll("[data-view]")];
  function show(view) {
    tabs.forEach(tab => {
      const active = tab.dataset.view === view;
      tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
      el("view-" + tab.dataset.view).hidden = !active;
    });
    if (view === "script") requestAnimationFrame(scriptDisclosure);
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => show(tab.dataset.view));
    tab.addEventListener("keydown", event => {
      const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
      if (next !== null) { event.preventDefault(); show(tabs[next].dataset.view); tabs[next].focus(); }
    });
  });
  document.querySelectorAll("[data-open-view]").forEach(button => button.addEventListener("click", () => show(button.dataset.openView)));
  function styles(value) {
    style = value;
    document.querySelectorAll("[data-cover-style]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.coverStyle === value)));
  }
  document.querySelectorAll("[data-cover-style]").forEach(button => button.addEventListener("click", () => styles(button.dataset.coverStyle)));
  function controls() {
    const ready = Boolean(current?.current_revision);
    el("plan-cover").disabled = locked || !ready || !config?.llm_configured;
    el("generate-cover").disabled = locked || !ready || !config?.image_configured || !el("cover-prompt").value.trim();
    el("cover-prompt").disabled = locked;
    document.querySelectorAll("[data-cover-style]").forEach(button => { button.disabled = locked; });
  }
  el("cover-prompt").addEventListener("input", controls);
  function request(action) {
    if (!current?.current_revision || locked) return;
    show("cover");
    void submit({action, session_id:current.id, revision_id:current.current_revision.id,
      cover:{style, prompt:el("cover-prompt").value.trim()}});
  }
  el("plan-cover").addEventListener("click", () => request("plan_cover"));
  el("generate-cover").addEventListener("click", () => request("generate_cover"));
  function sync(session, health, busy) {
    current = session; config = health; locked = busy;
    const id = session?.current_revision?.id || null;
    if (revision !== id) {
      revision = id; planJob = null; el("cover-prompt").value = "";
      el("cover-rationale").textContent = ""; styles("illustration");
      const options = session?.published_cover?.revision_id === id ? session.published_cover.cover_options : null;
      if (options) { el("cover-prompt").value = options.prompt; styles(options.style); }
    }
    const plan = session?.cover_plan;
    if (plan && plan.job_id !== planJob) {
      planJob = plan.job_id;
      const cover = session?.published_cover;
      if (!cover || cover.revision_id !== id || (Date.parse(plan.created_at) || 0) > (Date.parse(cover.created_at) || 0)) {
        el("cover-prompt").value = plan.prompt;
        el("cover-rationale").textContent = plan.rationale;
        styles(plan.style);
      }
    }
    const cover = session?.published_cover, url = artifactUrl(cover);
    el("cover-result").hidden = !url;
    el("episode-cover-image").hidden = !url;
    el("episode-cover-empty").hidden = Boolean(url);
    el("cover-stale-note").hidden = !session?.cover_is_stale;
    el("episode-cover-empty").textContent = "封面可选\n尚未生成";
    if (url) {
      for (const id of ["cover-image", "episode-cover-image"]) {
        if (el(id).getAttribute("src") !== url) el(id).src = url;
      }
      el("cover-download").href = url + "?download=true";
      el("cover-size").textContent = `${cover.image_width} × ${cover.image_height} · ${cover.image_model}`;
    } else {
      el("cover-image").removeAttribute("src"); el("episode-cover-image").removeAttribute("src");
    }
    el("generate-cover").textContent = cover && !session?.cover_is_stale ? "重新生成封面" : "生成节目封面";
    el("cover-config-note").textContent = !health ? "正在检查服务配置……" : !health.image_configured ? "请在本地 .env 填写 AGNES_API_KEY 并重启后台。仍可正常制作脚本和音频。" : "图像服务已配置；实际可用性以生成结果为准。";
    controls();
  }
  return {sync, show};
})();
