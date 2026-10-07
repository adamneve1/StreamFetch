const $ = id => document.getElementById(id);
const motion=window.StreamFetchMotion;
let csrf='', isAdmin=false, mode='oryx', sources=[], editing='', active=null, online=false, queued=0, pending=false, disk=null, archiveEnabled=false, historyRows=[], refreshId=0, estimateId=0, estimateTimer, lastObservedJob=null;
let focusedJobId=null, submittedJob=null, focusPending=false;
let historyTotal=0;
const routeView=()=>window.location.pathname==='/clipper'?'clipper':'control';
let clipMetadata=null,clipMetadataRevision=0,clipMetadataTimer=null,clipDurationLock=null,clipSubmitting=false;
const expandedHistoryDetails=new Set();
const retryingJobs=new Set();
let watchRefreshId=0,watchLoading=false,watchCreating=false,watchFormClosing=false;
let dialogRequest=null;
const toastQueue=[];
let activeToast=null;
function toast(message,tone='success'){
 message=String(message||'').trim();if(!message)return;
 if(activeToast?.message===message||toastQueue.some(item=>item.message===message))return;
 toastQueue.push({message,tone:['success','info','error'].includes(tone)?tone:'info'});showNextToast();
}
function showNextToast(){
 if(activeToast||!toastQueue.length)return;
 const entry=activeToast=toastQueue.shift(),node=document.createElement('div'),text=document.createElement('p'),close=document.createElement('button');
 entry.node=node;node.className='toast-card';node.dataset.tone=entry.tone;
 text.textContent=entry.message;close.type='button';close.className='quiet toast-dismiss';close.textContent='×';close.setAttribute('aria-label','Tutup notifikasi');
 node.append(text,close);$('toast-region').append(node);
 const live=$(entry.tone==='error'?'toast-error-live':'toast-status');live.textContent=entry.message;
 const resume=()=>{
  clearTimeout(entry.timer);
  if(activeToast===entry&&!entry.closing&&!entry.hovered&&!entry.focused)entry.timer=setTimeout(()=>dismissToast(entry),entry.tone==='error'?10000:4500);
 };
 node.addEventListener('mouseenter',()=>{entry.hovered=true;clearTimeout(entry.timer);});
 node.addEventListener('mouseleave',()=>{entry.hovered=false;resume();});
 node.addEventListener('focusin',()=>{entry.focused=true;clearTimeout(entry.timer);});
 node.addEventListener('focusout',event=>{entry.focused=node.contains(event.relatedTarget);resume();});
 close.onclick=()=>dismissToast(entry);entry.cancelMotion=motion?.toast?.(node,true);resume();
}
function dismissToast(entry=activeToast){
 if(!entry||activeToast!==entry||entry.closing)return;
 entry.closing=true;clearTimeout(entry.timer);entry.cancelMotion?.();
 const complete=()=>{
  if(activeToast!==entry)return;
  const focused=entry.node.contains(document.activeElement);entry.node.remove();activeToast=null;
  $('toast-status').textContent='';$('toast-error-live').textContent='';showNextToast();
  if(focused)(activeToast?.node.querySelector('button')||$('nav-control')).focus({preventScroll:true});
 };
 if(motion?.toast)entry.cancelMotion=motion.toast(entry.node,false,complete);else complete();
}
function resetToasts(){
 toastQueue.length=0;
 if(activeToast){clearTimeout(activeToast.timer);activeToast.cancelMotion?.();activeToast.node.remove();activeToast=null;}
 $('toast-status').textContent='';$('toast-error-live').textContent='';
}
function finishDialog(value){
 const request=dialogRequest;if(!request)return;
 dialogRequest=null;request.resolve(value);
 const dialog=$('app-dialog'),panel=$('app-dialog-form');
 const complete=()=>{
  if(dialogRequest)return;
  if(dialog.open)dialog.close();
  document.body.classList.toggle('dialog-open',false);
  if(request.trigger?.isConnected)request.trigger.focus({preventScroll:true});
 };
 if(motion?.dialogClose)motion.dialogClose(dialog,panel,complete);else complete();
}
function streamDialog({title,message='',value,confirmLabel='Lanjutkan',destructive=false,trigger}={}){
 closePalette(false);
 if(dialogRequest)finishDialog(dialogRequest.cancelValue);
 const dialog=$('app-dialog'),input=$('app-dialog-input'),hasInput=value!==undefined;
 $('app-dialog-title').textContent=title;$('app-dialog-message').textContent=message;
 $('app-dialog-message').hidden=!message;$('app-dialog-label').hidden=!hasInput;input.hidden=!hasInput;
 input.value=hasInput?value:'';$('app-dialog-confirm').textContent=confirmLabel;
 $('app-dialog-confirm').className=destructive?'dialog-danger':'primary';
 dialog.dataset.tone=destructive?'destructive':'default';
 const promise=new Promise(resolve=>{dialogRequest={resolve,trigger,cancelValue:hasInput?null:false,hasInput};});
 if(!dialog.open)dialog.showModal();document.body.classList.toggle('dialog-open',true);
 motion?.dialogOpen?.(dialog,$('app-dialog-form'));
 if(hasInput){input.focus({preventScroll:true});const dot=value.lastIndexOf('.');input.setSelectionRange(0,dot>0?dot:value.length);}
 else $('app-dialog-cancel').focus({preventScroll:true});
 return promise;
}
$('app-dialog-form').onsubmit=event=>{event.preventDefault();if(!dialogRequest)return;finishDialog(dialogRequest.hasInput?$('app-dialog-input').value:true);};
$('app-dialog-cancel').onclick=()=>finishDialog(dialogRequest?.cancelValue);
$('app-dialog').addEventListener('cancel',event=>{event.preventDefault();finishDialog(dialogRequest?.cancelValue);});
function showSection(section){
 const admin=section==='admin',clip=section==='clipper';
 $('workspace').dataset.destination=admin?'settings':clip?'clipper':'workspace';
 $('control-room').hidden=admin||clip;$('clipper-room').hidden=admin||!clip;$('history-panel').hidden=admin;$('admin-view').hidden=!admin;
 $('page-title').textContent=admin?'Settings':clip?'Clipper':'StreamFetch';
 for(const [id,current] of [['nav-control',!admin&&!clip],['nav-clipper',clip],['nav-admin',admin]]){
  if(current)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');
 }
 closeActionMenu();
 if(admin)loadWatches();
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
 $('watch-feedback').textContent=data.watches.length?'':'Belum ada Watch. Tambah Watch untuk menjadwalkan capture live.';
 for(const watch of data.watches){
  const row=document.createElement('li');row.className='watch-row';
  const copy=document.createElement('div');copy.className='watch-copy';
  const name=watch.name||watch.channel.replace(/^https?:\/\/(?:www\.)?youtube\.com\//,'');
  const title=document.createElement('strong');title.textContent=name;title.title=watch.channel;
  const window=document.createElement('small');window.textContent=watchWindow(watch)+' · '+(watch.mode==='every'?'Every live':'First live')+' · Auto-transcribe '+(watch.auto_transcribe?'on':'off');
  copy.append(title,window);
  if(watch.last_capture){const last=document.createElement('small');last.textContent='Terakhir: '+(watch.last_capture.title||watch.last_capture.video_id||watch.last_capture.job_id||'—')+(watch.last_capture.state?' · '+stateName(watch.last_capture.state):'');last.title=last.textContent;last.className='watch-last';copy.append(last);}
  const state=document.createElement('span');state.className='badge neutral';state.textContent=({waiting:'Waiting',active:'Waiting',recording:'Recording',discovery_issue:'Discovery issue',expired:'Expired',finished:'Selesai',cancelled:'Dibatalkan'})[watch.status]||watch.status;state.title=watch.discovery_error||state.textContent;
  row.append(copy,state);
  if(watch.can_cancel??['waiting','active','discovery_issue'].includes(watch.status)){const cancel=document.createElement('button');cancel.type='button';cancel.className='quiet watch-cancel';cancel.textContent='Batalkan';cancel.setAttribute('aria-label','Batalkan watch '+name);cancel.onclick=()=>cancelWatch(watch,cancel);row.append(cancel);}
  $('watch-list').append(row);
 }
}
async function loadWatches(force=false){
 if(watchLoading&&!force)return;
 const requestId=++watchRefreshId;watchLoading=true;
 try{const data=await api('watches');if(requestId===watchRefreshId)renderWatches(data);}
 catch(error){if(requestId===watchRefreshId)$('watch-feedback').textContent=error.message;}
 finally{if(requestId===watchRefreshId)watchLoading=false;}
}
async function cancelWatch(watch,button){
 if(button.disabled)return;button.disabled=true;
 try{await api('watches/'+encodeURIComponent(watch.id)+'/cancel',{});toast('Watch dibatalkan. Capture yang sudah berjalan tetap berlanjut.');await loadWatches(true);}
 catch(error){toast(error.message,'error');}
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
  storage:payload.storage,is_clip:!!payload.is_clip,clip_start:payload.clip_start,
  clip_end:payload.clip_end,clip_duration:payload.clip_duration,requested_at:Date.now()/1000,state:'starting',
  detail:'Capture diterima. Menunggu worker…'};
 // Create/focus the new row immediately; motion never gates publication or polling.
 showSection(payload.is_clip?'clipper':'control');
 clearHistoryFilters(false);
 renderHistory([submittedJob,...historyRows.filter(row=>row.job_id!==focusedJobId)]);
 renderOperationalStatus();
 const job=$('results').children[0];
 if(!payload.is_clip)collapseSource(true,{accepted:true,job});
 if(payload.source==='youtube'&&!payload.is_clip)$('youtube-url').value='';
 if(payload.source==='tiktok')$('tiktok-url').value='';
 if(payload.source==='instagram')$('instagram-url').value='';
 if(!payload.is_clip){clearTimeout(estimateTimer);estimateId++;$('size-estimate').textContent='Pilih sumber dulu untuk melihat perkiraan ukurannya.';}
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
 if(path==='status'){archiveEnabled=!!result.archive_enabled;$('storage-archive').disabled=!archiveEnabled;$('clip-storage-archive').disabled=!archiveEnabled;if(!archiveEnabled&&$('storage-target').value==='archive')$('storage-target').value='local';if(!archiveEnabled&&$('clip-storage').value==='archive')$('clip-storage').value='local';updateStorageHint();}
 return result;
}
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function showLogin(){const returning=$('login').hidden;closePalette(false);resetToasts();if(returning)motion?.reset();$('workspace').hidden=true;$('login').hidden=false;csrf='';isAdmin=false;active=null;lastObservedJob=null;focusedJobId=null;submittedJob=null;focusPending=false;historyRows=[];collapseSource(false);showSection(routeView());expandedHistoryDetails.clear();if(returning)motion?.loginReveal?.($('login'));$('password').focus({preventScroll:true});}
function controls(){const canMark=!!active&&active.state==='recording'&&active.is_live!==false;$('mark').disabled=pending||!canMark;$('record').disabled=pending||!online||!disk?.can_record||!!active||queued>0||(mode==='oryx'&&!$('source').value);$('stop').disabled=pending||!active||['stopping','ready','failed'].includes(active.state);updateClipControls();renderOperationalStatus();}
function bytes(n=0){return n>=1e9?(n/1e9).toFixed(2)+' GB':(n/1e6).toFixed(1)+' MB';}
function stateName(state){return ({queued:'Menunggu',starting:'Menghubungkan',recording:'Berjalan',waiting:'Menunggu YouTube',stopping:'Menghentikan',finalizing:'Memproses',ready:'Siap',failed:'Gagal',interrupted:'Terputus'})[state]||state||'Siap';}
function isFiniteDownload(job){return ['youtube','tiktok','instagram'].includes(job?.source)&&job.is_live===false;}
function jobStateName(job){return isFiniteDownload(job)&&job.state==='recording'?'Mengunduh':stateName(job?.state);}
function sourceDisplayName(job){if(job.is_clip&&Number.isFinite(job.clip_start)&&Number.isFinite(job.clip_end))return 'YouTube · '+formatClipTimestamp(job.clip_start)+'–'+formatClipTimestamp(job.clip_end)+' · '+formatClipTimestamp(job.clip_duration);return job.source==='tiktok'&&(!job.source_name||job.source_name==='TikTok Live')?'TikTok':job.source_name||({youtube:'YouTube',oryx:'Oryx',tiktok:'TikTok',instagram:'Instagram'})[job.source]||job.source||'';}
function statusTone(state){return ({starting:'working',recording:'working',waiting:'waiting',stopping:'waiting',finalizing:'working',ready:'success',failed:'error',interrupted:'error'})[state]||'idle';}
function presetName(row){if(row.output_format==='mp3'||row.filename?.toLowerCase().endsWith('.mp3'))return 'Audio MP3';return ({original:'Original',balanced:'Seimbang',compact:'Hemat'})[row.compression]||'Preset tidak tercatat';}
function duration(n=0){return [Math.floor(n/3600),Math.floor(n/60)%60,Math.floor(n)%60].map(x=>String(x).padStart(2,'0')).join(':');}
function parseClipTimestamp(value){
 if(typeof value==='number'){if(Number.isInteger(value)&&value>=0)return value;throw Error('Timestamp harus berupa detik positif.');}
 const text=String(value??'').trim().toLowerCase();
 if(!text||text.startsWith('-'))throw Error('Timestamp harus berupa detik positif.');
 if(/^\d+$/.test(text))return Number(text);
 if(/^\d+(?::\d{1,2}){1,2}$/.test(text)){
  const parts=text.split(':').map(Number);if(parts.slice(1).some(part=>part>=60))throw Error('Timestamp belum valid.');
  return parts.length===2?parts[0]*60+parts[1]:parts[0]*3600+parts[1]*60+parts[2];
 }
 const units=text.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/i);
 if(units&&(units[1]!==undefined||units[2]!==undefined||units[3]!==undefined))return Number(units[1]||0)*3600+Number(units[2]||0)*60+Number(units[3]||0);
 throw Error('Gunakan detik, MM:SS, HH:MM:SS, atau 1m30s.');
}
function formatClipTimestamp(value){const total=Math.max(0,Math.floor(Number(value)||0)),hours=Math.floor(total/3600),minutes=Math.floor(total/60)%60,seconds=total%60;return (hours?String(hours).padStart(2,'0')+':':'')+String(minutes).padStart(2,'0')+':'+String(seconds).padStart(2,'0');}
function transcriptStateName(state){return ({queued:'Queued',transcribing:'Transcribing',completed:'Completed',failed:'Failed',cancelled:'Cancelled'})[state]||state;}
function stopIcon(){const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.setAttribute('fill','currentColor');const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');for(const [key,value] of Object.entries({x:6,y:6,width:12,height:12,rx:1}))rect.setAttribute(key,value);icon.append(rect);return icon;}
function transcriptionStop(row){const button=document.createElement('button');button.type='button';button.className='transcription-stop quiet';button.title='Hentikan transkripsi';button.setAttribute('aria-label','Hentikan transkripsi');button.append(stopIcon());button.onclick=()=>cancelTranscript(row,button);return button;}
async function cancelTranscript(row,button){if(button?.disabled)return;if(button)button.disabled=true;try{await api('recordings/'+encodeURIComponent(row.job_id)+'/transcript/cancel',row.transcript?.request_id?{request_id:row.transcript.request_id}:{});lastObservedJob={...row,transcript:{...row.transcript,status:'cancelled'}};renderOperationalStatus();toast('Transkripsi dibatalkan. Media asli tetap tersimpan.');await refresh();}catch(error){toast(error.message,'error');}finally{if(button)button.disabled=false;}}
function languageName(code){if(!code)return '—';try{const name=new Intl.DisplayNames(['id'],{type:'language'}).of(code);return name.charAt(0).toUpperCase()+name.slice(1);}catch{return code;}}
function processingTime(seconds){if(seconds===undefined||seconds===null)return '—';const value=Math.round(seconds);return value>=60?Math.floor(value/60)+'m '+value%60+'s':value+'s';}
async function generateTranscript(row){try{await api('recordings/'+encodeURIComponent(row.job_id)+'/transcript',{});lastObservedJob={...row,transcript:{...row.transcript,status:'queued'}};renderOperationalStatus();toast('Transkrip masuk antrean. Statusnya akan diperbarui otomatis.');await refresh();}catch(error){toast(error.message,'error');}}
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
  const link=transcriptLink(row,'view','View Transcript');link.className='transcript-cta';link.onclick=event=>motion?.followLink?.(link,event,{shell:$('control-room'),origin:cell.querySelector('.recording-heading strong')||link,row:cell.parentElement});box.append(link);
 }else{
  const button=document.createElement('button');button.type='button';button.className='transcript-cta transcript-generate';
  button.textContent=({queued:'Waiting',transcribing:'Generating',failed:'Retry Transcript',cancelled:'Generate Transcript'})[state]||'Generate Transcript';
  button.disabled=['queued','transcribing'].includes(state);button.onclick=()=>generateTranscript(row);box.append(button);
  if(state==='queued'||state==='transcribing'){
   box.append(transcriptionStop(row));
   const progress=document.createElement('progress');progress.max=100;progress.className='transcript-progress';progress.setAttribute('aria-label','Transcript progress');
   if(state==='transcribing'&&Number.isFinite(transcript.progress_percent))progress.value=Math.min(100,Math.max(0,transcript.progress_percent));
   const hint=document.createElement('small');hint.setAttribute('role','status');
   hint.textContent=state==='queued'?'Waiting in queue':(Number.isFinite(transcript.progress_percent)?Math.round(transcript.progress_percent)+'% · ':'')+transcriptETA(transcript);
   box.append(progress,hint);
  }
  if(state==='failed'&&transcript.error){const error=document.createElement('small');error.className='transcript-status failed';error.textContent=transcript.error;box.append(error);}
  if(state==='cancelled'){const hint=document.createElement('small');hint.textContent='Cancelled';box.append(hint);}
 }
 cell.append(box);
}
function updateStorageHint(){$('storage-hint').textContent=$('storage-target').value==='archive'?'File disimpan di lokal, lalu disalin ke arsip.':archiveEnabled?'File disimpan di folder downloads.':'File disimpan di folder downloads. Arsipnya belum aktif.';}
function renderDownloadProgress(job){
 const download=isFiniteDownload(job);
 const visible=job?.state==='finalizing'||download&&['starting','recording','waiting'].includes(job.state);
 $('download-progress').hidden=!visible;
 if(!visible){motion?.progress($('progress-bar'),null);return;}
 const label=job.state==='finalizing'?'Memproses':'Progress capture';
 const percent=(job.state==='recording'||job.state==='finalizing'&&job.progress_phase==='processing')&&typeof job.progress_percent==='number'&&Number.isFinite(job.progress_percent)?Math.min(100,Math.max(0,job.progress_percent)):null;
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
 const phase=transcript?.status==='transcribing'?'transcribing':transcript?.status==='queued'?'queued':transcript?.status==='cancelled'?'cancelled':transcript?.status==='failed'?'failed':
  ({queued:'queued',starting:'starting',waiting:'queued',recording:'downloading',stopping:'processing',finalizing:'processing',ready:'completed',failed:'failed',interrupted:'failed'})[job?.state]||(queued?'queued':'hidden');
 const canCancelTranscript=['queued','transcribing'].includes(transcript?.status);
 const canStop=canCancelTranscript||!!active&&!['stopping','ready','failed'].includes(active.state);
 const canMark=!!active&&active.state==='recording'&&active.is_live!==false;
 const change=()=>{
 $('status-monitor').hidden=phase==='hidden';
 $('status-monitor').dataset.phase=phase;
 $('stop').hidden=!canStop;$('marker-controls').hidden=!canMark;
 $('stop').disabled=pending||!canStop;
 $('stop').title=canCancelTranscript?'Hentikan transkripsi':'Hentikan proses';$('stop').setAttribute('aria-label',$('stop').title);
 connection.textContent=online?'Online':'Offline';connection.classList.toggle('online',online);connection.classList.toggle('offline',!online);
 connection.title=online?'Sistem siap':'Sistem offline';connection.setAttribute('aria-label',connection.title);
 $('status-monitor').dataset.tone=statusTone(job?.state);
 $('state').textContent=({starting:'Menyiapkan',queued:'Menunggu',downloading:isFiniteDownload(job)?'Mengunduh':'Merekam',processing:'Memproses',transcribing:'Transkripsi',completed:'Selesai',failed:'Gagal',cancelled:'Dibatalkan'})[phase]||'';
 $('state').dataset.tone=statusTone(job?.state);
 $('status-job-title').textContent=job?islandTitle(job):queued+' capture';
 $('status-job-title').title=$('status-job-title').textContent;
 $('status-detail').hidden=phase!=='failed';
 $('status-detail').textContent=phase==='failed'?(transcript?.status==='failed'?'Transkripsi gagal.':job?.error_title?(job.error_title+'. '+job.error_message):'Capture gagal.') : '';
 $('island-details').hidden=phase!=='failed'||!(job?.detail||transcript?.error||historyRows.some(row=>row.job_id===job?.job_id));
 $('duration').textContent=['completed','failed','cancelled'].includes(phase)?'':islandTime(job,transcript);
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
 if(!historyRows.some(row=>row.job_id===job.job_id))await refresh();
 clearHistoryFilters();
 const row=[...$('results').children].find(row=>row.dataset.jobId===job.job_id);
 const details=row?.querySelector('.recording-details');
 if(details){expandedHistoryDetails.add(job.job_id);details.open=true;row.tabIndex=-1;row.focus({preventScroll:true});row.scrollIntoView({behavior:motion?.reduced()?'auto':'smooth',block:'center'});}
 else notice(job.error_message||job.transcript?.error||'Detail belum tersedia.');
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
function updateFormatControls(){const audio=mode==='youtube'&&$('media-format').value==='mp3';$('capture-options').hidden=false;$('format-field').hidden=mode!=='youtube';$('media-options').classList.toggle('has-format',mode==='youtube');$('media-format').disabled=mode!=='youtube';$('quality').disabled=mode==='oryx'||audio;$('compression').disabled=audio;if(mode==='oryx'||audio)$('quality').value='best';if(audio)$('compression').value='original';updateCompressionHint();$('note').placeholder=mode==='youtube'?'Kosongkan untuk memakai judul asli':'Contoh: Batam menyapa';$('filename-hint').textContent='Nama file: DDMMYYNN - '+(mode==='youtube'?'Judul video':'Judul')+'.'+(audio?'mp3':'mp4');$('record').textContent=mode==='youtube'?'Download':'Mulai';}
const sourceModes=['youtube','tiktok','instagram','oryx'];
function positionSourceTab(value=mode){const tabs=$('source-tabs'),tab=$('tab-'+value),indicator=$('source-tab-indicator');if(!tabs||!tab||!indicator||!Number.isFinite(tab.offsetLeft)||!tab.offsetWidth)return;indicator.style.left=tab.offsetLeft+'px';indicator.style.width=tab.offsetWidth+'px';tabs.dataset.motionReady='true';}
function applyMode(value){mode=value;for(const source of sourceModes){$('tab-'+source).setAttribute('aria-pressed',String(source===mode));$('tab-'+source).classList.toggle('selected',source===mode);$(source+'-fields').hidden=source!==mode;}if(mode!=='youtube')$('media-format').value='mp4';updateFormatControls();scheduleEstimate();controls();positionSourceTab();}
function setMode(value){
 const previous=mode,nextTab=$('tab-'+value),tabs=$('source-tabs');
 if(!tabs.dataset.motionReady)positionSourceTab(previous);
 const change=()=>applyMode(value);
 if(previous===value){change();motion?.sourcePress?.(nextTab);return;}
 const shared=[$('capture-content'),tabs,$('source-tab-indicator'),...$('capture-content').querySelectorAll('.capture-options,.capture-options .field,.capture-footer')];
 if(motion?.sourceMode)motion.sourceMode($('capture-panel'),change,{nextTab,indicator:$('source-tab-indicator'),outgoing:$(previous+'-fields'),incoming:$(value+'-fields'),shared,direction:Math.sign(sourceModes.indexOf(value)-sourceModes.indexOf(previous))});
 else change();
}
function downloadCount(row){return Math.max(0,Number.parseInt(row.download_count,10)||0);}
function downloadedAtWIB(value){
 const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<=0)return '';
 return new Intl.DateTimeFormat('id-ID',{timeZone:'Asia/Jakarta',dateStyle:'medium',timeStyle:'short',hour12:false}).format(new Date(seconds*1000))+' WIB';
}
function historyDetails(row){
 const details=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('div');
 details.className='recording-details';details.dataset.jobId=row.job_id;details.open=expandedHistoryDetails.has(row.job_id);
 summary.textContent=row.state==='failed'?'Detail teknis':'Lihat detail';
 body.className='recording-detail-body';
 const description=document.createElement('p');description.textContent=row.detail||row.error_message||row.note||'Belum ada detail tambahan.';
 const metadata=document.createElement('p');metadata.className='detail-metadata';
 const attempt=row.attempt_number?'Percobaan '+row.attempt_number+'/'+(row.attempt_total||row.attempt_number):row.download_attempt?'Percobaan internal '+row.download_attempt+(row.download_attempts?'/'+row.download_attempts:''):'';
 const clip=row.is_clip?' · Clip: '+formatClipTimestamp(row.clip_start)+'–'+formatClipTimestamp(row.clip_end)+' ('+formatClipTimestamp(row.clip_duration)+')':'';
 metadata.textContent='Preset: '+presetName(row)+clip+(attempt?' · '+attempt:'');
 body.append(description,metadata);
 if(isAdmin){const analytics=document.createElement('p'),last=downloadedAtWIB(row.last_downloaded_at);analytics.className='detail-download-stats';analytics.textContent=downloadCount(row)+'× download · '+(last?'Terakhir: '+last:'Belum pernah diunduh');body.append(analytics);}
 details.append(summary,body);
 details.addEventListener('toggle',()=>{if(!details.isConnected)return;if(details.open)expandedHistoryDetails.add(row.job_id);else expandedHistoryDetails.delete(row.job_id);});
 return details;
}
function historyFilters(){return {query:$('filter-q').value.trim().toLocaleLowerCase(),source:$('filter-source').value,state:$('filter-state').value,date:$('filter-date').value};}
function matchingHistory(rows,filters=historyFilters()){
 return rows.filter(row=>{
  const text=[row.filename,row.title,row.note,row.source_metadata?.title,row.source,sourceDisplayName(row)].filter(Boolean).join(' ').toLocaleLowerCase();
  const stateMatches=filters.state==='transcribing'?['queued','transcribing'].includes(row.transcript?.status):!filters.state||row.state===filters.state;
  const date=row.requested_at?new Date(row.requested_at*1000):null;
  const dateLabel=date&&!Number.isNaN(date.getTime())?[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-'):'';
  return (!filters.query||text.includes(filters.query))&&(!filters.source||row.source===filters.source)&&stateMatches&&(!filters.date||dateLabel===filters.date);
 });
}
function clearHistoryFilters(render=true){for(const id of ['filter-q','filter-source','filter-state','filter-date'])$(id).value='';if(render)applyHistoryFilters();}
function applyHistoryFilters(){closeActionMenu();renderHistory(historyRows);motion?.historyFilter?.($('results'));}
function captureFailureMessage(row){return row.error_title?(row.error_title+(row.error_message?' — '+row.error_message:'')):(sourceDisplayName(row)||'Sumber')+' gagal memberikan media. Buka detail untuk melihat penyebab.';}
function renderHistory(rows){
 historyRows=rows;
 const filters=historyFilters(),visible=matchingHistory(rows,filters),filtered=Object.values(filters).some(Boolean);
 const total=Math.max(historyTotal,rows.length);
 $('result-count').textContent=filtered?visible.length+' / '+rows.length+' hasil':rows.length?(total>rows.length?rows.length+' / '+total:rows.length)+' hasil':'Belum ada hasil';
 $('result-count').title=total>rows.length?'Filter berlaku pada '+rows.length+' rekaman yang dimuat.':'Hasil riwayat';
 $('empty').hidden=!!visible.length;
 $('empty').querySelector('strong').textContent=filtered?'Tidak ada rekaman yang cocok.':'Belum ada rekaman.';
 $('empty').querySelector('p').textContent=filtered?'Ubah pencarian atau bersihkan filter untuk melihat rekaman lainnya.':'Mulai capture dari Source. Rekaman dan download akan muncul di sini.';
 $('filter-reset').hidden=!filtered;
 $('filter-reset').disabled=!filtered;
 const selection=new Set(selectedJobs());
 for(const details of $('results').querySelectorAll('.recording-details')){
  if(details.open)expandedHistoryDetails.add(details.dataset.jobId);else expandedHistoryDetails.delete(details.dataset.jobId);
 }
 $('results').replaceChildren();
 for(const row of visible){
  const tr=document.createElement('tr'),selectCell=document.createElement('td'),checkbox=document.createElement('input');
  tr.dataset.jobId=row.job_id;
  if(row.job_id===focusedJobId){tr.className='focused-job';tr.tabIndex=-1;tr.setAttribute('aria-label','Capture terbaru');}
  selectCell.className='select-cell';checkbox.type='checkbox';checkbox.className='history-select';checkbox.value=row.job_id;
  checkbox.checked=selection.has(row.job_id);checkbox.setAttribute('aria-label','Pilih '+(row.filename||'riwayat'));checkbox.onchange=updateDeleteControls;
  const selectionLabel=document.createElement('label');selectionLabel.className='history-selection';selectionLabel.append(checkbox);selectCell.append(selectionLabel);tr.append(selectCell);
  const file=document.createElement('td'),strong=document.createElement('strong'),small=document.createElement('small');
  strong.textContent=row.filename||'Rekaman '+row.job_id.slice(0,8);strong.title=strong.textContent;
  small.className=row.state==='failed'?'capture-failure-message':'';
  small.textContent=row.state==='failed'?captureFailureMessage(row):row.processing_detail||row.note||(row.state==='interrupted'?'Proses belum selesai. Buka detail untuk melihat penyebab.':row.detail||'Tanpa catatan');
  small.title=row.state==='failed'?small.textContent:row.note||row.error_message||(!row.error_title?row.detail:'');
  const preset=document.createElement('span');preset.className='preset-badge';preset.textContent=presetName(row);
  const heading=document.createElement('div'),badge=document.createElement('span');heading.className='recording-heading';
  badge.className='status-badge';badge.dataset.tone=statusTone(row.state);badge.textContent=row.state==='failed'?'Capture gagal':jobStateName(row);
  heading.append(badge,strong);file.append(heading,small,preset);
  if(isAdmin){const downloads=document.createElement('span');downloads.className='download-stat';downloads.textContent=downloadCount(row)+'× download';file.append(downloads);}
  file.append(historyDetails(row));
  const meta=document.createElement('div');meta.className='history-mobile-meta';meta.textContent=[sourceDisplayName(row),row.requested_at?new Date(row.requested_at*1000).toLocaleString('id-ID'):null,row.size?bytes(row.size):null].filter(Boolean).join(' · ');file.append(meta);
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
  if(active?.job_id===row.job_id&&!['stopping','ready','failed'].includes(row.state)){const stop=document.createElement('button');stop.type='button';stop.className='capture-stop quiet';stop.title='Hentikan proses';stop.setAttribute('aria-label',stop.title);stop.append(stopIcon());stop.onclick=()=>$('stop').onclick();action.append(stop);}
  if(row.filename&&row.state==='ready'){
   const download=document.createElement('a');download.href='/api/files/'+encodeURIComponent(row.filename);download.className='download-direct';
   download.setAttribute('aria-label','Unduh '+row.filename);download.title='Unduh';download.append(downloadIcon());action.append(download);
  }
  if(row.state==='failed'&&row.can_retry){const retry=document.createElement('button');retry.type='button';retry.className='retry-capture secondary';retry.textContent=retryingJobs.has(row.job_id)?'Mengirim…':'Coba lagi';retry.disabled=retryingJobs.has(row.job_id);retry.onclick=()=>retryCapture(row,retry);action.append(retry);}
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
async function retryCapture(row,button){
 if(retryingJobs.has(row.job_id)||button?.disabled)return;
 retryingJobs.add(row.job_id);if(button){button.disabled=true;button.textContent='Mengirim…';button.setAttribute('aria-busy','true');}
 try{
  const result=await api('recordings/'+encodeURIComponent(row.job_id)+'/retry',{});
  const replacement={job_id:result.job_id,retry_of:row.job_id,attempt_root_id:result.attempt_root_id,
   attempt_number:result.attempt_number,attempt_total:result.attempt_total,source:row.source,
   source_name:row.source_name,note:row.note,filename:row.filename,quality:row.quality,
   output_format:row.output_format,compression:row.compression,storage:row.storage,is_live:row.is_live,
   is_clip:row.is_clip,clip_start:row.clip_start,clip_end:row.clip_end,clip_duration:row.clip_duration,
   requested_at:Date.now()/1000,state:'starting',detail:'Percobaan '+result.attempt_number+'/'+result.attempt_total+' · Menunggu worker…'};
  active=null;queued=Math.max(1,queued);focusedJobId=result.job_id;focusPending=true;submittedJob=replacement;lastObservedJob=replacement;
  clearHistoryFilters(false);
  renderHistory([replacement,...historyRows.filter(item=>item.job_id!==replacement.job_id).map(item=>item.job_id===row.job_id?{...item,can_retry:false}:item)]);controls();
  toast('Percobaan '+result.attempt_number+'/'+result.attempt_total+' masuk antrean.');
  await refresh();
 }catch(error){retryingJobs.delete(row.job_id);if(button){button.disabled=false;button.textContent='Coba lagi';button.setAttribute('aria-busy','false');}toast(error.message,'error');}
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
  const history=await api('recordings?');if(revision!==refreshId)return;
  const rows=history.recordings;
  historyTotal=Number.isFinite(history.total)?history.total:rows.length;
  // Admission can precede catalogue publication. Keep the accepted capture
  // visible until the worker supplies its durable row, without retaining URLs.
  if(submittedJob){
   if(rows.some(row=>row.job_id===submittedJob.job_id))submittedJob=null;
   else rows.unshift(active?.job_id===submittedJob.job_id?{...submittedJob,...active}:submittedJob);
  }
  if(!active&&lastObservedJob){
   let finished=rows.find(row=>row.job_id===lastObservedJob.job_id);
   if(finished&&['ready','failed','interrupted'].includes(finished.state))lastObservedJob=finished;
   else if(!['ready','failed','interrupted'].includes(lastObservedJob.state))lastObservedJob=null;
   renderOperationalStatus();
  }
  closeActionMenu();renderHistory(rows);renderOperationalStatus();
 }catch(error){online=false;controls();notice(error.message);}
}
function youtubeClipUrlValid(value){try{const url=new URL(value);return ['youtube.com','www.youtube.com','m.youtube.com','youtu.be'].includes(url.hostname)&&['http:','https:'].includes(url.protocol);}catch{return false;}}
function setClipDurationLock(seconds){
 clipDurationLock=seconds;
 for(const value of [15,30,60,90])$('clip-duration-'+value).setAttribute('aria-pressed',String(value===seconds));
 $('clip-duration-custom').setAttribute('aria-pressed',String(seconds===null));
}
function clipRange(showError=true){
 if(!clipMetadata)return null;
 let start,end,error='';
 try{start=parseClipTimestamp($('clip-start').value);}catch(exc){error='Waktu mulai tidak valid. '+exc.message;}
 if(!error){try{end=parseClipTimestamp($('clip-end').value);}catch(exc){error=$('clip-end').value.trim()?'Waktu selesai tidak valid. '+exc.message:'Isi waktu selesai.';}}
 if(!error&&end===start)error='Clip tidak boleh berdurasi nol.';
 if(!error&&end<start)error='Waktu selesai harus setelah waktu mulai.';
 if(!error&&(start>clipMetadata.duration||end>Math.ceil(clipMetadata.duration)))error='Rentang clip melewati durasi video.';
 $('clip-swap').hidden=!(Number.isFinite(start)&&Number.isFinite(end)&&start>end);
 $('clip-start').setAttribute('aria-invalid',String(!!error&&(!Number.isFinite(start)||start<0)));
 $('clip-end').setAttribute('aria-invalid',String(!!error));
 $('clip-calculated-duration').textContent=!error&&end>start?formatClipTimestamp(end-start):'—';
 if(showError)$('clip-error').textContent=error;
 return error?null:{start,end,duration:end-start};
}
function updateClipControls(){
 const range=clipRange(!!clipMetadata);
 $('create-clip').disabled=clipSubmitting||pending||!online||!disk?.can_record||!!active||queued>0||!range;
 return range;
}
function resetClipMetadata(message='Tempel link untuk membaca judul dan durasi tanpa mengunduh media.'){
 clipMetadata=null;clipMetadataRevision++;clearTimeout(clipMetadataTimer);setClipDurationLock(null);
 $('clip-metadata').hidden=true;$('clip-helpers').hidden=true;$('clip-thumbnail').hidden=true;$('clip-thumbnail').src='';
 $('clip-title').textContent='';$('clip-total-duration').textContent='';$('clip-metadata-status').textContent=message;
 $('clip-error').textContent='';$('clip-end').value='';$('clip-calculated-duration').textContent='—';$('create-clip').disabled=true;
 for(const value of [15,30,60,90])$('clip-duration-'+value).disabled=false;
}
async function loadClipMetadata(){
 const url=$('clip-url').value.trim(),revision=++clipMetadataRevision;
 if(!url){resetClipMetadata();return;}
 if(!youtubeClipUrlValid(url)){resetClipMetadata('Masukkan link YouTube yang valid.');return;}
 $('clip-metadata-status').textContent='Membaca metadata video…';$('clip-error').textContent='';
 try{
  const data=await api('clipper/metadata',{url});if(revision!==clipMetadataRevision)return;
  clipMetadata=data;$('clip-title').textContent=data.title;$('clip-total-duration').textContent='Durasi total · '+data.duration_label;
  for(const value of [15,30,60,90])$('clip-duration-'+value).disabled=value>Math.floor(data.duration);
  $('clip-thumbnail').hidden=!data.thumbnail;if(data.thumbnail)$('clip-thumbnail').src=data.thumbnail;
  $('clip-start').value=formatClipTimestamp(data.url_start||0);$('clip-end').value='';setClipDurationLock(null);
  const reveal=()=>{$('clip-metadata').hidden=false;$('clip-helpers').hidden=false;};
  if(motion?.clipperReveal)motion.clipperReveal($('clipper-panel'),reveal,[$('clip-metadata'),$('clip-helpers')]);else reveal();
  $('clip-metadata-status').textContent=data.url_start?'Timestamp URL dipakai sebagai Start · '+data.url_start_label:'Metadata siap. Pilih durasi atau isi End.';
  updateClipControls();
 }catch(error){if(revision===clipMetadataRevision)resetClipMetadata(error.message);}
}
function scheduleClipMetadata(){resetClipMetadata('Menunggu link YouTube…');clearTimeout(clipMetadataTimer);clipMetadataTimer=setTimeout(loadClipMetadata,500);}
function chooseClipDuration(seconds){
 if(!clipMetadata)return;setClipDurationLock(seconds);
 if(seconds!==null){let start;try{start=parseClipTimestamp($('clip-start').value);}catch{start=0;}start=Math.min(start,Math.max(0,Math.floor(clipMetadata.duration)-seconds));$('clip-start').value=formatClipTimestamp(start);$('clip-end').value=formatClipTimestamp(start+seconds);}
 updateClipControls();
}
function clipInputChanged(which){
 if(which==='end'&&clipDurationLock!==null)setClipDurationLock(null);
 if(which==='start'&&clipDurationLock!==null){try{$('clip-end').value=formatClipTimestamp(parseClipTimestamp($('clip-start').value)+clipDurationLock);}catch{}}
 updateClipControls();
}
function normalizeClipInput(id){try{$(id).value=formatClipTimestamp(parseClipTimestamp($(id).value));}catch{}updateClipControls();}
function nudgeClip(which,delta){
 if(!clipMetadata)return;const input=$(which==='start'?'clip-start':'clip-end');let value;try{value=parseClipTimestamp(input.value);}catch{value=0;}
 if(which==='end'&&clipDurationLock!==null)setClipDurationLock(null);
 const maximum=which==='start'&&clipDurationLock!==null?Math.max(0,Math.floor(clipMetadata.duration)-clipDurationLock):Math.floor(clipMetadata.duration);
 value=Math.max(0,Math.min(maximum,value+delta));input.value=formatClipTimestamp(value);
 if(which==='start'&&clipDurationLock!==null)$('clip-end').value=formatClipTimestamp(value+clipDurationLock);
 updateClipControls();input.focus({preventScroll:true});
}
function updateClipFormat(){const audio=$('clip-format').value==='mp3';$('clip-quality').disabled=audio;$('clip-compression').disabled=audio;if(audio){$('clip-quality').value='best';$('clip-compression').value='original';}$('clip-compression-hint').textContent=audio?'Kompresi video tidak berlaku untuk audio MP3.':'Pertahankan kualitas sumber jika kompatibel.';}
async function enter(auth){const view=routeView();isAdmin=!!auth?.is_admin;motion?.loginSuccess?.($('workspace'));$('login').hidden=true;$('workspace').hidden=false;showSection(view);positionSourceTab();$('role-badge').textContent=isAdmin?'Admin':'Pengguna';$('admin-panel').hidden=!isAdmin;$('settings-admin').hidden=!isAdmin;$('settings-nav-admin').hidden=!isAdmin;$('watches-panel').hidden=false;$('delete-selected').hidden=!isAdmin;updateFormatControls();updateClipFormat();await loadSources();await refresh();motion?.returnReveal?.($('workspace'),$('page-title'),[view==='clipper'?$('clipper-panel'):$('capture-panel'),$('history-panel')]);}
function loginReturnTarget(){
 const raw=new URLSearchParams(window.location.search).get('next');
 if(!raw||raw.startsWith('//')||(!raw.startsWith('/')&&!/^https?:\/\//i.test(raw)))return '';
 try{
  const target=new URL(raw+(raw.includes('#')?'':window.location.hash),window.location.origin);
  return target.origin===window.location.origin?target.pathname+target.search+target.hash:'';
 }catch{return '';}
}
const returnTarget=loginReturnTarget();
function completeLogin(auth){
 csrf=auth.csrf;$('password').value='';
 if(returnTarget){motion?.loginSuccess?.($('workspace'));window.location.replace(returnTarget);return;}
 return enter(auth);
}
let loginBusy=false;
function loginLoading(busy){loginBusy=busy;if(busy)motion?.loginInteract?.('submit');$('login-form').setAttribute('aria-busy',String(busy));$('login-submit').disabled=busy;$('login-submit').textContent=busy?'Masuk…':'Masuk';}
$('login-form').onsubmit=async e=>{e.preventDefault();if(loginBusy)return;loginLoading(true);$('login-error').textContent='';$('password').removeAttribute('aria-invalid');try{const auth=await api('login',{password:$('password').value});await completeLogin(auth);}catch(err){$('login-error').textContent=err.message;$('password').setAttribute('aria-invalid','true');motion?.loginError?.($('login-error'));$('password').focus({preventScroll:true});}finally{loginLoading(false);}};
$('password').oninput=()=>{motion?.loginInteract?.('typing');$('password').removeAttribute('aria-invalid');$('login-error').textContent='';};
$('password').addEventListener('focus',()=>motion?.loginInteract?.('focus'));
$('password').addEventListener('blur',()=>{if(!loginBusy)motion?.loginInteract?.('idle');});
$('password').addEventListener('pointerdown',()=>motion?.loginInteract?.('focus'));
$('island-details').onclick=showIslandDetails;
$('watch-refresh').onclick=()=>loadWatches(true);
$('logout').onclick=async()=>{try{await api('logout',{});showLogin();}catch(e){notice(e.message);}};
function navigateSection(section,updateUrl=false){
 const destination=section==='admin'?'settings':section==='clipper'?'clipper':'workspace';
 if($('workspace').dataset.destination===destination)return;
 if(updateUrl&&section!=='admin'&&window.history?.pushState)window.history.pushState({},'',section==='clipper'?'/clipper':'/');
 const change=()=>showSection(section),target=section==='admin'?'admin-view':section==='clipper'?'clipper-room':'control-room';
 if(motion?.contextChange)motion.contextChange($('workspace'),$('page-title'),change,()=>[$(target),section==='admin'?null:$('history-panel')]);else change();
}
$('nav-control').onclick=()=>navigateSection('control',true);$('nav-clipper').onclick=()=>navigateSection('clipper',true);$('nav-admin').onclick=()=>navigateSection('admin');
window.addEventListener('popstate',()=>navigateSection(window.location.pathname==='/clipper'?'clipper':'control'));
$('capture-another').onclick=()=>{if(motion)collapseSource(false,{onSettled:()=>sourceInput().focus({preventScroll:true})});else{collapseSource(false);sourceInput().focus({preventScroll:true});}};
$('tab-instagram').onclick=()=>setMode('instagram');$('tab-tiktok').onclick=()=>setMode('tiktok');$('tab-oryx').onclick=()=>setMode('oryx');$('tab-youtube').onclick=()=>setMode('youtube');$('source').onchange=()=>{editSource($('source').value);scheduleEstimate();controls();};$('new-source').onclick=()=>{$('source-manager').open=true;editSource('');$('source-name').focus();};
$('source-form').onsubmit=async e=>{e.preventDefault();try{const saved=await api('sources',{id:editing,name:$('source-name').value,url:$('source-url').value});await loadSources();$('source').value=saved.id;editSource(saved.id);$('source-feedback').textContent='Sip, sumbernya sudah tersimpan dan siap dipakai.';controls();}catch(err){$('source-feedback').textContent=err.message;}};
$('check').onclick=async()=>{$('check').disabled=true;$('source-feedback').textContent='Lagi mencoba terhubung…';try{const result=await api('check',{url:$('source-url').value});$('source-feedback').textContent='Berhasil tersambung · '+result.codecs.join(' / ');}catch(e){$('source-feedback').textContent=e.message;}finally{$('check').disabled=false;}};
function wibDate(days=0){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Jakarta',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(Date.now()+days*86400000));const value=Object.fromEntries(parts.map(part=>[part.type,part.value]));return value.year+'-'+value.month+'-'+value.day;}
function resetWatchForm(){
 $('watch-url').value='';$('watch-day').value='today';$('watch-date').value='';$('watch-date').min=wibDate();$('watch-date-field').hidden=true;
 $('watch-start').value='08:00';$('watch-end').value='10:00';$('watch-mode').value='first';$('watch-auto').checked=false;$('watch-error').textContent='';
}
function openWatchForm(){
 if(watchFormClosing||watchCreating)return;
 resetWatchForm();const panel=$('watch-create-panel');panel.hidden=false;$('watch-create').setAttribute('aria-expanded','true');
 motion?.dialogOpen?.(panel,$('watch-form'));$('watch-url').focus({preventScroll:true});
}
function closeWatchForm(){
 const panel=$('watch-create-panel');
 if(panel.hidden||watchFormClosing)return Promise.resolve();
 watchFormClosing=true;
 return new Promise(resolve=>{
  const complete=()=>{panel.hidden=true;watchFormClosing=false;$('watch-create').setAttribute('aria-expanded','false');resetWatchForm();$('watch-create').focus({preventScroll:true});resolve();};
  if(motion?.dialogClose)motion.dialogClose(panel,$('watch-form'),complete);else complete();
 });
}
function watchPayload(){
 const channel=$('watch-url').value.trim(),choice=$('watch-day').value;
 const date=choice==='today'?wibDate():choice==='tomorrow'?wibDate(1):$('watch-date').value;
 const start=$('watch-start').value,end=$('watch-end').value;
 if(!channel)throw Error('Masukkan URL channel YouTube atau @handle.');
 if(!date)throw Error('Pilih tanggal Watch.');
 if(!/^\d{2}:\d{2}$/.test(start)||!/^\d{2}:\d{2}$/.test(end)||end<=start)throw Error('Jam akhir harus setelah jam mulai (WIB).');
 if(Date.parse(date+'T'+end+':00+07:00')<=Date.now())throw Error('Window Watch sudah berakhir (WIB).');
 return {channel,date:choice==='date'?date:choice,start_time:start,end_time:end,mode:$('watch-mode').value,auto_transcribe:$('watch-auto').checked};
}
async function createWatch(){
 if(watchCreating)return;
 let payload;try{payload=watchPayload();}catch(error){$('watch-error').textContent=error.message;return;}
 watchCreating=true;$('watch-error').textContent='';$('watch-form').setAttribute('aria-busy','true');$('watch-create-submit').disabled=true;$('watch-create-cancel').disabled=true;$('watch-create-submit').textContent='Menyimpan…';
 try{await api('watches',payload);toast('Watch dibuat. Menunggu live sesuai jadwal.');const closing=closeWatchForm();await loadWatches(true);await closing;}
 catch(error){$('watch-error').textContent=error.message;}
 finally{watchCreating=false;$('watch-form').setAttribute('aria-busy','false');$('watch-create-submit').disabled=false;$('watch-create-cancel').disabled=false;$('watch-create-submit').textContent='Simpan Watch';}
}
$('record').onclick=async()=>{if(pending)return;pending=true;$('record').textContent='Mengirim…';$('capture-panel').setAttribute('aria-busy','true');controls();notice('');const payload={source:mode,source_id:$('source').value,url:mode==='oryx'?'':sourceInput().value,note:$('note').value,storage:$('storage-target').value,quality:$('quality').value,format:$('media-format').value,compression:$('compression').value};try{const result=await api('record',payload);captureSubmitted(result,payload);await refresh();}catch(e){collapseSource(false);notice(e.message);}finally{pending=false;updateFormatControls();$('capture-panel').setAttribute('aria-busy','false');controls();}};
$('clip-url').oninput=scheduleClipMetadata;
$('clip-start').oninput=()=>clipInputChanged('start');$('clip-end').oninput=()=>clipInputChanged('end');
$('clip-start').onblur=()=>normalizeClipInput('clip-start');$('clip-end').onblur=()=>normalizeClipInput('clip-end');
for(const value of [15,30,60,90])$('clip-duration-'+value).onclick=()=>chooseClipDuration(value);
$('clip-duration-custom').onclick=()=>chooseClipDuration(null);
for(const [id,which,delta] of [['clip-start-minus-5','start',-5],['clip-start-minus-1','start',-1],['clip-start-plus-1','start',1],['clip-start-plus-5','start',5],['clip-end-minus-5','end',-5],['clip-end-minus-1','end',-1],['clip-end-plus-1','end',1],['clip-end-plus-5','end',5]])$(id).onclick=()=>nudgeClip(which,delta);
$('clip-swap').onclick=()=>{const start=$('clip-start').value;$('clip-start').value=$('clip-end').value;$('clip-end').value=start;setClipDurationLock(null);updateClipControls();$('clip-start').focus({preventScroll:true});};
$('clip-format').onchange=()=>{updateClipFormat();updateClipControls();};
$('clip-form').onsubmit=async event=>{
 event.preventDefault();if(clipSubmitting)return;const range=updateClipControls();if(!range)return;
 clipSubmitting=true;$('clip-form').setAttribute('aria-busy','true');$('create-clip').textContent='Membuat…';updateClipControls();notice('');
 const payload={source:'youtube',url:$('clip-url').value.trim(),metadata_token:clipMetadata.token,start:range.start,end:range.end,
  clip_start:range.start,clip_end:range.end,clip_duration:range.duration,is_clip:true,format:$('clip-format').value,
  quality:$('clip-quality').value,compression:$('clip-compression').value,storage:$('clip-storage').value,note:''};
 try{const result=await api('clipper',payload);captureSubmitted(result,payload);$('clip-url').value='';resetClipMetadata();await refresh();}
 catch(error){$('clip-error').textContent=error.message;}
 finally{clipSubmitting=false;$('clip-form').setAttribute('aria-busy','false');$('create-clip').textContent='Create Clip';controls();}
};
$('storage-target').onchange=updateStorageHint;
$('quality').onchange=scheduleEstimate;$('compression').onchange=()=>{updateCompressionHint();scheduleEstimate();};$('media-format').onchange=()=>{updateFormatControls();scheduleEstimate();};$('youtube-url').oninput=scheduleEstimate;$('tiktok-url').oninput=scheduleEstimate;$('instagram-url').oninput=scheduleEstimate;
$('watch-create').onclick=openWatchForm;$('watch-create-cancel').onclick=closeWatchForm;$('watch-form').onsubmit=event=>{event.preventDefault();return createWatch();};
for(const id of ['watch-url','watch-date','watch-start','watch-end','watch-mode','watch-auto'])$(id).oninput=()=>{$('watch-error').textContent='';};
$('watch-day').onchange=()=>{$('watch-date-field').hidden=$('watch-day').value!=='date';$('watch-error').textContent='';};resetWatchForm();$('watch-create-panel').hidden=true;
$('stop').onclick=async()=>{const job=active||submittedJob||lastObservedJob;if(job?.state==='ready'&&['queued','transcribing'].includes(job.transcript?.status))return cancelTranscript(job,$('stop'));pending=true;controls();try{await api('stop',{job_id:active.job_id});notice('Lagi dihentikan. File-nya akan muncul sebentar lagi.');await refresh();}catch(e){notice(e.message);}finally{pending=false;controls();}};
setInterval(()=>{const job=active||submittedJob||lastObservedJob;if(!['completed','failed','cancelled'].includes($('status-monitor').dataset.phase))$('duration').textContent=islandTime(job,job?.transcript);},1000);
motion?.loginReveal?.($('login'));
(async()=>{try{const auth=await api('session');await completeLogin(auth);}catch{showLogin();}})();
async function poll(){await refresh();if(!$('admin-view').hidden)loadWatches();setTimeout(poll,2500);}setTimeout(poll,2500);

$('mark').onclick=async()=>{pending=true;controls();try{await api('markers',{job_id:active.job_id,note:$('marker-note').value});$('marker-note').value='';await refresh();}catch(e){notice(e.message);}finally{pending=false;controls();}};
$('filter-q').oninput=applyHistoryFilters;
for(const id of ['filter-source','filter-state','filter-date'])$(id).onchange=applyHistoryFilters;
$('filter-reset').onclick=()=>clearHistoryFilters();

function selectedJobs(){return [...$('results').querySelectorAll('.history-select:checked')].map(input=>input.value);}
function selectedRows(){const ids=new Set(selectedJobs());return historyRows.filter(row=>ids.has(row.job_id));}
function updateDeleteControls(){$('history-panel').dataset.selected=String(selectedJobs().length>0);$('delete-selected').hidden=!isAdmin;$('delete-selected').disabled=!isAdmin||!selectedJobs().length;$('download-selected').disabled=!selectedRows().some(row=>row.filename&&row.state==='ready');const boxes=[...$('results').querySelectorAll('.history-select')];$('select-all').checked=!!boxes.length&&boxes.every(box=>box.checked);$('select-all').indeterminate=boxes.some(box=>box.checked)&&!$('select-all').checked;}
function downloadSelected(){const rows=selectedRows().filter(row=>row.filename&&row.state==='ready');for(const row of rows){const link=document.createElement('a');link.href='/api/files/'+encodeURIComponent(row.filename);link.download=row.filename;document.body.append(link);link.click();link.remove();}toast(rows.length+' file mulai diunduh. Kalau browser bertanya, izinkan download beberapa file, ya.','info');}
async function deleteJobs(jobIds,trigger){
 if(!jobIds.length)return;
 const row=jobIds.length===1?historyRows.find(item=>item.job_id===jobIds[0]):null;
 const subject=row?.filename?'“'+row.filename+'” dan file lokalnya':jobIds.length+' rekaman yang dipilih beserta file lokalnya';
 const confirmed=await streamDialog({title:'Hapus rekaman?',message:subject+' akan dihapus. Tindakan ini tidak dapat dibatalkan.',confirmLabel:'Hapus',destructive:true,trigger});
 if(!confirmed)return;
 pending=true;$('delete-selected').disabled=true;
 try{const result=await api('recordings/delete',{job_ids:jobIds});const failed=result.skipped?.length||0;toast(result.deleted.length+' item berhasil dihapus'+(failed?' · '+failed+' item belum bisa dihapus':'')+'.',failed?'error':'success');await refresh();}catch(e){toast(e.message,'error');}finally{pending=false;updateDeleteControls();}
}
async function renameJob(row,trigger){
 const value=await streamDialog({title:'Ubah nama',value:row.filename||'',confirmLabel:'Simpan',trigger});
 if(value===null||value.trim()===''||value.trim()===row.filename)return;
 try{await api('recordings/'+encodeURIComponent(row.job_id)+'/rename',{filename:value.trim()});toast(row.archive_status==='archived'?'Nama file lokal sudah diganti. Nama di arsip tetap sama.':'Sip, nama file sudah diganti.');await refresh();}catch(e){toast(e.message,'error');}
}
let openActionMenu=null;
function closeActionMenu(focus=false){if(!openActionMenu)return;const {panel,trigger}=openActionMenu;panel.remove();trigger.setAttribute('aria-expanded','false');openActionMenu=null;if(focus)trigger.focus();}
function menuButton(label,handler,danger=false){const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('role','menuitem');if(danger)button.className='danger';button.onclick=()=>{const trigger=openActionMenu?.trigger||button;closeActionMenu();handler(trigger);};return button;}
function toggleActionMenu(trigger,row){if(openActionMenu?.trigger===trigger){closeActionMenu();return;}closeActionMenu();const panel=document.createElement('div');panel.className='action-menu-panel';panel.setAttribute('role','menu');panel.setAttribute('aria-label','Aksi '+(row.filename||'riwayat'));if(row.filename&&row.state==='ready')panel.append(menuButton('Ubah nama',dialogTrigger=>renameJob(row,dialogTrigger)));if(isAdmin)panel.append(menuButton('Hapus',dialogTrigger=>deleteJobs([row.job_id],dialogTrigger),true));document.body.append(panel);trigger.setAttribute('aria-expanded','true');openActionMenu={panel,trigger};const rect=trigger.getBoundingClientRect(),gap=6,margin=8,width=panel.offsetWidth,height=panel.offsetHeight;const left=Math.max(margin,Math.min(rect.right-width,window.innerWidth-width-margin));let top=rect.bottom+gap;if(top+height>window.innerHeight-margin)top=rect.top-height-gap;panel.style.left=left+'px';panel.style.top=Math.max(margin,top)+'px';panel.querySelector('button')?.focus();}
function downloadIcon(){const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.8');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');for(const d of ['M12 3v12','M7 10l5 5 5-5','M5 21h14']){const path=document.createElementNS(ns,'path');path.setAttribute('d',d);svg.append(path);}return svg;}
document.addEventListener('pointerdown',event=>{if(openActionMenu&&!openActionMenu.panel.contains(event.target)&&event.target!==openActionMenu.trigger)closeActionMenu();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeActionMenu(true);});
window.addEventListener('resize',()=>{closeActionMenu();positionSourceTab();});window.addEventListener('scroll',()=>closeActionMenu(),true);
$('select-all').onchange=()=>{for(const box of $('results').querySelectorAll('.history-select'))box.checked=$('select-all').checked;updateDeleteControls();};
$('download-selected').append(downloadIcon());
$('download-selected').onclick=downloadSelected;
$('delete-selected').onclick=()=>deleteJobs(selectedJobs(),$('delete-selected'));
$('password-form').onsubmit=async event=>{event.preventDefault();const feedback=$('password-feedback');const next=$('new-password').value;const confirmation=$('confirm-password').value;if(next!==confirmation){feedback.textContent='Password yang kamu ulangi belum sama.';return;}try{await api('admin/password',{target:$('password-target').value,current_password:$('current-admin-password').value,new_password:next});event.target.reset();feedback.textContent='Sip, password-nya sudah diganti.';}catch(error){feedback.textContent=error.message;}};

function goToSettingsSection(id){
 if(id==='settings-admin'&&!isAdmin)return;
 navigateSection('admin');const section=$(id);if(!section||section.hidden)return;
 section.tabIndex=-1;section.focus({preventScroll:true});section.scrollIntoView({behavior:motion?.reduced?.()?'auto':'smooth',block:'start'});
}
for(const [button,section] of [['settings-nav-watches','watches-panel'],['settings-nav-sources','settings-sources'],['settings-nav-storage','settings-storage'],['settings-nav-admin','settings-admin']])$(button).onclick=()=>goToSettingsSection(section);
const paletteCommands=[
 {id:'workspace',label:'Go to Workspace',run:()=>$('nav-control').onclick()},
 {id:'clipper',label:'Go to Clipper',run:()=>$('nav-clipper').onclick()},
 {id:'settings',label:'Go to Settings',run:()=>$('nav-admin').onclick()},
 {id:'capture',label:'New capture / focus Source',run:()=>{$('nav-control').onclick();if($('capture-content').hidden)$('capture-another').onclick();else sourceInput().focus({preventScroll:true});}},
 {id:'watch',label:'Add Watch',run:()=>{$('nav-admin').onclick();if($('watch-create-panel').hidden)$('watch-create').onclick();else $('watch-url').focus({preventScroll:true});$('watch-create-panel').scrollIntoView({behavior:'auto',block:'nearest'});}},
 {id:'history',label:'Focus History search',run:()=>{$('nav-control').onclick();$('filter-q').focus({preventScroll:true});$('history-panel').scrollIntoView({behavior:'auto',block:'start'});}}
];
let paletteMatches=[],paletteIndex=0,paletteTrigger=null,paletteClosing=false,paletteRevision=0;
function renderPalette(){
 const query=$('command-search').value.trim().toLocaleLowerCase();
 paletteMatches=paletteCommands.filter(command=>command.label.toLocaleLowerCase().includes(query));paletteIndex=0;
 $('command-list').replaceChildren();
 for(const command of paletteMatches){
  const option=document.createElement('li');option.id='command-'+command.id;option.className='command-option';option.setAttribute('role','option');option.textContent=command.label;
  option.addEventListener('pointerdown',event=>event.preventDefault());option.onclick=()=>executePalette(command);$('command-list').append(option);
 }
 $('command-empty').hidden=!!paletteMatches.length;selectPalette(0);
}
function selectPalette(index){
 paletteIndex=paletteMatches.length?(index+paletteMatches.length)%paletteMatches.length:0;
 for(const [i,option] of [...$('command-list').children].entries())option.setAttribute('aria-selected',String(i===paletteIndex));
 if(paletteMatches.length)$('command-search').setAttribute('aria-activedescendant','command-'+paletteMatches[paletteIndex].id);
 else $('command-search').removeAttribute('aria-activedescendant');
}
function openPalette(){
 const dialog=$('command-palette');
 if($('workspace').hidden||$('app-dialog').open)return;
 paletteRevision++;paletteClosing=false;if(!dialog.open)paletteTrigger=document.activeElement;closeActionMenu();
 $('command-search').value='';renderPalette();if(!dialog.open)dialog.showModal();
 document.body.classList.toggle('dialog-open',true);$('command-search').setAttribute('aria-expanded','true');
 motion?.dialogOpen?.(dialog,$('command-card'));$('command-search').focus({preventScroll:true});
}
function closePalette(animate=true,action){
 const dialog=$('command-palette');if(!dialog.open||paletteClosing&&animate)return;
 const revision=++paletteRevision;paletteClosing=true;
 const complete=()=>{
  if(revision!==paletteRevision)return;
  dialog.close();paletteClosing=false;$('command-search').setAttribute('aria-expanded','false');
  document.body.classList.toggle('dialog-open',!!$('app-dialog').open);
  if(action&&!$('workspace').hidden)action();else if(paletteTrigger?.isConnected)paletteTrigger.focus({preventScroll:true});
 };
 if(animate&&motion?.dialogClose)motion.dialogClose(dialog,$('command-card'),complete);else complete();
}
function executePalette(command=paletteMatches[paletteIndex]){if(command)closePalette(true,command.run);}
function typingTarget(target){return !!target&&(target.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(target.tagName?.toUpperCase())||!!target.closest?.('[contenteditable]:not([contenteditable="false"])'));}
function paletteShortcut(event){
 if(event.defaultPrevented||event.repeat||event.isComposing||event.altKey||typingTarget(event.target))return;
 if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'&&!$('workspace').hidden&&!$('app-dialog').open){event.preventDefault();$('command-palette').open?closePalette():openPalette();}
}
document.addEventListener('keydown',paletteShortcut);
$('command-open').onclick=openPalette;
$('command-close').onclick=()=>closePalette();
$('command-palette').addEventListener('cancel',event=>{event.preventDefault();closePalette();});
$('command-search').oninput=renderPalette;
$('command-search').onkeydown=event=>{
 if(event.isComposing)return;
 if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();selectPalette(paletteIndex+(event.key==='ArrowDown'?1:-1));}
 if(event.key==='Enter'){event.preventDefault();executePalette();}
};
window.addEventListener('pagehide',()=>{closePalette(false);resetToasts();});
