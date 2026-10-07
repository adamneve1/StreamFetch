const {test}=require('node:test');
const assert=require('node:assert/strict');
const createMotion=require('../app/static/motion.js');

function fixture(reduced=false,available=true,trailOptions){
 const flips=[],states=[],tweens=[],contexts=[],timelines=[],conversions=[],timers=new Map(),listeners={},created=[],sets=[],killedTargets=[];let timerId=0,current;
 const element=()=>({dataset:{},style:{},children:[],hidden:false,value:0,events:new Map(),eventAdds:[],
  addEventListener(type,fn,options){if(!this.events.has(type))this.events.set(type,new Set());this.events.get(type).add(fn);this.eventAdds.push({type,options});},
  removeEventListener(type,fn){this.events.get(type)?.delete(fn);},
  fire(type,event){for(const fn of [...this.events.get(type)||[]])fn(event);},
  setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
  append(node){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(child=>child!==node);node.parentNode=this;this.children.push(node);},remove(){this.removed=true;},
  querySelectorAll(selector){return selector==='input'?this.inputs||[]:[];},querySelector(selector){return this.queries?.[selector]||this.action;},
  cloneNode(){const clone=element(),cloneGlyph=element(),cloneRing=element();clone.action=element();clone.inputs=[element()];clone.inputs[0].value='not-to-be-retained';clone.queries={'.login-glyph':cloneGlyph,'.login-ring':cloneRing,'.login-logo':element(),'.login-surface':element(),'.login-brand span':element()};return clone;},
  getBoundingClientRect(){return {width:640,left:20,top:30};},offsetLeft:12,offsetTop:12});
 const panel=element(),content=element(),compact=element(),island=element(),bar=element(),job=element();
 const shell=element(),form=element(),brand=element(),label=element(),password=element(),button=element(),credit=element(),error=element(),workspace=element(),mark=element(),ring=element(),glyph=element(),wordmark=element(),surface=element(),body=element(),dialog=element(),dialogPanel=element();
 ring.tagName='circle';glyph.tagName='path';
 form.queries={'.login-brand':brand,label};
 const ids={'capture-content':content,'capture-another':compact,'login-form':form,password,'login-submit':button,'login-credit':credit,'login-mark':mark,'login-ring':ring,'login-glyph':glyph,'login-wordmark':wordmark,'login-surface':surface,login:shell};
 panel.dataset.collapsed='false';compact.hidden=true;island.hidden=true;
 island.children=[element(),element(),element()];
 const tween=(target,vars)=>{
  const t={target,vars,killed:false,kill(){this.killed=true;}};tweens.push(t);current?.animations.push(t);return t;
 };
 const media={matches:reduced,addEventListener(type,fn){listeners.media=fn;}};
 const trailMedia={matches:!!trailOptions&&!reduced&&trailOptions.fine!==false&&trailOptions.hover!==false&&trailOptions.mobile!==true,addEventListener(type,fn){listeners.trailMedia=fn;}};
 const gsap={registerPlugin(){},to:tween,fromTo(target,from,vars){const t=tween(target,vars);t.from=from;return t;},
  set(target,vars){sets.push({target,vars});},killTweensOf(target){killedTargets.push(target);const targets=Array.isArray(target)?target:[target];for(const t of tweens)if((Array.isArray(t.target)?t.target:[t.target]).some(node=>targets.includes(node)))t.kill();},
  timeline(vars){const timeline={vars,animations:[],killed:false,kill(){this.killed=true;for(const t of this.animations)t.kill();},to(target,vars,position){const t=tween(target,vars);t.position=position;this.animations.push(t);return this;},fromTo(target,from,vars,position){const t=tween(target,vars);t.from=from;t.position=position;this.animations.push(t);return this;}};timelines.push(timeline);return timeline;},
  context(fn){const c={animations:[],reverted:false,revert(){this.reverted=true;for(const t of this.animations)t.kill();}};
   contexts.push(c);current=c;fn();current=null;return c;}};
 const Flip={getState(targets,vars){const s={targets,vars,collapsed:panel.dataset.collapsed,phase:island.dataset.phase};states.push(s);return s;},
  from(state,vars){const t=tween(state.targets,vars);flips.push({state,vars,t});return t;}};
 const MorphSVGPlugin={convertToPath(target){target.tagName='path';conversions.push(target);return [target];}};
 const navigations=[],stored=new Map();
 const env={document:{getElementById:id=>ids[id],body,createElement(tag){const node=element();node.tagName=tag;created.push(node);return node;}},location:{assign:url=>navigations.push(url)},sessionStorage:{setItem:(key,value)=>stored.set(key,value),getItem:key=>stored.get(key),removeItem:key=>stored.delete(key)},
  gsap:available?gsap:null,Flip:available?Flip:null,MorphSVGPlugin:available?MorphSVGPlugin:null,matchMedia:query=>query.includes('pointer: fine')?trailMedia:media,
  setTimeout(fn){timers.set(++timerId,fn);return timerId;},clearTimeout(id){timers.delete(id);},
  addEventListener(type,fn){listeners[type]=fn;}};
 const motion=createMotion(env);
 const source=(collapsed,accepted=false)=>motion.source(panel,collapsed,()=>{
  panel.dataset.collapsed=String(collapsed);content.hidden=collapsed;compact.hidden=!collapsed;
 },{accepted,job});
 const status=(key,phase)=>motion.island(island,key,phase,()=>{island.hidden=false;island.detail=phase;});
 const tick=()=>{const callbacks=[...timers.values()];timers.clear();callbacks.forEach(fn=>fn());};
 return {motion,source,status,tick,flips,states,tweens,contexts,timelines,conversions,timers,listeners,media,trailMedia,created,sets,killedTargets,panel,content,compact,island,bar,job,Flip,shell,form,brand,label,password,button,credit,error,workspace,mark,ring,glyph,wordmark,surface,body,dialog,dialogPanel,navigations,stored};
}
const trailMove=(f,time=0,x=20,target={closest:()=>null},pointerType='mouse')=>f.shell.fire('pointermove',{timeStamp:time,clientX:x,clientY:40,target,pointerType,isPrimary:true});
const particleTweens=f=>f.tweens.filter(t=>f.created.includes(t.target)&&t.target.tagName==='span');
test('login cursor trail activates only on a visible login with a fine hover pointer',()=>{
 const f=fixture(false,true,{});
 assert.equal(f.created.length,0);assert.equal(f.shell.events.size,0);
 f.shell.hidden=true;f.motion.loginReveal(f.shell);assert.equal(f.created.length,0);
 f.shell.hidden=false;f.motion.loginReveal(f.shell);
 assert.equal(f.created.length,7);assert.equal(f.shell.children.length,1);
 const layer=f.shell.children[0];assert.equal(layer.children.length,6);assert.equal(layer['aria-hidden'],'true');assert.equal(layer.inert,true);
 assert.equal(f.shell.events.get('pointermove').size,1);assert.equal(f.shell.eventAdds[0].options.passive,true);
 trailMove(f);assert.equal(particleTweens(f).length,1);
 assert.equal(f.body.children.length,0);assert.equal(f.workspace.events.size,0);
 f.shell.hidden=true;trailMove(f,100,60);assert.equal(particleTweens(f).length,1);assert.equal(layer.hidden,true);
 assert.equal(f.shell.events.get('pointermove').size,0);
});
test('login cursor trail allocates no particles or pointer listeners for coarse, touch, mobile, reduced motion or missing GSAP',()=>{
 for(const f of [fixture(false,true,{fine:false}),fixture(false,true,{hover:false}),fixture(false,true,{mobile:true}),fixture(true,true,{}),fixture(false,false,{})]){
  f.motion.loginReveal(f.shell);assert.equal(f.created.length,0);assert.equal(f.shell.events.size,0);
 }
});
test('login cursor trail reuses six particles, throttles moves and animates transforms and opacity only',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);
 for(const node of [f.shell,f.form,...f.created])node.getBoundingClientRect=()=>{throw Error('No geometry reads during pointermove');};
 trailMove(f);trailMove(f,10,40);trailMove(f,100,21);assert.equal(particleTweens(f).length,1);
 for(let i=1;i<=12;i++)trailMove(f,i*100,i*40);
 const tweens=particleTweens(f);assert.equal(tweens.length,13);assert.equal(f.created.length,7);
 assert.equal(new Set(tweens.map(t=>t.target)).size,6);assert.equal(tweens[0].killed,true);
 assert.equal(f.killedTargets.filter(t=>t===tweens[0].target).length,3);
 for(const tween of tweens){
  assert.equal(tween.vars.duration,.42);assert.equal(tween.vars.opacity,0);assert.ok(tween.from.opacity<=.3);
  assert.deepEqual(Object.keys(tween.from).sort(),['opacity','scale','x','y']);
  assert.deepEqual(Object.keys(tween.vars).sort(),['duration','ease','opacity','scale','x','y']);
 }
});
test('login cursor trail suppresses the form and interactive controls and ignores touch events',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);
 for(const selector of ['.login-card','input','button','a','select','[contenteditable]']){
  trailMove(f,100,40,{closest(query){assert.ok(query.includes(selector));return {};}});
 }
 trailMove(f,100,40,undefined,'touch');assert.equal(particleTweens(f).length,0);
 trailMove(f,100,40);assert.equal(particleTweens(f).length,1);
});
test('successful login immediately removes cursor listeners and fades existing particles before the exit completes',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);trailMove(f);
 const particle=particleTweens(f)[0],layer=f.shell.children[0];f.motion.loginSuccess(f.workspace);
 assert.equal(layer.parentNode,f.body);assert.equal(layer.dataset.exiting,'true');
 assert.equal(particle.killed,true);assert.equal(f.shell.events.get('pointermove').size,0);assert.equal(f.shell.events.get('pointerleave').size,0);
 const clear=f.tweens.find(t=>Array.isArray(t.target)&&t.target[0]===layer.children[0]);assert.equal(clear.vars.duration,.12);
 trailMove(f,100,60);assert.equal(particleTweens(f).length,1);
 clear.vars.onComplete();assert.equal(layer.hidden,true);assert.equal(layer.parentNode,f.shell);assert.equal(layer.dataset.exiting,undefined);
 f.listeners.trailMedia();assert.equal(f.shell.events.get('pointermove').size,0);
});
test('rapid login/error/logout reinitialization reuses the pool and rejects stale fade callbacks',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);trailMove(f);const layer=f.shell.children[0];
 f.motion.loginError(f.error);assert.equal(f.shell.events.get('pointermove').size,1);
 f.motion.loginSuccess(f.workspace);const clear=f.tweens.find(t=>Array.isArray(t.target)&&t.target[0]===layer.children[0]);
 f.motion.reset();assert.equal(clear.killed,true);assert.equal(layer.hidden,true);
 f.motion.loginReveal(f.shell);f.motion.loginReveal(f.shell);
 clear.vars.onComplete();assert.equal(layer.hidden,false);assert.equal(f.created.length,7);
 assert.equal(f.shell.events.get('pointermove').size,1);assert.equal(f.shell.eventAdds.filter(e=>e.type==='pointermove').length,2);
 trailMove(f);assert.equal(particleTweens(f).length,2);
 f.motion.reset();assert.equal(f.shell.events.get('pointermove').size,0);assert.equal(particleTweens(f).at(-1).killed,true);
 assert.equal(f.sets.at(-1).vars.opacity,0);
});
test('login cursor trail responds to motion/device preference changes without reviving after login',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);trailMove(f);
 f.media.matches=true;f.listeners.media();assert.equal(f.shell.children[0].hidden,true);assert.equal(f.shell.events.get('pointermove').size,0);
 assert.equal(particleTweens(f)[0].killed,true);
 f.media.matches=false;f.listeners.media();assert.equal(f.shell.events.get('pointermove').size,1);
 f.trailMedia.matches=false;f.listeners.trailMedia();assert.equal(f.shell.events.get('pointermove').size,0);
 f.trailMedia.matches=true;f.listeners.trailMedia();assert.equal(f.shell.events.get('pointermove').size,1);
 f.motion.loginSuccess(f.workspace);f.listeners.trailMedia();assert.equal(f.shell.events.get('pointermove').size,0);
});
test('pagehide clears the login cursor trail and bfcache restores it only when login is visible',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);trailMove(f);
 f.listeners.pagehide();assert.equal(f.shell.events.get('pointermove').size,0);assert.equal(f.shell.children[0].hidden,true);
 f.listeners.pageshow({persisted:true});assert.equal(f.shell.events.get('pointermove').size,1);assert.equal(f.created.length,7);
 f.shell.hidden=true;f.listeners.pagehide();f.listeners.pageshow({persisted:true});assert.equal(f.shell.events.get('pointermove').size,0);
});
test('cursor particles remain independent of the existing MorphSVG login choreography',()=>{
 const f=fixture(false,true,{});f.motion.loginReveal(f.shell);assert.equal(f.tweens.length,7);assert.deepEqual(f.conversions,[f.ring]);
 const entrance=f.timelines[0];trailMove(f);const particle=particleTweens(f)[0];
 f.motion.loginInteract('submit');assert.equal(particle.killed,false);const loading=f.timelines.find(t=>t.animations.some(a=>a.vars.repeat===-1));
 assert.ok(f.tweens.some(t=>t.target===f.glyph&&t.vars.morphSVG?.shape.includes('V28a7')));
 assert.ok(f.tweens.some(t=>t.target===f.ring&&t.vars.repeat===-1));
 f.motion.loginError(f.error);assert.equal(loading.killed,true);assert.equal(particle.killed,false);
 f.timelines.at(-2).vars.onComplete();assert.match(f.glyph.d,/M32 25a7/);
 entrance.vars.onComplete();assert.equal(f.shell.children[0].hidden,false);
 f.motion.loginSuccess(f.workspace);assert.equal(particle.killed,true);
 assert.ok(f.tweens.some(t=>t.vars.morphSVG?.shape.includes('17-18')));
});
test('Source admission snapshots before mutation, morphs surface, and keeps snapshot inert',()=>{
 const f=fixture();f.source(true,true);
 assert.equal(f.states[0].collapsed,'false');assert.equal(f.panel.dataset.collapsed,'true');
 assert.equal(f.flips.length,1);assert.equal(f.flips[0].vars.duration,.48);
 const snapshot=f.panel.children[0];assert.equal(snapshot.inert,true);assert.equal(snapshot['aria-hidden'],'true');
 assert.equal(snapshot.action.textContent,'Diterima ✓');
 f.flips[0].vars.onComplete();assert.equal(snapshot.removed,true);
 assert.equal(f.panel.dataset.motion,undefined);assert.equal(f.contexts[0].reverted,true);
});
test('Source reopens through the same Flip surface without stale inline styles',()=>{
 const f=fixture();f.source(true,true);f.source(false);
 assert.equal(f.flips.length,2);assert.equal(f.states[1].collapsed,'true');
 assert.equal(f.content.hidden,false);assert.equal(f.compact.hidden,true);
 assert.equal(f.contexts[0].reverted,true);assert.equal(f.panel.children[0].removed,true);
 f.flips[1].vars.onComplete();assert.equal(f.panel.dataset.motion,undefined);
});
test('rapid repeated Source interaction replaces motion and ignores obsolete completion',()=>{
 const f=fixture();f.source(true,true);f.source(false);f.source(true,true);
 assert.equal(f.flips[0].t.killed,true);assert.equal(f.flips[1].t.killed,true);
 f.flips[0].vars.onComplete();assert.equal(f.panel.dataset.motion,'true');
 f.flips[2].vars.onComplete();assert.equal(f.panel.dataset.motion,undefined);
 f.source(true);assert.equal(f.flips.length,3);
});
test('island structural phases Flip the persistent object and its content positions',()=>{
 const f=fixture();
 for(const phase of ['starting','queued','downloading','processing','transcribing','completed','failed'])f.status('job',phase);
 assert.equal(f.flips.length,7);assert.equal(f.island.dataset.phase,'failed');
 assert.equal(f.flips[0].state.targets[0],f.island);assert.equal(f.flips[0].state.targets.length,4);
 assert.equal(f.states[1].phase,'starting');assert.equal(f.timers.size,0);
});
test('progress and ETA-only polling never start a full Flip and replace progress tweens',()=>{
 const f=fixture();f.status('job','downloading');f.motion.progress(f.bar,20);
 const first=f.tweens.at(-1);f.motion.progress(f.bar,40);f.status('job','downloading');
 assert.equal(f.flips.length,1);assert.equal(first.killed,true);assert.equal(f.bar['aria-valuenow'],40);
 const count=f.tweens.length;f.motion.progress(f.bar,40);assert.equal(f.tweens.length,count);
 f.motion.progress(f.bar,null);assert.equal(f.bar.value,undefined);assert.equal(f.bar['aria-valuenow'],undefined);
});
test('interrupted island transitions revert their own styles and stale callbacks cannot settle successors',()=>{
 const f=fixture();f.status('job','queued');f.status('job','processing');
 assert.equal(f.states[1].vars.kill,false);assert.equal(f.contexts[0].reverted,true);
 f.flips[0].vars.onComplete();assert.equal(f.island.dataset.motion,'true');
 f.flips[1].vars.onComplete();assert.equal(f.island.dataset.motion,undefined);
});
test('completed island holds once then dismisses without polling resurrecting it',()=>{
 const f=fixture();f.status('job','completed');f.status('job','completed');
 assert.equal(f.timers.size,1);assert.equal(f.island.hidden,false);f.tick();
 assert.equal(f.island.hidden,true);f.status('job','completed');assert.equal(f.island.hidden,true);
 f.status('next','queued');assert.equal(f.island.hidden,false);
});
test('failed remains visible and cancels obsolete success dismissal',()=>{
 const f=fixture();f.status('job','completed');const old=[...f.timers.values()][0];
 f.status('job','failed');old();f.tick();assert.equal(f.island.hidden,false);assert.equal(f.island.dataset.phase,'failed');
});
test('reduced motion preserves Source, progress, and completion dismissal with no animation',()=>{
 const f=fixture(true);f.source(true,true);f.source(false);f.status('job','completed');f.motion.progress(f.bar,75);
 assert.equal(f.flips.length,0);assert.equal(f.tweens.length,0);assert.equal(f.content.hidden,false);assert.equal(f.bar.value,75);
 f.tick();assert.equal(f.island.hidden,true);
});
test('changing reduced-motion preference or resizing immediately settles in-flight geometry',()=>{
 const f=fixture();f.source(true,true);f.status('job','downloading');f.motion.progress(f.bar,60);
 f.media.matches=true;f.listeners.media();
 assert.equal(f.panel.dataset.motion,undefined);assert.equal(f.island.dataset.motion,undefined);assert.equal(f.bar.value,60);
 f.media.matches=false;f.source(false);f.listeners.resize();assert.equal(f.panel.dataset.motion,undefined);
});
test('reset removes pending dismissals and detached snapshots; missing libraries preserve behavior',()=>{
 const f=fixture();f.source(true,true);f.status('job','completed');f.motion.reset();
 assert.equal(f.timers.size,0);assert.equal(f.panel.children[0].removed,true);
 f.status('job','completed');assert.equal(f.island.hidden,false);
 const unavailable=fixture(false,false);unavailable.source(true,true);unavailable.status('job','failed');
 assert.equal(unavailable.content.hidden,true);assert.equal(unavailable.island.hidden,false);assert.equal(unavailable.flips.length,0);
});
test('a failed geometry read cannot turn accepted admission into a rejected submission',()=>{
 const f=fixture();f.Flip.getState=()=>{throw Error('Motion unavailable');};
 f.source(true,true);f.status('job','failed');
 assert.equal(f.content.hidden,true);assert.equal(f.island.hidden,false);assert.equal(f.flips.length,0);
});
test('login signature converts and draws the ring, morphs only the glyph, and settles within 820ms',()=>{
 const f=fixture();f.motion.loginReveal(f.shell);
 assert.equal(f.flips.length,0);assert.equal(f.tweens.length,7);assert.deepEqual(f.conversions,[f.ring]);
 const [mark,draw,glyph,wordmark,surface,fields,credit]=f.tweens;
 assert.equal(mark.target,f.mark);assert.equal(mark.from.scale,.88);assert.ok(mark.from.x<=10);
 assert.equal(draw.target,f.ring);assert.equal(draw.from.strokeDasharray,'0 114');assert.equal(draw.vars.strokeDasharray,'114 0');
 assert.equal(glyph.target,f.glyph);assert.match(glyph.from.morphSVG.shape,/C28 26/);assert.equal(glyph.vars.morphSVG.shape,f.glyph.d);
 assert.equal(f.tweens.some(t=>t.vars.repeat||t.vars.yoyo),false);
 assert.equal(wordmark.from.clipPath,'inset(0 100% 0 0)');
 assert.equal(surface.target,f.surface);assert.match(surface.from.clipPath,/72%/);
 assert.equal(fields.from.y,4);assert.equal(fields.vars.stagger,f.motion.tokens.micro/4);
 assert.deepEqual(fields.target,[f.label,f.password,f.button]);
 assert.equal(credit.target,f.credit);assert.ok(Math.abs(credit.position+credit.vars.duration-f.motion.tokens.reveal)<.001);
 f.timelines[0].vars.onComplete();assert.equal(f.contexts[0].reverted,true);
});
test('login errors settle entrance, animate feedback without shake, and replace stale error motion',()=>{
 const f=fixture();f.motion.loginReveal(f.shell);f.motion.loginError(f.error);
 assert.equal(f.contexts[0].reverted,true);const old=f.tweens.at(-1),oldTimeline=f.timelines.at(-1);
 assert.equal(old.from.y,3);assert.equal(old.vars.duration,.16);assert.equal(old.vars.x,undefined);
 f.motion.loginError(f.error);assert.equal(old.killed,true);
 oldTimeline.vars.onComplete();assert.equal(f.contexts.at(-1).reverted,false);
 f.timelines.at(-1).vars.onComplete();assert.equal(f.contexts.at(-1).reverted,true);
});
test('success settles immediately without promise, timer, or architecture change',()=>{
 const f=fixture();f.motion.loginReveal(f.shell);f.motion.loginError(f.error);
 const result=f.motion.loginSuccess(f.workspace);
 assert.equal(result,undefined);assert.equal(f.timers.size,0);
 assert.equal(f.contexts[1].reverted,true);
 const ghost=f.body.children[0];assert.equal(ghost.inert,true);assert.equal(ghost['aria-hidden'],'true');assert.ok(!ghost.inputs[0].value);
 assert.equal(f.tweens.find(t=>t.target===f.workspace).vars.duration,.2);
 const check=f.tweens.find(t=>t.vars.morphSVG?.shape?.includes('17-18'));assert.ok(check);assert.equal(check.vars.duration,.32);
 assert.equal(f.tweens.at(-1).position+f.tweens.at(-1).vars.duration,.4);
 f.motion.reset();assert.equal(f.contexts.at(-1).reverted,true);
 assert.equal(ghost.removed,true);
});
test('typing or pointer interaction immediately settles signature motion without gating the password',()=>{
 const f=fixture();f.motion.loginReveal(f.shell);const entrance=[...f.tweens];f.motion.loginInteract();
 assert.equal(f.contexts[0].reverted,true);assert.equal(entrance.every(t=>t.killed),true);
 assert.equal(f.password.hidden,false);assert.equal(f.password.disabled,undefined);
});
test('login reduced motion and unavailable GSAP preserve visible, immediately usable controls',()=>{
 for(const f of [fixture(true),fixture(false,false)]){
  f.motion.loginReveal(f.shell);assert.match(f.glyph.d,/M32 25a7/);
  f.motion.loginInteract('submit');assert.match(f.glyph.d,/V28a7/);
  f.motion.loginError(f.error);assert.match(f.glyph.d,/M32 25a7/);
  f.motion.loginSuccess(f.workspace);assert.match(f.glyph.d,/17-18/);
  assert.equal(f.tweens.length,0);assert.equal(f.flips.length,0);assert.equal(f.form.hidden,false);
 }
 const f=fixture();f.motion.loginReveal(f.shell);f.media.matches=true;f.listeners.media();
 assert.equal(f.contexts[0].reverted,true);assert.equal(f.tweens.every(t=>t.killed),true);
});

