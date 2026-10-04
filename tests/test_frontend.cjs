// Dependency-free DOM logic checks, not a substitute for visual browser QA.
// Run with: node --test tests/test_frontend.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.className = ''; this.value = ''; this.checked = false; this.hidden = false;
    this.open = false; this.disabled = false; this.listeners = {}; this._text = '';
    this.classList = { toggle: (name, enabled) => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      if (enabled) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    } };
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get parentElement() { return this.parentNode; }
  get isConnected() { return !!this.root || !!this.parentNode?.isConnected; }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = []; this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'value') this.value = ''; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { this.focused = true; }
  scrollIntoView() { this.scrolled = true; }
  querySelectorAll(selector) {
    const [base, pseudo] = selector.split(':');
    const matches = node => (base.startsWith('.') ? node.className.split(' ').includes(base.slice(1)) : node.tagName === base)
      && (pseudo !== 'checked' || node.checked);
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function fixture(motion) {
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8');
  const body = new Element('body'); body.root = true;
  const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  body.append(...ids.values());
  ids.get('empty').append(new Element('strong'), new Element('p'));
  ids.get('quality').value = 'best'; ids.get('compression').value = 'balanced';
  ids.get('media-format').value = 'mp4'; ids.get('storage-target').value = 'local';
  const context = vm.createContext({
    document: { body, getElementById: id => ids.get(id), createElement: tag => new Element(tag),
      createElementNS: (_, tag) => new Element(tag), addEventListener() {} },
    window: { addEventListener() {}, StreamFetchMotion:motion }, URLSearchParams, Intl,
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    // Keep auto-login pending; each test drives the state itself.
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8'), context);
  return { context, get: id => ids.get(id), run: code => vm.runInContext(code, context) };
}

test('one transcript CTA follows all lifecycle states without provider details',()=>{
  const f=fixture();
  for(const [status,label,disabled] of [[undefined,'Generate Transcript',false],['queued','Waiting',true],['transcribing','Generating',true],['failed','Retry Transcript',false],['completed','View Transcript',false]]){
    f.context.rows=[{job_id:'transcript',state:'ready',filename:'video.mp4',transcript:{status,progress_percent:42,eta_seconds:125,model:'technical-model',provider:'cloudflare'}}];
    f.run('renderHistory(rows)');
    const box=f.get('results').querySelector('.transcript-box');
    assert.equal(box.querySelectorAll('.transcript-cta').length,1);
    assert.equal(box.querySelector('.transcript-cta').textContent,label);
    assert.equal(box.querySelector('.transcript-cta').disabled,disabled);
    assert.doesNotMatch(box.textContent,/cloudflare|technical-model|chunk/i);
    if(status==='transcribing'){assert.match(box.textContent,/42%.*About 3 min/);assert.equal(box.querySelector('progress').value,42);}
    if(status==='completed')assert.equal(box.querySelector('a').href,'/api/recordings/transcript/transcript/view');
  }
});
test('ETA is rounded, ages from its last update, and handles legacy missing estimates',()=>{
  const f=fixture();
  assert.equal(f.run('transcriptETA({})'),'Estimating time…');
  assert.equal(f.run('transcriptETA({eta_seconds:59})'),'Less than a minute remaining');
  assert.equal(f.run('transcriptETA({eta_seconds:61})'),'About 2 min remaining');
  assert.equal(f.run('transcriptETA({eta_seconds:120,updated_at:Date.now()/1000-100})'),'Less than a minute remaining');
});

test('download progress shows the real track and attempt, and resets when unknown', () => {
  const f = fixture();
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'recording',progress_phase:'audio',progress_percent:42.5,download_attempt:2,download_attempts:3})`);
  assert.equal(f.get('download-progress').hidden, false);
  assert.equal(f.get('progress-label').textContent, 'Mengunduh audio · Percobaan 2/3');
  assert.equal(f.get('progress-value').textContent, '42.5%');
  assert.equal(f.get('progress-bar').value, 42.5);
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'recording',progress_percent:null})`);
  assert.equal(f.get('progress-value').textContent, '');
  assert.equal(f.get('progress-bar').value, '');
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'finalizing',progress_percent:100})`);
  assert.equal(f.get('progress-value').textContent, '');
  assert.equal(f.get('progress-label').textContent, 'Menggabungkan dan memproses');
  f.run(`renderDownloadProgress({source:'youtube',is_live:true,state:'recording'})`);
  assert.equal(f.get('download-progress').hidden, true);
});

test('status uses semantic colors and retains the entire diagnostic text', () => {
  const f = fixture();
  for (const [state, tone] of [['recording', 'working'], ['waiting', 'waiting'], ['ready', 'success'], ['failed', 'error']]) {
    f.context.job = { source: 'youtube', is_live: false, state, detail: 'YouTube belum siap.\nFile parsial tetap disimpan. '.repeat(10) };
    f.run('active=job; renderOperationalStatus()');
    assert.equal(f.get('status-monitor').dataset.tone, tone);
    assert.equal(f.get('status-detail').textContent, f.context.job.detail);
    assert.equal(f.get('duration-label').textContent, 'WAKTU PROSES');
  }
});

test('format controls group media settings and hide technical video specs for MP3', () => {
  const f = fixture();
  f.run(`setMode('youtube')`);
  assert.equal(f.get('format-field').hidden, false);
  assert.match(f.get('media-options').className, /has-format/);
  assert.equal(f.get('compression-details').hidden, false);
  assert.match(f.get('compression-spec').textContent, /CRF 23/);
  f.get('media-format').value = 'mp3'; f.run('updateFormatControls()');
  assert.equal(f.get('compression').disabled, true);
  assert.equal(f.get('quality').disabled, true);
  assert.equal(f.get('compression-details').hidden, true);
  f.run(`setMode('oryx')`);
  assert.equal(f.get('format-field').hidden, true);
  assert.equal(f.get('compression').disabled, false);
});

test('history keeps expanded errors and selection through refresh without interpreting HTML', () => {
  const f = fixture();
  f.context.rows = [{ job_id: 'error-job', source: 'youtube', state: 'failed', compression: 'compact',
    detail: '<script>alert(1)</script>\n' + 'Arsip live belum siap. '.repeat(15) }];
  f.run('historyRows=rows; renderHistory(rows)');
  const table = f.get('results');
  assert.equal(table.children[0].children.length, 6);
  assert.equal(table.querySelector('.status-badge').dataset.tone, 'error');
  assert.equal(table.querySelector('.preset-badge').textContent, 'Hemat');
  assert.equal(table.querySelector('.recording-detail-body').children[0].textContent, f.context.rows[0].detail);
  assert.equal(table.querySelector('script'), null);
  assert.equal(table.querySelector('.action-trigger'), null); // No empty menu for normal users.
  table.querySelector('.recording-details').open = true;
  table.querySelector('.history-select').checked = true;
  f.run('renderHistory(rows)');
  assert.equal(table.querySelector('.recording-details').open, true);
  assert.equal(table.querySelector('.history-select').checked, true);
  table.querySelector('.recording-details').open = false;
  f.run('renderHistory(rows)');
  assert.equal(table.querySelector('.recording-details').open, false);
});

test('legacy presets are not invented and MP3 retains its own label and download action', () => {
  const f = fixture();
  f.context.rows = [
    { job_id: 'legacy', source: 'oryx', state: 'ready', filename: 'old.mp4' },
    { job_id: 'audio', source: 'youtube', state: 'ready', filename: 'audio.mp3', compression: 'original' },
  ];
  f.run('historyRows=rows; renderHistory(rows)');
  assert.equal(f.get('results').children[0].querySelector('.preset-badge').textContent, 'Preset tidak tercatat');
  assert.equal(f.get('results').children[1].querySelector('.preset-badge').textContent, 'Audio MP3');
  assert.equal(f.get('results').querySelectorAll('.download-direct').length, 2);
  assert.equal(f.get('results').querySelectorAll('.transcript-generate').length, 1);
});

test('polling retains the terminal result even when history filters hide the completed job', async () => {
  const f = fixture();
  const job = { job_id: 'download', source: 'youtube', is_live: false, state: 'recording', compression: 'balanced' };
  let activeJob = job, row = job;
  f.context.fetch = async url => ({
    ok: true, status: 200, headers: { get: () => 'application/json' },
    json: async () => url === '/api/status'
      ? { active: activeJob, online: true, queued: 0, disk: { available: true, can_record: true, free: 100, total: 200 } }
      : { recordings: url.includes('state=recording') && row.state !== 'recording' ? [] : [row], total: 1 },
  });
  f.run(`csrf='test'; mode='youtube'`);
  await f.run('refresh()');
  assert.equal(f.get('status-title').textContent, 'Mengunduh');
  f.get('filter-state').value = 'recording';
  activeJob = null; row = { ...job, state: 'ready', filename: 'result.mp4', detail: 'File siap digunakan.' };
  await f.run('refresh()');
  assert.equal(f.get('status-monitor').dataset.tone, 'success');
  assert.equal(f.get('status-detail').textContent, 'File siap digunakan.');
  assert.equal(f.get('record').disabled, false);
  assert.equal(f.get('stop').disabled, true);
  assert.equal(f.get('download-progress').hidden, true);
  assert.equal(f.get('results').children.length, 0);
  assert.equal(f.run('historyRows.length'), 0);
});

function captureAPI(f,fail=false){
  const requests=[];
  f.context.fetch=async(url,options)=>{
    if(url==='/api/record'){
      requests.push(JSON.parse(options.body));
      return {ok:!fail,status:fail?409:202,headers:{get:()=> 'application/json'},json:async()=>fail?{error:'Masih ada capture berjalan'}:{job_id:'new-job'}};
    }
    return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>
      url==='/api/status'?{active:null,online:true,queued:1,archive_enabled:true,disk:{can_record:true,free:100,total:200}}:
      url==='/api/sources'?{sources:[]}:{recordings:[],total:0}};
  };
  return requests;
}
test('successful capture collapses Source, clears only submitted URL, preserves selections and focuses new job',async()=>{
  const f=fixture(),requests=captureAPI(f);
  f.run("mode='youtube';online=true;disk={can_record:true}");
  f.get('youtube-url').value='https://youtu.be/abcdefghijk';f.get('tiktok-url').value='https://www.tiktok.com/@other/live';
  f.get('quality').value='720';f.get('compression').value='compact';f.get('storage-target').value='archive';f.get('note').value='Dialog Batam';
  f.get('filter-q').value='old job';
  await f.get('record').onclick();
  assert.equal(requests[0].url,'https://youtu.be/abcdefghijk');
  assert.equal(f.get('capture-content').hidden,true);assert.equal(f.get('capture-another').hidden,false);
  assert.equal(f.get('youtube-url').value,'');assert.match(f.get('tiktok-url').value,/@other/);
  assert.equal(f.get('quality').value,'720');assert.equal(f.get('compression').value,'compact');
  assert.equal(f.get('storage-target').value,'archive');assert.equal(f.get('media-format').value,'mp4');assert.equal(f.get('note').value,'Dialog Batam');
  assert.equal(f.get('filter-q').value,'');
  assert.equal(f.get('results').children.length,1);
  assert.equal(f.get('results').children[0].dataset.jobId,'new-job');
  assert.match(f.get('results').children[0].className,/focused-job/);
  assert.equal(f.run('focusPending'),false);
  f.get('capture-another').onclick();assert.equal(f.get('capture-content').hidden,false);
  assert.equal(f.get('youtube-url').focused,true);
});
test('submission failure keeps Source expanded and every input intact',async()=>{
  const f=fixture();captureAPI(f,true);
  f.run("mode='tiktok';online=true;disk={can_record:true}");
  f.get('tiktok-url').value='https://www.tiktok.com/@batam/live';f.get('quality').value='480';f.get('note').value='Judul';
  await f.get('record').onclick();
  assert.equal(f.get('capture-content').hidden,false);assert.equal(f.get('capture-another').hidden,true);
  assert.match(f.get('tiktok-url').value,/@batam/);assert.equal(f.get('quality').value,'480');assert.equal(f.get('note').value,'Judul');
  assert.match(f.get('notice').textContent,/Masih ada capture/);assert.equal(f.run('focusedJobId'),null);
});
test('TikTok clears submitted URL and live capture preserves reusable source selection',()=>{
  const f=fixture();
  f.get('tiktok-url').value='https://www.tiktok.com/@batam/live';f.get('source').value='studio';
  f.run("captureSubmitted({job_id:'tiktok-job'},{source:'tiktok',quality:'best',format:'mp4',compression:'balanced',storage:'local'})");
  assert.equal(f.get('tiktok-url').value,'');
  assert.equal(f.get('results').children[0].focused,true);assert.equal(f.get('results').children[0].scrolled,true);
  f.run("captureSubmitted({job_id:'live-job'},{source:'oryx',source_id:'studio',quality:'best',format:'mp4',compression:'balanced',storage:'local'})");
  assert.equal(f.get('source').value,'studio');
});
test('Settings navigation separates advanced controls and returning preserves the capture form',async()=>{
  const f=fixture();captureAPI(f);
  await f.run('enter({is_admin:false})');
  assert.equal(f.get('admin-panel').hidden,true);
  f.get('youtube-url').value='draft';
  f.get('nav-admin').onclick();
  assert.equal(f.get('control-room').hidden,true);assert.equal(f.get('admin-view').hidden,false);
  assert.equal(f.get('page-title').textContent,'Settings');assert.equal(f.get('nav-admin').attributes['aria-current'],'page');
  f.get('nav-control').onclick();
  assert.equal(f.get('control-room').hidden,false);assert.equal(f.get('admin-view').hidden,true);
  assert.equal(f.get('youtube-url').value,'draft');
  await f.run('enter({is_admin:true})');assert.equal(f.get('admin-panel').hidden,false);
  f.run('showLogin()');assert.equal(f.get('control-room').hidden,false);assert.equal(f.get('capture-content').hidden,false);
});
test('workspace markup places History immediately after Source and configuration only in Admin',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  assert.match(html,/<\/div><\/section>\s*<section id="history-panel"/);
  const control=html.slice(html.indexOf('<section id="control-room"'),html.indexOf('<section id="admin-view"'));
  const admin=html.slice(html.indexOf('<section id="admin-view"'));
  for(const id of ['capture-panel','quality','media-format','compression','storage-target','history-panel','status-monitor','stop','marker-controls'])assert.ok(control.includes('id="'+id+'"'),id);
  for(const id of ['password-form','source-form','disk-meter']){assert.ok(admin.includes('id="'+id+'"'));assert.ok(!control.includes('id="'+id+'"'));}
  assert.ok(admin.includes('TRANSCRIPTION_PROVIDER'));assert.ok(!control.includes('TRANSCRIPTION_PROVIDER'));
});

test('StreamFetch branding retains the existing mark and removes old user-facing names',()=>{
  const root=path.resolve(__dirname,'../app/static');
  for(const file of ['index.html','transcript.html']){
    const html=fs.readFileSync(path.join(root,file),'utf8');
    assert.doesNotMatch(html,/Grabby|GRABBY|Control Room|TikTok Live/);
    assert.match(html,/<img class="brand-logo" src="\/static\/favicon.svg" alt=""><strong>StreamFetch<\/strong>/);
  }
  const f=fixture();f.run("showSection('control')");assert.equal(f.get('page-title').textContent,'StreamFetch');
  f.run("showSection('admin')");assert.equal(f.get('page-title').textContent,'Settings');
});
test('segmented sources preserve mode behavior and expose the selected state accessibly',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  assert.ok(html.indexOf('id="tab-youtube"')<html.indexOf('id="tab-tiktok"'));
  assert.ok(html.indexOf('id="tab-tiktok"')<html.indexOf('id="tab-instagram"'));
  assert.ok(html.indexOf('id="tab-instagram"')<html.indexOf('id="tab-oryx"'));
  const f=fixture();
  for(const source of ['youtube','tiktok','instagram','oryx']){
    f.run(`setMode('${source}')`);
    for(const tab of ['youtube','tiktok','instagram','oryx'])assert.equal(f.get('tab-'+tab).attributes['aria-pressed'],String(tab===source));
    assert.equal(f.get(source+'-fields').hidden,false);
  }
  assert.equal(f.run("sourceDisplayName({source:'tiktok',source_name:'TikTok Live'})"),'TikTok');
  assert.equal(f.run("sourceDisplayName({source:'instagram'})"),'Instagram');
  assert.equal(f.run("sourceDisplayName({source:'oryx',source_name:'PRO 2 RRI BATAM'})"),'PRO 2 RRI BATAM');
});
test('Instagram source input, submission collapse, and finite social progress reuse existing UI',()=>{
  const f=fixture();f.run("setMode('instagram')");
  f.get('instagram-url').value='https://instagram.com/reel/ABC/';
  assert.equal(f.run('sourceInput().value'),'https://instagram.com/reel/ABC/');
  f.get('quality').value='720';
  f.run("captureSubmitted({job_id:'insta-job'},{source:'instagram',quality:'720',format:'mp4',compression:'balanced'})");
  assert.equal(f.get('instagram-url').value,'');
  assert.equal(f.get('capture-content').hidden,true);
  assert.equal(f.get('quality').value,'720');
  for(const source of ['tiktok','instagram']){
    f.context.job={source,is_live:false,state:'recording',progress_percent:50,progress_phase:'video'};
    f.run('renderDownloadProgress(job)');
    assert.equal(f.get('download-progress').hidden,false);
    assert.equal(f.run('jobStateName(job)'),'Mengunduh');
  }
  assert.equal(f.run("isFiniteDownload({source:'tiktok',is_live:true})"),false);
});
test('compact jobs retain every state, title, full diagnostics, metadata and secondary stop control',()=>{
  const f=fixture();
  for(const state of ['starting','recording','waiting','stopping','finalizing','ready','failed','interrupted']){
    f.context.rows=[{job_id:'job',state,source:'tiktok',source_name:'TikTok Live',note:'Dialog Batam',detail:'Detail lengkap',filename:state==='ready'?'dialog.mp4':undefined}];
    f.run('renderHistory(rows);active=rows[0];renderOperationalStatus()');
    assert.equal(f.get('results').querySelector('.recording-heading').querySelector('.status-badge').textContent,f.run('jobStateName(rows[0])'));
    assert.equal(f.get('status-job-title').textContent,state==='ready'?'dialog.mp4':'Dialog Batam');
    assert.equal(f.get('status-detail').textContent,'Detail lengkap');
    assert.equal(f.get('active-source').textContent,'TikTok');
  }
  const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
  assert.match(css,/grid-template-areas:'state content action' '\. metadata action' 'markers markers markers'/);
  assert.match(css,/\.workspace-page #status-monitor>\.panel-heading\{display:flex;grid-area:state/);
  assert.match(css,/\.workspace-page #stop\{height:auto;min-height:32px/);
  assert.match(css,/\.workspace-page #capture-panel \.capture-options \.form-grid\{display:contents\}/);
});

test('shell groups labelled destinations separately from secondary utilities and keeps navigation state',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  const header=html.match(/<header class="app-header">.*?<\/header>/)[0];
  assert.match(header,/<nav class="workspace-nav" aria-label="Primary navigation">/);
  for(const [id,label,target] of [['nav-control','Workspace','control-room'],['nav-admin','Settings','admin-view']]){
    const button=header.match(new RegExp('<button id="'+id+'".*?</button>'))[0];
    assert.match(button,new RegExp('aria-label="'+label+'"'));assert.match(button,new RegExp('aria-controls="'+target+'"'));
    assert.match(button,new RegExp('<span class="nav-label">'+label+'</span>'));
    assert.match(button,/<svg.*aria-hidden="true"/);
  }
  assert.ok(header.indexOf('</nav>')<header.indexOf('id="logout"'));
  assert.match(header,/id="logout".*aria-label="Keluar"/);
  assert.doesNotMatch(header,/header-right|>●</);
  const f=fixture();
  f.get('nav-admin').onclick();assert.equal(f.get('nav-admin').attributes['aria-current'],'page');assert.equal(f.get('nav-control').attributes['aria-current'],undefined);
  f.get('nav-control').onclick();assert.equal(f.get('nav-control').attributes['aria-current'],'page');assert.equal(f.get('nav-admin').attributes['aria-current'],undefined);
});

test('shell retains meaningful online/offline status and long job state without altering actions',()=>{
  const f=fixture();
  f.run("online=false;renderOperationalStatus()");assert.equal(f.get('connection').textContent,'Offline');assert.equal(f.get('connection').attributes['aria-label'],'Sistem offline');
  f.context.longTitle='JudulTanpaSpasi'.repeat(30);
  f.run("online=true;active={job_id:'long',source:'instagram',state:'recording',is_live:false,note:longTitle,progress_percent:50};controls()");
  assert.equal(f.get('connection').textContent,'Online');assert.equal(f.get('connection').attributes['aria-label'],'Sistem siap');
  assert.equal(f.get('state').textContent,'Mengunduh');assert.equal(f.get('status-job-title').title,f.context.longTitle);
  assert.equal(f.get('download-progress').hidden,false);assert.equal(f.get('stop').disabled,false);
  f.run("active.state='finalizing';controls()");assert.equal(f.get('stop').disabled,true);
});

test('mobile shell and island use dedicated composition, touch targets, and local history scrolling',()=>{
  const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
  const shell=css.slice(css.indexOf('/* Application shell:'));
  assert.match(shell,/\.app-header\{grid-template-columns:minmax\(0,1fr\) auto auto/);
  assert.match(shell,/width:44px;min-height:44px/);assert.match(shell,/env\(safe-area-inset-left\)/);
  assert.match(shell,/grid-template-areas:'state action' 'content content' 'metadata metadata' 'markers markers'/);
  assert.match(shell,/grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(shell,/\.workspace-page \.table-scroll\{overflow-x:auto;overscroll-behavior-x:contain\}/);
  assert.match(shell,/overflow-wrap:anywhere/);assert.doesNotMatch(shell,/backdrop-filter:blur|overflow-x:hidden/);
});

test('Settings gear is labelled and existing configuration is grouped without changing IDs',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  const button=html.match(/<button id="nav-admin".*?<\/button>/)[0];
  assert.match(button,/aria-label="Settings"/);assert.match(button,/title="Settings"/);
  assert.match(button,/aria-controls="admin-view"/);assert.match(button,/<svg.*aria-hidden="true"/);
  assert.doesNotMatch(button,/>Admin</);
  const settings=html.slice(html.indexOf('<section id="admin-view"'));
  assert.match(settings,/aria-label="Settings"/);
  assert.ok(settings.indexOf('<h2>Saved Sources</h2>')<settings.indexOf('<h2>Storage</h2>'));
  assert.ok(settings.indexOf('<h2>Storage</h2>')<settings.indexOf('<h2>Security</h2>'));
  assert.ok(settings.indexOf('<h2>Security</h2>')<settings.indexOf('<h2>System</h2>'));
  for(const id of ['source-form','password-form','disk-free','disk-limit','disk-meter'])assert.equal([...html.matchAll(new RegExp('id="'+id+'"','g'))].length,1);
  assert.doesNotMatch(settings,/Pengaturan admin|Sistem &amp; pengaturan default/);
});
test('Settings ends with a minimal product footer and preserves the existing safe GitHub link',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  const footer=html.match(/<footer class="product-footer".*?<\/footer>/)[0];
  assert.ok(html.indexOf(footer)>html.indexOf('<h2>System</h2>'));
  assert.match(footer,/src="\/static\/favicon.svg"/);assert.match(footer,/<strong>StreamFetch<\/strong>/);
  assert.match(footer,/href="https:\/\/github.com\/adamneve1"/);
  assert.match(footer,/target="_blank" rel="noopener noreferrer"/);
  assert.match(footer,/aria-label="GitHub \(opens in a new tab\)"/);
  assert.match(footer,/>GitHub <svg/);
  assert.doesNotMatch(footer,/@adamneve1|Built by|About|credit-link/);
});
test('login composition remains minimal with accessible authentication and a quiet safe developer credit',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  const login=html.slice(html.indexOf('<div id="login"'),html.indexOf('<div id="workspace"'));
  assert.match(login,/aria-label="Masuk StreamFetch" aria-busy="false"/);
  assert.match(login,/<label for="password">Password<\/label>/);
  assert.match(login,/name="password" type="password" autocomplete="current-password" aria-describedby="login-error" autofocus required/);
  assert.match(login,/<button id="login-submit" class="primary" type="submit">Masuk<\/button>/);
  assert.match(login,/id="login-error" role="alert"/);
  assert.doesNotMatch(login,/<h1|class="muted"|Welcome|<footer|feature|slogan/);
  assert.match(login,/<\/form><p id="login-credit" class="login-credit">Built with 💖 by /);
  assert.match(login,/href="https:\/\/github.com\/adamneve1" target="_blank" rel="noopener noreferrer"/);
  assert.match(login,/>@adamneve1<\/a>/);
  const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8').split('/* Compact authentication composition')[1];
  assert.match(css,/max-width:344px/);assert.match(css,/min-height:100dvh/);
  assert.match(css,/align-items:safe center/);assert.match(css,/font-size:16px/);
  assert.match(css,/min-height:44px/);assert.match(css,/input\[aria-invalid=true\]/);
  assert.doesNotMatch(css,/gradient|backdrop-filter/);
  assert.match(css,/grid-template-rows:minmax\(min-content,1fr\) auto/);
  assert.match(css,/#login-error:empty\{display:block;min-height:1.5em/);
  assert.match(css,/\.login-credit\{margin:0;font-size:11px/);
});

test('login submission shows busy state, preserves the API payload, and clears password only on success',async()=>{
  const f=fixture();let finish;const requests=[];let entered;
  f.context.fetch=(url,options)=>{requests.push({url,options});return new Promise(resolve=>finish=resolve);};
  f.context.enter=async auth=>{entered=auth;};
  f.get('password').value='test-password';let prevented=false;
  const submission=f.get('login-form').onsubmit({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(f.get('login-submit').disabled,true);
  assert.equal(f.get('login-submit').textContent,'Masuk…');assert.equal(f.get('login-form').attributes['aria-busy'],'true');
  await f.get('login-form').onsubmit({preventDefault(){}});assert.equal(requests.length,1);
  assert.equal(requests[0].url,'/api/login');assert.equal(requests[0].options.method,'POST');
  assert.deepEqual(JSON.parse(requests[0].options.body),{password:'test-password'});
  finish({status:200,ok:true,headers:{get:()=> 'application/json'},json:async()=>({csrf:'test-csrf',is_admin:true})});
  await submission;
  assert.equal(f.run('csrf'),'test-csrf');assert.equal(entered.is_admin,true);
  assert.equal(f.get('password').value,'');assert.equal(f.get('login-submit').disabled,false);
  assert.equal(f.get('login-submit').textContent,'Masuk');assert.equal(f.get('login-form').attributes['aria-busy'],'false');
});

test('login errors remain visible, retain input, support correction, and returning to login focuses password',async()=>{
  const f=fixture();f.get('password').value='wrong';
  f.context.fetch=async()=>({status:401,ok:false,headers:{get:()=> 'application/json'},json:async()=>({error:'Password salah.'})});
  await f.get('login-form').onsubmit({preventDefault(){}});
  assert.equal(f.get('password').value,'wrong');assert.equal(f.get('login-error').textContent,'Password salah.');
  assert.equal(f.get('password').attributes['aria-invalid'],'true');assert.equal(f.get('login-submit').disabled,false);
  assert.equal(f.get('password').focused,true);
  f.get('password').oninput();assert.equal(f.get('login-error').textContent,'');assert.equal(f.get('password').attributes['aria-invalid'],undefined);
  f.run('showLogin()');assert.equal(f.get('password').focused,true);assert.equal(f.get('login').hidden,false);assert.equal(f.get('workspace').hidden,true);
});
test('wide Source form bounds input/options and connects the action row without overriding smaller layouts',()=>{
  const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
  const wide=css.slice(css.indexOf('/* Wide Source cards')).split('/* Only admission')[0];
  assert.match(wide,/@media\(min-width:1100px\)/);
  assert.match(wide,/#capture-content\{width:100%;max-width:1160px\}/);
  assert.match(wide,/\.source-field\{max-width:760px\}/);
  assert.match(wide,/#oryx-fields\{max-width:380px\}/);
  assert.match(wide,/\.capture-options\{max-width:760px;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(wide,/#format-field\{grid-column:1\/-1/);
  assert.match(wide,/\.capture-footer\{max-width:760px;justify-content:flex-start/);
  assert.doesNotMatch(wide,/display:none|position:absolute/);
  const f=fixture();f.get('quality').value='720';f.get('storage-target').value='archive';f.get('note').value='Dialog';
  for(const source of ['youtube','tiktok','instagram']){f.run(`setMode('${source}')`);assert.equal(f.get('quality').value,'720');assert.equal(f.get('storage-target').value,'archive');assert.equal(f.get('note').value,'Dialog');}
  assert.equal(f.get('compression-details').hidden,false);
});

function motionSpy(){
 const calls=[];return {calls,reduced:()=>false,reset(){calls.push(['reset']);},
  loginReveal(element){calls.push(['loginReveal',element]);},
  loginError(element){calls.push(['loginError',element]);},
  loginSuccess(element){calls.push(['loginSuccess',element]);},
  source(panel,collapsed,change,options){calls.push(['source',collapsed,options,panel.dataset.collapsed]);change();},
  island(panel,key,phase,change){calls.push(['island',key,phase]);change();},
  progress(panel,value){if(value===null)panel.removeAttribute('value');else panel.value=value;},
 };
}
test('accepted submission morphs Source only after admission and immediately publishes the job',async()=>{
 const motion=motionSpy(),f=fixture(motion);let accept;
 f.run("mode='youtube';online=true;disk={can_record:true}");
 f.get('youtube-url').value='https://youtu.be/abcdefghijk';
 f.context.fetch=()=>new Promise(resolve=>{accept=resolve;});
 const request=f.get('record').onclick();
 assert.equal(f.get('capture-content').hidden,false);
 assert.equal(f.get('capture-panel').attributes['aria-busy'],'true');
 assert.equal(f.get('record').textContent,'Mengirim…');
 assert.equal(motion.calls.filter(c=>c[0]==='source').length,0);
 accept({ok:true,status:202,headers:{get:()=> 'application/json'},json:async()=>({job_id:'accepted'})});
 await request;
 const admission=motion.calls.find(c=>c[0]==='source');
 assert.equal(admission[1],true);assert.equal(admission[2].accepted,true);
 assert.equal(admission[2].job.dataset.jobId,'accepted');
 assert.equal(f.get('status-job-title').textContent,'YouTube');
 assert.equal(f.get('capture-panel').attributes['aria-busy'],'false');
 f.get('capture-another').onclick();
 assert.equal(motion.calls.filter(c=>c[0]==='source').at(-1)[1],false);
 assert.equal(f.get('capture-content').hidden,false);
});
test('login entrance and errors use scoped motion while keeping authentication immediate and focus restored',async()=>{
 const motion=motionSpy(),f=fixture(motion);
 assert.equal(motion.calls[0][0],'loginReveal');
 f.get('password').value='wrong';
 f.context.fetch=async()=>({status:401,ok:false,headers:{get:()=> 'application/json'},json:async()=>({error:'Password salah.'})});
 await f.get('login-form').onsubmit({preventDefault(){}});
 assert.equal(motion.calls.some(c=>c[0]==='loginError'&&c[1]===f.get('login-error')),true);
 assert.equal(f.get('password').focused,true);assert.equal(f.get('password').value,'wrong');
 const reveals=motion.calls.filter(c=>c[0]==='loginReveal').length;
 f.run('showLogin()');assert.equal(motion.calls.filter(c=>c[0]==='loginReveal').length,reveals);
 captureAPI(f);await f.run('enter({is_admin:false})');
 assert.equal(f.get('workspace').hidden,false);assert.equal(f.get('login').hidden,true);
 assert.equal(motion.calls.some(c=>c[0]==='loginSuccess'),true);
 f.run('showLogin()');assert.equal(motion.calls.filter(c=>c[0]==='loginReveal').length,reveals+1);
});
test('rejected submission never runs the accepted morph or discards input',async()=>{
 const motion=motionSpy(),f=fixture(motion);captureAPI(f,true);
 f.run("mode='youtube';online=true;disk={can_record:true}");f.get('youtube-url').value='draft';
 await f.get('record').onclick();
 assert.equal(motion.calls.some(c=>c[0]==='source'&&c[1]),false);
 assert.equal(f.get('capture-content').hidden,false);assert.equal(f.get('youtube-url').value,'draft');
});
test('island maps capture and transcript phases without exposing provider details',()=>{
 const motion=motionSpy(),f=fixture(motion);
 for(const [state,transcript,phase] of [['starting',null,'queued'],['recording',null,'downloading'],['finalizing',null,'processing'],['ready',{status:'queued'},'queued'],['ready',{status:'transcribing',progress_percent:35,eta_seconds:120,provider:'cloudflare'},'transcribing'],['ready',{status:'completed'},'completed'],['ready',{status:'failed',error:'Try again'},'failed']]){
  f.context.job={job_id:'island',source:'instagram',is_live:false,state,transcript};
  f.run('active=job;controls()');
  assert.equal(motion.calls.filter(c=>c[0]==='island').at(-1)[2],phase);
  assert.doesNotMatch(f.get('status-detail').textContent,/cloudflare/i);
 }
});
test('transcription started from History is observed by the same island without changing queue requests',async()=>{
 const motion=motionSpy(),f=fixture(motion);const calls=[];
 f.context.fetch=async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return {ok:true,status:202,headers:{get:()=> 'application/json'},json:async()=>({})};};
 await f.run("generateTranscript({job_id:'old-recording',state:'ready',source:'tiktok',filename:'old.mp4'})");
 assert.deepEqual(calls,[['/api/recordings/old-recording/transcript',{}]]);
 assert.equal(f.get('state').textContent,'Waiting');assert.equal(f.get('status-job-title').textContent,'old.mp4');
 assert.equal(motion.calls.filter(c=>c[0]==='island').at(-1)[2],'queued');
});
test('GSAP and Flip load locally and only the workspace opts into the motion system',()=>{
 const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'app/static/index.html'),'utf8');
 const scripts=[...html.matchAll(/<script defer src="([^"]+)"/g)].map(match=>match[1]);
 assert.deepEqual(scripts,['/static/vendor/gsap.min.js','/static/vendor/Flip.min.js','/static/motion.js','/static/app.js']);
 assert.equal(fs.readFileSync(path.join(root,'package.json'),'utf8').includes('"gsap": "3.15.0"'),true);
 assert.doesNotMatch(fs.readFileSync(path.join(root,'app/static/transcript.html'),'utf8'),/gsap|Flip|motion\.js/);
});
test('rapid repeated submission cannot duplicate admission and a changed source retains its action label',async()=>{
 const motion=motionSpy(),f=fixture(motion);let reject,count=0;
 f.run("setMode('youtube');online=true;disk={can_record:true}");
 f.context.fetch=()=>{count++;return new Promise((_,fail)=>{reject=fail;});};
 const first=f.get('record').onclick();await f.get('record').onclick();
 assert.equal(count,1);f.run("setMode('instagram')");reject(Error('Offline'));await first;
 assert.equal(f.get('record').textContent,'Mulai');assert.equal(f.get('capture-content').hidden,false);
 assert.equal(motion.calls.some(c=>c[0]==='source'&&c[1]),false);
});
