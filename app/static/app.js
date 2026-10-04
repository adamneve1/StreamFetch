const $ = id => document.getElementById(id);
const motion=window.StreamFetchMotion;
let csrf='', isAdmin=false, mode='oryx', sources=[], editing='', active=null, online=false, queued=0, pending=false, disk=null, archiveEnabled=false, historyRows=[], refreshId=0, estimateId=0, estimateTimer, lastObservedJob=null;
let focusedJobId=null, submittedJob=null, focusPending=false;
const expandedHistoryDetails=new Set();
let watchRefreshId=0,watchLoading=false;
function showSection(section){
 const admin=section==='admin';
 $('workspace').dataset.destination=admin?'settings':'workspace';
 $('control-room').hidden=admin;$('admin-view').hidden=!admin;
 $('page-title').textContent=admin?'Settings':'StreamFetch';
 for(const [id,current] of [['nav-control',!admin],['nav-admin',admin]]){
  if(current)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');
 }
 closeActionMenu();
 if(admin&&isAdmin)loadWatches();
}
function watchWindow(watch){
 const options={timeZone:'Asia/Jakarta'};
 const date=new Intl.DateTimeFormat('id-ID',{...options,day:'2-digit',month:'short',year:'numeric'});
 const clock=new Intl.DateTimeFormat('en-GB',{...options,hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
 if(!Number.isFinite(watch.start)||!Number.isFinite(watch.end))return '—';
 return date.format(new Date(watch.start*1000))+' · '+clock.format(new Date(watch.start*1000))+'–'+clock.format(new Date(watch.end*1000))+' WIB';
}
function renderWatches(data){
 $('watch-count').textContent=data.active_count+' aktif';$('watch-list').replaceChildren();
 $('watch-feedback').textContent=data.watches.length?'':'Belum ada watch.';
 for(const watch of data.watches){
  const row=document.createElement('li');row.className='watch-row';
  const copy=document.createElement('div');copy.className='watch-copy';
  const name=watch.name||watch.channel.replace(/^https?:\/\/(?:www\.)?youtube\.com\//,'');
  const title=document.createElement('strong');title.textContent=name;title.title=watch.channel;
  const window=document.createElement('small');window.textContent=watchWindow(watch)+' · '+(watch.mode==='every'?'Every live':'First live')+' · Auto-transcribe '+(watch.auto_transcribe?'on':'off');
  copy.append(title,window);
  if(watch.last_capture){const last=document.createElement('small');last.textContent='Terakhir: '+(watch.last_capture.title||watch.last_capture.video_id||watch.last_capture.job_id||'—')+(watch.last_capture.state?' · '+stateName(watch.last_capture.state):'');last.title=last.textContent;last.className='watch-last';copy.append(last);}
  const state=document.createElement('span');state.className='badge neutral';state.textContent=({waiting:'Waiting',active:'Active',expired:'Expired',finished:'Selesai',cancelled:'Dibatalkan'})[watch.status]||watch.status;
  row.append(copy,state);
  if(['waiting','active'].includes(watch.status)){const cancel=document.createElement('button');cancel.type='button';cancel.className='quiet watch-cancel';cancel.textContent='Batalkan';cancel.setAttribute('aria-label','Batalkan watch '+name);cancel.onclick=()=>cancelWatch(watch,cancel);row.append(cancel);}
  $('watch-list').append(row);
 }
}
async function loadWatches(force=false){
 if(!isAdmin||(watchLoading&&!force))return;
 const requestId=++watchRefreshId;watchLoading=true;
 try{const data=await api('admin/watches');if(requestId===watchRefreshId&&isAdmin)renderWatches(data);}
 catch(error){if(requestId===watchRefreshId&&isAdmin)$('watch-feedback').textContent=error.message;}
 finally{if(requestId===watchRefreshId)watchLoading=false;}
}
async function cancelWatch(watch,button){
 if(button.disabled||!isAdmin)return;button.disabled=true;
 try{await api('admin/watches/'+encodeURIComponent(watch.id)+'/cancel',{});await loadWatches(true);}
 catch(error){$('watch-feedback').textContent=error.message;}
 finally{button.disabled=false;}
}
function collapseSource(collapsed,options={}){
 const change=()=>{
  $('capture-panel').dataset.collapsed=String(collapsed);
  $('capture-content').hidden=collapsed;$('capture-another').hidden=!collapsed;
  $('capture-another').setAttribute('aria-expanded',String(!collapsed));
 };
 if(motion)motion.source($('capture-panel'),collapsed,change,options);else change();
}
function captureSubmitted(result,payload){
 focusedJobId=result.job_id;focusPending=true;
 submittedJob={job_id:result.job_id,source:payload.source,note:payload.note,
  quality:payload.quality,output_format:payload.format,compression:payload.compression,
  storage:payload.storage,requested_at:Date.now()/1000,state:'starting',
  detail:'Capture diterima. Menunggu worker…'};
 // Create/focus the new row immediately; motion never gates publication or polling.
 showSection('control');
 renderHistory([submittedJob,...historyRows.filter(row=>row.job_id!==focusedJobId)]);
 renderOperationalStatus();
 const job=$('results').children[0];
 collapseSource(true,{accepted:true,job});
 if(payload.source==='youtube')$('youtube-url').value='';
 if(payload.source==='tiktok')$('tiktok-url').value='';
 if(payload.source==='instagram')$('instagram-url').value='';
 clearTimeout(estimateTimer);estimateId++;
 $('size-estimate').textContent='Pilih sumber dulu untuk melihat perkiraan ukurannya.';
 for(const id of ['filter-q','filter-source','filter-state','filter-date'])$(id).value='';
 $('empty').hidden=true;
}
async function api(path, data){
 const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
 const contentType=response.headers.get('content-type')||'';
 let result;
 if(contentType.includes('application/json')){
  result=await response.json();
 }else{
  await response.text();
  throw Error(`Layanannya lagi bermasalah (${response.status}). Coba muat ulang halaman, ya.`);
 }
 if(response.status===401 && path!=='login'){showLogin();throw Error('Sesi kamu sudah habis. Masuk lagi, ya.');}
 if(!response.ok)throw Error(result.error || 'Belum berhasil. Coba lagi, ya.');
 if(path==='status'){archiveEnabled=!!result.archive_enabled;$('storage-archive').disabled=!archiveEnabled;if(!archiveEnabled&&$('storage-target').value==='archive')$('storage-target').value='local';updateStorageHint();}
 return result;
}
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function showLogin(){const returning=$('login').hidden;if(returning)motion?.reset();$('workspace').hidden=true;$('login').hidden=false;csrf='';isAdmin=false;active=null;lastObservedJob=null;focusedJobId=null;submittedJob=null;focusPending=false;collapseSource(false);showSection('control');expandedHistoryDetails.clear();if(returning)motion?.loginReveal?.($('login'));$('password').focus({preventScroll:true});}
function controls(){const canMark=!!active&&active.state==='recording'&&active.is_live!==false;$('mark').disabled=pending||!canMark;$('record').disabled=pending||!online||!disk?.can_record||!!active||queued>0||(mode==='oryx'&&!$('source').value);$('stop').disabled=pending||!active||['finalizing','stopping','ready','failed'].includes(active.state);renderOperationalStatus();}
function bytes(n=0){return n>=1e9?(n/1e9).toFixed(2)+' GB':(n/1e6).toFixed(1)+' MB';}
function stateName(state){return ({starting:'Menghubungkan',recording:'Berjalan',waiting:'Menunggu YouTube',stopping:'Menghentikan',finalizing:'Memproses',ready:'Siap',failed:'Gagal',interrupted:'Terputus'})[state]||state||'Siap';}
function isFiniteDownload(job){return ['youtube','tiktok','instagram'].includes(job?.source)&&job.is_live===false;}
function jobStateName(job){return isFiniteDownload(job)&&job.state==='recording'?'Mengunduh':stateName(job?.state);}
function sourceDisplayName(job){return job.source==='tiktok'&&(!job.source_name||job.source_name==='TikTok Live')?'TikTok':job.source_name||({youtube:'YouTube',oryx:'Oryx',tiktok:'TikTok',instagram:'Instagram'})[job.source]||job.source||'';}
function statusTone(state){return ({starting:'working',recording:'working',waiting:'waiting',stopping:'waiting',finalizing:'working',ready:'success',failed:'error',interrupted:'error'})[state]||'idle';}
function presetName(row){if(row.output_format==='mp3'||row.filename?.toLowerCase().endsWith('.mp3'))return 'Audio MP3';return ({original:'Original',balanced:'Seimbang',compact:'Hemat'})[row.compression]||'Preset tidak tercatat';}
function duration(n=0){return [Math.floor(n/3600),Math.floor(n/60)%60,Math.floor(n)%60].map(x=>String(x).padStart(2,'0')).join(':');}
function transcriptStateName(state){return ({queued:'Queued',transcribing:'Transcribing',completed:'Completed',failed:'Failed'})[state]||state;}
function languageName(code){if(!code)return '—';try{const name=new Intl.DisplayNames(['id'],{type:'language'}).of(code);return name.charAt(0).toUpperCase()+name.slice(1);}catch{return code;}}
function processingTime(seconds){if(seconds===undefined||seconds===null)return '—';const value=Math.round(seconds);return value>=60?Math.floor(value/60)+'m '+value%60+'s':value+'s';}
async function generateTranscript(row){try{await api('recordings/'+encodeURIComponent(row.job_id)+'/transcript',{});lastObservedJob={...row,transcript:{...row.transcript,status:'queued'}};renderOperationalStatus();notice('Transkrip masuk antrean. Statusnya akan diperbarui otomatis.');await refresh();}catch(error){notice(error.message);}}
function transcriptLink(row,kind,label){const link=document.createElement('a');link.href='/api/recordings/'+encodeURIComponent(row.job_id)+'/transcript/'+kind;link.textContent=label;if(kind==='view'){link.target='_blank';link.rel='noopener';}return link;}
function transcriptETA(transcript){
 if(!Number.isFinite(transcript.eta_seconds))return 'Estimating time…';
 const elapsed=transcript.updated_at?Math.max(0,Date.now()/1000-transcript.updated_at):0;
 const seconds=Math.max(0,transcript.eta_seconds-elapsed);
 return seconds<60?'Less than a minute remaining':'About '+Math.ceil(seconds/60)+' min remaining';
}
function renderTranscript(row,cell){
 if(row.state!=='ready'||(!row.transcript&&!row.filename?.toLowerCase().endsWith('.mp4')))return;
 const transcript=row.transcript||{},state=transcript.status,box=document.createElement('div');box.className='transcript-box';
 if(state==='completed'){
  const link=transcriptLink(row,'view','View Transcript');link.className='transcript-cta';box.append(link);
 }else{
  const button=document.createElement('button');button.type='button';button.className='transcript-cta transcript-generate';
  button.textContent=({queued:'Waiting',transcribing:'Generating',failed:'Retry Transcript'})[state]||'Generate Transcript';
  button.disabled=['queued','transcribing'].includes(state);button.onclick=()=>generateTranscript(row);box.append(button);
  if(state==='queued'||state==='transcribing'){
   const progress=document.createElement('progress');progress.max=100;progress.className='transcript-progress';progress.setAttribute('aria-label','Transcript progress');
   if(state==='transcribing'&&Number.isFinite(transcript.progress_percent))progress.value=Math.min(100,Math.max(0,transcript.progress_percent));
   const hint=document.createElement('small');hint.setAttribute('role','status');
   hint.textContent=state==='queued'?'Waiting in queue':(Number.isFinite(transcript.progress_percent)?Math.round(transcript.progress_percent)+'% · ':'')+transcriptETA(transcript);
   box.append(progress,hint);
  }
  if(state==='failed'&&transcript.error){const error=document.createElement('small');error.className='transcript-status failed';error.textContent=transcript.error;box.append(error);}
 }
 cell.append(box);
}
function updateStorageHint(){$('storage-hint').textContent=$('storage-target').value==='archive'?'File disimpan di lokal, lalu disalin ke arsip.':archiveEnabled?'File disimpan di folder downloads.':'File disimpan di folder downloads. Arsipnya belum aktif.';}
function renderDownloadProgress(job){
 const download=isFiniteDownload(job);
 const visible=download&&['starting','recording','waiting','finalizing'].includes(job.state);
 $('download-progress').hidden=!visible;
 if(!visible){motion?.progress($('progress-bar'),null);return;}
 const label=job.state==='finalizing'?'Memproses':'Progress capture';
 const percent=job.state==='recording'&&typeof job.progress_percent==='number'&&Number.isFinite(job.progress_percent)?Math.min(100,Math.max(0,job.progress_percent)):null;
 if(motion)motion.progress($('progress-bar'),percent);
 else if(percent===null)$('progress-bar').removeAttribute('value');else $('progress-bar').value=percent;
 $('progress-value').textContent=percent===null?'':Math.round(percent)+'%';
 $('progress-bar').setAttribute('aria-label',label);
}
function islandTitle(job){
 const saved=historyRows.find(row=>row.job_id===job?.job_id);
 return [job?.note,job?.source_metadata?.title,job?.title,saved?.note,saved?.source_metadata?.title,saved?.title,job?.source_name]
  .find(value=>typeof value==='string'&&value.trim())?.trim()||'Capture baru';
}
function islandTime(job,transcript){
 const estimate=transcript?.eta_seconds??job?.eta_seconds;
 if(Number.isFinite(estimate)&&estimate>=0){
  const age=transcript?.updated_at?Math.max(0,Date.now()/1000-transcript.updated_at):0;
  const seconds=Math.max(0,estimate-age);
  return seconds<60?'~'+Math.max(5,Math.ceil(seconds/5)*5)+'s':'~'+Math.ceil(seconds/60)+'m';
 }
 const elapsed=transcript?.status==='queued'?null:transcript?.status==='transcribing'?
  (transcript.started_at?Math.max(0,Date.now()/1000-transcript.started_at):null):
  job?.state==='recording'&&job.started_at?Math.max(0,Date.now()/1000-job.started_at):job?.elapsed;
 if(!Number.isFinite(elapsed)||elapsed<=0)return '';
 return elapsed<60?Math.floor(elapsed)+'s':duration(elapsed).replace(/^00:/,'');
}
function renderOperationalStatus(){
 const job=active||submittedJob||lastObservedJob,connection=$('connection');
 const transcript=job?.state==='ready'?job.transcript:null;
 const phase=transcript?.status==='transcribing'?'transcribing':transcript?.status==='queued'?'queued':transcript?.status==='failed'?'failed':
  ({starting:'starting',waiting:'queued',recording:'downloading',stopping:'processing',finalizing:'processing',ready:'completed',failed:'failed',interrupted:'failed'})[job?.state]||(queued?'queued':'hidden');
 const canStop=!!active&&!['finalizing','stopping','ready','failed'].includes(active.state);
 const canMark=!!active&&active.state==='recording'&&active.is_live!==false;
 const change=()=>{
 $('status-monitor').hidden=phase==='hidden';
 $('status-monitor').dataset.phase=phase;
 $('stop').hidden=!canStop;$('marker-controls').hidden=!canMark;
 connection.textContent=online?'Online':'Offline';connection.classList.toggle('online',online);connection.classList.toggle('offline',!online);
 connection.title=online?'Sistem siap':'Sistem offline';connection.setAttribute('aria-label',connection.title);
 $('status-monitor').dataset.tone=statusTone(job?.state);
 $('state').textContent=({starting:'Menyiapkan',queued:'Menunggu',downloading:isFiniteDownload(job)?'Mengunduh':'Merekam',processing:'Memproses',transcribing:'Transkripsi',completed:'Selesai',failed:'Gagal'})[phase]||'';
 $('state').dataset.tone=statusTone(job?.state);
 $('status-job-title').textContent=job?islandTitle(job):queued+' capture';
 $('status-job-title').title=$('status-job-title').textContent;
 $('status-detail').hidden=phase!=='failed';
 $('status-detail').textContent=phase==='failed'?(transcript?.status==='failed'?'Transkripsi gagal.':'Capture gagal.') : '';
 $('island-details').hidden=phase!=='failed'||!(job?.detail||transcript?.error||historyRows.some(row=>row.job_id===job?.job_id));
 $('duration').textContent=['completed','failed'].includes(phase)?'':islandTime(job,transcript);
 $('progress-value').textContent='';
 if(!transcript||!['queued','transcribing'].includes(transcript.status))renderDownloadProgress(job);
 if(transcript&&['queued','transcribing'].includes(transcript.status)){
  $('state').dataset.tone='working';$('status-monitor').dataset.tone='working';
  $('download-progress').hidden=true;
  if(transcript.status==='transcribing')$('download-progress').hidden=false;
  $('progress-bar').setAttribute('aria-label','Transcript progress');
  const percent=transcript.status==='transcribing'&&Number.isFinite(transcript.progress_percent)?Math.min(100,Math.max(0,transcript.progress_percent)):null;
  if(motion)motion.progress($('progress-bar'),percent);else if(percent!==null)$('progress-bar').value=percent;else $('progress-bar').removeAttribute('value');
  $('progress-value').textContent=percent===null?'':Math.round(percent)+'%';
 }else if(transcript?.status==='failed'){
  $('state').dataset.tone='error';$('status-monitor').dataset.tone='error';
 }
 };
 // Percent/ETA/detail changes aren't structural and never start a new Flip.
 const key=[job?.job_id||'queue',job?.state,transcript?.status,canStop].join(':');
 if(motion)motion.island($('status-monitor'),key,phase,change);else change();
}
async function showIslandDetails(){
 const job=active||submittedJob||lastObservedJob;if(!job)return;
 if(!historyRows.some(row=>row.job_id===job.job_id)){
  for(const id of ['filter-q','filter-source','filter-state','filter-date'])$(id).value='';
  await refresh();
 }
 const row=[...$('results').children].find(row=>row.dataset.jobId===job.job_id);
 const details=row?.querySelector('.recording-details');
 if(details){expandedHistoryDetails.add(job.job_id);details.open=true;row.tabIndex=-1;row.focus({preventScroll:true});row.scrollIntoView({behavior:motion?.reduced()?'auto':'smooth',block:'center'});}
 else notice(job.transcript?.error||job.detail||'Detail belum tersedia.');
}
function editSource(id){editing=id;const s=sources.find(x=>x.id===id);$('source-name').value=s?.name||'';$('source-url').value=s?.url||'';$('source-feedback').textContent='';}
async function loadSources(){const previous=$('source').value; sources=(await api('sources')).sources;$('source').replaceChildren();for(const s of sources){const o=document.createElement('option');o.value=s.id;o.textContent=s.name;$('source').append(o);}if(!sources.length){const o=document.createElement('option');o.value='';o.textContent='Belum ada sumber — tambahkan dulu';$('source').append(o);}if(sources.some(x=>x.id===previous))$('source').value=previous;editSource($('source').value);scheduleEstimate();controls();}
function estimateText(data){const detail=data.format==='mp3'?' · MP3':data.height?' · '+data.height+'p':'';const compressed=data.compression&&data.compression!=='original';if(data.is_live)return data.bytes_per_hour?'Perkiraan sumber '+bytes(data.bytes_per_hour)+' per jam'+detail+(compressed?' · hasil preset bisa berbeda':''):'Ukuran live bergantung pada durasi dan bitrate sumber'+detail;return data.estimated_bytes?(compressed?'Perkiraan ukuran sumber ':'Perkiraan ukuran ')+bytes(data.estimated_bytes)+detail+(compressed?' · hasil preset bisa berbeda':''):'Ukuran belum tersedia dari sumber'+detail;}
function sourceInput(){return mode==='oryx'?$('source'):$(mode+'-url');}
async function estimateSize(){const requestId=++estimateId;const url=mode==='oryx'?'':sourceInput().value.trim();if((mode==='oryx'&&!$('source').value)||(mode!=='oryx'&&!url)){$('size-estimate').textContent='Pilih sumber dulu untuk melihat perkiraan ukurannya.';return;}$('size-estimate').textContent='Lagi menghitung perkiraan ukuran…';try{const data=await api('estimate',{source:mode,source_id:$('source').value,url,quality:$('quality').value,format:$('media-format').value,compression:$('compression').value});if(requestId===estimateId)$('size-estimate').textContent=estimateText(data);}catch(e){if(requestId===estimateId)$('size-estimate').textContent=e.message;}}
function scheduleEstimate(){clearTimeout(estimateTimer);estimateTimer=setTimeout(estimateSize,500);}
function updateCompressionHint(){
 const audio=mode==='youtube'&&$('media-format').value==='mp3';
 const hints={original:'Proses paling cepat, mempertahankan kualitas sumber jika kompatibel.',balanced:'Kualitas bagus dengan ukuran lebih hemat.',compact:'Ukuran lebih ringkas, proses lebih lama. Perangkat lama mungkin tidak mendukung.'};
 const specs={original:'H.264 + AAC. Kompresi ulang hanya jika sumber belum kompatibel.',balanced:'H.264 · CRF 23 · audio AAC 128 kbps.',compact:'H.265 / HEVC · CRF 27 · audio AAC 128 kbps.'};
 $('compression-hint').textContent=audio?'Preset video tidak berlaku untuk audio MP3.':hints[$('compression').value];
 $('compression-spec').textContent=specs[$('compression').value];$('compression-details').hidden=audio;
}
function updateFormatControls(){const audio=mode==='youtube'&&$('media-format').value==='mp3';$('format-field').hidden=mode!=='youtube';$('media-options').classList.toggle('has-format',mode==='youtube');$('media-format').disabled=mode!=='youtube';$('quality').disabled=mode==='oryx'||audio;$('compression').disabled=audio;if(mode==='oryx'||audio)$('quality').value='best';if(audio)$('compression').value='original';updateCompressionHint();$('note').placeholder=mode==='youtube'?'Kosongkan untuk memakai judul asli':'Contoh: Batam menyapa';$('filename-hint').textContent='Nama file: DDMMYYNN - '+(mode==='youtube'?'Judul video':'Judul')+'.'+(audio?'mp3':'mp4');$('record').textContent=mode==='youtube'?'Download':'Mulai';}
function setMode(value){mode=value;for(const source of ['youtube','tiktok','instagram','oryx']){$('tab-'+source).setAttribute('aria-pressed',String(source===mode));$('tab-'+source).classList.toggle('selected',source===mode);$(source+'-fields').hidden=source!==mode;}if(mode!=='youtube')$('media-format').value='mp4';updateFormatControls();scheduleEstimate();controls();}
function historyDetails(row){
 const details=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('div');
 details.className='recording-details';details.dataset.jobId=row.job_id;details.open=expandedHistoryDetails.has(row.job_id);
 summary.textContent=['failed','interrupted'].includes(row.state)?'Lihat penyebab':'Lihat detail';
 body.className='recording-detail-body';
 const description=document.createElement('p');description.textContent=row.detail||row.note||'Belum ada detail tambahan.';
 const metadata=document.createElement('p');metadata.className='detail-metadata';
 metadata.textContent='Preset: '+presetName(row)+(row.download_attempt?' · Percobaan '+row.download_attempt+(row.download_attempts?'/'+row.download_attempts:''):'');
 body.append(description,metadata);details.append(summary,body);
 details.addEventListener('toggle',()=>{if(!details.isConnected)return;if(details.open)expandedHistoryDetails.add(row.job_id);else expandedHistoryDetails.delete(row.job_id);});
 return details;
}
function renderHistory(rows){
 historyRows=rows;
 const selection=new Set(selectedJobs());
 for(const details of $('results').querySelectorAll('.recording-details')){
  if(details.open)expandedHistoryDetails.add(details.dataset.jobId);else expandedHistoryDetails.delete(details.dataset.jobId);
 }
 $('results').replaceChildren();
 for(const row of rows){
  const tr=document.createElement('tr'),selectCell=document.createElement('td'),checkbox=document.createElement('input');
  tr.dataset.jobId=row.job_id;
  if(row.job_id===focusedJobId){tr.className='focused-job';tr.tabIndex=-1;tr.setAttribute('aria-label','Capture terbaru');}
  selectCell.className='select-cell';checkbox.type='checkbox';checkbox.className='history-select';checkbox.value=row.job_id;
  checkbox.checked=selection.has(row.job_id);checkbox.setAttribute('aria-label','Pilih '+(row.filename||'riwayat'));checkbox.onchange=updateDeleteControls;
  selectCell.append(checkbox);tr.append(selectCell);
  const file=document.createElement('td'),strong=document.createElement('strong'),small=document.createElement('small');
  strong.textContent=row.filename||'Rekaman '+row.job_id.slice(0,8);strong.title=strong.textContent;
  small.textContent=row.note||(['failed','interrupted'].includes(row.state)?'Proses belum selesai. Buka detail untuk melihat penyebab.':row.detail||'Tanpa catatan');
  small.title=row.note||row.detail||'';
  const preset=document.createElement('span');preset.className='preset-badge';preset.textContent=presetName(row);
  const heading=document.createElement('div'),badge=document.createElement('span');heading.className='recording-heading';
  badge.className='status-badge';badge.dataset.tone=statusTone(row.state);badge.textContent=jobStateName(row);
  heading.append(badge,strong);file.append(heading,small,preset,historyDetails(row));
  if(row.markers?.length){
   const details=document.createElement('details'),summary=document.createElement('summary'),list=document.createElement('ul');summary.textContent=row.markers.length+' tanda momen';
   for(const marker of row.markers){const li=document.createElement('li');li.textContent=duration(marker.seconds)+' — '+marker.note;list.append(li);}
   details.append(summary,list);file.append(details);
  }
  renderTranscript(row,file);tr.append(file);
  for(const value of [sourceDisplayName(row),row.requested_at?new Date(row.requested_at*1000).toLocaleString('id-ID'):'—',bytes(row.size)]){
   const cell=document.createElement('td');cell.textContent=value;tr.append(cell);
  }
  const action=document.createElement('td');action.className='row-actions';
  if(row.filename&&row.state==='ready'){
   const download=document.createElement('a');download.href='/api/files/'+encodeURIComponent(row.filename);download.className='download-direct';
   download.setAttribute('aria-label','Unduh '+row.filename);download.title='Unduh';download.append(downloadIcon());action.append(download);
  }
  if((row.filename&&row.state==='ready')||isAdmin){
   const trigger=document.createElement('button');trigger.type='button';trigger.className='action-trigger';trigger.textContent='⋯';
   trigger.setAttribute('aria-label','Aksi lainnya untuk '+(row.filename||'riwayat'));trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');
   trigger.title='Aksi lainnya';trigger.onclick=()=>toggleActionMenu(trigger,row);action.append(trigger);
  }
  tr.append(action);$('results').append(tr);
  if(row.job_id===focusedJobId&&focusPending){tr.focus({preventScroll:true});tr.scrollIntoView({behavior:motion?.reduced()?'auto':'smooth',block:'center'});focusPending=false;}
 }
 updateDeleteControls();
}
async function refresh(){
 if(!csrf)return;
 const revision=++refreshId;
 try{
  const data=await api('status');if(revision!==refreshId)return;
  disk=data.disk;
  $('disk-free').textContent=disk?.available?bytes(disk.free)+' kosong dari '+bytes(disk.total):'Penyimpanan belum bisa dicek';
  $('disk-limit').textContent='Batas minimum: '+bytes(disk?.minimum)+' · '+(disk?.can_record?'Siap dipakai':'Ruangnya perlu dikosongkan dulu');
  $('disk-meter').value=disk?.total?100*(1-disk.free/disk.total):0;
  $('disk-free').parentElement.classList.toggle('low',!disk?.can_record);
  active=data.active;online=data.online;queued=data.queued;if(active)lastObservedJob=active;controls();
  $('live-markers').replaceChildren();
  for(const marker of active?.markers||[]){const li=document.createElement('li');li.textContent=duration(marker.seconds)+' — '+marker.note;$('live-markers').append(li);}
  const params=new URLSearchParams({q:$('filter-q').value,source:$('filter-source').value,state:$('filter-state').value,date:$('filter-date').value});
  const history=await api('recordings?'+params);if(revision!==refreshId)return;
  const rows=history.recordings;
  const filtered=[...params.values()].some(Boolean);
  // Admission can precede catalogue publication. Keep the accepted capture
  // visible until the worker supplies its durable row, without retaining URLs.
  if(submittedJob){
   if(rows.some(row=>row.job_id===submittedJob.job_id))submittedJob=null;
   else if(!filtered)rows.unshift(active?.job_id===submittedJob.job_id?{...submittedJob,...active}:submittedJob);
  }
  if(!active&&lastObservedJob){
   let finished=rows.find(row=>row.job_id===lastObservedJob.job_id);
   if(!finished&&filtered&&(!['ready','failed','interrupted'].includes(lastObservedJob.state)||['queued','transcribing'].includes(lastObservedJob.transcript?.status))){
    // Monitor completion must not depend on the user's history filters.
    const all=await api('recordings?');if(revision!==refreshId)return;
    finished=all.recordings.find(row=>row.job_id===lastObservedJob.job_id);
   }
   if(finished&&['ready','failed','interrupted'].includes(finished.state))lastObservedJob=finished;
   else if(!['ready','failed','interrupted'].includes(lastObservedJob.state))lastObservedJob=null;
   renderOperationalStatus();
  }
  $('result-count').textContent=rows.length?Math.max(history.total,rows.length)+' hasil':'Belum ada hasil';$('empty').hidden=!!rows.length;
  $('empty').querySelector('strong').textContent=filtered?'Belum ketemu hasil yang cocok.':'Belum ada hasil.';
  $('empty').querySelector('p').textContent=filtered?'Coba ubah filternya atau mulai rekaman baru.':'Hasil rekaman dan download kamu akan muncul di sini.';
  closeActionMenu();renderHistory(rows);renderOperationalStatus();
 }catch(error){online=false;controls();notice(error.message);}
}
async function enter(auth){isAdmin=!!auth?.is_admin;motion?.loginSuccess?.($('workspace'));$('login').hidden=true;$('workspace').hidden=false;$('role-badge').textContent=isAdmin?'Admin':'Pengguna';$('admin-panel').hidden=!isAdmin;$('watches-panel').hidden=!isAdmin;$('delete-selected').hidden=!isAdmin;updateFormatControls();await loadSources();await refresh();}
let loginBusy=false;
function loginLoading(busy){loginBusy=busy;$('login-form').setAttribute('aria-busy',String(busy));$('login-submit').disabled=busy;$('login-submit').textContent=busy?'Masuk…':'Masuk';}
$('login-form').onsubmit=async e=>{e.preventDefault();if(loginBusy)return;loginLoading(true);$('login-error').textContent='';$('password').removeAttribute('aria-invalid');try{const auth=await api('login',{password:$('password').value});csrf=auth.csrf;$('password').value='';await enter(auth);}catch(err){$('login-error').textContent=err.message;$('password').setAttribute('aria-invalid','true');motion?.loginError?.($('login-error'));$('password').focus({preventScroll:true});}finally{loginLoading(false);}};
$('password').oninput=()=>{motion?.loginInteract?.();$('password').removeAttribute('aria-invalid');$('login-error').textContent='';};
$('password').addEventListener('pointerdown',()=>motion?.loginInteract?.());
$('password').addEventListener('keydown',()=>motion?.loginInteract?.());
$('island-details').onclick=showIslandDetails;
$('watch-refresh').onclick=()=>loadWatches(true);
$('logout').onclick=async()=>{try{await api('logout',{});showLogin();}catch(e){notice(e.message);}};
$('nav-control').onclick=()=>showSection('control');$('nav-admin').onclick=()=>showSection('admin');
$('capture-another').onclick=()=>{collapseSource(false);sourceInput().focus();};
$('tab-instagram').onclick=()=>setMode('instagram');$('tab-tiktok').onclick=()=>setMode('tiktok');$('tab-oryx').onclick=()=>setMode('oryx');$('tab-youtube').onclick=()=>setMode('youtube');$('source').onchange=()=>{editSource($('source').value);scheduleEstimate();controls();};$('new-source').onclick=()=>{$('source-manager').open=true;editSource('');$('source-name').focus();};
$('source-form').onsubmit=async e=>{e.preventDefault();try{const saved=await api('sources',{id:editing,name:$('source-name').value,url:$('source-url').value});await loadSources();$('source').value=saved.id;editSource(saved.id);$('source-feedback').textContent='Sip, sumbernya sudah tersimpan dan siap dipakai.';controls();}catch(err){$('source-feedback').textContent=err.message;}};
$('check').onclick=async()=>{$('check').disabled=true;$('source-feedback').textContent='Lagi mencoba terhubung…';try{const result=await api('check',{url:$('source-url').value});$('source-feedback').textContent='Berhasil tersambung · '+result.codecs.join(' / ');}catch(e){$('source-feedback').textContent=e.message;}finally{$('check').disabled=false;}};
$('record').onclick=async()=>{if(pending)return;pending=true;$('record').textContent='Mengirim…';$('capture-panel').setAttribute('aria-busy','true');controls();notice('');const payload={source:mode,source_id:$('source').value,url:mode==='oryx'?'':sourceInput().value,note:$('note').value,storage:$('storage-target').value,quality:$('quality').value,format:$('media-format').value,compression:$('compression').value};try{const result=await api('record',payload);captureSubmitted(result,payload);await refresh();}catch(e){collapseSource(false);notice(e.message);}finally{pending=false;$('record').textContent=mode==='youtube'?'Download':'Mulai';$('capture-panel').setAttribute('aria-busy','false');controls();}};
$('storage-target').onchange=updateStorageHint;
$('quality').onchange=scheduleEstimate;$('compression').onchange=()=>{updateCompressionHint();scheduleEstimate();};$('media-format').onchange=()=>{updateFormatControls();scheduleEstimate();};$('youtube-url').oninput=scheduleEstimate;$('tiktok-url').oninput=scheduleEstimate;$('instagram-url').oninput=scheduleEstimate;
$('stop').onclick=async()=>{pending=true;controls();try{await api('stop',{job_id:active.job_id});notice('Lagi dihentikan. File-nya akan muncul sebentar lagi.');await refresh();}catch(e){notice(e.message);}finally{pending=false;controls();}};
setInterval(()=>{const job=active||submittedJob||lastObservedJob;if(!['completed','failed'].includes($('status-monitor').dataset.phase))$('duration').textContent=islandTime(job,job?.transcript);},1000);
motion?.loginReveal?.($('login'));
(async()=>{try{const auth=await api('session');csrf=auth.csrf;await enter(auth);}catch{showLogin();}})();
async function poll(){await refresh();if(isAdmin&&!$('admin-view').hidden)loadWatches();setTimeout(poll,2500);}setTimeout(poll,2500);

$('mark').onclick=async()=>{pending=true;controls();try{await api('markers',{job_id:active.job_id,note:$('marker-note').value});$('marker-note').value='';await refresh();}catch(e){notice(e.message);}finally{pending=false;controls();}};
let searchTimer;
$('filter-q').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(refresh,250);};
for(const id of ['filter-source','filter-state','filter-date'])$(id).onchange=refresh;
$('filter-reset').onclick=()=>{for(const id of ['filter-q','filter-source','filter-state','filter-date'])$(id).value='';refresh();};

function selectedJobs(){return [...$('results').querySelectorAll('.history-select:checked')].map(input=>input.value);}
function selectedRows(){const ids=new Set(selectedJobs());return historyRows.filter(row=>ids.has(row.job_id));}
function updateDeleteControls(){$('delete-selected').hidden=!isAdmin;$('delete-selected').disabled=!isAdmin||!selectedJobs().length;$('download-selected').disabled=!selectedRows().some(row=>row.filename&&row.state==='ready');const boxes=[...$('results').querySelectorAll('.history-select')];$('select-all').checked=!!boxes.length&&boxes.every(box=>box.checked);$('select-all').indeterminate=boxes.some(box=>box.checked)&&!$('select-all').checked;}
function downloadSelected(){const rows=selectedRows().filter(row=>row.filename&&row.state==='ready');for(const row of rows){const link=document.createElement('a');link.href='/api/files/'+encodeURIComponent(row.filename);link.download=row.filename;document.body.append(link);link.click();link.remove();}notice(rows.length+' file mulai diunduh. Kalau browser bertanya, izinkan download beberapa file, ya.');}
async function deleteJobs(jobIds){if(!jobIds.length||!confirm('Yakin mau menghapus yang dipilih? File lokalnya juga ikut dihapus.'))return;pending=true;$('delete-selected').disabled=true;try{const result=await api('recordings/delete',{job_ids:jobIds});const failed=result.skipped?.length||0;notice(result.deleted.length+' item berhasil dihapus'+(failed?' · '+failed+' item belum bisa dihapus':'')+'.');await refresh();}catch(e){notice(e.message);}finally{pending=false;updateDeleteControls();}}
async function renameJob(row){const value=prompt('Mau diberi nama apa?',row.filename||'');if(value===null||value.trim()===''||value.trim()===row.filename)return;try{await api('recordings/'+encodeURIComponent(row.job_id)+'/rename',{filename:value.trim()});notice(row.archive_status==='archived'?'Nama file lokal sudah diganti. Nama di arsip tetap sama.':'Sip, nama file sudah diganti.');await refresh();}catch(e){notice(e.message);}}
let openActionMenu=null;
function closeActionMenu(focus=false){if(!openActionMenu)return;const {panel,trigger}=openActionMenu;panel.remove();trigger.setAttribute('aria-expanded','false');openActionMenu=null;if(focus)trigger.focus();}
function menuButton(label,handler,danger=false){const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('role','menuitem');if(danger)button.className='danger';button.onclick=()=>{closeActionMenu();handler();};return button;}
function toggleActionMenu(trigger,row){if(openActionMenu?.trigger===trigger){closeActionMenu();return;}closeActionMenu();const panel=document.createElement('div');panel.className='action-menu-panel';panel.setAttribute('role','menu');panel.setAttribute('aria-label','Aksi '+(row.filename||'riwayat'));if(row.filename&&row.state==='ready')panel.append(menuButton('Ubah nama',()=>renameJob(row)));if(isAdmin)panel.append(menuButton('Hapus',()=>deleteJobs([row.job_id]),true));document.body.append(panel);trigger.setAttribute('aria-expanded','true');openActionMenu={panel,trigger};const rect=trigger.getBoundingClientRect(),gap=6,margin=8,width=panel.offsetWidth,height=panel.offsetHeight;const left=Math.max(margin,Math.min(rect.right-width,window.innerWidth-width-margin));let top=rect.bottom+gap;if(top+height>window.innerHeight-margin)top=rect.top-height-gap;panel.style.left=left+'px';panel.style.top=Math.max(margin,top)+'px';panel.querySelector('button')?.focus();}
function downloadIcon(){const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.8');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');for(const d of ['M12 3v12','M7 10l5 5 5-5','M5 21h14']){const path=document.createElementNS(ns,'path');path.setAttribute('d',d);svg.append(path);}return svg;}
document.addEventListener('pointerdown',event=>{if(openActionMenu&&!openActionMenu.panel.contains(event.target)&&event.target!==openActionMenu.trigger)closeActionMenu();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeActionMenu(true);});
window.addEventListener('resize',()=>closeActionMenu());window.addEventListener('scroll',()=>closeActionMenu(),true);
$('select-all').onchange=()=>{for(const box of $('results').querySelectorAll('.history-select'))box.checked=$('select-all').checked;updateDeleteControls();};
$('download-selected').onclick=downloadSelected;
$('delete-selected').onclick=()=>deleteJobs(selectedJobs());
$('password-form').onsubmit=async event=>{event.preventDefault();const feedback=$('password-feedback');const next=$('new-password').value;const confirmation=$('confirm-password').value;if(next!==confirmation){feedback.textContent='Password yang kamu ulangi belum sama.';return;}try{await api('admin/password',{target:$('password-target').value,current_password:$('current-admin-password').value,new_password:next});event.target.reset();feedback.textContent='Sip, password-nya sudah diganti.';}catch(error){feedback.textContent=error.message;}};