test('motion tokens provide shared bounded timing and restrained easing',()=>{
 const {tokens}=fixture().motion;assert.equal(Object.isFrozen(tokens),true);
 assert.ok(tokens.micro>=.12&&tokens.micro<=.18);assert.ok(tokens.navigation>=.28&&tokens.navigation<=.42);assert.ok(tokens.reveal>=.5&&tokens.reveal<=.8);
 assert.equal(tokens.ease,'power3.out');assert.equal(tokens.source,.44);
});
test('typing is a deduplicated state response, never character or form movement',()=>{
 const f=fixture();f.motion.loginInteract('focus');f.motion.loginInteract('typing');const count=f.timelines.length;
 for(let n=0;n<30;n++)f.motion.loginInteract('typing');assert.equal(f.timelines.length,count);
 assert.equal(f.tweens.some(t=>t.target===f.form||t.target===f.password),false);
 assert.equal(f.tweens.at(-2).vars.scale,1.025);assert.equal(f.tweens.at(-1).vars.scale,1);
 f.motion.loginInteract('submit');const submitted=f.timelines.length;f.motion.loginInteract('typing');assert.equal(f.timelines.length,submitted);
 f.motion.loginError(f.error);const errors=f.timelines.length;f.motion.loginInteract('focus');assert.equal(f.timelines.length,errors);
 f.motion.loginInteract('typing');assert.equal(f.timelines.length,errors+1);
});
test('submit, error, and success icon morphs are restrained and interruption-safe',()=>{
 const f=fixture();f.motion.loginInteract('submit');
 const loading=f.tweens.find(t=>t.target===f.glyph&&t.vars.morphSVG);assert.match(loading.vars.morphSVG.shape,/V28a7/);
 const spinner=f.tweens.find(t=>t.target===f.ring&&t.vars.repeat===-1);assert.ok(spinner);
 f.motion.loginError(f.error);assert.equal(loading.killed,true);assert.equal(spinner.killed,true);
 const normal=f.tweens.findLast(t=>t.target===f.glyph&&t.vars.morphSVG);assert.match(normal.vars.morphSVG.shape,/M32 25a7/);
 f.motion.loginInteract('typing');f.motion.loginInteract('submit');const stale=f.tweens.findLast(t=>t.target===f.ring&&t.vars.repeat===-1);
 f.motion.loginSuccess(f.workspace);assert.equal(stale.killed,true);assert.ok(f.tweens.some(t=>t.vars.morphSVG?.shape?.includes('17-18')));
});
test('manual Source collapse preserves a visual snapshot and interpolates padding, not height alone',()=>{
 const f=fixture();f.source(true);assert.equal(f.flips[0].vars.duration,f.motion.tokens.source);
 assert.match(f.states[0].vars.props,/padding/);assert.ok(f.panel.children[0].inert);
 assert.notEqual(f.panel.children[0].action.textContent,'Diterima ✓');
 const out=f.tweens.find(t=>t.vars.y===-6);assert.ok(out);assert.equal(out.vars.duration,f.motion.tokens.micro*1.5);
 assert.equal(f.tweens.some(t=>t.vars.height!==undefined),false);
});
test('expanded Source controls reveal in order and focus only after current geometry settles',()=>{
 const f=fixture();f.source(true);let focused=0;
 f.motion.source(f.panel,false,()=>{f.panel.dataset.collapsed='false';f.content.hidden=false;},{onSettled:()=>focused++});
 assert.equal(focused,0);const incoming=f.tweens.find(t=>Array.isArray(t.target)&&t.from?.y===6);assert.ok(incoming);assert.equal(incoming.vars.stagger,.018);
 f.flips[0].vars.onComplete();assert.equal(focused,0);f.flips[1].vars.onComplete();assert.equal(focused,1);
});
test('platform switching Flips shared geometry and directionally stages only entering/leaving fields',()=>{
 const f=fixture();let changed=0;
 f.motion.sourceMode(f.panel,()=>{changed++;f.compact.style.left='48px';},{currentTab:f.button,nextTab:f.job,indicator:f.compact,outgoing:f.island,incoming:f.bar,shared:[f.content],direction:1});
 assert.equal(changed,1);assert.deepEqual(f.states[0].targets,[f.panel,f.content,f.compact,f.island,f.bar]);
 assert.equal(f.flips[0].vars.duration,f.motion.tokens.navigation);assert.equal(f.flips[0].vars.ease,f.motion.tokens.settle);assert.equal(f.flips[0].vars.absoluteOnLeave,true);
 f.flips[0].vars.onLeave([f.island]);let outgoing=f.tweens.at(-1);assert.equal(outgoing.target[0],f.island);assert.equal(outgoing.vars.x,-12);assert.equal(outgoing.vars.scale,.985);assert.equal(outgoing.vars.stagger,.018);
 f.flips[0].vars.onEnter([f.bar]);let incoming=f.tweens.at(-1);assert.equal(incoming.target[0],f.bar);assert.equal(incoming.from.x,12);assert.equal(incoming.vars.x,0);assert.equal(incoming.vars.stagger,.018);
 assert.equal(f.tweens.some(t=>t.target===f.button),false);assert.ok(f.tweens.some(t=>t.target===f.job&&t.vars.scale===.985));
});
test('rapid platform switches replace stale Flip/press motion and keep the latest DOM state',()=>{
 const f=fixture();let value='oryx';const options={currentTab:f.button,nextTab:f.job,indicator:f.compact,outgoing:f.island,incoming:f.bar,shared:[f.content],direction:1};
 f.motion.sourceMode(f.panel,()=>value='youtube',options);const first=f.flips[0],firstContext=f.contexts[0];
 f.motion.sourceMode(f.panel,()=>value='instagram',{...options,direction:-1});assert.equal(value,'instagram');assert.equal(first.t.killed,true);assert.equal(firstContext.reverted,true);
 first.vars.onComplete();assert.equal(f.panel.dataset.motion,'true');f.flips[1].vars.onComplete();assert.equal(f.panel.dataset.motion,undefined);
 f.motion.sourcePress(f.job);const press=f.timelines.at(-1);f.motion.sourcePress(f.button);assert.equal(press.animations.every(t=>t.killed),true);
});
test('reduced-motion platform switching applies final state immediately without Flip',()=>{
 const f=fixture(true);let value='oryx';f.motion.sourceMode(f.panel,()=>value='youtube',{nextTab:f.job,outgoing:f.island,incoming:f.bar,direction:-1});
 assert.equal(value,'youtube');assert.equal(f.flips.length,0);assert.equal(f.timelines.length,0);f.motion.sourcePress(f.job);assert.equal(f.timelines.length,0);
});
function follow(f,href='/next',target=''){
 const link={href,target},event={button:0,preventDefault(){this.defaultPrevented=true;}};
 f.motion.followLink(link,event,{shell:f.workspace,origin:f.wordmark,row:f.job});return event;
}
test('same-document context change applies immediately with coordinated title/content reveal and no Flip',()=>{
 const f=fixture();let changed=false;f.motion.contextChange(f.workspace,f.wordmark,()=>changed=true,()=>[f.job]);
 assert.equal(changed,true);assert.equal(f.flips.length,0);assert.equal(f.tweens[0].vars.duration,f.motion.tokens.navigation);
 assert.equal(f.body.children[0].inert,true);f.timelines.at(-1).vars.onComplete();assert.equal(f.body.children[0].removed,true);
});
test('same-tab navigation anchors title then uses normal navigation with a failsafe and return reveal',()=>{
 const f=fixture();const event=follow(f);assert.equal(event.defaultPrevented,true);assert.equal(f.navigations.length,0);
 assert.equal(f.body.children[0].inert,true);assert.equal(f.tweens[0].vars.y,-4);assert.equal(f.flips.length,0);
 f.tick();assert.deepEqual(f.navigations,['/next']);assert.equal(f.body.children[0].removed,true);
 f.motion.returnReveal(f.workspace,f.wordmark,[f.job]);assert.equal(f.tweens.at(-1).from.y,-6);assert.equal(f.stored.size,0);
});
test('existing new-tab transcript action stays native; modifier clicks stay native and unanimated',()=>{
 const f=fixture();assert.equal(follow(f,'/reader','_blank').defaultPrevented,undefined);f.tick();assert.equal(f.navigations.length,0);
 const count=f.timelines.length;f.motion.followLink({href:'/reader'},{ctrlKey:true},{shell:f.workspace});assert.equal(f.timelines.length,count);
});
test('rapid navigation replaces old destination and obsolete completion cannot navigate',()=>{
 const f=fixture();follow(f,'/old');const old=f.timelines.at(-1);follow(f,'/new');old.vars.onComplete();assert.equal(f.navigations.length,0);
 f.timelines.at(-1).vars.onComplete();f.tick();assert.deepEqual(f.navigations,['/new']);
});
test('reduced motion/resize settle pending navigation; pagehide cancels and bfcache leaves no stale styles',()=>{
 const f=fixture();follow(f);f.media.matches=true;f.listeners.media();assert.deepEqual(f.navigations,['/next']);
 const r=fixture(true);assert.equal(follow(r).defaultPrevented,undefined);r.motion.pageReveal(r.workspace,r.wordmark,[r.job]);assert.equal(r.tweens.length,0);
 const b=fixture();follow(b);b.listeners.pagehide();b.tick();assert.equal(b.navigations.length,0);assert.equal(b.body.children[0].removed,true);
 b.listeners.pageshow({persisted:true});assert.equal(b.contexts.at(-1).reverted,true);
 const resized=fixture();follow(resized);resized.listeners.resize();assert.deepEqual(resized.navigations,['/next']);
});
test('reduced-motion Source expansion focuses immediately without creating snapshots',()=>{
 const f=fixture(true);f.source(true);let focused=false;f.motion.source(f.panel,false,()=>f.content.hidden=false,{onSettled:()=>focused=true});
 assert.equal(focused,true);assert.equal(f.panel.children.length,0);assert.equal(f.flips.length,0);
});
test('dialog motion fades the backdrop, enters sharply, and ignores an interrupted close',()=>{
 const f=fixture();let closed=0;f.motion.dialogOpen(f.dialog,f.dialogPanel);
 const opening=f.timelines.at(-1);assert.equal(opening.animations.length,2);
 assert.equal(opening.animations[0].from['--dialog-backdrop-opacity'],0);assert.equal(opening.animations[0].vars['--dialog-backdrop-opacity'],.28);
 assert.equal(opening.animations[1].from.y,8);assert.equal(opening.animations[1].from.scale,.985);assert.equal(opening.animations[1].vars.duration,.2);
 f.motion.dialogClose(f.dialog,f.dialogPanel,()=>closed++);const staleClose=f.timelines.at(-1);
 assert.equal(opening.animations.every(t=>t.killed),true);assert.equal(staleClose.animations[1].vars.y,5);assert.equal(staleClose.animations[1].vars.duration,.2);
 f.motion.dialogOpen(f.dialog,f.dialogPanel);assert.equal(staleClose.animations.every(t=>t.killed),true);
 staleClose.vars.onComplete();assert.equal(closed,0);
 f.motion.dialogClose(f.dialog,f.dialogPanel,()=>closed++);f.timelines.at(-1).vars.onComplete();assert.equal(closed,1);
});
test('dialog reduced motion closes immediately without creating animation',()=>{
 const f=fixture(true);let closed=false;f.motion.dialogOpen(f.dialog,f.dialogPanel);f.motion.dialogClose(f.dialog,f.dialogPanel,()=>closed=true);
 assert.equal(closed,true);assert.equal(f.timelines.length,0);
});
test('Flip completion fired during context revert cannot recursively revert or focus stale Source',()=>{
 const f=fixture();let focused=0;f.motion.source(f.panel,true,()=>f.panel.dataset.collapsed='true',{onSettled:()=>focused++});
 const context=f.contexts[0],original=context.revert.bind(context);let reverted=0;
 context.revert=()=>{reverted++;f.flips[0].vars.onComplete();original();};
 f.source(false);assert.equal(reverted,1);assert.equal(focused,0);assert.equal(f.panel.dataset.collapsed,'false');
});
test('changing reduced motion mid-expansion settles geometry before deferred focus',()=>{
 const f=fixture();f.source(true);let focused=0;f.motion.source(f.panel,false,()=>{f.panel.dataset.collapsed='false';},{onSettled:()=>focused++});
 f.media.matches=true;f.listeners.media();assert.equal(f.panel.dataset.motion,undefined);assert.equal(focused,1);
 f.flips.at(-1).vars.onComplete();assert.equal(focused,1);
});
test('toast motion owns each surface and interrupted enter/exit callbacks cannot affect successors',()=>{
 const f=fixture();let dismissed=0;
 const cancel=f.motion.toast(f.dialogPanel,true),entry=f.timelines.at(-1);
 assert.equal(entry.animations[0].from.y,5);assert.equal(entry.animations[0].vars.duration,.16);
 cancel();assert.equal(entry.animations[0].killed,true);
 f.motion.toast(f.dialogPanel,false,()=>dismissed++);const exit=f.timelines.at(-1);
 f.motion.toast(f.dialogPanel,true);exit.vars.onComplete();assert.equal(dismissed,0);
 f.motion.toast(f.job,false,()=>dismissed++);f.timelines.at(-1).vars.onComplete();assert.equal(dismissed,1);
 assert.equal(f.timelines.at(-2).animations[0].killed,false);
});
test('reduced motion preserves toast completion and immediate filters without animations',()=>{
 const f=fixture(true);let dismissed=0;
 f.motion.toast(f.job,true);f.motion.toast(f.job,false,()=>dismissed++);f.motion.historyFilter(f.panel);
 assert.equal(dismissed,1);assert.equal(f.timelines.length,0);
 const changed=fixture();changed.motion.toast(changed.job,false,()=>dismissed++);
 changed.media.matches=true;changed.listeners.media();assert.equal(dismissed,2);
 changed.timelines.at(-1).vars.onComplete();assert.equal(dismissed,2);
});
test('rapid History filters settle their own styles and leave the Dynamic Island untouched',()=>{
 const f=fixture();f.status('job','downloading');const islandContext=f.contexts.at(-1);
 f.motion.historyFilter(f.panel);const first=f.timelines.at(-1);f.motion.historyFilter(f.panel);
 assert.equal(first.animations[0].killed,true);assert.equal(islandContext.reverted,false);
 assert.equal(f.flips.length,1);assert.equal(f.timelines.at(-1).animations[0].vars.duration,.16);
});
test('opening the palette cannot interrupt an unrelated Watch form closing callback',()=>{
 const f=fixture();let watchClosed=0;
 f.motion.dialogClose(f.panel,f.content,()=>watchClosed++);const watchExit=f.timelines.at(-1);
 f.motion.dialogOpen(f.dialog,f.dialogPanel);
 assert.equal(watchExit.animations.some(t=>t.killed),false);watchExit.vars.onComplete();assert.equal(watchClosed,1);
 assert.equal(f.timelines.at(-1).animations.some(t=>t.killed),false);
});
