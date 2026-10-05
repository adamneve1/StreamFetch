const assert=require('node:assert/strict');
const {test}=require('node:test');
const reader=require('../app/static/transcript.js');
const fs=require('node:fs');
const vm=require('node:vm');
const segments=[{start:1.25,end:2.5,text:'Halo dunia'},{start:65,end:68,text:'Selamat pagi'}];

test('timestamp modes format the same immutable segments',()=>{
  const original=JSON.stringify(segments);
  assert.equal(reader.timestampLabel(segments[0],'none'),'');
  assert.equal(reader.timestampLabel(segments[1],'segment'),'00:01:05');
  assert.equal(reader.timestampLabel(segments[0],'subtitle'),'00:00:01.250 → 00:00:02.500');
  assert.equal(reader.timestampLabel({start:null,end:null},'subtitle'),'');
  assert.equal(JSON.stringify(segments),original);
});
test('compact display timestamps preserve hour boundaries and original copy format',()=>{
  for(const [seconds,label] of [[0,'00:00'],[65,'01:05'],[3599,'59:59'],[3600,'1:00:00'],[3665,'1:01:05'],[36000,'10:00:00']]){
    assert.equal(reader.readerDisplayTimestamp(seconds),label);
  }
  assert.equal(reader.timestampLabel(segments[0],'subtitle',true),'00:01.250 → 00:02.500');
  assert.equal(reader.timestampLabel(segments[1],'segment',true),'01:05');
  assert.equal(reader.timestampLabel(segments[0],'none',true),'');
  assert.equal(reader.formatTranscript(segments,'segment'),'[00:00:01]\nHalo dunia\n\n[00:01:05]\nSelamat pagi');
});
test('copy formatting keeps info, roles and transcript separate',()=>{
  const info={program:'Dialog',date_time:'4 Oktober',theme:'Pendidikan',guests:[{name:'Rina',role:'Dosen'}],presenter:'Sari'};
  assert.equal(reader.formatInfo(info),'Program: Dialog\nDate/Time: 4 Oktober\nTheme: Pendidikan\nGuests / Narasumber: Rina — Dosen\nPresenter: Sari');
  assert.equal(reader.formatTranscript(segments,'none'),'Halo dunia\n\nSelamat pagi');
  assert.equal(reader.formatTranscript(segments,'segment'),'[00:00:01]\nHalo dunia\n\n[00:01:05]\nSelamat pagi');
  assert.match(reader.formatTranscript(segments,'subtitle'),/00:00:01\.250 → 00:00:02\.500/);
});
test('literal case insensitive search highlights repeated words and punctuation',()=>{
  assert.deepEqual(reader.findMatches('Halo halo HALO','halo'),[{start:0,end:4},{start:5,end:9},{start:10,end:14}]);
  assert.deepEqual(reader.findMatches('a+b [test]','a+b'),[{start:0,end:3}]);
  assert.deepEqual(reader.findMatches('Halo','   '),[]);
  assert.deepEqual(reader.findMatches('Halo','missing'),[]);
});
test('search navigation wraps in both directions and handles no matches',()=>{
  assert.equal(reader.nextMatchIndex(2,1,3),0);
  assert.equal(reader.nextMatchIndex(0,-1,3),2);
  assert.equal(reader.nextMatchIndex(-1,1,0),-1);
});
test('old TXT-only jobs have readable copy without fabricated timestamps or metadata',()=>{
  assert.equal(reader.formatTranscript([{start:null,end:null,text:'Lama'}],'subtitle'),'Lama');
  assert.equal(reader.formatInfo({program:'Rekaman',guests:[]}),'Program: Rekaman');
});

