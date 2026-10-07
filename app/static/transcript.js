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
  player.seekTo(seconds);return true;
}
function youtubePlayerAdapter(player){
  return {seekTo:seconds=>player.seekTo(seconds,true),getCurrentTime:()=>player.getCurrentTime()};
}
function localPlayerAdapter(video){
  return {seekTo:seconds=>{video.currentTime=Number.isFinite(video.duration)?Math.min(seconds,video.duration):seconds;
    const playing=video.play();if(playing&&playing.catch)playing.catch(()=>{});
  },getCurrentTime:()=>video.currentTime};
}
function createReaderScroller(env){
  const reduced=env.matchMedia?.('(prefers-reduced-motion: reduce)')||{matches:false};
  const desktop=env.matchMedia?.('(min-width: 1001px) and (hover: hover) and (pointer: fine)')||{matches:false};
  const wrapper=env.document?.getElementById('reader-smooth-wrapper'),content=env.document?.getElementById('reader-smooth-content');
  const tools=env.document?.querySelector?.('.reader-tools'),documentPanel=env.document?.querySelector?.('.reader-document');
  let smoother=null,toolsPin=null,started=false,lifecycle=false;
  const mediaListen=(query,method)=>{
    if(query[method])query[method]('change',sync);
    else query[method==='addEventListener'?'addListener':'removeListener']?.(sync);
  };
  const setSmoothingMode=active=>{
    env.document?.documentElement?.classList?.toggle('reader-smoothing',active);
    env.document?.body?.classList?.toggle('reader-smoothing',active);
  };
  const stopInstance=()=>{
    const position=smoother?.scrollTop?.()||0;
    toolsPin?.kill?.();toolsPin=null;
    smoother?.kill?.();smoother=null;setSmoothingMode(false);
    if(position&&desktop.matches){
      if(env.scrollTo)env.scrollTo({top:position,behavior:'auto'});
      else if(env.document?.scrollingElement)env.document.scrollingElement.scrollTop=position;
    }
  };
  function refresh(){
    if(!smoother)return;
    // Async Reader data starts hidden. Pinning before it has a layout would
    // freeze zero-sized toolbar/spacer geometry and collapse both tool rows.
    if(!toolsPin&&tools?.offsetHeight>0&&documentPanel?.offsetWidth>0&&env.ScrollTrigger.create){
      toolsPin=env.ScrollTrigger.create({trigger:documentPanel,pin:tools,pinSpacing:false,start:'top top+=76',end:()=>`bottom top+=${76+(tools.offsetHeight||0)}`,invalidateOnRefresh:true});
    }
    // Refresh all triggers so the pin and the page smoother measure the same
    // revealed/resized layout. smoother.refresh() only refreshes its own trigger.
    env.ScrollTrigger.refresh?.();
  }
  function sync(){
    stopInstance();
    if(reduced.matches||!desktop.matches||!env.gsap||!env.ScrollTrigger||!env.ScrollSmoother)return;
    if(!wrapper||!content)return;
    env.gsap.registerPlugin(env.ScrollTrigger,env.ScrollSmoother);
    env.ScrollSmoother.get?.()?.kill?.();
    setSmoothingMode(true);
    try{
      smoother=env.ScrollSmoother.create({wrapper,content,smooth:1,smoothTouch:0,effects:false,normalizeScroll:false,ignoreMobileResize:true});
      refresh();
    }catch{toolsPin?.kill?.();toolsPin=null;smoother?.kill?.();setSmoothingMode(false);smoother=null;}
  }
  const resize=()=>refresh();
  function stop(){
    if(!started)return;
    started=false;mediaListen(reduced,'removeEventListener');mediaListen(desktop,'removeEventListener');env.removeEventListener?.('resize',resize);stopInstance();
  }
  const pagehide=()=>stop(),pageshow=event=>{if(event.persisted)start();};
  function start(){
    if(!lifecycle){lifecycle=true;env.addEventListener?.('pagehide',pagehide);env.addEventListener?.('pageshow',pageshow);}
    if(started)return;
    started=true;mediaListen(reduced,'addEventListener');mediaListen(desktop,'addEventListener');env.addEventListener?.('resize',resize);sync();
  }
  function destroy(){
    stop();
    if(lifecycle){env.removeEventListener?.('pagehide',pagehide);env.removeEventListener?.('pageshow',pageshow);lifecycle=false;}
  }
  function jumpTo(target,smooth=true,position='center center'){
    if(!target)return false;
    if(smoother){if(content?.contains?.(target))smoother.scrollTo(target,smooth,position);return true;}
    target.scrollIntoView?.({behavior:reduced.matches?'auto':smooth?'smooth':'auto',block:position.startsWith('center')?'center':'start'});
    return true;
  }
  return {start,destroy,jumpTo,refresh,isActive:()=>!!smoother};
}

