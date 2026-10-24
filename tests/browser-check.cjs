// 运行前在本机安装 playwright，或通过 NODE_PATH 指向已有安装。
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
 const browser = await chromium.launch({headless:true, ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {})});
 const page = await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[]; page.on('pageerror', e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8000/');
 await page.locator('#connection-status').filter({hasText:'后台已连接'}).waitFor();
 assert.match(await page.locator('#connection-status').innerText(), /Edge TTS/);
 assert.equal(await page.locator('#host-voice').inputValue(),'zh-CN-XiaoxiaoNeural');
 await page.screenshot({path:'data/connected-desktop.png',fullPage:true});
 const id='11111111-1111-4111-8111-111111111111', rev='22222222-2222-4222-8222-222222222222';
 const audio='33333333-3333-4333-8333-333333333333';
 let version=1, hasAudio=false, stale=false, stage='done', failed=false, posts=[];
 const settings={audience:'general',style:'casual',target_minutes:3,host_voice:'zh-CN-XiaoxiaoNeural',guest_voice:'zh-CN-YunxiNeural'};
 const session=()=>({id,source_text:'用于界面隔离测试的材料',settings,current_revision:{id:rev,number:version,settings,script:{title:'<img src=x onerror=alert(1)>',turns:[{role:'host',text:'测试主持人'},{role:'guest',text:'测试嘉宾'}],outline:['提纲'],review_summary:'审校摘要'}},published_audio:hasAudio?{id:audio,revision_id:rev,available:true,url:`/artifacts/${audio}`,duration_seconds:9.24,tts_protocol:'edge_tts'}:null,audio_is_stale:stale,active_job:null});
 await page.route('**/health',route=>route.fulfill({json:{llm_configured:true,tts_configured:true,ffmpeg_available:true,tts_protocol:'edge_tts',default_voices:[settings.host_voice,settings.guest_voice],max_source_chars:20000}}));
 await page.route('**/chat',async route=>{
  const body=route.request().postDataJSON(); posts.push(body);
  if(body.action==='render_audio') {hasAudio=true;stale=false;}
  if(body.action==='revise') {version++;stale=hasAudio;}
  await route.fulfill({status:202,json:{job_id:id,session_id:id,trace_id:id,status:'queued'}});
 });
 await page.route('**/sessions/*',route=>route.fulfill({json:session()}));
 await page.route('**/sessions/*/artifacts',route=>route.fulfill({json:{artifacts:[{id:rev,revision_id:rev,kind:'script',available:true,url:`/artifacts/${rev}`}]}}));
 await page.route('**/jobs/*',route=>route.fulfill({json:{id,session_id:id,trace_id:id,stage,status:stage==='done'?(failed?'failed':'succeeded'):'running',action:posts.at(-1)?.action,error_message:failed?'音频制作失败，已有脚本和成品已保留。':null}}));
 await page.route('**/artifacts/*',route=>route.fulfill({status:404,body:''}));
 await page.reload();
 await page.locator('#use-example').click();
 await page.locator('#preview-script').check();
 stage='writer';
 await page.locator('#create-button').click();
 await page.locator('#running-title').filter({hasText:'正在编写对话'}).waitFor();
 assert.equal(posts.length,1);
 assert.equal(posts[0].preview_only,true);
 assert.equal(await page.locator('#create-button').isDisabled(),true);
 stage='done';
 await page.locator('#state-badge').filter({hasText:'脚本就绪'}).waitFor();
 assert.equal(await page.locator('#panel-script').innerText(),'主持人\n\n测试主持人\n\n嘉宾\n\n测试嘉宾');
 assert.equal(await page.locator('.episode-heading img').count(),0);
 await page.locator('#render-audio').click();
 await page.locator('#state-badge').filter({hasText:'音频就绪'}).waitFor();
 assert.deepEqual(Object.keys(posts.at(-1).settings).sort(),['guest_voice','host_voice']);
 assert.match(await page.locator('#audio-download').getAttribute('href'),/download=true/);
 await page.locator('#revision-text').fill('开场简短些');
 await page.locator('#revision-submit').click();
 await page.locator('#stale-banner').waitFor();
 assert.equal(posts.at(-1).action,'revise');
 assert.equal(posts.at(-1).preview_only,true);
 await page.reload();
 await page.locator('#revision-label').filter({hasText:'第 2 版'}).waitFor();
 failed=true;
 await page.locator('#render-new-audio').click();
 await page.locator('#failure-banner').waitFor();
 assert.equal(await page.locator('#audio-panel').isVisible(),true);
 await page.locator('#tab-outline').focus();
 await page.keyboard.press('ArrowRight');
 assert.equal(await page.locator('#tab-review').getAttribute('aria-selected'),'true');
 for(const width of [375,768,1024,1440]){
  await page.setViewportSize({width,height:1000});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow ${width}`);
 }
 await page.setViewportSize({width:375,height:900});
 await page.screenshot({path:'data/connected-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('PASS: real health and Edge defaults; isolated UI create/progress/preview/render/revise/stale/restore/failure/download/keyboard/XSS/responsive checks');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
