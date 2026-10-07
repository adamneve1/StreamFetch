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
    this.open = false; this.disabled = false; this.listeners = {}; this._text = ''; this.focusCount = 0;
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
  removeAttribute(name) { delete this.attributes[name]; if (name === 'value') this.value = ''; if(name==='src')delete this.src; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { this.focused = true; this.focusCount++; if(this.ownerDocument)this.ownerDocument.activeElement=this; }
  contains(node) { return node===this||this.children.some(child=>child.contains(node)); }
  remove() { if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;} }
  showModal() { this.open = true; }
  close() { this.open = false; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  scrollIntoView() { this.scrolled = true; }
  pause() { this.pauseCount = (this.pauseCount || 0) + 1; this.paused = true; }
  load() { this.loadCount = (this.loadCount || 0) + 1; }
  querySelectorAll(selector) {
    const [base, pseudo] = selector.split(':');
    const matches = node => (base.startsWith('.') ? node.className.split(' ').includes(base.slice(1)) : node.tagName === base)
      && (pseudo !== 'checked' || node.checked);
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function fixture(motion, href = 'https://streamfetch.example/') {
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8');
  const parsedLocation = new URL(href);
  const location = { origin: parsedLocation.origin, pathname: parsedLocation.pathname, search: parsedLocation.search, hash: parsedLocation.hash,
    replace(value) { this.replaced = value; } };
  const body = new Element('body'); body.root = true;
  const head = new Element('head'); head.root = true;
  const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  body.append(...ids.values());
  ids.get('empty').append(new Element('strong'), new Element('p'));
  ids.get('quality').value = 'best'; ids.get('compression').value = 'balanced';
  ids.get('media-format').value = 'mp4'; ids.get('storage-target').value = 'local';
  ids.get('clip-start').value = '00:00'; ids.get('clip-format').value = 'mp4';
  ids.get('clip-quality').value = 'best'; ids.get('clip-compression').value = 'original'; ids.get('clip-storage').value = 'local';
  const listeners={},windowListeners={},timers=new Map(),intervals=new Map();let timerId=0,intervalId=0;
  const findId=(node,id)=>node.id===id?node:node.children.map(child=>findId(child,id)).find(Boolean);
  const document={ body,head,activeElement:body,getElementById:id=>ids.get(id)||findId(head,id)||findId(body,id),
    createElement:tag=>{const element=new Element(tag);element.ownerDocument=document;return element;},
    createElementNS:(_,tag)=>document.createElement(tag),
    addEventListener(type,callback){(listeners[type]||=[]).push(callback);} };
  for(const element of ids.values())element.ownerDocument=document;
  const context = vm.createContext({
    document,
    window: { addEventListener(type,callback){(windowListeners[type]||=[]).push(callback);}, StreamFetchMotion:motion, location }, URL, URLSearchParams, Intl,
    setInterval(callback,delay){intervals.set(++intervalId,{callback,delay});return intervalId;},clearInterval(id){intervals.delete(id);},setTimeout(callback,delay){timers.set(++timerId,{callback,delay});return timerId;},clearTimeout(id){timers.delete(id);},
    // Keep auto-login pending; each test drives the state itself.
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8'), context);
  return { context,document,listeners,windowListeners,timers,intervals,fireTimer(id){const timer=timers.get(id);timers.delete(id);timer?.callback();},get: id => ids.get(id), run: code => vm.runInContext(code, context) };
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
 assert.equal(row.children[5].querySelector('.download-direct').href,'/api/files/original.mp4');
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

test('download analytics stay compact and render only for admins with WIB detail time',()=>{
  const row={job_id:'analytics',source:'youtube',state:'ready',filename:'Dialog.mp4',download_count:12,last_downloaded_at:1704067200};
  const admin=fixture();admin.context.row=row;admin.run('isAdmin=true;renderHistory([row])');
  assert.equal(admin.get('results').querySelector('.download-stat').textContent,'12× download');
  const detail=admin.get('results').querySelector('.detail-download-stats');
  assert.match(detail.textContent,/12× download · Terakhir: .* WIB/);

  const user=fixture();user.context.row=row;user.run('isAdmin=false;renderHistory([row])');
  assert.equal(user.get('results').querySelector('.download-stat'),null);
  assert.equal(user.get('results').querySelector('.detail-download-stats'),null);
});

test('one accessible native dialog replaces every user-facing prompt and confirm',()=>{
  const root=path.resolve(__dirname,'../app/static');
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.match(html,/<dialog id="app-dialog" class="stream-dialog" aria-labelledby="app-dialog-title" aria-describedby="app-dialog-message">/);
  assert.match(html,/id="app-dialog-input"[^>]*autocomplete="off"/);
  assert.match(html,/id="app-dialog-cancel"[^>]*type="button">Batal<\/button>/);
  assert.match(html,/id="app-dialog-confirm"[^>]*type="submit">Simpan<\/button>/);
  for(const file of ['app.js','motion.js','transcript.js']){
    const source=fs.readFileSync(path.join(root,file),'utf8');
    assert.doesNotMatch(source,/\b(?:window\.)?(?:prompt|confirm|alert)\s*\(/,file);
  }
});

test('rename dialog selects the editable title, supports cancel, and preserves rename API validation',async()=>{
  const f=fixture(),requests=[];f.context.requests=requests;
  f.run("api=async(path,data)=>{requests.push({path,data});return {}};refresh=async()=>{}");
  f.context.row={job_id:'rename-me',filename:'Dialog Batam.mp4',archive_status:'local'};
  const trigger=f.get('delete-selected'),cancelled=f.run('renameJob(row,$("delete-selected"))');
  assert.equal(f.get('app-dialog').open,true);assert.equal(f.get('app-dialog-title').textContent,'Ubah nama');
  assert.equal(f.get('app-dialog-input').value,'Dialog Batam.mp4');assert.equal(f.get('app-dialog-input').focused,true);
  assert.equal(f.get('app-dialog-input').selectionStart,0);assert.equal(f.get('app-dialog-input').selectionEnd,'Dialog Batam'.length);
  f.get('app-dialog-cancel').onclick();await cancelled;
  assert.equal(requests.length,0);assert.equal(f.get('app-dialog').open,false);assert.ok(trigger.focusCount>0);
  const empty=f.run('renameJob(row,$("delete-selected"))');f.get('app-dialog-input').value='   ';
  f.get('app-dialog-form').onsubmit({preventDefault(){}});await empty;assert.equal(requests.length,0);
  const renamed=f.run('renameJob(row,$("delete-selected"))');f.get('app-dialog-input').value='  Dialog Baru.mp4  ';
  f.get('app-dialog-form').onsubmit({preventDefault(){}});await renamed;
  assert.equal(JSON.stringify(requests),JSON.stringify([{path:'recordings/rename-me/rename',data:{filename:'Dialog Baru.mp4'}}]));
  assert.match(f.get('toast-status').textContent,/nama file sudah diganti/i);
});

test('delete dialog identifies the target, defaults focus to cancel, and confirms destructively',async()=>{
  const f=fixture(),requests=[];f.context.requests=requests;
  f.run("api=async(path,data)=>{requests.push({path,data});return {deleted:data.job_ids,skipped:[]}};refresh=async()=>{};historyRows=[{job_id:'delete-me',filename:'Siaran Batam.mp4'}]");
  const trigger=f.get('delete-selected'),cancelled=f.run('deleteJobs(["delete-me"],$("delete-selected"))');
  assert.equal(f.get('app-dialog-title').textContent,'Hapus rekaman?');
  assert.match(f.get('app-dialog-message').textContent,/Siaran Batam\.mp4.*file lokalnya.*tidak dapat dibatalkan/);
  assert.equal(f.get('app-dialog').dataset.tone,'destructive');assert.equal(f.get('app-dialog-confirm').textContent,'Hapus');
  assert.equal(f.get('app-dialog-cancel').focused,true);
  f.get('app-dialog').listeners.cancel({preventDefault(){this.defaultPrevented=true;}});await cancelled;assert.equal(requests.length,0);
  const confirmed=f.run('deleteJobs(["delete-me"],$("delete-selected"))');
  f.get('app-dialog-form').onsubmit({preventDefault(){}});await confirmed;
  assert.equal(JSON.stringify(requests),JSON.stringify([{path:'recordings/delete',data:{job_ids:['delete-me']}}]));
  assert.equal(f.get('toast-status').textContent,'1 item berhasil dihapus.');assert.equal(f.get('app-dialog').open,false);assert.ok(trigger.focusCount>0);
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

test('History previews every ready canonical video or audio in the shared dialog only',()=>{
  const f=fixture();
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  assert.match(html,/<video id="history-video-preview" controls preload="metadata"/);assert.match(html,/<audio id="history-audio-preview" controls preload="metadata"/);
  f.context.rows=[
    {job_id:'source',source:'youtube',state:'ready',filename:'Source video.mp4',transcript:{status:'completed'}},
    {job_id:'clip',source:'youtube',is_clip:true,state:'ready',filename:'Clip audio.mp3',output_format:'mp3'},
    {job_id:'watch',source:'youtube',state:'ready',filename:'Watch.webm'},
    {job_id:'telegram',source:'oryx',state:'ready',filename:'Telegram.mp4'},
    {job_id:'failed',source:'youtube',state:'failed',filename:'partial.mp4'},
    {job_id:'partial',source:'youtube',state:'finalizing',filename:'partial-final.mp4'},
  ];
  f.run('renderHistory(rows)');
  const previews=f.get('results').querySelectorAll('.media-preview-direct');
  assert.equal(previews.length,4);assert.equal(f.get('results').querySelectorAll('.download-direct').length,4);
  assert.ok(f.get('results').querySelector('.transcript-cta'));
  previews[0].onclick();
  const dialog=f.get('app-dialog'),video=f.get('history-video-preview'),audio=f.get('history-audio-preview');
  assert.equal(dialog.open,true);assert.equal(dialog.dataset.mode,'media');assert.equal(video.hidden,false);
  assert.equal(video.src,'/api/files/Source%20video.mp4?inline=1');assert.equal(f.get('app-dialog-confirm').hidden,true);assert.equal(f.get('app-dialog-cancel').textContent,'Tutup');
  video.onerror();assert.match(f.get('history-preview-status').textContent,/tetap bisa diunduh/);
  const pauses=video.pauseCount;previews[1].onclick();
  assert.ok(video.pauseCount>pauses);assert.equal(video.src,undefined);assert.equal(audio.hidden,false);assert.equal(audio.src,'/api/files/Clip%20audio.mp3?inline=1');
  const audioPauses=audio.pauseCount;f.get('app-dialog-cancel').onclick();
  assert.equal(dialog.open,false);assert.ok(audio.pauseCount>audioPauses);assert.equal(audio.src,undefined);assert.equal(f.get('app-dialog-media').hidden,true);
});

test('History media dialog cleanup restores normal rename dialog controls',async()=>{
  const f=fixture();f.context.row={job_id:'ready',state:'ready',filename:'Ready.mp4'};f.run('historyRows=[row];renderHistory(historyRows)');
  f.get('results').querySelector('.media-preview-direct').onclick();f.get('app-dialog-cancel').onclick();
  const request=f.run('streamDialog({title:"Ubah nama",value:"Ready.mp4"})');
  assert.equal(f.get('app-dialog-confirm').hidden,false);assert.equal(f.get('app-dialog-cancel').textContent,'Batal');assert.equal(f.get('app-dialog-media').hidden,true);
  f.get('app-dialog-cancel').onclick();await request;
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
  assert.equal(f.run('historyRows.length'), 1); // The Island observes the complete client dataset.
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
test('workspace markup keeps Source and Clipper separate while sharing History before Admin',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
  assert.match(html,/<section id="clipper-room"[\s\S]*<section id="history-panel"[\s\S]*<section id="admin-view"/);
  const workspace=html.slice(html.indexOf('<section id="control-room"'),html.indexOf('<section id="admin-view"'));
  const control=html.slice(html.indexOf('<section id="control-room"'),html.indexOf('<section id="clipper-room"'));
  const clipper=html.slice(html.indexOf('<section id="clipper-room"'),html.indexOf('<section id="history-panel"'));
  const admin=html.slice(html.indexOf('<section id="admin-view"'));
  for(const id of ['capture-panel','quality','media-format','compression','storage-target'])assert.ok(control.includes('id="'+id+'"'),id);
  for(const id of ['clipper-panel','clip-url','clip-start','clip-end','create-clip'])assert.ok(clipper.includes('id="'+id+'"'),id);
  assert.ok(!control.includes('id="clip-url"'));assert.ok(!clipper.includes('id="youtube-url"'));
  for(const id of ['history-panel','status-monitor','stop','marker-controls'])assert.ok(workspace.includes('id="'+id+'"'),id);
  for(const id of ['password-form','source-form','disk-meter']){assert.ok(admin.includes('id="'+id+'"'));assert.ok(!workspace.includes('id="'+id+'"'));}
  assert.ok(admin.includes('TRANSCRIPTION_PROVIDER'));assert.ok(!workspace.includes('TRANSCRIPTION_PROVIDER'));
});
test('Clipper timestamp helpers normalize supported forms and reject invalid values',()=>{
  const f=fixture();
  for(const value of [90,'90','1:30','01:30','00:01:30','1m30s']){
    f.context.value=value;assert.equal(f.run('parseClipTimestamp(value)'),90);
  }
  assert.equal(f.run('formatClipTimestamp(755)'),'12:35');
  for(const value of ['', '-1', '1:70', 'bad']){
    f.context.value=value;assert.throws(()=>f.run('parseClipTimestamp(value)'));
  }
});
test('Clipper quick durations lock End to Start and nudges respect video boundaries',()=>{
  const f=fixture();
  f.run("clipMetadata={token:'meta',duration:200};online=true;disk={can_record:true};$('clip-start').value='01:30';$('clip-end').value='';chooseClipDuration(60)");
  assert.equal(f.get('clip-start').value,'01:30');assert.equal(f.get('clip-end').value,'02:30');
  assert.equal(f.get('clip-calculated-duration').textContent,'01:00');assert.equal(f.get('clip-duration-60').attributes['aria-pressed'],'true');
  f.run("$('clip-start').value='02:00';clipInputChanged('start')");assert.equal(f.get('clip-end').value,'03:00');
  f.run("nudgeClip('start',50)");assert.equal(f.get('clip-start').value,'02:20');assert.equal(f.get('clip-end').value,'03:20');
  f.run("nudgeClip('end',5)");assert.equal(f.get('clip-end').value,'03:20');assert.equal(f.get('clip-duration-custom').attributes['aria-pressed'],'true');
});
test('Clipper initializes one youtube-nocookie player, seeks URL t, and replaces it cleanly',async()=>{
  const f=fixture(undefined,'https://streamfetch.example/clipper');
  f.run(`window.testPlayers=[];window.YT={Player:function(mount,config){this.mount=mount;this.config=config;this.currentTime=0;this.seeks=[];this.getCurrentTime=()=>this.currentTime;this.seekTo=value=>{this.currentTime=value;this.seeks.push(value);};this.destroy=()=>{this.destroyed=true;};window.testPlayers.push(this);}}`);
  await f.run("clipMetadata={duration:1200,video_id:'abcdefghijk',url_start:755};initClipPlayer('abcdefghijk',755)");
  const first=f.context.window.testPlayers[0];
  assert.equal(first.config.videoId,'abcdefghijk');assert.equal(first.config.host,'https://www.youtube-nocookie.com');assert.equal(first.config.playerVars.start,755);
  first.config.events.onReady({target:first});
  assert.equal(first.seeks.length,1);assert.equal(first.seeks[0],755);assert.equal(f.get('clip-current-time').textContent,'12:35');
  await f.run("clipMetadata={duration:300,video_id:'lmnopqrstuv',url_start:0};initClipPlayer('lmnopqrstuv',0)");
  assert.equal(first.destroyed,true);assert.equal(f.context.window.testPlayers.length,2);
  const second=f.context.window.testPlayers[1];assert.equal(second.config.videoId,'lmnopqrstuv');
  f.get('clip-url').value='https://youtu.be/zzzzzzzzzzz';f.get('clip-url').oninput();
  assert.equal(second.destroyed,true);assert.equal(f.get('clip-preview').hidden,true);
});
test('Clipper loads one IFrame API script and only creates the latest requested player',async()=>{
  const f=fixture(undefined,'https://streamfetch.example/clipper');
  f.run("clipMetadata={duration:300};firstInit=initClipPlayer('abcdefghijk',10);secondInit=initClipPlayer('lmnopqrstuv',20)");
  assert.equal(f.document.head.children.length,1);assert.equal(f.document.head.children[0].src,'https://www.youtube.com/iframe_api');
  f.run(`window.testPlayers=[];window.YT={Player:function(mount,config){this.config=config;this.destroy=()=>{};window.testPlayers.push(this);}};window.onYouTubeIframeAPIReady()`);
  await f.run('Promise.all([firstInit,secondInit])');
  assert.equal(f.context.window.testPlayers.length,1);assert.equal(f.context.window.testPlayers[0].config.videoId,'lmnopqrstuv');
});
test('Clipper player sets and jumps boundaries, keeps duration lock, and honors I/O shortcuts outside controls',async()=>{
  const f=fixture(undefined,'https://streamfetch.example/clipper');
  f.run(`window.testPlayers=[];window.YT={Player:function(mount,config){this.config=config;this.currentTime=100.9;this.seeks=[];this.getCurrentTime=()=>this.currentTime;this.seekTo=value=>{this.currentTime=value;this.seeks.push(value);};this.destroy=()=>{};window.testPlayers.push(this);}}`);
  await f.run("clipMetadata={duration:300,video_id:'abcdefghijk'};online=true;disk={can_record:true};initClipPlayer('abcdefghijk',0)");
  const player=f.context.window.testPlayers[0];player.config.events.onReady({target:player});
  player.currentTime=101.9;f.run('updateClipPlayerTime()');assert.equal(f.get('clip-current-time').textContent,'01:41');assert.equal(f.get('clip-start').value,'00:00');
  player.currentTime=100.9;f.run('chooseClipDuration(30)');f.get('clip-set-start').onclick();
  assert.equal(f.get('clip-start').value,'01:40');assert.equal(f.get('clip-end').value,'02:10');
  player.currentTime=160.7;f.get('clip-set-end').onclick();assert.equal(f.get('clip-end').value,'02:40');
  f.get('clip-start').value='00:15';f.get('clip-jump-start').onclick();assert.equal(player.seeks.at(-1),15);
  f.get('clip-end').value='00:45';f.get('clip-jump-end').onclick();assert.equal(player.seeks.at(-1),45);
  const shortcut=(key,target=f.document.body)=>({key,target,preventDefault(){this.defaultPrevented=true;}});
  player.currentTime=75.9;const setStart=shortcut('i');f.context.shortcut=setStart;f.run('clipBoundaryShortcut(shortcut)');assert.equal(setStart.defaultPrevented,true);assert.equal(f.get('clip-start').value,'01:15');
  player.currentTime=90.2;const setEnd=shortcut('o');f.context.shortcut=setEnd;f.run('clipBoundaryShortcut(shortcut)');assert.equal(f.get('clip-end').value,'01:30');
  f.get('clip-start').tagName='input';player.currentTime=120;const ignored=shortcut('i',f.get('clip-start'));f.context.shortcut=ignored;f.run('clipBoundaryShortcut(shortcut)');assert.equal(ignored.defaultPrevented,undefined);assert.equal(f.get('clip-start').value,'01:15');
  f.run("nudgeClip('start',5)");assert.equal(player.seeks.at(-1),80);
});
test('Clipper embed failure leaves validated manual clipping available',async()=>{
  const f=fixture(undefined,'https://streamfetch.example/clipper');
  f.run(`window.testPlayers=[];window.YT={Player:function(mount,config){this.config=config;this.destroy=()=>{this.destroyed=true;};window.testPlayers.push(this);}}`);
  await f.run("clipMetadata={duration:120,video_id:'abcdefghijk'};online=true;disk={can_record:true};$('clip-start').value='10';$('clip-end').value='20';updateClipControls();initClipPlayer('abcdefghijk',0)");
  const player=f.context.window.testPlayers[0];player.config.events.onError({data:101});
  assert.equal(f.get('clip-player-frame').hidden,true);assert.match(f.get('clip-player-status').textContent,/manual/i);
  assert.equal(f.get('create-clip').disabled,false);assert.equal(f.get('clip-start').value,'10');assert.equal(f.get('clip-end').value,'20');
});
test('Clipper validates zero, reversed and out-of-range clips and offers an accessible swap',()=>{
  const f=fixture();f.run("clipMetadata={token:'meta',duration:100};online=true;disk={can_record:true}");
  for(const [start,end,message] of [['10','10','nol'],['20','10','setelah'],['10','101','melewati']]){
    f.get('clip-start').value=start;f.get('clip-end').value=end;f.run('updateClipControls()');
    assert.match(f.get('clip-error').textContent,new RegExp(message,'i'));assert.equal(f.get('create-clip').disabled,true);
  }
  assert.equal(f.get('clip-swap').hidden,true);
  f.get('clip-start').value='20';f.get('clip-end').value='10';f.run('updateClipControls()');assert.equal(f.get('clip-swap').hidden,false);
  f.get('clip-swap').onclick();assert.equal(f.get('clip-start').value,'10');assert.equal(f.get('clip-end').value,'20');assert.equal(f.get('create-clip').disabled,false);
});
test('Clipper metadata lookup reveals helpers, applies URL t and submits canonical seconds',async()=>{
  const f=fixture(),requests=[];f.context.requests=requests;
  f.run("csrf='token';online=true;disk={can_record:true};api=async(path,data)=>{requests.push({path,data});if(path==='clipper/metadata')return {token:'meta',title:'Dialog Batam',thumbnail:'',duration:1200,duration_label:'20:00',url_start:755,url_start_label:'12:35'};if(path==='clipper')return {job_id:'clip-job'};return {}};refresh=async()=>{}");
  f.get('clip-url').value='https://youtu.be/abcdefghijk?t=755';await f.run('loadClipMetadata()');
  assert.equal(f.get('clip-title').textContent,'Dialog Batam');assert.equal(f.get('clip-start').value,'12:35');assert.equal(f.get('clip-helpers').hidden,false);
  f.run('chooseClipDuration(90)');await f.get('clip-form').onsubmit({preventDefault(){}});
  assert.equal(requests[0].path,'clipper/metadata');assert.equal(requests[1].path,'clipper');
  assert.deepEqual(JSON.parse(JSON.stringify(requests[1].data)),{source:'youtube',url:'https://youtu.be/abcdefghijk?t=755',metadata_token:'meta',start:755,end:845,clip_start:755,clip_end:845,clip_duration:90,is_clip:true,format:'mp4',quality:'best',compression:'original',storage:'local',note:''});
});
test('Clipper route selects its own navigation while History labels clips clearly',async()=>{
  const f=fixture(undefined,'https://streamfetch.example/clipper');f.run("api=async path=>path==='sources'?{sources:[]}:path==='status'?{disk:{available:true,free:1,total:2,minimum:0,can_record:true},active:null,online:true,queued:0,archive_enabled:false}:{recordings:[],total:0}");
  await f.run('enter({is_admin:false})');assert.equal(f.get('clipper-room').hidden,false);assert.equal(f.get('control-room').hidden,true);assert.equal(f.get('nav-clipper').attributes['aria-current'],'page');
  f.run("renderHistory([{job_id:'clip',source:'youtube',source_name:'YouTube',is_clip:true,clip_start:755,clip_end:845,clip_duration:90,state:'ready',filename:'clip.mp4'}])");
  assert.equal(f.get('results').children[0].children[2].textContent,'YouTube · 12:35–14:05 · 01:30');
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
  const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
  assert.match(html,/id="source-tabs" class="tabs" role="group" aria-label="Source"><span id="source-tab-indicator" class="source-tab-indicator" aria-hidden="true"><\/span>/);
  assert.match(css,/\.source-tab-indicator\{position:absolute;z-index:0/);assert.match(css,/\.tabs\[data-motion-ready=true\] \.tab\.selected\{background:transparent;box-shadow:none\}/);
  assert.ok(html.indexOf('id="tab-youtube"')<html.indexOf('id="tab-tiktok"'));
  assert.ok(html.indexOf('id="tab-tiktok"')<html.indexOf('id="tab-instagram"'));
  assert.ok(html.indexOf('id="tab-instagram"')<html.indexOf('id="tab-oryx"'));
  const f=fixture();
  for(const source of ['youtube','tiktok','instagram','oryx']){
    assert.match(html,new RegExp('id="tab-'+source+'" class="tab(?: selected)?" type="button" aria-pressed="(?:true|false)" aria-controls="'+source+'-fields"'));
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
    assert.equal(f.get('results').querySelector('.recording-heading').querySelector('.status-badge').textContent,state==='failed'?'Capture gagal':f.run('jobStateName(rows[0])'));
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

test('successful login returns to the safe Reader URL, including its query and fragment',async()=>{
  const motion={loginReveal(){},loginInteract(){},loginSuccess(){this.succeeded=true;}};
  const next=encodeURIComponent('/api/recordings/reader-job/transcript/view?mode=compact');
  const f=fixture(motion,'https://streamfetch.example/?next='+next+'#segment-4');
  f.context.fetch=async()=>({status:200,ok:true,headers:{get:()=> 'application/json'},json:async()=>({csrf:'reader-csrf',is_admin:false})});
  f.context.enter=()=>{throw Error('Reader login must navigate instead of entering the workspace');};
  f.get('password').value='reader-test';
  await f.get('login-form').onsubmit({preventDefault(){}});
  assert.equal(f.context.window.location.replaced,'/api/recordings/reader-job/transcript/view?mode=compact#segment-4');
  assert.equal(motion.succeeded,true);assert.equal(f.get('password').value,'');
});

test('external and protocol-relative login return URLs are rejected',async()=>{
  for(const target of ['https://evil.example/transcript','//evil.example/transcript','/\\evil.example/transcript']){
    const f=fixture(undefined,'https://streamfetch.example/?next='+encodeURIComponent(target));let entered;
    f.context.fetch=async()=>({status:200,ok:true,headers:{get:()=> 'application/json'},json:async()=>({csrf:'safe-csrf',is_admin:false})});
    f.context.enter=async auth=>{entered=auth;};
    await f.get('login-form').onsubmit({preventDefault(){}});
    assert.equal(f.context.window.location.replaced,undefined);assert.equal(entered.csrf,'safe-csrf');
  }
});

test('authentication tokens are kept out of browser storage',()=>{
  const source=fs.readFileSync(path.resolve(__dirname,'../app/static/app.js'),'utf8');
  assert.doesNotMatch(source,/\b(?:localStorage|sessionStorage)\b/);
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
 const phone=css.slice(css.indexOf('/* Phones use a composition')).split('/* Small shared feedback')[0];
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
  sourceMode(panel,change,options){calls.push(['sourceMode',panel,options]);change();},
  sourcePress(tab){calls.push(['sourcePress',tab]);},
  dialogOpen(dialog,panel){calls.push(['dialogOpen',dialog,panel]);},
  dialogClose(dialog,panel,complete){calls.push(['dialogClose',dialog,panel]);complete();},
  island(panel,key,phase,change){calls.push(['island',key,phase]);change();},
  progress(panel,value){if(value===null)panel.removeAttribute('value');else panel.value=value;},
};
}
test('platform tabs hand synchronous state changes and direction to scoped Source motion',()=>{
 const motion=motionSpy(),f=fixture(motion);
 f.run("setMode('youtube')");let call=motion.calls.findLast(item=>item[0]==='sourceMode');
 assert.equal(f.run('mode'),'youtube');assert.equal(call[1],f.get('capture-panel'));assert.equal(call[2].outgoing,f.get('oryx-fields'));assert.equal(call[2].incoming,f.get('youtube-fields'));assert.equal(call[2].direction,-1);
 assert.ok(call[2].shared.includes(f.get('capture-content')));assert.equal(f.get('youtube-fields').hidden,false);
 f.run("setMode('tiktok');setMode('instagram');setMode('oryx')");assert.equal(f.run('mode'),'oryx');
 assert.equal(motion.calls.filter(item=>item[0]==='sourceMode').at(-1)[2].direction,1);
 const switches=motion.calls.filter(item=>item[0]==='sourceMode').length;f.run("setMode('oryx')");
 assert.equal(motion.calls.filter(item=>item[0]==='sourceMode').length,switches);assert.deepEqual(motion.calls.at(-1),['sourcePress',f.get('tab-oryx')]);
});
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
 f.run('renderWatches({watches:[],active_count:0})');assert.match(f.get('watch-feedback').textContent,/Belum ada Watch.*Tambah Watch/);
});
test('Watches load for authenticated roles and handle list errors locally',async()=>{
 const f=fixture(),calls=[];
 f.context.fetch=async url=>{calls.push(url);return {ok:false,status:503,headers:{get:()=> 'application/json'},json:async()=>({error:'Watch unavailable'})};};
 await f.run('loadWatches()');assert.deepEqual(calls,['/api/watches']);
 assert.equal(f.get('watch-feedback').textContent,'Watch unavailable');
 assert.equal(f.get('notice').textContent,'');
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 const panel=html.split('id="watches-panel"')[1].split('</section>')[0];
 assert.match(panel,/hidden/);assert.match(panel,/Watches/);assert.match(panel,/id="watch-create"[^>]*>\+ Tambah Watch/);assert.match(panel,/id="watch-url"/);
 assert.doesNotMatch(html,/id="tab-watch"|id="watch-fields"/);assert.equal((html.match(/id="watch-url"/g)||[]).length,1);
});
test('Watches visibility follows authenticated roles and cancellation reuses the shared API without stopping capture',async()=>{
 const f=fixture();captureAPI(f);await f.run('enter({is_admin:false})');assert.equal(f.get('watches-panel').hidden,false);
 await f.run('enter({is_admin:true})');assert.equal(f.get('watches-panel').hidden,false);
 f.run("renderWatches({active_count:1,watches:[{id:'watch',channel:'@rri',start:100,end:200,status:'active',mode:'first',auto_transcribe:false}]})");
 const calls=[];f.context.fetch=async(url,options)=>{calls.push([url,options.method]);return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>url.endsWith('/cancel')?{ok:true}:{active_count:0,watches:[{id:'watch',channel:'@rri',start:100,end:200,status:'cancelled',mode:'first',auto_transcribe:false}]}};};
 const button=f.get('watch-list').querySelector('button');const pending=button.onclick();assert.equal(button.disabled,true);await pending;
 assert.deepEqual(calls,[['/api/watches/watch/cancel','POST'],['/api/watches','GET']]);
 assert.equal(f.get('watch-count').textContent,'0 aktif');assert.equal(f.get('watch-list').querySelector('button'),null);
 assert.match(f.get('toast-status').textContent,/Watch dibatalkan.*Capture.*berlanjut/);
});
test('Settings Watch form uses the existing endpoint, refreshes Waiting state, and keeps validation inline',async()=>{
 const motion=motionSpy(),f=fixture(motion),calls=[];
 assert.equal(f.get('watch-create-panel').hidden,true);f.get('watch-create').onclick();
 assert.equal(f.get('watch-create-panel').hidden,false);assert.equal(f.get('watch-create').attributes['aria-expanded'],'true');assert.equal(f.get('watch-url').focused,true);
 f.get('watch-url').value='@rribatam';f.get('watch-day').value='date';f.get('watch-day').onchange();
 f.get('watch-date').value='2099-10-06';f.get('watch-start').value='08:00';f.get('watch-end').value='10:00';
 f.get('watch-mode').value='every';f.get('watch-auto').checked=true;
 f.context.fetch=async(url,options)=>{calls.push([url,options?.method,options?.body&&JSON.parse(options.body)]);return {ok:true,status:url==='/api/watches'&&options?.method==='POST'?201:200,headers:{get:()=> 'application/json'},json:async()=>url==='/api/watches'&&options?.method==='POST'?{id:'watch',status:'waiting'}:{active_count:1,watches:[{id:'watch',channel:'@rribatam',start:1,end:2,status:'waiting',mode:'every',auto_transcribe:true}]}};};
 await f.get('watch-form').onsubmit({preventDefault(){}});
 assert.deepEqual(calls[0],['/api/watches','POST',{channel:'@rribatam',date:'2099-10-06',start_time:'08:00',end_time:'10:00',mode:'every',auto_transcribe:true}]);
 assert.deepEqual(calls[1].slice(0,2),['/api/watches','GET']);assert.equal(f.get('watch-count').textContent,'1 aktif');assert.match(f.get('watch-list').textContent,/Waiting/);
 assert.equal(f.get('watch-create-panel').hidden,true);assert.equal(f.get('watch-create').attributes['aria-expanded'],'false');assert.equal(f.get('watch-url').value,'');
 assert.match(f.get('toast-status').textContent,/Watch dibuat.*Menunggu live/);
 assert.equal(motion.calls.filter(call=>call[0]==='dialogOpen').length,1);assert.equal(motion.calls.filter(call=>call[0]==='dialogClose').length,1);
 f.get('watch-create').onclick();f.get('watch-url').value='@rribatam';f.get('watch-start').value='10:00';f.get('watch-end').value='09:00';
 calls.length=0;await f.get('watch-form').onsubmit({preventDefault(){}});assert.equal(calls.length,0);assert.match(f.get('watch-error').textContent,/Jam akhir/);assert.equal(f.get('watch-create-panel').hidden,false);assert.equal(f.get('notice').textContent,'');
});
test('Settings Watch cancel resets the one creation form without calling the API',async()=>{
 const motion=motionSpy(),f=fixture(motion);let requests=0;f.context.fetch=async()=>{requests++;throw Error('unexpected');};
 f.get('watch-create').onclick();f.get('watch-url').value='@draft';await f.get('watch-create-cancel').onclick();
 assert.equal(requests,0);assert.equal(f.get('watch-create-panel').hidden,true);assert.equal(f.get('watch-url').value,'');assert.equal(f.get('watch-create').focused,true);
 assert.equal(motion.calls.filter(call=>call[0]==='dialogClose').length,1);
});
test('Watch endpoint validation remains inside the open Settings form',async()=>{
 const f=fixture();f.get('watch-create').onclick();f.get('watch-url').value='invalid';f.get('watch-day').value='date';f.get('watch-day').onchange();f.get('watch-date').value='2099-10-06';
 f.context.fetch=async()=>({ok:false,status:400,headers:{get:()=> 'application/json'},json:async()=>({error:'Channel YouTube tidak valid.'})});
 await f.get('watch-form').onsubmit({preventDefault(){}});
 assert.equal(f.get('watch-error').textContent,'Channel YouTube tidak valid.');assert.equal(f.get('watch-create-panel').hidden,false);assert.equal(f.get('watch-create-submit').disabled,false);
});
test('failed cancellation preserves watch controls, and a stale list cannot overwrite post-cancel data',async()=>{
 const f=fixture();f.run("isAdmin=true;renderWatches({active_count:1,watches:[{id:'watch',channel:'@rri',start:100,end:200,status:'active',mode:'first'}]})");
 f.context.fetch=async()=>({ok:false,status:500,headers:{get:()=> 'application/json'},json:async()=>({error:'Try again'})});
 const button=f.get('watch-list').querySelector('button');await button.onclick();assert.equal(button.disabled,false);assert.equal(f.get('toast-error-live').textContent,'Try again');
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
test('login cursor lifecycle stops before workspace visibility and resets before showing login again',async()=>{
 const motion=motionSpy(),f=fixture(motion),events=[];captureAPI(f);
 motion.loginSuccess=()=>{events.push('success');assert.equal(f.get('login').hidden,false);assert.equal(f.get('workspace').hidden,true);};
 motion.reset=()=>events.push('reset');
 motion.loginReveal=()=>{events.push('reveal');assert.equal(f.get('login').hidden,false);assert.equal(f.get('workspace').hidden,true);};
 f.get('workspace').hidden=true;await f.run("completeLogin({csrf:'token',is_admin:false})");
 assert.equal(f.get('login').hidden,true);assert.deepEqual(events,['success']);
 f.run('showLogin()');assert.deepEqual(events,['success','reset','reveal']);
 f.run('showLogin()');assert.deepEqual(events,['success','reset','reveal']);
});
test('login geometric layers use explicit ambient/trail/content ordering and device eligibility, not a narrow-screen cutoff',()=>{
 const css=fs.readFileSync(path.resolve(__dirname,'../app/static/style.css'),'utf8');
 assert.match(css,/\.workspace-page \.login-shell\{position:relative;isolation:isolate\}/);
 assert.match(css,/\.login-shell>\.login-card,\.workspace-page \.login-shell>\.login-credit\{position:relative;z-index:2\}/);
 assert.match(css,/\.login-cursor-trail\{position:fixed;inset:0;z-index:0;overflow:clip;pointer-events:none;user-select:none\}/);
 assert.match(css,/\.login-ambient\{z-index:0\}\.workspace-page \.login-trail-particles\{z-index:1\}/);
 assert.match(css,/\.login-geometry\{[^}]*display:block;[^}]*width:20px;height:20px;[^}]*pointer-events:none/);
 for(const shape of ['circle','diamond','plus'])assert.ok(css.includes('[data-shape='+shape+']::before'));
 assert.match(css,/@media\(prefers-reduced-motion:reduce\),\(pointer:coarse\),\(hover:none\)\{\.workspace-page \.login-trail-particles\{display:none\}\}/);
 assert.match(css,/prefers-reduced-motion:reduce\)\{\.workspace-page \.login-ambient \.login-geometry\{opacity:\.08!important\}/);
 assert.doesNotMatch(css,/@media[^}]*max-width:[^}]*login-(cursor-trail|trail-particles)/);
 assert.doesNotMatch(css,/cursor\s*:\s*none/);
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
test('failed capture shows concise classified copy, raw detail, attempt number, and one guarded Retry',async()=>{
 const f=fixture(),requests=[];f.context.requests=requests;
 f.context.row={job_id:'failed',state:'failed',source:'youtube',source_name:'YouTube',is_live:false,
  quality:'720',compression:'compact',output_format:'mp4',storage:'archive',filename:'Dialog.mp4',
  attempt_root_id:'failed',attempt_number:1,attempt_total:1,can_retry:true,error_code:'http_403',
  error_title:'Gagal mengambil media',error_message:'YouTube menolak permintaan media. Coba lagi untuk mengambil sumber media baru.',
  detail:'ERROR: HTTP Error 403: Forbidden'};
 f.context.retryGate={};f.run("api=async(path,data)=>{requests.push([path,data]);return new Promise(resolve=>retryGate.resolve=resolve)};refresh=async()=>{}");
 f.run('historyRows=[row];renderHistory(historyRows);lastObservedJob=row;renderOperationalStatus()');
 const rendered=f.get('results').children[0],retry=rendered.querySelector('.retry-capture');
 assert.ok(retry);assert.equal(retry.textContent,'Coba lagi');assert.match(rendered.children[1].children[1].textContent,/Gagal mengambil media.*YouTube menolak/);
 assert.doesNotMatch(rendered.children[1].children[1].title,/HTTP Error 403/);
 assert.equal(rendered.querySelector('.recording-detail-body').children[0].textContent,'ERROR: HTTP Error 403: Forbidden');
 assert.match(rendered.querySelector('.detail-metadata').textContent,/Percobaan 1\/1/);
 assert.match(f.get('status-detail').textContent,/Gagal mengambil media/);
 const first=retry.onclick();retry.onclick();assert.equal(requests.length,1);assert.equal(retry.disabled,true);
 f.run('renderHistory(historyRows)');const pendingRetry=f.get('results').querySelector('.retry-capture');
 assert.equal(pendingRetry.disabled,true);assert.equal(pendingRetry.textContent,'Mengirim…');pendingRetry.onclick();assert.equal(requests.length,1);
 f.context.retryGate.resolve({job_id:'retry-2',retry_of:'failed',attempt_root_id:'failed',attempt_number:2,attempt_total:2});await first;
 assert.equal(f.get('results').children[0].dataset.jobId,'retry-2');assert.equal(f.get('status-monitor').dataset.phase,'starting');
 assert.equal(requests[0][0],'recordings/failed/retry');assert.match(f.get('toast-status').textContent,/Percobaan 2\/2/);
});
test('failed Retry request restores the action and reports a non-blocking error toast',async()=>{
 const f=fixture();f.context.row={job_id:'failed',state:'failed',source:'instagram',can_retry:true,detail:'diagnostic'};
 f.run("api=async()=>{throw Error('Masih ada capture yang berjalan.')};historyRows=[row];renderHistory(historyRows)");
 const retry=f.get('results').querySelector('.retry-capture');await retry.onclick();
 assert.equal(retry.disabled,false);assert.match(f.get('toast-error-live').textContent,/Masih ada capture/);
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
 assert.deepEqual(scripts,['/static/vendor/gsap.min.js','/static/vendor/Flip.min.js','/static/vendor/MorphSVGPlugin.min.js','/static/motion.js','/static/app.js']);
 assert.equal(fs.readFileSync(path.join(root,'package.json'),'utf8').includes('"gsap": "3.15.0"'),true);
 const reader=fs.readFileSync(path.join(root,'app/static/transcript.html'),'utf8');
 assert.deepEqual([...reader.matchAll(/<script src="([^"]+)" defer/g)].map(match=>match[1]),['/static/vendor/gsap.min.js','/static/vendor/Flip.min.js','/static/vendor/ScrollTrigger.min.js','/static/vendor/ScrollSmoother.min.js','/static/motion.js','/static/transcript.js']);
 const build=fs.readFileSync(path.join(root,'scripts/build-frontend.mjs'),'utf8');
 assert.match(build,/ScrollTrigger\.min\.js/);assert.match(build,/ScrollSmoother\.min\.js/);
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

test('toasts announce success politely and errors assertively, dismiss on time, and preserve page notices',()=>{
 const f=fixture();f.run("notice('Persistent feedback');toast('Watch dibuat.')");
 assert.equal(f.get('toast-region').children.length,1);assert.equal(f.get('toast-status').textContent,'Watch dibuat.');
 const timer=f.run('activeToast.timer');assert.equal(f.timers.get(timer).delay,4500);
 assert.equal(f.document.activeElement,f.document.body);f.fireTimer(timer);assert.equal(f.get('toast-region').children.length,0);
 f.run("toast('Permintaan gagal.','error')");assert.equal(f.get('toast-error-live').textContent,'Permintaan gagal.');
 assert.equal(f.timers.get(f.run('activeToast.timer')).delay,10000);
 const close=f.get('toast-region').querySelector('button');assert.equal(close.attributes['aria-label'],'Tutup notifikasi');close.onclick();
 assert.equal(f.get('toast-region').children.length,0);assert.equal(f.get('notice').textContent,'Persistent feedback');
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 assert.match(html,/id="toast-status"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"/);
 assert.match(html,/id="toast-error-live"[^>]*role="alert"[^>]*aria-live="assertive"/);
});
test('rapid toast events queue without overlap, deduplicate repeats, and ignore obsolete exit callbacks',()=>{
 const exits=[],f=fixture({toast(node,visible,done){if(!visible)exits.push(done);return ()=>{};}});
 f.run("toast('First');toast('Second');toast('Second');toast('Third','error')");
 assert.equal(f.get('toast-region').children.length,1);assert.equal(f.run('toastQueue.length'),2);
 f.run('dismissToast();dismissToast()');assert.equal(exits.length,1);exits[0]();
 assert.equal(f.get('toast-region').children.length,1);assert.equal(f.get('toast-status').textContent,'Second');
 exits[0]();assert.equal(f.get('toast-status').textContent,'Second');
 f.run('dismissToast()');exits[1]();assert.equal(f.get('toast-error-live').textContent,'Third');
 f.run('resetToasts()');assert.equal(f.get('toast-region').children.length,0);assert.equal(f.run('toastQueue.length'),0);
});
test('toast timers pause during hover or keyboard focus and logout clears queued feedback',()=>{
 const f=fixture();f.run("toast('Hover or focus');toast('Queued')");const card=f.get('toast-region').children[0];
 const initial=f.run('activeToast.timer');card.listeners.mouseenter();assert.equal(f.timers.has(initial),false);
 card.listeners.mouseleave();assert.equal(f.timers.has(f.run('activeToast.timer')),true);
 const timer=f.run('activeToast.timer');card.listeners.focusin();assert.equal(f.timers.has(timer),false);
 card.listeners.focusout({relatedTarget:card.querySelector('button')});assert.equal(f.timers.has(f.run('activeToast.timer')),false);
 card.listeners.focusout({relatedTarget:null});assert.equal(f.timers.has(f.run('activeToast.timer')),true);
 f.run('showLogin()');assert.equal(f.get('toast-region').children.length,0);assert.equal(f.run('toastQueue.length'),0);
});
const polishRows=[
 {job_id:'one',filename:'PAGI.mp4',note:'Dialog Batam',source:'youtube',source_name:'RRI Batam',state:'ready',source_metadata:{title:'Berita Nusantara'},download_count:7},
 {job_id:'two',title:'Evening stream',source:'tiktok',state:'failed',detail:'ERROR: internal diagnostic'},
 {job_id:'three',filename:'Diskusi.mp4',source:'instagram',state:'ready',transcript:{status:'transcribing'}},
 {job_id:'four',filename:'Queued.mp4',source:'youtube',state:'ready',transcript:{status:'queued'}},
 {job_id:'five',title:'Live now',source:'oryx',state:'recording'}
];
test('History bulk download reuses the accessible row icon without initial admin placeholders',()=>{
 const html=fs.readFileSync(path.resolve(__dirname,'../app/static/index.html'),'utf8');
 const button=html.match(/<button id="download-selected"[^>]*>/)[0];
 assert.match(button,/class="download-direct"/);assert.match(button,/type="button"/);
 assert.match(button,/aria-label="Unduh pilihan"/);assert.match(button,/title="Unduh pilihan"/);assert.match(button,/ disabled/);
 assert.match(html,/<button id="download-selected"[^>]*><\/button>/);
 assert.match(html,/<button id="delete-selected"[^>]* hidden disabled>/);
 assert.match(html,/<button id="filter-reset"[^>]* hidden disabled>/);
 const f=fixture();f.context.rows=polishRows;f.run('renderHistory(rows)');
 const bulk=f.get('download-selected'),row=f.get('results').querySelector('.download-direct');
 assert.equal(bulk.querySelectorAll('svg').length,1);assert.equal(bulk.querySelector('svg').attributes['aria-hidden'],'true');
 assert.deepEqual(bulk.querySelectorAll('path').map(path=>path.attributes.d),row.querySelectorAll('path').map(path=>path.attributes.d));
 assert.equal(bulk.textContent,'');assert.equal(bulk.disabled,true);assert.equal(f.get('delete-selected').hidden,true);
 assert.ok(f.get('results').querySelector('.action-trigger'),'Row overflow actions stay available');
});
test('History selection enables bulk download only for ready files and preserves select-all and download behavior',()=>{
 const f=fixture();f.context.rows=[{job_id:'ready',state:'ready',filename:'A B.mp4'},{job_id:'failed',state:'failed',filename:'partial.mp4'},{job_id:'recording',state:'recording'},{job_id:'no-file',state:'ready'}];f.run('renderHistory(rows)');
 const boxes=f.get('results').querySelectorAll('.history-select');
 for(const box of boxes.slice(1)){box.checked=true;box.onchange();assert.equal(f.get('download-selected').disabled,true);}
 assert.equal(f.get('select-all').indeterminate,true);
 boxes[0].checked=true;boxes[0].onchange();assert.equal(f.get('download-selected').disabled,false);assert.equal(f.get('select-all').checked,true);
 const downloads=[],create=f.document.createElement;
 f.document.createElement=tag=>{const element=create(tag);if(tag==='a')element.click=()=>downloads.push({href:element.href,filename:element.download});return element;};
 f.get('download-selected').onclick();assert.deepEqual(downloads,[{href:'/api/files/A%20B.mp4',filename:'A B.mp4'}]);
 f.get('select-all').checked=false;f.get('select-all').onchange();
 assert.ok(boxes.every(box=>!box.checked));assert.equal(f.get('download-selected').disabled,true);assert.equal(f.get('select-all').indeterminate,false);
 f.get('select-all').checked=true;f.get('select-all').onchange();
 assert.ok(boxes.every(box=>box.checked));assert.equal(f.get('download-selected').disabled,false);
 f.get('filter-state').value='failed';f.get('filter-state').onchange();
 assert.equal(f.get('download-selected').disabled,true,'Filtered-out ready files must not remain downloadable selections');
});
test('History admin bulk deletion remains visible and follows selection while normal users never see it',()=>{
 const f=fixture();f.context.rows=polishRows;f.run('isAdmin=true;renderHistory(rows)');
 assert.equal(f.get('delete-selected').hidden,false);assert.equal(f.get('delete-selected').disabled,true);
 const checkbox=f.get('results').querySelectorAll('.history-select')[1];checkbox.checked=true;checkbox.onchange();
 assert.equal(f.get('delete-selected').disabled,false);assert.equal(f.get('download-selected').disabled,true);
 f.run('isAdmin=false;updateDeleteControls()');assert.equal(f.get('delete-selected').hidden,true);assert.equal(f.get('delete-selected').disabled,true);
});
test('History clear action appears only for active search, source, status or date filters',()=>{
 const f=fixture();f.context.rows=polishRows;f.run('renderHistory(rows)');
 assert.equal(f.get('filter-reset').hidden,true);
 for(const [id,value] of [['filter-q','missing'],['filter-source','youtube'],['filter-state','ready'],['filter-date','2026-10-07']]){
  f.get(id).value=value;(id==='filter-q'?f.get(id).oninput:f.get(id).onchange)();
  assert.equal(f.get('filter-reset').hidden,false);assert.equal(f.get('filter-reset').disabled,false);
  f.get('filter-reset').onclick();assert.equal(f.get('filter-reset').hidden,true);assert.equal(f.get('filter-reset').disabled,true);
 }
 f.get('filter-q').value='  ';f.get('filter-q').oninput();assert.equal(f.get('filter-reset').hidden,true);
 f.get('filter-source').value='youtube';f.get('filter-state').value='ready';f.get('filter-source').onchange();
 f.get('filter-source').value='';f.get('filter-source').onchange();assert.equal(f.get('filter-reset').hidden,false,'Remaining active filters keep the clear action visible');
});
test('History searches filename, title, metadata and source immediately without a request',()=>{
 const f=fixture();f.context.rows=polishRows;f.run('renderHistory(rows)');
 f.context.fetch=()=>assert.fail('Client-side filters must not request the backend');
 for(const [query,expected] of [['  pagi  ',['one']],['evening',['two']],['nusantara',['one']],['RRI',['one']],['INSTAGRAM',['three']]]){
  f.get('filter-q').value=query;f.get('filter-q').oninput();assert.deepEqual(f.get('results').children.map(row=>row.dataset.jobId),expected);
 }
 assert.equal(f.run('historyRows.length'),5);f.get('filter-reset').onclick();assert.equal(f.get('results').children.length,5);assert.equal(f.get('filter-reset').disabled,true);
});
test('History status/source filters combine and Transcribing includes queued work while admin fields stay scoped',()=>{
 const f=fixture();f.context.rows=polishRows;f.run('isAdmin=true;renderHistory(rows)');
 assert.equal(f.get('results').querySelector('.download-stat').textContent,'7× download');
 f.get('filter-state').value='transcribing';f.get('filter-state').onchange();assert.deepEqual(f.get('results').children.map(row=>row.dataset.jobId),['three','four']);
 f.get('filter-source').value='youtube';f.get('filter-source').onchange();assert.deepEqual(f.get('results').children.map(row=>row.dataset.jobId),['four']);
 f.get('filter-state').value='failed';f.get('filter-source').value='';f.get('filter-state').onchange();assert.equal(f.get('results').children[0].dataset.jobId,'two');
 f.get('filter-reset').onclick();f.run('isAdmin=false;renderHistory(historyRows)');assert.equal(f.get('results').querySelector('.download-stat'),null);
 f.get('filter-state').value='recording';f.get('filter-state').onchange();assert.equal(f.get('results').children[0].dataset.jobId,'five');
});
test('empty filtered History explains how to recover and remains distinct from no recordings',()=>{
 const f=fixture();f.context.rows=polishRows;f.run('renderHistory(rows)');f.get('filter-q').value='missing';f.get('filter-q').oninput();
 assert.equal(f.get('empty').hidden,false);assert.match(f.get('empty').textContent,/Tidak ada rekaman yang cocok.*bersihkan filter/);
 assert.equal(f.get('result-count').textContent,'0 / 5 hasil');assert.equal(f.get('filter-reset').disabled,false);
 f.get('filter-reset').onclick();assert.equal(f.get('empty').hidden,true);
 f.run('renderHistory([])');assert.match(f.get('empty').textContent,/Belum ada rekaman.*Source/);
});
test('History preserves the existing date filter locally and only visible selections are acted on',()=>{
 const f=fixture();f.context.rows=[{job_id:'first',state:'ready',filename:'a.mp4',requested_at:new Date(2026,9,7,8).getTime()/1000},{job_id:'second',state:'ready',filename:'b.mp4',requested_at:new Date(2026,9,8,8).getTime()/1000}];f.run('renderHistory(rows)');
 f.get('results').querySelector('.history-select').checked=true;f.get('filter-date').value='2026-10-08';f.get('filter-date').onchange();
 assert.equal(f.get('results').children.length,1);assert.equal(f.get('results').children[0].dataset.jobId,'second');assert.equal(f.run('selectedJobs().length'),0);
});
test('History distinguishes the loaded client dataset from the existing server total',()=>{
 const f=fixture();f.context.rows=polishRows;f.run('historyTotal=250;renderHistory(rows)');
 assert.equal(f.get('result-count').textContent,'5 / 250 hasil');assert.match(f.get('result-count').title,/5 rekaman yang dimuat/);
 f.get('filter-source').value='youtube';f.get('filter-source').onchange();assert.equal(f.get('result-count').textContent,'2 / 5 hasil');
});
test('unclassified failed capture hides raw diagnostics from its primary message and cannot invent Retry',()=>{
 const f=fixture();f.run("renderHistory([{job_id:'old-failure',state:'failed',source:'youtube',detail:'HTTP 403: stack trace <script>bad</script>'}])");
 const row=f.get('results').children[0];assert.equal(row.querySelector('.status-badge').textContent,'Capture gagal');
 assert.match(row.querySelector('.capture-failure-message').textContent,/YouTube gagal memberikan media/);assert.doesNotMatch(row.querySelector('.capture-failure-message').textContent,/HTTP|stack trace/);
 assert.equal(row.querySelector('.retry-capture'),null);assert.equal(row.querySelector('.recording-details').querySelector('summary').textContent,'Detail teknis');
 assert.match(row.querySelector('.recording-detail-body').textContent,/HTTP 403/);assert.equal(row.querySelector('script'),null);
});
function keyEvent(key,target,mods={}){return {key,target,...mods,preventDefault(){this.defaultPrevented=true;}};}
function paletteFixture(motion){const f=fixture(motion);f.run("csrf='test';$('workspace').hidden=false;$('workspace').dataset.destination='workspace';api=async()=>({watches:[],active_count:0})");return f;}
test('command palette shortcuts ignore typing, repeats and modal dialogs while Cmd/Ctrl+K opens six commands',()=>{
 const f=paletteFixture();
 for(const target of [new Element('input'),new Element('textarea'),new Element('select'),{isContentEditable:true},{tagName:'SPAN',closest:()=>({})}]){
  const event=keyEvent('k',target,{ctrlKey:true});f.context.event=event;f.run('paletteShortcut(event)');assert.equal(event.defaultPrevented,undefined);assert.equal(f.get('command-palette').open,false);
 }
 const repeat=keyEvent('k',f.document.body,{metaKey:true,repeat:true});f.context.event=repeat;f.run('paletteShortcut(event)');assert.equal(f.get('command-palette').open,false);
 f.get('app-dialog').open=true;f.get('command-open').onclick();assert.equal(f.get('command-palette').open,false);f.get('app-dialog').open=false;
 const event=keyEvent('K',f.document.body,{metaKey:true});f.context.event=event;f.run('paletteShortcut(event)');assert.equal(event.defaultPrevented,true);
 assert.equal(f.get('command-palette').open,true);assert.equal(f.get('command-list').children.length,6);assert.equal(f.document.activeElement,f.get('command-search'));
 assert.equal(f.get('command-list').children[0].attributes['aria-selected'],'true');assert.equal(f.get('command-search').attributes['aria-activedescendant'],'command-workspace');
});
test('command palette arrows wrap, Enter runs the existing action, and Escape restores trigger focus',()=>{
 const f=paletteFixture();f.get('command-open').focus();f.get('command-open').onclick();
 f.get('command-search').onkeydown(keyEvent('ArrowUp'));assert.equal(f.get('command-search').attributes['aria-activedescendant'],'command-history');
 f.get('command-search').onkeydown(keyEvent('Enter'));assert.equal(f.get('command-palette').open,false);assert.equal(f.document.activeElement,f.get('filter-q'));
 assert.equal(f.get('workspace').dataset.destination,'workspace');
 f.get('command-open').focus();f.get('command-open').onclick();f.get('command-search').onkeydown(keyEvent('ArrowDown'));
 f.get('command-palette').listeners.cancel(keyEvent('Escape'));assert.equal(f.get('command-palette').open,false);assert.equal(f.document.activeElement,f.get('command-open'));
});
test('command palette searches only commands, handles no matches, opens the one Watch form and preserves a draft',()=>{
 const f=paletteFixture();f.get('watch-create').onclick();f.get('watch-url').value='@draft';f.get('command-open').onclick();
 f.get('command-search').value='impossible';f.get('command-search').oninput();assert.equal(f.get('command-empty').hidden,false);
 assert.equal(f.get('command-search').attributes['aria-activedescendant'],undefined);f.get('command-search').onkeydown(keyEvent('Enter'));assert.equal(f.get('command-palette').open,true);
 f.get('command-search').value='watch';f.get('command-search').oninput();assert.equal(f.get('command-list').children.length,1);
 f.get('command-search').onkeydown(keyEvent('Enter'));assert.equal(f.get('watch-url').value,'@draft');assert.equal(f.get('workspace').dataset.destination,'settings');assert.equal(f.document.activeElement,f.get('watch-url'));
});
test('palette Workspace, Settings and New capture commands invoke existing navigation and Source focus',()=>{
 const f=paletteFixture();
 const execute=query=>{f.get('command-open').onclick();f.get('command-search').value=query;f.get('command-search').oninput();f.get('command-search').onkeydown(keyEvent('Enter'));};
 execute('Go to Settings');assert.equal(f.get('workspace').dataset.destination,'settings');
 execute('Go to Workspace');assert.equal(f.get('workspace').dataset.destination,'workspace');
 f.run("mode='youtube';collapseSource(true)");f.get('youtube-url').value='draft';execute('New capture');
 assert.equal(f.get('capture-content').hidden,false);assert.equal(f.document.activeElement,f.get('youtube-url'));assert.equal(f.get('youtube-url').value,'draft');
 execute('Add Watch');assert.equal(f.get('watch-create-panel').hidden,false);assert.equal(f.document.activeElement,f.get('watch-url'));
});
test('palette close/reopen interruption rejects stale actions and logout forcibly closes an in-flight modal',()=>{
 const closes=[],f=paletteFixture({dialogOpen(){},dialogClose(dialog,panel,done){closes.push(done);},reset(){},source(panel,collapsed,change){change();}});
 f.get('command-open').onclick();f.get('command-search').onkeydown(keyEvent('ArrowUp'));f.get('command-search').onkeydown(keyEvent('Enter'));
 f.get('command-open').onclick();closes[0]();assert.equal(f.get('command-palette').open,true);assert.notEqual(f.document.activeElement,f.get('filter-q'));
 f.get('command-close').onclick();f.run('showLogin()');assert.equal(f.get('command-palette').open,false);closes[1]();assert.notEqual(f.document.activeElement,f.get('filter-q'));
});
test('Settings section shortcuts reuse navigation, preserve inputs, and only expose existing admin sections to admins',async()=>{
 const f=fixture();captureAPI(f);await f.run('enter({is_admin:false})');
 assert.equal(f.get('settings-admin').hidden,true);assert.equal(f.get('settings-nav-admin').hidden,true);assert.equal(f.get('watches-panel').hidden,false);
 f.get('source-name').value='Draft';f.get('settings-nav-sources').onclick();assert.equal(f.get('workspace').dataset.destination,'settings');assert.equal(f.document.activeElement,f.get('settings-sources'));assert.equal(f.get('source-name').value,'Draft');
 f.get('settings-nav-storage').onclick();assert.equal(f.document.activeElement,f.get('settings-storage'));f.get('settings-nav-admin').onclick();assert.equal(f.document.activeElement,f.get('settings-storage'));
 await f.run('enter({is_admin:true})');assert.equal(f.get('settings-admin').hidden,false);assert.equal(f.get('settings-nav-admin').hidden,false);f.get('settings-nav-admin').onclick();assert.equal(f.document.activeElement,f.get('settings-admin'));
});