if(typeof module!=='undefined')module.exports={readerTimestamp,readerDisplayTimestamp,timestampLabel,formatInfo,formatTranscript,findMatches,nextMatchIndex,seekTranscript,youtubePlayerAdapter,localPlayerAdapter,createReaderScroller};
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',async()=>{
  const $=id=>document.getElementById(id);
  const readerScroll=createReaderScroller(window);readerScroll.start();
  for(const link of document.querySelectorAll('.reader-back,.reader-header .brand'))link.addEventListener('click',event=>window.StreamFetchMotion?.followLink?.(link,event,{shell:document.querySelector('.reader-shell'),origin:$('reader-title'),direction:'workspace'}));
  const status=$('reader-status'),content=$('reader-content'),article=$('transcript-text');
  const base=location.pathname.replace(/\/(?:view)?$/,'');
  let data,matchNodes=[],current=-1,player=null,playerReady=false,pendingSeek=null,hasPlayer=false;
  function notify(message){status.textContent=message;}
  function seek(seconds){
    if(!hasPlayer||!Number.isFinite(seconds)||seconds<0)return;
    if(playerReady){try{seekTranscript(player,seconds);}catch{playerFailed();}return;}
    pendingSeek=seconds;$('player-status').hidden=false;$('player-status').textContent='Video masih dimuat. Posisi akan dibuka saat siap.';
  }
  function playerLoaded(){
    playerReady=true;$('player-status').hidden=true;
    if(pendingSeek!==null){const seconds=pendingSeek;pendingSeek=null;seek(seconds);}
  }
  function playerFailed(){
    hasPlayer=false;playerReady=false;pendingSeek=null;player=null;
    $('reader-player').hidden=true;$('player-status').hidden=false;
    $('player-status').textContent='Video tidak dapat diputar di sini. Transkrip tetap tersedia.';
    if($('local-player').pause)$('local-player').pause();
    render();
  }
  function initPlayer(videoId,mediaUrl){
    if(!/^[A-Za-z0-9_-]{11}$/.test(videoId||'')){
      if(typeof mediaUrl!=='string'||!/^\/api\/files\/[^/?#]+\?inline=1$/.test(mediaUrl))return;
      const video=$('local-player');hasPlayer=true;
      if(video.canPlayType&&!video.canPlayType('video/mp4')){playerFailed();return;}
      $('youtube-player').hidden=true;video.hidden=false;
      $('reader-player').classList.toggle('local-video',true);$('reader-player').hidden=false;
      player=localPlayerAdapter(video);
      video.addEventListener('loadedmetadata',()=>{
        if(!hasPlayer)return;
        $('reader-player').classList.toggle('portrait',video.videoHeight>video.videoWidth);
        playerLoaded();
      });
      video.addEventListener('error',playerFailed);video.src=mediaUrl;
      return;
    }
    hasPlayer=true;
    $('reader-player').hidden=false;
    function createPlayer(){
      player=youtubePlayerAdapter(new window.YT.Player('youtube-player',{
        videoId,playerVars:{playsinline:1,origin:location.origin},
        events:{onReady:playerLoaded,onError:playerFailed}
      }));
    }
    if(window.YT&&window.YT.Player){createPlayer();return;}
    window.onYouTubeIframeAPIReady=createPlayer;
    const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';
    script.addEventListener('error',playerFailed);document.head.append(script);
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
    if(scroll&&matchNodes[current])readerScroll.jumpTo(matchNodes[current],true,'center center');
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
          const timestamp=document.createElement(hasPlayer?'button':'time');timestamp.textContent=label;
          if(hasPlayer){timestamp.type='button';timestamp.className='reader-timestamp';timestamp.setAttribute('aria-label','Seek to '+label);timestamp.addEventListener('click',()=>{seek(segment.start);readerScroll.jumpTo(row,true,'center center');});}
          row.append(timestamp);
        }
        else if(mode!=='none'){const empty=document.createElement('span');row.append(empty);}
        const paragraph=document.createElement('p');highlightedText(paragraph,segment.text,query);row.append(paragraph);article.append(row);
      }
    }
    if(!data.segments.length&&!$('raw-view').checked){article.textContent='Transkrip kosong.';}
    current=matchNodes.length?(reset?0:Math.max(0,Math.min(current,matchNodes.length-1))):-1;updateMatch();
    if(!content.hidden)readerScroll.refresh();
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
    initPlayer(data.youtube_id,data.media_url);
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
    render();content.hidden=false;notify('');readerScroll.refresh();
    const jumpToHash=(smooth=false)=>{
      if(!location.hash)return;
      let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}
      readerScroll.jumpTo($(id),smooth,'top top+=24');
    };
    jumpToHash();window.addEventListener?.('hashchange',()=>jumpToHash(true));
    // Animate the tool rows, not the toolbar container whose transform is owned
    // by ScrollTrigger while pinned inside the page-level smooth content.
    window.StreamFetchMotion?.pageReveal?.(document.querySelector('.reader-shell'),$('reader-title'),[document.querySelector('.reader-toolbar'),document.querySelector('.reader-actions'),$('transcript-text')]);
  }catch(error){notify(error.message);}
});
