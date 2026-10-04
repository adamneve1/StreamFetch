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
  scrollIntoView(){this.scrolled=true;}
}
async function fixture(data){
  const html=fs.readFileSync(require.resolve('../app/static/transcript.html'),'utf8');
  const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],new Node()]));
  nodes.get('timestamp-mode').value='segment';
  let init;const copies=[],requests=[];
  const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:()=>new Node(),createTextNode:text=>{const node=new Node();node.textContent=text;return node;},addEventListener:(_,callback)=>init=callback},location:{pathname:'/api/recordings/old/transcript/view'},navigator:{clipboard:{writeText:async text=>copies.push(text)}},fetch:async url=>{requests.push(url);return {ok:true,json:async()=>data};}});
  vm.runInContext(fs.readFileSync(require.resolve('../app/static/transcript.js'),'utf8'),context);
  await init();
  return {get:id=>nodes.get(id),copies,requests};
}
function descendants(node){return node.children.flatMap(child=>[child,...descendants(child)]);}
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
