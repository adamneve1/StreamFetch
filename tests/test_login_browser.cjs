// Opt-in real CSS/GSAP verification, using an installed Chromium browser and no dependencies.
// STREAMFETCH_TEST_BROWSER=/path/to/chromium node --test tests/test_login_browser.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const executable=process.env.STREAMFETCH_TEST_BROWSER;

test('real login renderer positions visible geometric shapes, animates/recycles them and cleans up across login/re-entry', {skip:!executable,timeout:45000},async t=>{
 const staticRoot=path.resolve(__dirname,'../app/static');
 const profile=fs.mkdtempSync(path.join(os.tmpdir(),'streamfetch-login-test-'));
 let browser,socket,session,id=0,loginAttempts=0;
 const pending=new Map();
 // Test-only auth responses; production frontend and GSAP are served unchanged.
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/api/')){
   const sessionRequest=url.pathname==='/api/session',loginRejected=url.pathname==='/api/login'&&++loginAttempts===1;
   const data=sessionRequest?{error:'Login required'}:loginRejected?{error:'Test login rejected'}:url.pathname==='/api/login'?{csrf:'test',is_admin:false}:
    url.pathname==='/api/sources'?{sources:[]}:url.pathname==='/api/status'?{active:null,online:true,queued:0,disk:{can_record:true,free:100,total:200}}:{recordings:[],total:0};
   res.writeHead(sessionRequest||loginRejected?401:200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));return;
  }
  const file=url.pathname==='/'?path.join(staticRoot,'index.html'):path.resolve(staticRoot,'.'+url.pathname.replace(/^\/static/,''));
  if(!file.startsWith(staticRoot+path.sep)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html');
  fs.createReadStream(file).on('error',()=>{res.writeHead(404);res.end();}).pipe(res);
 });
 t.after(async()=>{
  socket?.close();browser?.kill();
  if(browser?.exitCode===null)await new Promise(resolve=>{browser.once('exit',resolve);setTimeout(resolve,2000).unref();});
  await new Promise(resolve=>server.close(resolve));
  fs.rmSync(profile,{recursive:true,force:true});
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
 browser=spawn(executable,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
 const endpoint=await new Promise((resolve,reject)=>{
  let stderr='';const timeout=setTimeout(()=>reject(Error('Headless browser startup timed out')),15000);
  browser.stderr.on('data',data=>{stderr+=data;const match=stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(match){clearTimeout(timeout);resolve(match[1]);}});
  browser.once('error',error=>{clearTimeout(timeout);reject(error);});
  browser.once('exit',code=>{clearTimeout(timeout);reject(Error('Browser exited '+code+' '+stderr));});
 });
 socket=new WebSocket(endpoint);
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 socket.addEventListener('message',event=>{
  const data=JSON.parse(event.data),request=pending.get(data.id);if(!request)return;
  pending.delete(data.id);clearTimeout(request.timeout);data.error?request.reject(Error(JSON.stringify(data.error))):request.resolve(data.result);
 });
 socket.addEventListener('close',()=>{for(const request of pending.values()){clearTimeout(request.timeout);request.reject(Error('Browser disconnected'));}pending.clear();});
 const send=(method,params={})=>new Promise((resolve,reject)=>{
  const next=++id,timeout=setTimeout(()=>{pending.delete(next);reject(Error('Browser command timed out: '+method));},10000);
  pending.set(next,{resolve,reject,timeout});socket.send(JSON.stringify({id:next,method,params,...(session?{sessionId:session}:{})}));
 });
 const evaluate=async expression=>{
  const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value;
 };
 const wait=condition=>evaluate(`new Promise((resolve,reject)=>{const start=performance.now();const check=()=>{if(${condition})resolve(true);else if(performance.now()-start>5000)reject(Error('Condition timed out'));else setTimeout(check,20);};check();})`);
 const move=(x,y)=>send('Input.dispatchMouseEvent',{type:'mouseMoved',x,y});
 const snapshot=()=>evaluate(`(()=>{const layer=document.querySelector('.login-cursor-trail');return {hidden:layer.hidden,zIndex:getComputedStyle(layer).zIndex,
  count:layer.querySelectorAll('.login-geometry').length,particleHidden:layer.querySelector('.login-trail-particles').hidden,
  ambient:[...layer.querySelectorAll('.login-ambient span')].map(node=>({opacity:+getComputedStyle(node).opacity,loops:gsap.getTweensOf(node).filter(t=>t.repeat()===-1).length})),
  particles:[...layer.querySelectorAll('.login-trail-particles span')].map(node=>{const style=getComputedStyle(node),rect=node.getBoundingClientRect();return {shape:node.dataset.shape,opacity:+style.opacity,
   transform:style.transform,x:rect.x+rect.width/2,y:rect.y+rect.height/2,width:rect.width,height:rect.height,paint:getComputedStyle(node,'::before').borderTopColor,tweens:gsap.getTweensOf(node).length};}),
  contentZ:getComputedStyle(document.querySelector('#login-form')).zIndex};})()`);
 const target=await send('Target.createTarget',{url:'about:blank'});
 session=(await send('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
 await send('Page.enable');
 await send('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:'http://127.0.0.1:'+server.address().port+'/'});
 await wait("window.StreamFetchMotion && document.querySelectorAll('.login-geometry').length===26 && !document.querySelector('#login').hidden");
 await wait("getComputedStyle(document.querySelector('#login-credit')).opacity==='1' && getComputedStyle(document.querySelector('#password')).opacity==='1'");
 assert.equal(await evaluate("matchMedia('(pointer:fine) and (hover:hover)').matches"),true,'Desktop mouse/trackpad media query must be genuinely active');
 let state=await snapshot();assert.equal(state.count,26);assert.equal(state.zIndex,'0');assert.equal(state.contentZ,'2');assert.equal(state.hidden,false);
 assert.ok(state.ambient.every(node=>node.opacity>=.08&&node.opacity<=.2&&node.loops===1));
 assert.ok(state.particles.every(node=>node.opacity===0));
 await move(100,100);await move(220,160);
 // Inspect the GSAP start state before a frame can fade it; then verify a painted frame.
 const emitted=await snapshot(),visible=emitted.particles.filter(node=>node.opacity>.5);
 assert.ok(visible.length>=1&&visible.length<=4,'Pointer travel must produce visibly opaque shapes');
 assert.ok(visible.some(node=>Math.abs(node.x-220)<12&&Math.abs(node.y-160)<12),'A selected shape must be positioned at the pointer');
 assert.ok(visible.every(node=>node.width>=8&&node.height>=8&&node.transform!=='none'&&node.paint!=='rgba(0, 0, 0, 0)'));
 const changed=await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(getComputedStyle(document.querySelector('.login-trail-particles span')).transform))))`);
 assert.notEqual(changed,emitted.particles[0].transform,'The visible shape must actually move/rotate');
 if(process.env.STREAMFETCH_TEST_SCREENSHOT){const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(process.env.STREAMFETCH_TEST_SCREENSHOT,Buffer.from(shot.data,'base64'));}
 await wait("[...document.querySelectorAll('.login-trail-particles span')].every(node=>+getComputedStyle(node).opacity===0)");
 for(let i=0;i<12;i++){await move(100+i*20,160);await evaluate('new Promise(resolve=>setTimeout(resolve,26))');await move(520-i*10,180);}
 state=await snapshot();assert.equal(state.count,26);assert.ok(state.particles.every(node=>node.tweens<=1),'Reuse must kill the previous GSAP tween');
 // Directly over a non-interactive part of the card still emits; input/button do not.
 await wait("[...document.querySelectorAll('.login-trail-particles span')].every(node=>+getComputedStyle(node).opacity===0)");
 const card=await evaluate("(()=>{const r=document.querySelector('#login-form').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width};})()");
 await move(card.x+5,card.y+12);await move(card.x+card.width-5,card.y+12);
 assert.ok((await snapshot()).particles.some(node=>node.opacity>.5),'The login card must not suppress the trail');
 await wait("[...document.querySelectorAll('.login-trail-particles span')].every(node=>+getComputedStyle(node).opacity===0)");
 const input=await evaluate("(()=>{const r=document.querySelector('#password').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})()");
 await move(input.x+5,input.y+input.height/2);await move(input.x+input.width-5,input.y+input.height/2);
 assert.ok((await snapshot()).particles.every(node=>node.opacity===0),'Actual input interaction must not emit');
 await send('Emulation.setDeviceMetricsOverride',{width:600,height:850,deviceScaleFactor:1,mobile:false});
 await move(30,80);await move(250,80);assert.ok((await snapshot()).particles.some(node=>node.opacity>.5),'Narrow desktop/trackpad must not be width-gated');
 await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await wait("document.querySelector('.login-trail-particles').hidden");
 state=await snapshot();assert.ok(state.ambient.every(node=>node.loops===0&&node.opacity<=.08));assert.ok(state.particles.every(node=>node.tweens===0));
 await send('Emulation.setEmulatedMedia',{features:[]});await wait("!document.querySelector('.login-trail-particles').hidden");
 await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await wait("!matchMedia('(pointer:fine)').matches && document.querySelector('.login-trail-particles').hidden");
 await send('Emulation.setTouchEmulationEnabled',{enabled:false});await wait("!document.querySelector('.login-trail-particles').hidden");
 // Drive the existing auth handler with test-only responses, not a new flow.
 await evaluate("document.querySelector('#password').value='test-only';document.querySelector('#login-form').requestSubmit()");
 await wait("document.querySelector('#login-error').textContent==='Test login rejected' && /^M32 25a7/.test(document.querySelector('#login-glyph').getAttribute('d'))");
 assert.equal(await evaluate("document.querySelector('#password').value"),'test-only');
 state=await snapshot();assert.equal(state.hidden,false);assert.ok(state.ambient.every(node=>node.loops===1));
 await evaluate("document.querySelector('#login-form').requestSubmit()");
 await wait("document.querySelector('#login').hidden && document.querySelector('.login-cursor-trail').hidden");
 state=await snapshot();assert.ok(state.ambient.every(node=>node.loops===0));assert.ok(state.particles.every(node=>node.tweens===0));
 await move(30,80);await move(250,80);assert.ok((await snapshot()).particles.every(node=>node.opacity===0));
 await evaluate("document.querySelector('#logout').click()");await wait("!document.querySelector('#login').hidden");
 await evaluate('showLogin();showLogin()');state=await snapshot();assert.equal(state.count,26);assert.ok(state.ambient.every(node=>node.loops===1));
 assert.equal(await evaluate("document.querySelectorAll('.login-cursor-trail').length"),1);
 await move(30,80);await move(250,80);assert.ok((await snapshot()).particles.some(node=>node.opacity>.5));
});
