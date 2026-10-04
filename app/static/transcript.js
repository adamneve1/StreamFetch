/* Read-only transcript presentation. Timestamp modes never modify source data. */
'use strict';
function readerTimestamp(seconds, subtitle=false){
  const milliseconds=Math.max(0,Math.round(seconds*1000));
  const h=Math.floor(milliseconds/3600000),m=Math.floor(milliseconds/60000)%60,s=Math.floor(milliseconds/1000)%60;
  const value=[h,m,s].map(n=>String(n).padStart(2,'0')).join(':');
  return subtitle?value+'.'+String(milliseconds%1000).padStart(3,'0'):value;
}
function readerDisplayTimestamp(seconds,subtitle=false){
  const parts=readerTimestamp(seconds,subtitle).split(':');
  const hours=Number(parts.shift());
  return (hours?hours+':':'')+parts.join(':');
}
function timestampLabel(segment,mode,compact=false){
  if(mode==='none'||!Number.isFinite(segment.start))return '';
  const format=compact?readerDisplayTimestamp:readerTimestamp;
  if(mode==='subtitle')return format(segment.start,true)+' → '+format(segment.end,true);
  return format(segment.start);
}
function formatInfo(info){
  const guests=(info.guests||[]).map(guest=>guest.role?guest.name+' — '+guest.role:guest.name).join('\n');
  return [['Program',info.program], [info.date_time_label||'Date/Time',info.date_time],['Theme',info.theme],['Guests / Narasumber',guests],['Presenter',info.presenter],['Source',info.source]]
    .filter(([,value])=>value).map(([label,value])=>label+': '+value).join('\n');
}
function formatTranscript(segments,mode='none'){
  return segments.map(segment=>{const time=timestampLabel(segment,mode);return (time?'['+time+']\n':'')+segment.text;}).join('\n\n');
}
function findMatches(text,query){
  if(!query.trim())return [];
  const matches=[],needle=query.toLocaleLowerCase(),haystack=text.toLocaleLowerCase();
  let index=0;
  while((index=haystack.indexOf(needle,index))!==-1){matches.push({start:index,end:index+query.length});index+=query.length;}
  return matches;
}
function nextMatchIndex(current,delta,count){return count?(current+delta+count)%count:-1;}

function seekTranscript(player,seconds){
  if(!player||!Number.isFinite(seconds)||seconds<0)return false;
  player.seekTo(seconds,true);return true;
}

