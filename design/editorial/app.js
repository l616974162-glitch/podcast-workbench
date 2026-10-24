/* 离线交互设计稿。禁止在此加入后端请求或伪造的自动生成结果。 */
(() => {
  const $ = id => document.getElementById(id);
  const sample = '阅读不一定要占据一整块时间。一篇文章里的观点，可以在散步、通勤或做家务时慢慢理解。\n\n把文章改编成双人播客时，主持人负责提出听众关心的问题，嘉宾负责解释与举例。两个人的对话，让抽象的概念更容易进入生活。\n\n表达可以更轻松，但事实应忠于原文。好的改编，是换一种方式理解材料，而不是增加没有依据的结论。';
  let busy = false;
  const states = {
    empty: {label:'等待创作',result:false},
    running: {label:'制作中布局示意',result:false},
    script: {label:'脚本就绪示意',result:true},
    audio: {label:'音频就绪示意',result:true},
    failed: {label:'失败状态示意',result:true},
    stale: {label:'旧版音频示意',result:true}
  };
  function message(text) {
    $('design-message').textContent = text;
    $('design-message').hidden = false;
  }
  function validate() {
    const length = [...$('source-text').value].length;
    const hasContent = $('source-text').value.trim().length > 0;
    const tooLong = length > 20000;
    const sameVoice = $('host-voice').value === $('guest-voice').value;
    $('character-count').textContent = length.toLocaleString();
    $('source-error').hidden = !tooLong;
    $('source-error').textContent = tooLong ? '材料超过 20,000 字符，请缩短后再制作。' : '';
    $('source-text').setAttribute('aria-invalid', String(tooLong));
    $('voice-error').hidden = !sameVoice;
    $('create-button').disabled = busy || !hasContent || tooLong || (sameVoice && !$('preview-script').checked);
    $('revision-submit').disabled = busy || !$('revision-text').value.trim();
    $('create-label').textContent = busy ? '正在制作…' : $('preview-script').checked ? '生成双人脚本' : '生成双人播客';
    $('episode-style').textContent = $('style').selectedOptions[0].textContent;
    $('episode-audience').textContent = $('audience').selectedOptions[0].textContent;
  }
  function setState(next) {
    if (!states[next]) return;
    busy = next === 'running';
    if (next !== 'empty' && !$('source-text').value.trim()) $('source-text').value = sample;
    document.body.classList.toggle('running-state', busy);
    document.querySelectorAll('[data-state]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.state === next)));
    $('state-badge').textContent = states[next].label;
    $('empty-output').hidden = next !== 'empty';
    $('running-output').hidden = !busy;
    $('result-output').hidden = !states[next].result;
    $('audio-panel').hidden = !['audio','stale'].includes(next);
    $('failure-banner').hidden = next !== 'failed';
    $('stale-banner').hidden = next !== 'stale';
    $('script-ready-callout').hidden = next !== 'script';
    $('revision-label').textContent = next === 'stale' ? '第 2 版' : '第 1 版';
    $('trace-details').hidden = next === 'empty';
    document.querySelectorAll('#material-form textarea,#material-form select,#material-form input').forEach(control => { control.disabled = busy; });
    $('use-example').disabled = busy;
    $('new-program').disabled = busy;
    $('design-message').hidden = true;
    validate();
  }
  function chooseTab(name, focus = false) {
    document.querySelectorAll('[data-tab]').forEach(button => {
      const selected = button.dataset.tab === name;
      button.setAttribute('aria-selected',String(selected));
      button.tabIndex = selected ? 0 : -1;
      $('panel-' + button.dataset.tab).hidden = !selected;
      if (selected && focus) button.focus();
    });
  }
  document.querySelectorAll('[data-state]').forEach(button => button.addEventListener('click', () => setState(button.dataset.state)));
  $('source-text').addEventListener('input',validate);
  $('revision-text').addEventListener('input',validate);
  ['audience','style','host-voice','guest-voice','preview-script'].forEach(id => $(id).addEventListener('change',validate));
  function useExample() { $('source-text').value = sample; validate(); $('source-text').focus(); }
  $('use-example').addEventListener('click',useExample);
  $('empty-example').addEventListener('click',useExample);
  $('material-form').addEventListener('submit', event => {
    event.preventDefault();
    validate();
    if ($('create-button').disabled) return;
    setState('running');
    message('已切换到制作中的设计状态。没有发送请求，也不会自动出现生成结果；请使用顶部状态切换查看后续布局。');
  });
  $('revision-form').addEventListener('submit',event => {
    event.preventDefault();
    if (!$('revision-text').value.trim() || busy) return;
    setState('running');
    message('修改中的布局示意。原材料和设置仍保留，没有提交到后端。');
  });
  document.querySelectorAll('[data-edit]').forEach(button => button.addEventListener('click',() => { $('revision-text').value = button.dataset.edit; validate(); $('revision-text').focus(); }));
  $('change-voices').addEventListener('click',() => { $('host-voice').focus(); message('音色可以在材料区调整。正式对接后，仅改音色会复用当前脚本。'); });
  ['render-audio','render-new-audio','retry-audio'].forEach(id => $(id).addEventListener('click',() => { setState('running'); message('配音任务布局示意，没有调用语音服务。'); }));
  $('script-download').addEventListener('click',() => message('脚本下载入口示意。正式对接后使用当前任务返回的产物地址，不下载设计示例。'));
  $('new-program').addEventListener('click',() => { $('material-form').reset(); $('source-text').value = ''; $('revision-text').value = ''; setState('empty'); chooseTab('script'); $('source-text').focus(); });
  document.querySelectorAll('[data-tab]').forEach(button => {
    button.addEventListener('click',() => chooseTab(button.dataset.tab));
    button.addEventListener('keydown',event => {
      const names = ['script','outline','review'];
      let index = names.indexOf(button.dataset.tab);
      if (event.key === 'ArrowRight') index = (index + 1) % names.length;
      else if (event.key === 'ArrowLeft') index = (index + names.length - 1) % names.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = names.length - 1;
      else return;
      event.preventDefault(); chooseTab(names[index],true);
    });
  });
  $('help-open').addEventListener('click',() => $('help-dialog').showModal());
  $('help-close').addEventListener('click',() => $('help-dialog').close());
  [17,24,12,30,36,18,25,14,32,39,23,16,29,34,19,11,24,35,30,17,26,37,18,32,14,23,33,17,27,40,24,15,31,22,35,17,26,13,30,20,33,24,16,29,19,36,23,13].forEach(height => {
    const mark = document.createElement('i'); mark.style.height = height + 'px'; $('waveform').append(mark);
  });
  setState(new URLSearchParams(location.search).get('state') || 'empty');
})();
