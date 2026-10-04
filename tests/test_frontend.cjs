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

function fixture() {
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
    window: { addEventListener() {} }, URLSearchParams, Intl,
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
  assert.match(css,/\.workspace-page #status-monitor\{width:fit-content;max-width:100%/);
  assert.match(css,/padding:10px 12px;border:1px solid #36363b;border-radius:12px/);
  assert.match(css,/\.workspace-page #stop\{height:30px/);
  assert.match(css,/\.workspace-page #capture-panel \.capture-options \.form-grid\{display:contents\}/);
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