if(typeof module!=='undefined')module.exports={readerTimestamp,readerDisplayTimestamp,timestampLabel,formatInfo,formatTranscript,findMatches,nextMatchIndex,seekTranscript};
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',async()=>{
  const $=id=>document.getElementById(id);
  const status=$('reader-status'),content=$('reader-content'),article=$('transcript-text');
  const base=location.pathname.replace(/\/(?:view)?$/,'');
  let data,matchNodes=[],current=-1,player=null,playerReady=false,pendingSeek=null;
  function notify(message){status.textContent=message;}
  function seek(seconds){
    if(playerReady){seekTranscript(player,seconds);return;}
    pendingSeek=seconds;$('player-status').hidden=false;$('player-status').textContent='Video masih dimuat. Posisi akan dibuka saat siap.';
  }
  function initPlayer(videoId){
    if(!/^[A-Za-z0-9_-]{11}$/.test(videoId||''))return;
    $('reader-player').hidden=false;
    function createPlayer(){
      player=new window.YT.Player('youtube-player',{
        videoId,playerVars:{playsinline:1,origin:location.origin},
        events:{onReady:()=>{playerReady=true;$('player-status').hidden=true;if(pendingSeek!==null){seekTranscript(player,pendingSeek);pendingSeek=null;}},
          onError:()=>{playerReady=false;$('reader-player').hidden=true;$('player-status').hidden=false;$('player-status').textContent='Video tidak dapat diputar di sini. Transkrip tetap tersedia.';}}
      });
    }
    if(window.YT&&window.YT.Player){createPlayer();return;}
    window.onYouTubeIframeAPIReady=createPlayer;
    const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';
    script.addEventListener('error',()=>{$('reader-player').hidden=true;$('player-status').hidden=false;$('player-status').textContent='Video tidak dapat dimuat. Transkrip tetap tersedia.';});document.head.append(script);
  }
  function highlightedText(parent,text,query){
    let last=0;
    for(const match of findMatches(text,query)){
      parent.append(document.createTextNode(text.slice(last,match.start)));
      const mark=document.createElement('mark');mark.textContent=text.slice(match.start,match.end);parent.append(mark);matchNodes.push(mark);last=match.end;
    }
    parent.append(document.createTextNode(text.slice(last)));
  }
  function updateMatch(scroll=false){
    matchNodes.forEach((node,index)=>node.classList.toggle('current-match',index===current));
    $('search-count').textContent=$('transcript-search').value.trim()?(matchNodes.length?(current+1)+' / '+matchNodes.length+' matches':'No matches'):'';
    $('search-previous').disabled=$('search-next').disabled=!matchNodes.length;
    if(scroll&&matchNodes[current])matchNodes[current].scrollIntoView({behavior:'smooth',block:'center'});
  }
  function render(reset=false){
    article.replaceChildren();matchNodes=[];
    const mode=$('timestamp-mode').value,query=$('transcript-search').value;
    article.dataset.mode=mode;
    if($('raw-view').checked){
      const pre=document.createElement('pre');highlightedText(pre,data.raw,query);article.append(pre);
    }else{
      for(const segment of data.segments){
        const row=document.createElement('section');row.className='reader-segment';
        const label=timestampLabel(segment,mode,true);
        if(label){
          const timestamp=document.createElement(data.youtube_id?'button':'time');timestamp.textContent=label;
          if(data.youtube_id){timestamp.type='button';timestamp.className='reader-timestamp';timestamp.setAttribute('aria-label','Seek to '+label);timestamp.addEventListener('click',()=>seek(segment.start));}
          row.append(timestamp);
        }
        else if(mode!=='none'){const empty=document.createElement('span');row.append(empty);}
        const paragraph=document.createElement('p');highlightedText(paragraph,segment.text,query);row.append(paragraph);article.append(row);
      }
    }
    if(!data.segments.length&&!$('raw-view').checked){article.textContent='Transkrip kosong.';}
    current=matchNodes.length?(reset?0:Math.max(0,Math.min(current,matchNodes.length-1))):-1;updateMatch();
  }
  function showInfo(info){
    const dl=$('program-info');
    for(const [label,value] of [['Date/Time',info.date_time_label==='Source upload date'?'':info.date_time],['Theme',info.theme],['Narasumber',info.guests],['Presenter',info.presenter]]){
      const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;
      if(Array.isArray(value)&&value.length){
        const list=document.createElement('ul');
        for(const guest of value){const li=document.createElement('li');li.textContent=guest.name;if(guest.role){const role=document.createElement('span');role.className='guest-role';role.textContent=' — '+guest.role;li.append(role);}list.append(li);}dd.append(list);
      }else{dd.textContent=Array.isArray(value)||!value?'Tidak tersedia':value;}
      dl.append(dt,dd);
    }
    $('source-description').textContent=info.description||'Deskripsi sumber tidak tersedia untuk rekaman ini.';
  }
  async function copy(text){
    try{await navigator.clipboard.writeText(text);notify('Disalin ke clipboard.');}
    catch{notify('Clipboard tidak tersedia. Pilih teks dan salin secara manual.');}
  }
  try{
    const response=await fetch(base+'/data');
    if(!response.ok){const error=await response.json();throw Error(error.error||'Transkrip tidak dapat dibuka.');}
    data=await response.json();document.title=data.info.program+' · StreamFetch';
    $('reader-title').textContent=data.info.program;
    $('reader-source').textContent=[data.info.source,data.language,data.info.date_time_label==='Source upload date'?'Source upload date: '+data.info.date_time:''].filter(Boolean).join(' · ');
    initPlayer(data.youtube_id);
    showInfo(data.info);
    for(const kind of data.exports){const link=document.createElement('a');link.href=base+'/'+kind;link.textContent='Export '+kind.toUpperCase();$('transcript-exports').append(link);}
    $('reader-export-menu').hidden=!data.exports.length;
    if(!data.segments.some(segment=>Number.isFinite(segment.start))){$('timestamp-mode').value='none';$('timestamp-mode').disabled=true;}
    $('timestamp-mode').addEventListener('change',()=>render());
    $('raw-view').addEventListener('change',()=>{render(true);$('timestamp-mode').disabled=$('raw-view').checked||!data.segments.some(segment=>Number.isFinite(segment.start));});
    $('transcript-search').addEventListener('input',()=>render(true));
    function navigate(delta){current=nextMatchIndex(current,delta,matchNodes.length);updateMatch(true);}
    $('search-previous').addEventListener('click',()=>navigate(-1));$('search-next').addEventListener('click',()=>navigate(1));
    $('transcript-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();navigate(event.shiftKey?-1:1);}});
    function transcriptCopy(){return $('raw-view').checked?data.raw:formatTranscript(data.segments,$('timestamp-mode').value);}
    $('copy-info').addEventListener('click',()=>copy(formatInfo(data.info)));
    $('copy-transcript').addEventListener('click',()=>copy(transcriptCopy()));
    $('copy-all').addEventListener('click',()=>copy(formatInfo(data.info)+'\n\nTranscript\n\n'+transcriptCopy()));
    render();content.hidden=false;notify('');
  }catch(error){notify(error.message);}
});
