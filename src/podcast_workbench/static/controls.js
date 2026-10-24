"use strict";
// 仅负责已选布局的设置交互；生成请求仍由 app.js 提交。
const workbenchUI = (() => {
  const byId = id => document.getElementById(id);
  let role = "host", voices = [], defaults = [], busy = false;
  const voiceDialog = byId("voice-dialog"), nameDialog = byId("name-dialog");
  const voiceOptions = document.createElement("div");
  voiceOptions.id = "voice-options";
  voiceDialog.querySelectorAll(".voice-choice").forEach(button => button.remove());
  voiceDialog.insertBefore(voiceOptions, voiceDialog.querySelector("details"));
  const changed = () => document.dispatchEvent(new Event("workbench-settings"));
  const dropdowns = [...document.querySelectorAll(".custom-select")].map(group => {
    const trigger = group.querySelector(".choice-trigger"), menu = group.querySelector(".choice-menu");
    const input = group.querySelector("input"), options = [...menu.querySelectorAll("[role=option]")];
    let active = 0;
    function close() {
      menu.hidden = true; trigger.setAttribute("aria-expanded", "false");
      trigger.removeAttribute("aria-activedescendant");
      options.forEach(option => option.classList.remove("is-active"));
    }
    function mark(index) {
      active = index;
      options.forEach((option, n) => option.classList.toggle("is-active", n === index));
      trigger.setAttribute("aria-activedescendant", options[index].id);
    }
    function open() {
      if (busy) return;
      dropdowns.forEach(item => item.close());
      menu.hidden = false; trigger.setAttribute("aria-expanded", "true");
      mark(Math.max(0, options.findIndex(option => option.dataset.value === input.value)));
    }
    function choose(index) {
      if (busy) return;
      input.value = options[index].dataset.value;
      close(); changed(); trigger.focus();
    }
    trigger.addEventListener("click", () => menu.hidden ? open() : close());
    menu.addEventListener("pointerdown", event => event.preventDefault());
    options.forEach((option, index) => option.addEventListener("click", () => choose(index)));
    trigger.addEventListener("keydown", event => {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (menu.hidden) {
          open();
          if (event.key === "Home") mark(0);
          if (event.key === "End") mark(options.length - 1);
        } else mark(event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (active + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault(); menu.hidden ? open() : choose(active);
      } else if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (event.key === "Tab") close();
    });
    group.addEventListener("focusout", event => { if (!group.contains(event.relatedTarget)) close(); });
    document.addEventListener("click", event => { if (!group.contains(event.target)) close(); });
    return {close, sync() {
      trigger.disabled = busy;
      if (busy) close();
      options.forEach(option => {
        const selected = option.dataset.value === input.value;
        option.setAttribute("aria-selected", String(selected));
        if (selected) trigger.querySelector("span").textContent = option.querySelector("span").textContent;
      });
    }};
  });
  function voiceLabel(value) {
    return voices.find(item => item[0] === value)?.[1] || (value.endsWith("Neural") ? "自定义音色" : value || "选择声音");
  }
  function sync(isBusy) {
    busy = isBusy;
    dropdowns.forEach(item => item.sync());
    for (const speaker of ["host", "guest"]) {
      const name = byId(speaker + "-name").value;
      const nameLabel = document.querySelector(`[data-name="${speaker}"]`);
      nameLabel.textContent = name; nameLabel.title = name;
      nameLabel.parentElement.querySelector("small").hidden = name === (speaker === "host" ? "主持人" : "嘉宾");
      const value = byId(speaker + "-voice").value;
      const label = document.querySelector(`[data-voice-label="${speaker}"]`);
      label.textContent = voiceLabel(value); label.title = value;
    }
    document.querySelectorAll("[data-change],[data-rename]").forEach(button => { button.disabled = busy; });
  }
  document.querySelectorAll("[data-change]").forEach(button => button.addEventListener("click", () => {
    if (busy) return;
    role = button.dataset.change;
    const current = byId(role + "-voice").value;
    byId("voice-title").textContent = `为${role === "host" ? "主持人" : "嘉宾"}选择声音`;
    voiceOptions.replaceChildren(...voices.map(([value, label]) => {
      const choice = document.createElement("button");
      choice.type = "button"; choice.className = "voice-choice";
      choice.dataset.voice = value;
      const selected = current === value;
      choice.classList.toggle("is-selected", selected);
      choice.setAttribute("aria-pressed", String(selected));
      const title = document.createElement("strong"); title.textContent = label;
      const check = document.createElement("span"); check.className = "check"; check.textContent = selected ? "已选择" : "";
      choice.append(title, check);
      choice.addEventListener("click", () => { byId(role + "-voice").value = value; voiceDialog.close(); changed(); });
      return choice;
    }));
    byId("custom-id").value = voices.some(item => item[0] === current) ? "" : current;
    byId("custom-error").hidden = true;
    voiceDialog.querySelector("details").open = false;
    voiceDialog.showModal();
  }));
  byId("custom-apply").addEventListener("click", () => {
    const value = byId("custom-id").value.trim();
    if (!/^[a-z]{2,3}-[A-Z]{2}-.+Neural$/.test(value) || value.length > 80) {
      byId("custom-error").textContent = "请输入完整 Edge 音色 ID，例如 zh-CN-XiaoxiaoNeural。";
      byId("custom-error").hidden = false; return;
    }
    byId(role + "-voice").value = value;
    voiceDialog.close(); changed();
  });
  document.querySelectorAll("[data-rename]").forEach(button => button.addEventListener("click", () => {
    if (busy) return;
    role = button.dataset.rename;
    byId("name-title").textContent = `修改${role === "host" ? "主持人" : "嘉宾"}称呼`;
    byId("new-name").value = byId(role + "-name").value;
    byId("new-name").setCustomValidity("");
    nameDialog.showModal();
    byId("new-name").select();
  }));
  byId("name-form").addEventListener("submit", event => {
    event.preventDefault();
    const value = byId("new-name").value.trim();
    if (!value) { byId("new-name").setCustomValidity("请输入称呼。"); byId("new-name").reportValidity(); return; }
    byId(role + "-name").value = value; nameDialog.close(); changed();
  });
  byId("new-name").addEventListener("input", () => byId("new-name").setCustomValidity(""));
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  function reset() {
    byId("audience").value = "general"; byId("style").value = "casual"; byId("minutes").value = "3";
    byId("host-name").value = "主持人"; byId("guest-name").value = "嘉宾";
    byId("host-voice").value = defaults[0] || ""; byId("guest-voice").value = defaults[1] || "";
    dropdowns.forEach(item => item.close());
  }
  return {sync, reset, configure(health) {
    voices = health.tts_protocol === "edge_tts" ? [["zh-CN-XiaoxiaoNeural", "晓晓 · 女声"], ["zh-CN-YunxiNeural", "云希 · 男声"]] : [["Cherry", "芊悦 Cherry"], ["Ethan", "晨煦 Ethan"], ["Serena", "苏瑶 Serena"]];
    defaults = health.default_voices;
    voiceDialog.querySelector("details").hidden = health.tts_protocol !== "edge_tts";
    reset();
  }};
})();