class Node {
  constructor(){this.children=[];this.dataset={};this.listeners={};this._text='';this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.className='';this.scrolled=false;this.classList={toggle:(name,on)=>{const values=new Set(this.className.split(' ').filter(Boolean));on?values.add(name):values.delete(name);this.className=[...values].join(' ');}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];this._text='';}
  set textContent(value){this.replaceChildren();this._text=String(value);}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  addEventListener(type,callback){this.listeners[type]=callback;}
  setAttribute(name,value){this[name]=value;}
  scrollIntoView(options){this.scrolled=options||true;}
}
function mediaQuery(matches=false){
  const listeners=new Set();
  return {matches,addEventListener(type,listener){if(type==='change')listeners.add(listener);},removeEventListener(type,listener){if(type==='change')listeners.delete(listener);},change(value){this.matches=value;for(const listener of [...listeners])listener({matches:value});},get listenerCount(){return listeners.size;}};
}
test('desktop Reader creates one subtle smoother and routes jumps without native competition',()=>{
  const reduced=mediaQuery(false),desktop=mediaQuery(true),wrapper=new Node(),content=new Node(),registrations=[],creates=[],scrolls=[];
  let current=null;
  const ScrollSmoother={get:()=>current,create:options=>{const instance={options,killed:false,kill(){this.killed=true;if(current===this)current=null;},scrollTo(...args){scrolls.push(args);},refresh(){this.refreshed=true;}};creates.push(instance);return current=instance;}};
  const events={};
  const env={document:{getElementById:id=>id==='reader-smooth-wrapper'?wrapper:id==='reader-smooth-content'?content:null},gsap:{registerPlugin:(...plugins)=>registrations.push(plugins)},ScrollTrigger:{},ScrollSmoother,
    matchMedia:query=>query.includes('reduced')?reduced:desktop,addEventListener:(type,listener)=>events[type]=listener,removeEventListener:type=>delete events[type]};
  const scroller=reader.createReaderScroller(env);scroller.start();scroller.start();
  assert.equal(creates.length,1);assert.deepEqual(registrations[0],[env.ScrollTrigger,ScrollSmoother]);
  assert.deepEqual(creates[0].options,{wrapper,content,smooth:1,smoothTouch:0,effects:false,normalizeScroll:false,ignoreMobileResize:true});
  const target=new Node();assert.equal(scroller.jumpTo(target,true,'center center'),true);
  assert.deepEqual(scrolls,[[target,true,'center center']]);assert.equal(target.scrolled,false);
  scroller.refresh();assert.equal(creates[0].refreshed,true);
  reduced.change(true);assert.equal(creates[0].killed,true);assert.equal(scroller.isActive(),false);
  scroller.jumpTo(target,true,'center center');assert.deepEqual(target.scrolled,{behavior:'auto',block:'center'});
  assert.equal(reduced.listenerCount,1);events.pagehide();assert.equal(reduced.listenerCount,0);
  reduced.matches=false;events.pageshow({persisted:true});assert.equal(creates.length,2);
  scroller.destroy();assert.equal(creates[1].killed,true);assert.equal(events.pagehide,undefined);
});
test('touch/mobile Reader stays native and keeps quick centered jumps',()=>{
  const reduced=mediaQuery(false),mobile=mediaQuery(false),creates=[];
  const scroller=reader.createReaderScroller({document:{getElementById:()=>new Node()},gsap:{registerPlugin(){}},ScrollTrigger:{},ScrollSmoother:{create:options=>creates.push(options)},matchMedia:query=>query.includes('reduced')?reduced:mobile});
  scroller.start();const target=new Node();scroller.jumpTo(target,true,'center center');
  assert.equal(creates.length,0);assert.deepEqual(target.scrolled,{behavior:'smooth',block:'center'});scroller.destroy();
});
async function fixture(data,window={},video={},hash=''){
  const html=fs.readFileSync(require.resolve('../app/static/transcript.html'),'utf8');
  const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],new Node()]));
  nodes.get('timestamp-mode').value='segment';
  nodes.get('reader-player').hidden=true;nodes.get('local-player').hidden=true;
  Object.assign(nodes.get('local-player'),video);
  let init;const copies=[],requests=[];
  const head=new Node(),back=new Node(),shell=new Node(),tools=new Node();
  const context=vm.createContext({window,document:{head,querySelectorAll:()=>[back],querySelector:selector=>selector==='.reader-shell'?shell:tools,getElementById:id=>nodes.get(id),createElement:()=>new Node(),createTextNode:text=>{const node=new Node();node.textContent=text;return node;},addEventListener:(_,callback)=>init=callback},location:{origin:'https://streamfetch.example',pathname:'/api/recordings/old/transcript/view',hash},navigator:{clipboard:{writeText:async text=>copies.push(text)}},fetch:async url=>{requests.push(url);return {ok:true,json:async()=>data};}});
  vm.runInContext(fs.readFileSync(require.resolve('../app/static/transcript.js'),'utf8'),context);
  await init();
  return {get:id=>nodes.get(id),copies,requests,head,back,shell,tools};
}
test('reader enters after data renders and Back uses shared motion without changing player or transcript requests',async()=>{
 const calls=[];
 const f=await fixture({info:{program:'Dialog'},segments,raw:'TXT',exports:[]},{StreamFetchMotion:{pageReveal(...args){calls.push(['enter',...args]);},followLink(...args){calls.push(['back',...args]);}}});
 assert.equal(calls[0][1],f.shell);assert.equal(calls[0][2],f.get('reader-title'));assert.equal(f.get('reader-content').hidden,false);
 f.back.listeners.click({});assert.equal(calls[1][1],f.back);assert.equal(calls[1][3].direction,'workspace');assert.equal(f.requests.length,1);
});
test('Reader restores deep links after async transcript content is revealed',async()=>{
 const f=await fixture({info:{program:'Dialog'},segments,raw:'TXT',exports:[]},{},{},'#reader-title');
 assert.equal(f.get('reader-title').scrolled.behavior,'auto');assert.equal(f.get('reader-title').scrolled.block,'start');
});
function descendants(node){return node.children.flatMap(child=>[child,...descendants(child)]);}
test('YouTube timestamp seeks the single player including clicks before readiness',async()=>{
  let options;const seeks=[];
  const window={YT:{Player:function(id,config){assert.equal(id,'youtube-player');options=config;this.seekTo=(...args)=>seeks.push(args);}}};
  const f=await fixture({youtube_id:'abcdefghijk',info:{program:'Dialog'},segments,raw:'TXT',exports:['txt']},window);
  assert.equal(options.videoId,'abcdefghijk');
  assert.equal(options.playerVars.origin,'https://streamfetch.example');
  assert.equal(f.get('reader-player').hidden,false);
  const buttons=descendants(f.get('transcript-text')).filter(node=>node.className==='reader-timestamp');
  assert.equal(buttons[0].textContent,'00:01');assert.equal(buttons[1].textContent,'01:05');
  buttons[0].listeners.click();assert.equal(seeks.length,0);
  assert.equal(f.get('transcript-text').children[0].scrolled.behavior,'smooth');assert.equal(f.get('transcript-text').children[0].scrolled.block,'center');
  options.events.onReady();assert.deepEqual(seeks,[[1.25,true]]);
  buttons[1].listeners.click();assert.deepEqual(seeks.at(-1),[65,true]);
  assert.equal(f.get('transcript-text').children[1].scrolled.behavior,'smooth');assert.equal(f.get('transcript-text').children[1].scrolled.block,'center');
  options.events.onError();assert.equal(f.get('reader-player').hidden,true);
  assert.match(f.get('player-status').textContent,/Transkrip tetap tersedia/);
  assert.equal(f.requests.length,1);
});
test('YouTube API loads once and unsupported sources omit the player',async()=>{
  const data={info:{program:'Old'},segments,raw:'TXT',exports:['txt']};
  const old=await fixture(data);assert.equal(old.head.children.length,0);
  const f=await fixture({...data,youtube_id:'abcdefghijk'});
  assert.equal(f.head.children.length,1);
  assert.equal(f.head.children[0].src,'https://www.youtube.com/iframe_api');
  f.get('timestamp-mode').value='none';f.get('timestamp-mode').listeners.change();
  assert.equal(f.head.children.length,1);assert.equal(f.requests.length,1);
  f.head.children[0].listeners.error();assert.equal(f.get('reader-player').hidden,true);
});
test('seek rejects unavailable players and invalid timestamps',()=>{
  assert.equal(reader.seekTranscript(null,1),false);
  assert.equal(reader.seekTranscript({seekTo:()=>assert.fail()},NaN),false);
  assert.equal(reader.seekTranscript({seekTo:()=>assert.fail()},-1),false);
});
test('TikTok and Instagram local players seek through the same transcript controls without external widgets',async()=>{
  for(const source of ['TikTok','Instagram']){
    const f=await fixture({media_url:'/api/files/video.mp4?inline=1',info:{program:source},segments,raw:'TXT',exports:['txt']});
    const video=f.get('local-player');let plays=0;
    video.play=()=>{plays++;return Promise.resolve();};video.duration=100;video.videoWidth=1920;video.videoHeight=1080;
    assert.equal(video.hidden,false);assert.equal(video.src,'/api/files/video.mp4?inline=1');
    assert.equal(f.get('youtube-player').hidden,true);assert.equal(f.head.children.length,0);
    const timestamps=descendants(f.get('transcript-text')).filter(node=>node.className==='reader-timestamp');
    timestamps[0].listeners.click();assert.equal(video.currentTime,undefined);
    video.listeners.loadedmetadata();assert.equal(video.currentTime,1.25);assert.equal(plays,1);
    timestamps[1].listeners.click();assert.equal(video.currentTime,65);assert.equal(plays,2);
    f.get('timestamp-mode').value='none';f.get('timestamp-mode').listeners.change();
    assert.equal(video.currentTime,65);assert.equal(f.requests.length,1);
  }
});
test('YouTube remains primary when a stored media URL is also available',async()=>{
  let options;
  const f=await fixture({youtube_id:'abcdefghijk',media_url:'/api/files/video.mp4?inline=1',info:{program:'YT'},segments,raw:'TXT',exports:[]},
    {YT:{Player:function(id,config){options=config;this.seekTo=()=>{};}}});
  assert.equal(options.videoId,'abcdefghijk');assert.equal(f.get('local-player').src,undefined);
});
test('player adapters expose current time, cap local seeks, and tolerate rejected autoplay',async()=>{
  const calls=[],video={currentTime:0,duration:10,play:()=>Promise.reject(Error('Autoplay denied'))};
  const local=reader.localPlayerAdapter(video);assert.equal(reader.seekTranscript(local,20),true);
  assert.equal(local.getCurrentTime(),10);await Promise.resolve();
  const youtube=reader.youtubePlayerAdapter({seekTo:(...args)=>calls.push(args),getCurrentTime:()=>65});
  reader.seekTranscript(youtube,3);assert.deepEqual(calls,[[3,true]]);assert.equal(youtube.getCurrentTime(),65);
});
test('portrait metadata enables bounded natural-aspect playback without changing YouTube sizing',async()=>{
  const f=await fixture({media_url:'/api/files/video.mp4?inline=1',info:{program:'Portrait'},segments,raw:'TXT',exports:[]});
  const video=f.get('local-player');video.videoWidth=1080;video.videoHeight=1920;video.listeners.loadedmetadata();
  assert.match(f.get('reader-player').className,/portrait/);
  video.videoWidth=1920;video.videoHeight=1080;video.listeners.loadedmetadata();
  assert.doesNotMatch(f.get('reader-player').className,/portrait/);
  const css=fs.readFileSync(require.resolve('../app/static/transcript.css'),'utf8');
  assert.match(css,/\.reader-player\.portrait video\{width:auto;height:min\(320px,40dvh\)/);
  assert.match(css,/object-fit:contain/);assert.match(css,/\.local-video\.portrait\{width:fit-content;max-width:100%;aspect-ratio:auto/);
});
test('missing or failed local media leaves reader search, timestamps, copy, and exports usable',async()=>{
  const data={media_url:'/api/files/video.mp4?inline=1',info:{program:'Local'},segments,raw:'TXT',exports:['txt']};
  const f=await fixture(data);const video=f.get('local-player');let paused=false;video.pause=()=>{paused=true;};
  video.listeners.error();assert.equal(paused,true);assert.equal(f.get('reader-player').hidden,true);
  assert.match(f.get('player-status').textContent,/Transkrip tetap tersedia/);
  assert.equal(descendants(f.get('transcript-text')).filter(node=>node.className==='reader-timestamp').length,0);
  f.get('transcript-search').value='Halo';f.get('transcript-search').listeners.input();assert.equal(f.get('search-count').textContent,'1 / 1 matches');
  await f.get('copy-transcript').listeners.click();assert.match(f.copies[0],/Halo dunia/);
  assert.equal(f.get('transcript-exports').children.length,1);
  const missing=await fixture({...data,media_url:null});assert.equal(missing.get('reader-player').hidden,true);
  const external=await fixture({...data,media_url:'https://instagram.com/video.mp4'});assert.equal(external.get('local-player').src,undefined);
  const unsupported=await fixture(data,{}, {canPlayType:()=>''});assert.equal(unsupported.get('reader-player').hidden,true);
  assert.match(unsupported.get('transcript-text').textContent,/Halo dunia/);
});
test('reader renders metadata safely and preserves full original description',async()=>{
  const info={program:'<script>Program</script>',theme:'Tema',date_time:'4 Oktober',guests:[{name:'Rina',role:'Dosen'}],presenter:'Sari',description:'Full original\n<script>unsafe</script>'};
  const f=await fixture({info,segments,raw:'Original TXT',exports:['txt','srt','vtt']});
  assert.equal(f.get('reader-title').textContent,info.program);
  assert.match(f.get('program-info').textContent,/Rina — Dosen/);
  assert.equal(f.get('source-description').textContent,info.description);
  assert.equal(f.get('reader-content').hidden,false);
  assert.equal(f.get('transcript-exports').children.length,3);
  assert.equal(f.get('transcript-exports').children[2].href,'/api/recordings/old/transcript/vtt');
});
test('reader highlights search, navigates, switches modes and copies without further requests',async()=>{
  const data={info:{program:'Dialog',guests:[]},segments:[{start:1,end:2,text:'Halo halo'}],raw:'Halo halo\n',exports:['txt','srt']};
  const f=await fixture(data);
  f.get('transcript-search').value='halo';f.get('transcript-search').listeners.input();
  assert.equal(f.get('search-count').textContent,'1 / 2 matches');
  f.get('search-next').listeners.click();assert.equal(f.get('search-count').textContent,'2 / 2 matches');
  assert.ok(descendants(f.get('transcript-text')).some(node=>node.scrolled));
  f.get('search-next').listeners.click();assert.equal(f.get('search-count').textContent,'1 / 2 matches');
  f.get('timestamp-mode').value='none';f.get('timestamp-mode').listeners.change();
  assert.equal(f.get('transcript-text').textContent,'Halo halo');
  await f.get('copy-transcript').listeners.click();assert.equal(f.copies.at(-1),'Halo halo');
  f.get('timestamp-mode').value='subtitle';f.get('timestamp-mode').listeners.change();
  await f.get('copy-all').listeners.click();assert.equal(f.copies.at(-1),'Program: Dialog\n\nTranscript\n\n[00:00:01.000 → 00:00:02.000]\nHalo halo');
  f.get('raw-view').checked=true;f.get('raw-view').listeners.change();
  assert.equal(f.get('transcript-text').textContent,'Halo halo\n');
  await f.get('copy-transcript').listeners.click();assert.equal(f.copies.at(-1),data.raw);
  assert.deepEqual(f.requests,['/api/recordings/old/transcript/data']);
  assert.equal(data.segments[0].start,1);
});
test('reader displays unavailable metadata and disables absent timestamps for old jobs',async()=>{
  const f=await fixture({info:{program:'Old',guests:[],description:''},segments:[{start:null,end:null,text:'Legacy'}],raw:'Legacy',exports:['txt']});
  assert.match(f.get('program-info').textContent,/Tidak tersedia/);
  assert.match(f.get('source-description').textContent,/tidak tersedia/);
  assert.equal(f.get('timestamp-mode').disabled,true);
  assert.equal(f.get('transcript-text').textContent,'Legacy');
});
test('secondary and unknown sections stay out of compact metadata and raw description stays accessible',async()=>{
  const description='Produser: Ratna\nEditor:\nDina\nTema: Pendidikan';
  const f=await fixture({info:{program:'Dialog',theme:'Pendidikan',guests:[],secondary:{producer:'Ratna'},unknown_sections:[{heading:'Editor',content:'Dina'}],description},segments,raw:'TXT',exports:['txt']});
  assert.doesNotMatch(f.get('program-info').textContent,/Ratna|Dina|Editor|Produser/);
  assert.equal(f.get('source-description').textContent,description);
});
test('desktop player sizing is capped and centered without changing the 16:9 embed',()=>{
  const css=fs.readFileSync(require.resolve('../app/static/transcript.css'),'utf8');
  assert.match(css,/\.reader-shell \.reader-player\{max-width:680px;width:100%;margin:8px auto 12px;aspect-ratio:16\/9\}/);
});
test('reader workspace separates sticky sidebar from sticky tools and stacks on tablet',()=>{
  const html=fs.readFileSync(require.resolve('../app/static/transcript.html'),'utf8');
  const css=fs.readFileSync(require.resolve('../app/static/transcript.css'),'utf8');
  const sidebar=html.slice(html.indexOf('<aside'),html.indexOf('</aside>'));
  const tools=html.slice(html.indexOf('<div class="reader-tools">'),html.indexOf('<article'));
  assert.match(html,/id="reader-smooth-wrapper"[\s\S]*id="reader-smooth-content"[\s\S]*class="app-header reader-header"/);
  for(const id of ['reader-player','reader-title','program-info','full-description'])assert.ok(sidebar.includes('id="'+id+'"'));
  for(const id of ['transcript-search','timestamp-mode','copy-transcript','copy-info','copy-all','transcript-exports','raw-view'])assert.ok(tools.includes('id="'+id+'"'));
  assert.match(css,/grid-template-columns:minmax\(0,38fr\) minmax\(0,62fr\)/);
  assert.match(css,/\.reader-sidebar\{position:sticky;top:16px/);
  assert.match(css,/\.reader-tools\{position:sticky;top:16px/);
  assert.match(css,/@media\(max-width:1000px\)\{\.reader-workspace\{grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css,/\.reader-sidebar\{position:static;max-height:none;overflow:visible\}/);
  assert.match(css,/user-select:text/);
  assert.match(css,/\.reader-segment\+\.reader-segment\{border-top:0\}/);
  assert.match(css,/#reader-smooth-wrapper,#reader-smooth-content\{width:100%;min-height:100%\}/);
  assert.match(html,/<summary>More<\/summary>.*id="raw-view"/);
});
test('non-YouTube timestamps remain readable, exports omit absent files, and display changes make no requests',async()=>{
  const f=await fixture({info:{program:'Radio',guests:[]},segments:[{start:3665,end:3669,text:'Siaran panjang'}],raw:'TXT',exports:[]});
  assert.equal(f.get('transcript-text').children[0].children[0].textContent,'1:01:05');
  assert.equal(f.get('transcript-text').children[0].children[0].listeners.click,undefined);
  assert.equal(f.get('reader-export-menu').hidden,true);
  assert.equal(f.head.children.length,0);
  f.get('timestamp-mode').value='subtitle';f.get('timestamp-mode').listeners.change();
  assert.match(f.get('transcript-text').textContent,/1:01:05.000 → 1:01:09.000/);
  f.get('timestamp-mode').value='none';f.get('timestamp-mode').listeners.change();
  assert.equal(f.get('transcript-text').textContent,'Siaran panjang');
  assert.equal(f.requests.length,1);
});
test('reader shares compact shell and narrow toolbar gives search its own row',()=>{
  const html=fs.readFileSync(require.resolve('../app/static/transcript.html'),'utf8');
  const css=fs.readFileSync(require.resolve('../app/static/transcript.css'),'utf8');
  assert.match(html,/<body class="reader-page">/);
  assert.match(html,/<header class="app-header reader-header">/);
  assert.match(html,/<a class="reader-back" href="\/">← Workspace<\/a>/);
  const responsive=css.slice(css.indexOf('/* Keep the reader shell'));
  assert.match(responsive,/\.reader-tools \.reader-toolbar\{display:grid;grid-template-columns:minmax\(0,1fr\)/);
  assert.match(responsive,/\.reader-tools \.reader-search-controls button\{min-width:44px/);
  assert.match(responsive,/\.reader-raw-toggle input\{width:auto;flex:none\}/);
  assert.match(responsive,/env\(safe-area-inset-bottom\)/);
});
