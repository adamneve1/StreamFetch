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

test('island progress omits track helpers and attempts, and resets when unknown', () => {
  const f = fixture();
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'recording',progress_phase:'audio',progress_percent:42.5,download_attempt:2,download_attempts:3})`);
  assert.equal(f.get('download-progress').hidden, false);
  assert.equal(f.get('progress-bar').attributes['aria-label'], 'Progress capture');
  assert.equal(f.get('progress-value').textContent, '43%');
  assert.equal(f.get('progress-bar').value, 42.5);
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'recording',progress_percent:null})`);
  assert.equal(f.get('progress-value').textContent, '');
  assert.equal(f.get('progress-bar').value, '');
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'finalizing',progress_percent:100})`);
  assert.equal(f.get('progress-value').textContent, '');
  assert.equal(f.get('progress-bar').attributes['aria-label'], 'Memproses');
  f.run(`renderDownloadProgress({source:'youtube',is_live:true,state:'recording'})`);
  assert.equal(f.get('download-progress').hidden, true);
});

test('Original is the Source default and watch recording uses the canonical Merekam island',()=>{
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 assert.match(html,/<option value="original" selected>/);assert.doesNotMatch(html,/<option value="balanced" selected>/);
 const f=fixture();
 f.run("active=null;submittedJob=null;lastObservedJob=null;queued=0;renderOperationalStatus()");
 assert.equal(f.get('status-monitor').hidden,true);
 f.run("active={job_id:'watch',origin:'telegram_watch',source:'youtube',is_live:true,state:'queued'};renderOperationalStatus()");
 assert.equal(f.get('state').textContent,'Menunggu');
 f.run("active.state='recording';active.source_metadata={title:'Dialog RRI Batam'};controls()");
 assert.equal(f.get('state').textContent,'Merekam');assert.equal(f.get('status-job-title').textContent,'Dialog RRI Batam');
 assert.equal(f.get('stop').disabled,false);
 f.run("active.state='finalizing';active.progress_phase='processing';active.progress_percent=68;controls()");
 assert.equal(f.get('state').textContent,'Memproses');assert.equal(f.get('progress-value').textContent,'68%');
 assert.equal(f.get('download-progress').hidden,false);assert.equal(f.get('stop').hidden,false);
 f.run("active.progress_percent=null;controls()");assert.equal(f.get('progress-value').textContent,'');
});

test('Watches render Waiting Recording Discovery issue and Expired with cancellation tied to discovery',()=>{
 const f=fixture();
 f.run("renderWatches({active_count:3,watches:['waiting','recording','discovery_issue','expired'].map((status,i)=>({id:String(i),channel:'@rribatam',start:100,end:200,status,can_cancel:['waiting','discovery_issue'].includes(status)}))})");
 const rows=f.get('watch-list').children;
 assert.deepEqual(rows.map(row=>row.children[1].textContent),['Waiting','Recording','Discovery issue','Expired']);
 assert.deepEqual(rows.map(row=>row.children.length),[3,2,3,2]);
});

test('History reports processing failure as a usable Original rather than a capture failure',()=>{
 const f=fixture();
 f.run("renderHistory([{job_id:'preserved',source:'youtube',state:'ready',filename:'original.mp4',compression:'original',note:'Dialog',processing_detail:'Rekaman berhasil · kompresi gagal · Original tersedia.'}])");
 const row=f.get('results').children[0];
 assert.equal(row.children[1].children[1].textContent,'Rekaman berhasil · kompresi gagal · Original tersedia.');
 assert.equal(row.children[5].children[0].href,'/api/files/original.mp4');
});

test('island uses semantic colors and concise feedback while History retains diagnostics', () => {
  const f = fixture();
  for (const [state, tone] of [['recording', 'working'], ['waiting', 'waiting'], ['ready', 'success'], ['failed', 'error']]) {
    f.context.job = { job_id:'diagnostic',source: 'youtube', is_live: false, state, detail: 'YouTube belum siap.\nFile parsial tetap disimpan. '.repeat(10) };
    f.run('active=job; renderOperationalStatus()');
    assert.equal(f.get('status-monitor').dataset.tone, tone);
    assert.equal(f.get('status-detail').textContent, state==='failed'?'Capture gagal.':'');
    assert.equal(f.get('status-detail').hidden,state!=='failed');
    f.run('renderHistory([job])');
    assert.equal(f.get('results').querySelector('.recording-detail-body').children[0].textContent,f.context.job.detail);
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
  assert.equal(f.get('state').textContent, 'Mengunduh');
  f.get('filter-state').value = 'recording';
  activeJob = null; row = { ...job, state: 'ready', filename: 'result.mp4', detail: 'File siap digunakan.' };
  await f.run('refresh()');
  assert.equal(f.get('status-monitor').dataset.tone, 'success');
  assert.equal(f.get('status-detail').textContent, '');
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
    assert.equal(f.get('status-job-title').textContent,'Dialog Batam');
    assert.equal(f.get('status-detail').textContent,['failed','interrupted'].includes(state)?'Capture gagal.':'');
    assert.equal(f.get('results').querySelector('.recording-detail-body').children[0].textContent,'Detail lengkap');
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
  f.run("active.state='finalizing';controls()");assert.equal(f.get('stop').disabled,false);
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
  const wide=css.slice(css.indexOf('/* Source and History share')).split('/* Only admission')[0];
  assert.match(wide,/@media\(min-width:1100px\)/);
  assert.match(wide,/--workspace-panel-width:1240px/);
  assert.match(wide,/#capture-panel,\.workspace-page #history-panel\{width:100%;max-width:var\(--workspace-panel-width\);padding:16px\}/);
  assert.match(wide,/#capture-content\{width:100%;max-width:none\}/);
  assert.match(wide,/\.source-field\{max-width:none\}/);
  assert.match(wide,/#oryx-fields\{max-width:380px\}/);
  assert.match(wide,/\.capture-options\{max-width:none;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(wide,/#format-field\{grid-column:1\/-1/);
  assert.match(wide,/\.capture-footer\{max-width:none;justify-content:flex-start/);
  assert.doesNotMatch(wide,/display:none|position:absolute/);
  const f=fixture();f.get('quality').value='720';f.get('storage-target').value='archive';f.get('note').value='Dialog';
  for(const source of ['youtube','tiktok','instagram']){f.run(`setMode('${source}')`);assert.equal(f.get('quality').value,'720');assert.equal(f.get('storage-target').value,'archive');assert.equal(f.get('note').value,'Dialog');}
  assert.equal(f.get('compression-details').hidden,false);
});

test('desktop Source and History share container, control geometry and spacing without changing mobile rules',()=>{
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
 const wide=css.slice(css.indexOf('/* Source and History share')).split('/* Only admission')[0];
 assert.match(wide,/max-width:calc\(var\(--workspace-panel-width\) \+ 80px\)/);
 assert.doesNotMatch(wide,/1480px/);
 assert.match(wide,/#history-panel \.filters button\{height:34px;min-height:34px;border-radius:7px;padding:6px 10px;font-size:12px;font-weight:400;line-height:1\.4\}/);
 assert.match(wide,/\.filters\{gap:14px var\(--workspace-gap\)\}/);
 assert.match(wide,/\.capture-options\{[^}]*gap:14px var\(--workspace-gap\)/);
 assert.match(css,/#status-monitor\[data-phase=transcribing\]\{width:100%;min-width:0;max-width:100%\}/);
 const phone=css.slice(css.lastIndexOf('@media(max-width:600px)'));
 assert.match(phone,/#capture-panel \.capture-options\{grid-template-columns:minmax\(0,1fr\)/);
 assert.match(phone,/#history-panel\{padding:0;border:0;background:transparent/);
});

function motionSpy(){
 const calls=[];return {calls,reduced:()=>false,reset(){calls.push(['reset']);},
  loginReveal(element){calls.push(['loginReveal',element]);},
  loginError(element){calls.push(['loginError',element]);},
  loginSuccess(element){calls.push(['loginSuccess',element]);},
  loginInteract(state){calls.push(['loginInteract',state]);},
  contextChange(shell,title,change){calls.push(['contextChange']);change();},
  followLink(link,event,options){calls.push(['followLink',link,event,options]);},
  source(panel,collapsed,change,options){calls.push(['source',collapsed,options,panel.dataset.collapsed]);change();},
  island(panel,key,phase,change){calls.push(['island',key,phase]);change();},
  progress(panel,value){if(value===null)panel.removeAttribute('value');else panel.value=value;},
};
}
test('navigation hooks preserve native transcript links and immediate Settings state',()=>{
 const motion=motionSpy(),f=fixture(motion);
 f.run("renderHistory([{job_id:'done',state:'ready',filename:'Dialog.mp4',transcript:{status:'completed'}}])");
 const link=f.get('results').querySelector('.transcript-cta');assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener');
 const event={};link.onclick(event);assert.equal(motion.calls.at(-1)[0],'followLink');assert.equal(motion.calls.at(-1)[1],link);
 f.get('nav-admin').onclick();assert.equal(f.get('workspace').dataset.destination,'settings');
 f.get('nav-control').onclick();assert.equal(f.get('workspace').dataset.destination,'workspace');assert.equal(motion.calls.filter(c=>c[0]==='contextChange').length,2);
});
test('password events use state feedback and submission still starts the API immediately',()=>{
 const motion=motionSpy(),f=fixture(motion);f.get('password').value='private';f.get('password').oninput();
 assert.deepEqual(motion.calls.at(-1),['loginInteract','typing']);
 const requests=[];f.context.fetch=(url)=>{requests.push(url);return new Promise(()=>{});};
 f.get('login-form').onsubmit({preventDefault(){}});assert.deepEqual(requests,['/api/login']);
 assert.deepEqual(motion.calls.at(-1),['loginInteract','submit']);assert.equal(f.get('login-submit').disabled,true);
});
test('Source reopen supplies a deferred focus callback instead of focusing during the morph',()=>{
 const motion=motionSpy(),f=fixture(motion);f.run("setMode('youtube');collapseSource(true)");
 f.get('capture-another').onclick();const call=motion.calls.filter(c=>c[0]==='source').at(-1);
 assert.equal(call[1],false);assert.equal(f.get('youtube-url').focused,undefined);call[2].onSettled();assert.equal(f.get('youtube-url').focused,true);
});
test('phone Source and History use deliberate single-column/cards while tablet rules stay intact',()=>{
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
 const phone=css.split('/* Phones use a composition')[1];
 assert.match(phone,/@media\(max-width:600px\)/);
 assert.match(phone,/\.capture-options\{grid-template-columns:minmax\(0,1fr\);gap:14px\}/);
 assert.match(phone,/#record\{width:100%;min-height:44px\}/);
 assert.match(phone,/\.history-table thead\{display:none\}/);
 assert.match(phone,/\.history-table tr\{display:block;position:relative/);
 assert.match(phone,/\.history-table tbody\{display:grid;gap:10px/);
 assert.match(phone,/grid-template-areas:'state summary action' 'title title title' 'progress progress progress'/);
 assert.match(phone,/env\(safe-area-inset-top\)/);
 assert.match(phone,/data-destination=workspace\] \.page-heading\{display:none\}/);
});
test('phone History metadata and bulk selection reuse existing recording and selection state',()=>{
 const f=fixture();f.context.rows=[{job_id:'phone',state:'ready',filename:'Long filename.mp4',source:'instagram',size:1000000,requested_at:100}];
 f.run('historyRows=rows;renderHistory(rows)');
 assert.match(f.get('results').querySelector('.history-mobile-meta').textContent,/Instagram.*1.0 MB/);
 assert.equal(f.get('history-panel').dataset.selected,'false');
 const checkbox=f.get('results').querySelector('.history-select');checkbox.checked=true;checkbox.onchange();
 assert.equal(f.get('history-panel').dataset.selected,'true');assert.equal(f.get('download-selected').disabled,false);
});
test('History and island Stop request real transcription cancellation and expose restart after cancelled',async()=>{
 const f=fixture();f.context.row={job_id:'transcribe',state:'ready',filename:'recording.mp4',source:'youtube',transcript:{status:'transcribing',progress_percent:42}};
 f.run('lastObservedJob=row;renderHistory([row]);controls()');
 assert.equal(f.get('stop').hidden,false);assert.equal(f.get('stop').disabled,false);assert.equal(f.get('stop').attributes['aria-label'],'Hentikan transkripsi');
 const calls=[];f.context.fetch=async(url,options)=>{calls.push([url,options.method]);return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({status:'cancelled'})};};
 await f.get('stop').onclick();assert.deepEqual(calls,[['/api/recordings/transcribe/transcript/cancel','POST']]);
 assert.equal(f.get('state').textContent,'Dibatalkan');assert.equal(f.get('status-monitor').dataset.phase,'cancelled');
 f.run("row.transcript.status='cancelled';renderHistory([row])");
 assert.equal(f.get('results').querySelector('.transcript-cta').disabled,false);assert.equal(f.get('results').querySelector('.transcription-stop'),null);
 f.run("row.transcript.status='queued';renderHistory([row])");const button=f.get('results').querySelector('.transcription-stop');assert.ok(button.querySelector('svg'));
 await button.onclick();assert.equal(calls.at(-1)[0],'/api/recordings/transcribe/transcript/cancel');
});
test('History capture Stop retains the existing immediate capture request',()=>{
 const f=fixture();f.run("active={job_id:'capture',state:'recording',source:'youtube',is_live:true};renderHistory([active]);controls()");
 const calls=[];f.context.fetch=(url,options)=>{calls.push([url,JSON.parse(options.body)]);return new Promise(()=>{});};
 f.get('results').querySelector('.capture-stop').onclick();assert.deepEqual(calls,[['/api/stop',{job_id:'capture'}]]);
});
test('phone reader stacks safe metadata labels and compacts prose, timestamps and toolbar without changing seek',()=>{
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/transcript.css'),'utf8').split('@media(max-width:600px)')[1];
 assert.match(css,/grid-template-columns:94px minmax\(0,1fr\)/);
 assert.match(css,/overflow-wrap:normal;word-break:normal/);
 assert.match(css,/\.reader-prose\{font-size:14px;line-height:1.65/);
 assert.match(css,/grid-template-columns:42px minmax\(0,1fr\)/);
 assert.match(css,/\.reader-tools \.reader-search\{grid-column:1\/-1/);
 assert.match(css,/\.reader-tools \.reader-toolbar\{display:contents\}/);
 assert.match(fs.readFileSync(path.resolve(__dirname,'../app/static/transcript.css'),'utf8'),/@media\(max-width:600px\) and \(max-height:500px\)\{\.reader-tools\{position:static\}\}/);
});
test('Settings Watches show shared schedules, compact WIB metadata, terminal states and last item safely',()=>{
 const f=fixture();
 f.context.watches=[{id:'active',channel:'https://www.youtube.com/@rribatam',name:'<script>RRI Batam</script>',start:Date.UTC(2026,9,5,1)/1000,end:Date.UTC(2026,9,5,3)/1000,mode:'every',auto_transcribe:true,status:'active',last_capture:{title:'Dialog Batam',state:'ready'}},
  {id:'waiting',channel:'@channel',start:100,end:200,mode:'first',auto_transcribe:false,status:'waiting'},
  ...['expired','finished','cancelled'].map(status=>({id:status,channel:'@old',start:100,end:200,mode:'first',auto_transcribe:false,status}))];
 f.run('renderWatches({watches,active_count:2})');
 assert.equal(f.get('watch-count').textContent,'2 aktif');
 const rows=f.get('watch-list').children;assert.equal(rows.length,5);
 assert.equal(rows[0].querySelector('strong').textContent,'<script>RRI Batam</script>');assert.equal(rows[0].querySelector('script'),null);
 assert.match(rows[0].textContent,/08:00–10:00 WIB · Every live · Auto-transcribe on/);
 assert.match(rows[0].textContent,/Terakhir: Dialog Batam · Siap/);
 assert.match(rows[1].textContent,/First live · Auto-transcribe off/);
 assert.equal(f.get('watch-list').querySelectorAll('button').length,2);
 assert.equal(rows[0].querySelector('button').attributes['aria-label'],'Batalkan watch <script>RRI Batam</script>');
 assert.equal(f.run('watchWindow({})'),'—');
 f.run('renderWatches({watches:[],active_count:0})');assert.equal(f.get('watch-feedback').textContent,'Belum ada watch.');
});
test('Watches load only for admins, handle list errors locally, and expose no web creation form',async()=>{
 const f=fixture(),calls=[];
 f.context.fetch=async url=>{calls.push(url);return {ok:false,status:503,headers:{get:()=> 'application/json'},json:async()=>({error:'Watch unavailable'})};};
 await f.run('loadWatches()');assert.deepEqual(calls,[]);
 f.run("isAdmin=true;showSection('admin')");await new Promise(setImmediate);
 assert.deepEqual(calls,['/api/admin/watches']);assert.equal(f.get('watch-feedback').textContent,'Watch unavailable');
 assert.equal(f.get('notice').textContent,'');
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 const panel=html.split('id="watches-panel"')[1].split('</section>')[0];
 assert.match(panel,/hidden/);assert.match(panel,/Watches/);assert.doesNotMatch(panel,/<form|<input|<select/);
});
test('Watches visibility follows login roles and cancellation reuses the admin API without stopping capture',async()=>{
 const f=fixture();captureAPI(f);await f.run('enter({is_admin:false})');assert.equal(f.get('watches-panel').hidden,true);
 await f.run('enter({is_admin:true})');assert.equal(f.get('watches-panel').hidden,false);
 f.run("renderWatches({active_count:1,watches:[{id:'watch',channel:'@rri',start:100,end:200,status:'active',mode:'first',auto_transcribe:false}]})");
 const calls=[];f.context.fetch=async(url,options)=>{calls.push([url,options.method]);return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>url.endsWith('/cancel')?{ok:true}:{active_count:0,watches:[{id:'watch',channel:'@rri',start:100,end:200,status:'cancelled',mode:'first',auto_transcribe:false}]}};};
 const button=f.get('watch-list').querySelector('button');const pending=button.onclick();assert.equal(button.disabled,true);await pending;
 assert.deepEqual(calls,[['/api/admin/watches/watch/cancel','POST'],['/api/admin/watches','GET']]);
 assert.equal(f.get('watch-count').textContent,'0 aktif');assert.equal(f.get('watch-list').querySelector('button'),null);
});
test('failed cancellation preserves watch controls, and a stale list cannot overwrite post-cancel data',async()=>{
 const f=fixture();f.run("isAdmin=true;renderWatches({active_count:1,watches:[{id:'watch',channel:'@rri',start:100,end:200,status:'active',mode:'first'}]})");
 f.context.fetch=async()=>({ok:false,status:500,headers:{get:()=> 'application/json'},json:async()=>({error:'Try again'})});
 const button=f.get('watch-list').querySelector('button');await button.onclick();assert.equal(button.disabled,false);assert.equal(f.get('watch-feedback').textContent,'Try again');
 let resolveOld;f.context.fetch=()=>new Promise(resolve=>{resolveOld=resolve;});const old=f.run('loadWatches()');
 f.context.fetch=async()=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({watches:[],active_count:0})});
 await f.run('loadWatches(true)');
 resolveOld({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({watches:[],active_count:99})});await old;
 assert.equal(f.get('watch-count').textContent,'0 aktif');
});
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
 assert.equal(f.get('status-job-title').textContent,'Capture baru');
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
 for(const [state,transcript,phase] of [['starting',null,'starting'],['recording',null,'downloading'],['finalizing',null,'processing'],['ready',{status:'queued'},'queued'],['ready',{status:'transcribing',progress_percent:35,eta_seconds:120,provider:'cloudflare'},'transcribing'],['ready',{status:'completed'},'completed'],['ready',{status:'failed',error:'Try again'},'failed']]){
  f.context.job={job_id:'island',source:'instagram',is_live:false,state,transcript};
  f.run('active=job;controls()');
  assert.equal(motion.calls.filter(c=>c[0]==='island').at(-1)[2],phase);
  assert.doesNotMatch(f.get('status-detail').textContent,/cloudflare/i);
 }
});
test('island prefers real media titles including persisted metadata over filenames and platform labels',()=>{
 const f=fixture();
 f.run("historyRows=[{job_id:'title',source_metadata:{title:'Dialog RRI Batam'}}]");
 assert.equal(f.run("islandTitle({job_id:'title',source:'youtube',filename:'/downloads/output.mp4'})"),'Dialog RRI Batam');
 assert.equal(f.run("islandTitle({job_id:'title',note:'Judul operator',source_metadata:{title:'Media title'}})"),'Judul operator');
 assert.equal(f.run("islandTitle({source_metadata:{title:'Reel title'},source:'instagram'})"),'Reel title');
 f.run("historyRows=[{job_id:'legacy',title:'Legacy title'}]");
 assert.equal(f.run("islandTitle({job_id:'legacy',source:'tiktok'})"),'Legacy title');
 assert.equal(f.run("islandTitle({source:'youtube',filename:'output.mp4'})"),'Capture baru');
});
test('island time is compact, rounded, and follows transcription rather than old capture duration',()=>{
 const f=fixture();
 assert.equal(f.run('islandTime({eta_seconds:18})'),'~20s');
 assert.equal(f.run('islandTime({}, {eta_seconds:61})'),'~2m');
 assert.equal(f.run('islandTime({}, {eta_seconds:90,updated_at:Date.now()/1000-72})'),'~20s');
 assert.equal(f.run('islandTime({elapsed:42})'),'42s');
 assert.equal(f.run('islandTime({elapsed:125})'),'02:05');
 assert.equal(f.run("islandTime({elapsed:7200},{status:'queued'})"),'');
 assert.equal(f.run("islandTime({elapsed:7200},{status:'transcribing',started_at:Date.now()/1000-12})"),'12s');
 assert.equal(f.run('islandTime({})'),'');
});
test('island keeps live marker and Stop permissions while removing normal diagnostics',()=>{
 const f=fixture();
 f.run("active={job_id:'live',source:'youtube',note:'Batam live',state:'recording',is_live:true,detail:'Percobaan 1/1 · Menggabungkan video/audio',filename:'/downloads/out.mp4'};renderOperationalStatus()");
 assert.equal(f.get('marker-controls').hidden,false);assert.equal(f.get('stop').hidden,false);assert.equal(f.get('stop').disabled,false);
 assert.equal(f.get('state').textContent,'Merekam');assert.equal(f.get('status-detail').hidden,true);
 f.run("active.state='finalizing';controls()");
 assert.equal(f.get('marker-controls').hidden,true);assert.equal(f.get('stop').hidden,false);assert.equal(f.get('stop').disabled,false);
 assert.equal(f.get('state').textContent,'Memproses');
 assert.equal(f.get('status-detail').textContent,'');
});
test('failed island opens existing History diagnostics without inventing a second detail view',async()=>{
 const f=fixture();
 f.run("active={job_id:'failure',state:'failed',source:'instagram',detail:'Extractor diagnostic'};historyRows=[active];renderHistory(historyRows);controls()");
 assert.equal(f.get('island-details').hidden,false);assert.equal(f.get('status-detail').textContent,'Capture gagal.');
 await f.get('island-details').onclick();
 const row=f.get('results').children[0];
 assert.equal(row.querySelector('.recording-details').open,true);assert.equal(row.focused,true);assert.equal(row.scrolled,true);
 f.run('renderHistory(historyRows)');assert.equal(f.get('results').querySelector('.recording-details').open,true);
});
test('minimal island markup omits duplicate metadata and moves live markers outside the signal',()=>{
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 const island=html.split('id="status-monitor"')[1].split('</section>')[0];
 for(const id of ['state','status-job-title','progress-value','duration','stop','progress-bar','island-details'])assert.ok(island.includes('id="'+id+'"'),id);
 assert.doesNotMatch(island,/monitor-footer|marker-controls|active-source|WAKTU PROSES|UKURAN|progress-caption|status-title/);
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8').split('/* The island is a two-row signal')[1];
 assert.match(css,/width:fit-content/);assert.match(css,/\[data-phase=processing\]/);assert.match(css,/\[data-phase=completed\]\{min-width:0;min-height:0;max-width:min\(100%,360px\)/);
 assert.match(css,/white-space:nowrap;text-overflow:ellipsis;overflow:hidden/);
});
test('Source selector fills the bounded form with four equally sized, quieter controls',()=>{
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8').split('/* One continuous Source selector')[1];
 assert.match(css,/#capture-panel \.tabs\{display:grid;grid-template-columns:repeat\(4,minmax\(0,1fr\)\);width:100%\}/);
 assert.match(css,/#capture-panel \.tab\{min-width:0;text-align:center;font-weight:400/);
 assert.match(css,/\.tab\.selected\{font-weight:500\}/);
 assert.match(css,/#capture-panel label\{font-weight:500/);
 const f=fixture();
 for(const mode of ['youtube','tiktok','instagram','oryx']){
  f.run(`setMode('${mode}')`);
  for(const source of ['youtube','tiktok','instagram','oryx'])assert.equal(f.get('tab-'+source).attributes['aria-pressed'],String(source===mode));
 }
});
test('active island fills History while compact states and single-line title hierarchy remain scoped',()=>{
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8').split('/* The island is a two-row signal')[1];
 assert.match(css,/#status-monitor\[data-phase=downloading\],\.workspace-page #status-monitor\[data-phase=processing\],\.workspace-page #status-monitor\[data-phase=transcribing\]\{width:100%;min-width:0;max-width:100%\}/);
 assert.match(css,/\[data-phase=queued\]\{max-width:min\(100%,540px\)\}/);
 assert.match(css,/:is\(\[data-phase=downloading\],\[data-phase=processing\],\[data-phase=transcribing\]\) #status-job-title\{max-width:none\}/);
 assert.match(css,/min-width:0;white-space:nowrap;text-overflow:ellipsis;overflow:hidden/);
 assert.match(css,/\.island-state\{[^}]*font-weight:600/);
 assert.match(css,/#progress-value\{[^}]*font-weight:500/);
});
test('island Stop retains its accessible square icon across polls and immediately requests cancellation',async()=>{
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 const stop=html.match(/<button id="stop".*?<\/button>/)[0];
 assert.match(stop,/aria-label="Hentikan proses" title="Hentikan proses"/);
 assert.match(stop,/<svg[^>]*aria-hidden="true"[^>]*><rect[^>]*width="12" height="12"/);
 assert.doesNotMatch(stop,/>Hentikan|<path|>×|>X/);
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8').split('/* The island is a two-row signal')[1];
 assert.match(css,/#stop\{[^}]*width:44px;min-width:44px;height:44px;min-height:44px/);
 assert.match(css,/#stop:focus-visible\{outline:2px solid/);
 const f=fixture(),svg=new Element('svg');f.get('stop').append(svg);
 for(const state of ['starting','waiting','recording']){
  f.run(`active={job_id:'cancel',source:'youtube',state:'${state}',is_live:false};controls()`);
  assert.equal(f.get('stop').children[0],svg);assert.equal(f.get('stop').hidden,false);
 }
 const calls=[];f.context.fetch=(url,options)=>{calls.push([url,JSON.parse(options.body)]);return new Promise(()=>{});};
 f.get('stop').onclick();assert.deepEqual(calls,[['/api/stop',{job_id:'cancel'}]]);assert.equal(f.get('stop').disabled,true);
});
test('transcription started from History is observed by the same island without changing queue requests',async()=>{
 const motion=motionSpy(),f=fixture(motion);const calls=[];
 f.context.fetch=async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return {ok:true,status:202,headers:{get:()=> 'application/json'},json:async()=>({})};};
 await f.run("generateTranscript({job_id:'old-recording',state:'ready',source:'tiktok',filename:'old.mp4'})");
 assert.deepEqual(calls,[['/api/recordings/old-recording/transcript',{}]]);
 assert.equal(f.get('state').textContent,'Menunggu');assert.equal(f.get('status-job-title').textContent,'Capture baru');
 assert.equal(motion.calls.filter(c=>c[0]==='island').at(-1)[2],'queued');
});
test('workspace and reader load the same local GSAP motion system',()=>{
 const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'app/static/index.html'),'utf8');
 const scripts=[...html.matchAll(/<script defer src="([^"]+)"/g)].map(match=>match[1]);
 assert.deepEqual(scripts,['/static/vendor/gsap.min.js','/static/vendor/Flip.min.js','/static/motion.js','/static/app.js']);
 assert.equal(fs.readFileSync(path.join(root,'package.json'),'utf8').includes('"gsap": "3.15.0"'),true);
 const reader=fs.readFileSync(path.join(root,'app/static/transcript.html'),'utf8');
 assert.deepEqual([...reader.matchAll(/<script src="([^"]+)" defer/g)].map(match=>match[1]),['/static/vendor/gsap.min.js','/static/vendor/Flip.min.js','/static/motion.js','/static/transcript.js']);
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
