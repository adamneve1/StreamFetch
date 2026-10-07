/* Restrained choreography; ordinary hover/focus/press states stay in CSS. */
(function(root, factory) {
 if(typeof module==='object'&&module.exports)module.exports=factory;
 else root.StreamFetchMotion=factory(root);
})(typeof window==='undefined'?{}:window, function(env) {
 const {gsap,Flip,MorphSVGPlugin}=env, media=env.matchMedia?.('(prefers-reduced-motion: reduce)');
 const available=!!(gsap&&Flip), regions=new Map(), progressTweens=new Map();
 const toastKeys=new WeakMap();let toastId=0;
 const dialogKeys=new WeakMap();let dialogId=0;
 let islandKey='',dismissedKey='',dismissTimer;
 let authState='idle',pendingNavigation,loginMarkMotion;
 const trailMedia=env.matchMedia?.('(pointer: fine) and (hover: hover) and (prefers-reduced-motion: no-preference)');
 let loginTrailShell=null,loginTrail;
 const tokens=Object.freeze({micro:.16,navigation:.32,reveal:.8,geometry:.38,source:.44,accepted:.48,sourceMobile:.36,acceptedMobile:.4,ease:'power3.out',settle:'power2.inOut'});
 const loginShapes=Object.freeze({
  normal:'M32 25a7 7 0 1 1 0 14 7 7 0 1 1 0-14Z',
  entry:'M24 32C28 26 30 23 32 23C34 23 36 26 40 32C36 38 34 41 32 41C30 41 28 38 24 32Z',
  loading:'M25 31V28a7 7 0 0 1 14 0v3h2v12H23V31h2Zm4 0h6v-3a3 3 0 0 0-6 0v3Z',
  success:'M20 32l7 7 17-18 4 4-21 22-11-11 4-4Z',
 });
 const phone=()=>!!env.matchMedia?.('(max-width:600px)').matches;
 if(gsap&&Flip)gsap.registerPlugin(Flip);
 if(gsap&&MorphSVGPlugin)gsap.registerPlugin(MorphSVGPlugin);
 const reduced=()=>!!media?.matches;
 const trailShapes=['circle','diamond','plus','square'];
 // Deliberate negative space around the form; no randomly placed confetti.
 const ambientShapes=[
  [9,18,42,'circle',.12,14,-18,14], [28,9,22,'diamond',.16,-16,12,18],
  [66,13,34,'square',.1,18,16,16], [89,30,26,'plus',.16,-12,-20,12],
  [13,74,32,'diamond',.14,16,-12,20], [37,88,18,'plus',.12,-18,-14,15],
  [72,82,48,'circle',.08,14,-20,19], [94,68,26,'square',.18,-18,12,13],
  [5,48,20,'square',.12,12,18,17], [80,52,22,'diamond',.1,-14,-16,11],
 ];
 function resetTrailTravel(trail){trail.lastX=null;trail.lastTime=0;trail.lastEmit=-Infinity;trail.distance=0;}
 function detachLoginTrail(trail){
  trail.shell.removeEventListener('pointermove',trail.move);trail.shell.removeEventListener('pointerleave',trail.leave);
  trail.active=false;resetTrailTravel(trail);
 }
 function parkLoginTrail(trail){
  trail.layer.hidden=true;delete trail.layer.dataset.exiting;
  if(trail.layer.parentNode!==trail.shell)trail.shell.append(trail.layer);
 }
 function stopLoginTrail(fade=false){
  const trail=loginTrail;if(!trail)return;
  detachLoginTrail(trail);trail.ambientAnimated=false;
  const generation=++trail.generation;
  trail.clearTween?.kill();trail.clearTween=null;gsap?.killTweensOf(trail.decorations);
  if(fade&&gsap&&!reduced()&&!trail.layer.hidden){
   // Like the logo exit snapshot, let existing particles finish after login hides.
   // No listener survives this move, so the workspace can never spawn a trail.
   trail.layer.dataset.exiting='true';env.document.body.append(trail.layer);
   trail.clearTween=gsap.to(trail.decorations,{opacity:0,scale:.3,duration:.2,ease:tokens.ease,onComplete:()=>{
    if(trail.generation!==generation)return;
    trail.clearTween=null;parkLoginTrail(trail);
   }});
  }else{gsap?.set(trail.decorations,{opacity:0});parkLoginTrail(trail);}
 }
 function syncLoginTrail(){
  const shell=loginTrailShell;
  if(!shell||shell.hidden){stopLoginTrail();return;}
  if(!loginTrail){
   const layer=env.document.createElement('div');layer.className='login-cursor-trail';layer.setAttribute('aria-hidden','true');layer.inert=true;layer.hidden=true;
   const ambientLayer=env.document.createElement('div');ambientLayer.className='login-ambient';
   const particleLayer=env.document.createElement('div');particleLayer.className='login-trail-particles';
   layer.append(ambientLayer);layer.append(particleLayer);
   const ambient=ambientShapes.map(([left,top,size,shape,opacity])=>{
    const node=env.document.createElement('span');node.className='login-geometry';node.dataset.shape=shape;
    Object.assign(node.style,{left:left+'%',top:top+'%',width:size+'px',height:size+'px',opacity});ambientLayer.append(node);return node;
   });
   const particles=Array.from({length:16},(_,index)=>{const particle=env.document.createElement('span');particle.className='login-geometry';particle.dataset.shape=trailShapes[index%4];particleLayer.append(particle);return particle;});
   const trail=loginTrail={shell,layer,particleLayer,ambient,particles,decorations:[...ambient,...particles],active:false,ambientAnimated:false,next:0,generation:0};
   resetTrailTravel(trail);trail.leave=()=>resetTrailTravel(trail);
   trail.move=event=>{
    if(!trail.active)return;
    if(shell.hidden){stopLoginTrail();return;}
    if(event.pointerType==='touch'||event.isPrimary===false||event.target?.closest?.('input,textarea,select,button,a,summary,[role="button"],[role="link"],[contenteditable]:not([contenteditable="false"])')){resetTrailTravel(trail);return;}
    const x=event.clientX,y=event.clientY,time=event.timeStamp??Date.now();
    if(!Number.isFinite(x)||!Number.isFinite(y))return;
    if(trail.lastX===null){trail.lastX=x;trail.lastY=y;trail.lastTime=time;return;}
    const dx=x-trail.lastX,dy=y-trail.lastY,distance=Math.hypot(dx,dy),elapsed=Math.max(8,time-trail.lastTime);
    trail.lastTime=time;trail.lastX=x;trail.lastY=y;trail.distance+=distance;
    if(!distance||time-trail.lastEmit<24)return;
    const strength=Math.min(1,distance/elapsed/2),spacing=48-24*strength,count=Math.min(4,Math.floor(trail.distance/spacing));
    if(!count)return;
    trail.distance%=spacing;trail.lastEmit=time;
    const ux=dx/distance,uy=dy/distance,angle=Math.atan2(dy,dx)*180/Math.PI;
    // Fixed viewport coordinates; distance sampling needs no layout reads.
    for(let n=count-1;n>=0;n--){
     const index=trail.next++%particles.length,particle=particles[index],back=Math.min(distance,n*spacing);
     const px=x-ux*back,py=y-uy*back,sign=index%2?1:-1,drift=14+42*strength;
     gsap.killTweensOf(particle);
     gsap.fromTo(particle,{x:px,y:py,opacity:.8+.18*strength,scale:.55+.7*strength,rotation:angle},{
      x:px-ux*drift+uy*sign*8,y:py-uy*drift-ux*sign*8+6,rotation:angle+sign*(30+150*strength),
      opacity:0,scale:.12,duration:.5+.18*strength,ease:'power1.in',
     });
    }
   };
   shell.append(layer);
  }
  const trail=loginTrail;
  if(trail.layer.hidden||trail.layer.dataset.exiting){
   ++trail.generation;trail.clearTween?.kill();trail.clearTween=null;gsap?.killTweensOf(trail.decorations);
   gsap?.set(trail.particles,{opacity:0});trail.next=0;
   delete trail.layer.dataset.exiting;if(trail.layer.parentNode!==trail.shell)trail.shell.append(trail.layer);
   trail.layer.hidden=false;
   trail.ambient.forEach((node,index)=>{node.style.opacity=ambientShapes[index][4];gsap?.set(node,{x:0,y:0,rotation:0,scale:1,opacity:ambientShapes[index][4]});});
  }
  const animateAmbient=!!gsap&&!reduced();
  if(animateAmbient!==trail.ambientAnimated){
   gsap?.killTweensOf(trail.ambient);trail.ambientAnimated=animateAmbient;
   if(animateAmbient)trail.ambient.forEach((node,index)=>{
    const spec=ambientShapes[index];gsap.to(node,{x:spec[5],y:spec[6],rotation:index%2?-30:24,duration:spec[7],repeat:-1,yoyo:true,ease:'sine.inOut'});
   });
  }
  const active=!!gsap&&!!trailMedia?.matches&&!reduced();trail.particleLayer.hidden=!active;
  if(active===trail.active)return;
  if(!active){detachLoginTrail(trail);gsap?.killTweensOf(trail.particles);gsap?.set(trail.particles,{opacity:0});return;}
  resetTrailTravel(trail);trail.active=true;
  trail.shell.addEventListener('pointermove',trail.move,{passive:true});trail.shell.addEventListener('pointerleave',trail.leave,{passive:true});
 }
 function choreograph(name,element,animate,ghost){
  settle(name);
  if(!gsap||reduced())return;
  const region={element,ghost};regions.set(name,region);
  const cleanup=()=>{if(regions.get(name)!==region)return false;settle(name);return true;};
  try{region.context=gsap.context(()=>animate(cleanup));}catch{cleanup();}
 }
 function dialogKey(dialog){if(!dialogKeys.has(dialog))dialogKeys.set(dialog,'dialog-'+(++dialogId));return dialogKeys.get(dialog);}
 function dialogOpen(dialog,panel){
  const name=dialogKey(dialog);settle(name);
  if(!gsap||reduced())return;
  choreograph(name,dialog,done=>gsap.timeline({onComplete:done,defaults:{ease:tokens.ease}})
   .fromTo(dialog,{'--dialog-backdrop-opacity':0},{'--dialog-backdrop-opacity':.28,duration:tokens.micro},0)
   .fromTo(panel,{opacity:0,y:8,scale:.985},{opacity:1,y:0,scale:1,duration:tokens.micro*1.25},0));
 }
 function dialogClose(dialog,panel,onComplete){
  const name=dialogKey(dialog);settle(name);
  if(!gsap||reduced()){onComplete?.();return;}
  const region={element:dialog,onSettled:onComplete};regions.set(name,region);
  const cleanup=()=>{if(regions.get(name)!==region)return false;settle(name);onComplete?.();return true;};
  try{region.context=gsap.context(()=>gsap.timeline({onComplete:cleanup,defaults:{ease:tokens.settle}})
   .to(dialog,{'--dialog-backdrop-opacity':0,duration:tokens.micro*1.25},0)
   .to(panel,{opacity:0,y:5,scale:.99,duration:tokens.micro*1.25},0));}catch{cleanup();}
 }
 function toast(element,visible,onComplete){
  if(!toastKeys.has(element))toastKeys.set(element,'toast-'+(++toastId));
  const name=toastKeys.get(element);settle(name);
  if(!gsap||reduced()){onComplete?.();return ()=>{};}
  const region={element,onSettled:onComplete};regions.set(name,region);
  const complete=()=>{if(regions.get(name)===region){settle(name);onComplete?.();}};
  try{region.context=gsap.context(()=>{
   const timeline=gsap.timeline({onComplete:complete,defaults:{ease:tokens.ease}});
   if(visible)timeline.fromTo(element,{opacity:0,y:5},{opacity:1,y:0,duration:tokens.micro},0);
   else timeline.to(element,{opacity:0,y:3,duration:tokens.micro},0);
  });}catch{complete();}
  return ()=>settle(name);
 }
 function historyFilter(element){
  choreograph('history-filter',element,done=>gsap.timeline({onComplete:done})
   .fromTo(element,{opacity:.8},{opacity:1,duration:tokens.micro,ease:tokens.ease},0));
 }
 function loginParts(root){
  let ring=root?.querySelector?.('.login-ring')||env.document.getElementById('login-ring');
  const glyph=root?.querySelector?.('.login-glyph')||env.document.getElementById('login-glyph');
  if(ring&&MorphSVGPlugin?.convertToPath&&ring.tagName?.toLowerCase()!=='path'){
   try{ring=MorphSVGPlugin.convertToPath(ring)[0]||ring;}catch{}
  }
  return {glyph,ring};
 }
 function applyLoginMark(state,root){
  const {glyph,ring}=loginParts(root);if(!glyph||!ring)return;
  glyph.setAttribute('d',loginShapes[state]||loginShapes.normal);
  for(const property of ['transform','stroke-dasharray','stroke-dashoffset']){
   ring.style?.removeProperty?.(property);
   if(ring.style&&!ring.style.removeProperty)delete ring.style[property];
  }
  ring.removeAttribute('transform');ring.removeAttribute('stroke-dasharray');ring.removeAttribute('stroke-dashoffset');
  if(state==='loading'){ring.setAttribute('stroke-dasharray','52 61');ring.setAttribute('stroke-dashoffset','-12');}
 }
 function stopLoginMark(finalState){
  loginMarkMotion?.timeline?.kill();loginMarkMotion=null;
  if(finalState)applyLoginMark(finalState);
 }
 function animateLoginMark(state){
  const parts=loginParts();stopLoginMark();
  if(!parts.glyph||!parts.ring)return;
  if(!gsap||!MorphSVGPlugin||reduced()){applyLoginMark(state);return;}
  const motion={state};loginMarkMotion=motion;
  const finish=()=>{if(loginMarkMotion!==motion)return;loginMarkMotion=null;applyLoginMark(state);};
  const timeline=motion.timeline=gsap.timeline({onComplete:state==='loading'?undefined:finish,defaults:{ease:tokens.ease}})
   .to(parts.glyph,{morphSVG:{shape:loginShapes[state],map:'complexity'},duration:tokens.navigation},0);
  if(state==='loading')timeline
   .to(parts.ring,{strokeDasharray:'52 61',strokeDashoffset:-12,rotation:90,transformOrigin:'50% 50%',duration:tokens.navigation},0)
   .to(parts.ring,{strokeDashoffset:-125,rotation:450,duration:tokens.reveal,ease:'none',repeat:-1},tokens.navigation);
  else timeline.to(parts.ring,{strokeDasharray:'114 0',strokeDashoffset:0,rotation:0,transformOrigin:'50% 50%',duration:tokens.navigation},0);
 }
 function loginReveal(shell){
  loginTrailShell=shell;syncLoginTrail();
  stopLoginMark('normal');
  choreograph('login',shell,done=>{
   const form=env.document.getElementById('login-form');
   const mark=env.document.getElementById('login-mark'),{ring,glyph}=loginParts();
   const wordmark=env.document.getElementById('login-wordmark'),surface=env.document.getElementById('login-surface');
   const fields=[form.querySelector('label'),env.document.getElementById('password'),env.document.getElementById('login-submit')].filter(Boolean);
   const credit=env.document.getElementById('login-credit');
   // The surface alone is masked: inputs remain in flow, focusable, and usable.
   gsap.timeline({onComplete:done,defaults:{ease:tokens.ease}})
    .fromTo(mark,{opacity:0,scale:.88,x:10,y:6},{opacity:1,scale:1,x:0,y:0,duration:tokens.navigation},0)
    .fromTo(ring,{strokeDasharray:'0 114',strokeDashoffset:28,rotation:-18,transformOrigin:'50% 50%'},{strokeDasharray:'114 0',strokeDashoffset:0,rotation:0,duration:tokens.geometry},0)
    .fromTo(glyph,{morphSVG:{shape:loginShapes.entry,map:'complexity'}},{morphSVG:{shape:loginShapes.normal,map:'complexity'},duration:tokens.navigation},0)
    .fromTo(wordmark,{clipPath:'inset(0 100% 0 0)',x:-3},{clipPath:'inset(0 0% 0 0)',x:0,duration:tokens.navigation},tokens.micro)
    .fromTo(surface,{clipPath:'inset(0 0 72% 0 round 12px)',opacity:.5},{clipPath:'inset(0 0 0% 0 round 12px)',opacity:1,duration:tokens.geometry},tokens.micro*1.5)
    .fromTo(fields,{opacity:0,y:4},{opacity:1,y:0,duration:tokens.micro,stagger:tokens.micro/4},tokens.reveal/2)
    .fromTo(credit,{opacity:0,y:4},{opacity:1,y:0,duration:tokens.micro},tokens.reveal-tokens.micro);
  });
 }
 function loginError(element){
  // A fast response during entrance should settle the form before showing feedback.
  authState='error';settle('login');settle('login-input');animateLoginMark('normal');
  choreograph('login-error',element,done=>gsap.timeline({onComplete:done})
   .fromTo(env.document.getElementById('password'),{borderColor:'#dfe5df'},{borderColor:'#b65e55',duration:tokens.micro},0)
   .fromTo(element,{opacity:0,y:3},{opacity:1,y:0,duration:tokens.micro,ease:tokens.ease},0));
 }
 function loginInteract(state='focus'){
  if(state===authState||authState==='submit'&&state!=='idle'||authState==='error'&&state==='focus')return;
  authState=state;settle('login');settle('login-error');
  if(state==='submit')animateLoginMark('loading');
  const surface=env.document.getElementById('login-surface'),ring=env.document.getElementById('login-ring');
  choreograph('login-input',surface,done=>{
   const timeline=gsap.timeline({onComplete:done,defaults:{ease:tokens.ease}})
    .fromTo(surface,{borderColor:'#e1e4df'},{borderColor:state==='idle'?'#e1e4df':'#a8bc8e',duration:tokens.micro},0);
   if(state!=='submit')timeline
    .fromTo(ring,{scale:1,svgOrigin:'32 32'},{scale:state==='typing'?1.025:1.01,svgOrigin:'32 32',duration:tokens.micro},0)
    .to(ring,{scale:1,duration:tokens.micro,ease:tokens.settle},tokens.micro);
   return timeline;
  });
 }
 function loginSuccess(workspace){
  loginTrailShell=null;stopLoginTrail(true);
  authState='idle';settle('login');settle('login-error');settle('login-input');
  const form=env.document.getElementById('login-form');
  if(!gsap||reduced()){stopLoginMark();applyLoginMark('success');return;}
  // A non-interactive visual exit keeps navigation immediate. Never retain passwords.
  const ghost=env.document.getElementById('login')?.hidden?null:form.cloneNode(true);
  if(ghost){
   const rect=form.getBoundingClientRect();ghost.removeAttribute('id');ghost.inert=true;ghost.setAttribute('aria-hidden','true');
   for(const input of ghost.querySelectorAll('input')){input.value='';input.removeAttribute('value');}
   for(const node of ghost.querySelectorAll('[id]'))node.removeAttribute('id');
   ghost.className+=' auth-exit-snapshot';Object.assign(ghost.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px'});
   env.document.body.append(ghost);
  }
  stopLoginMark('normal');
  // enter() does not await this timeline, and the snapshot cannot intercept input.
  choreograph('login-success',workspace,done=>{
   const ghostGlyph=ghost?.querySelector('.login-glyph'),ghostRing=ghost?.querySelector('.login-ring');
   const timeline=gsap.timeline({onComplete:done,defaults:{ease:tokens.ease}})
    .fromTo(workspace,{opacity:.96},{opacity:1,duration:tokens.micro*1.25},0);
   if(ghost){
    if(ghostGlyph&&MorphSVGPlugin)timeline.to(ghostGlyph,{morphSVG:{shape:loginShapes.success,map:'complexity'},duration:tokens.navigation},0);
    else if(ghostGlyph)ghostGlyph.setAttribute('d',loginShapes.success);
    if(ghostRing)timeline.to(ghostRing,{strokeDasharray:'114 0',strokeDashoffset:0,rotation:0,duration:tokens.navigation},0);
    timeline
     .to([...ghost.querySelectorAll('label,input,button')],{opacity:0,y:-3,duration:tokens.micro},tokens.navigation*.75)
     .to(ghost.querySelector('.login-surface'),{clipPath:'inset(0 0 72% 0 round 12px)',opacity:0,duration:tokens.micro},tokens.navigation*.75)
     .to(ghost.querySelector('.login-brand span'),{clipPath:'inset(0 100% 0 0)',duration:tokens.micro},tokens.navigation*.75)
     .to(ghost.querySelector('.login-logo'),{opacity:0,scale:.96,duration:tokens.micro},tokens.navigation*.75);
   }
  },ghost);
 }
 function settle(name,complete=false){
  const region=regions.get(name);if(!region)return;
  // Flip.revert() can synchronously fire completion; invalidate ownership first.
  regions.delete(name);
  // Revert only this region's GSAP-owned styles, never arbitrary application styles.
  region.context?.revert();region.ghost?.remove();
  delete region.element.dataset.motion;
  if(complete)region.onSettled?.();
 }
 function morph(name,element,targets,change,options={}){
  if(!available||reduced()) {settle(name);change();options.onSettled?.();return;}
  // Read the in-flight geometry BEFORE reverting obsolete animation styles.
  let before;
  try{before=Flip.getState(targets,{kill:false,props:'padding,borderRadius,backgroundColor,borderColor'});}
  catch{settle(name);change();options.onSettled?.();return;}
  settle(name);
  const ghost=options.ghost?.();
  change();
  const region={element,ghost,onSettled:options.onSettled};regions.set(name,region);element.dataset.motion='true';
  const cleanup=()=>{if(regions.get(name)===region)settle(name,true);};
  try{
   region.context=gsap.context(()=>{
    Flip.from(before,{
     duration:options.duration||tokens.geometry,ease:options.ease||tokens.ease,nested:true,prune:true,
     absoluteOnLeave:true,
     onEnter:options.animate?undefined:items=>gsap.fromTo(items,{opacity:0,y:4},{opacity:1,y:0,duration:tokens.micro,delay:options.accepted?tokens.micro:0}),
     onLeave:options.animate?undefined:items=>gsap.to(items,{opacity:0,duration:tokens.micro}),
     onComplete:cleanup,
    });
    options.animate?.(ghost);
    if(options.job)gsap.fromTo(options.job,{opacity:.65,y:4},{opacity:1,y:0,duration:tokens.navigation,ease:tokens.ease});
   });
  }catch{cleanup();} // The final DOM state must work even when motion is unavailable.
 }
 function sourcePress(tab){
  choreograph('source-press',tab,done=>gsap.timeline({onComplete:done,defaults:{ease:tokens.settle}})
   .fromTo(tab,{scale:1},{scale:.985,duration:tokens.micro/2,overwrite:true},0)
   .to(tab,{scale:1,duration:tokens.micro,overwrite:true},tokens.micro/2));
 }
 function sourceMode(panel,change,{nextTab,indicator,outgoing,incoming,shared=[],direction=1}={}){
  settle('source');
  const targets=[...new Set([panel,...shared,indicator,outgoing,incoming].filter(Boolean))];
  if(!available||reduced()){settle('source-mode');settle('source-press');change();return;}
  let before;
  try{before=Flip.getState(targets,{kill:false,props:'padding,borderRadius,backgroundColor,boxShadow'});}
  catch{settle('source-mode');settle('source-press');change();return;}
  settle('source-mode');settle('source-press');change();
  const region={element:panel};regions.set('source-mode',region);panel.dataset.motion='true';
  const cleanup=()=>{if(regions.get('source-mode')===region)settle('source-mode');};
  const shift=(direction||1)*12;
  try{
   region.context=gsap.context(()=>{
    gsap.timeline({defaults:{ease:tokens.settle}})
     .fromTo(nextTab,{scale:1},{scale:.985,duration:tokens.micro/2,overwrite:true},0)
     .to(nextTab,{scale:1,duration:tokens.micro,overwrite:true},tokens.micro/2);
    Flip.from(before,{
     duration:tokens.navigation,ease:tokens.settle,nested:true,prune:true,absoluteOnLeave:true,
     onLeave:items=>gsap.to(items,{opacity:0,x:-shift,y:-2,scale:.985,duration:tokens.micro,stagger:.018,ease:'power2.in',overwrite:true}),
     onEnter:items=>gsap.fromTo(items,{opacity:0,x:shift,y:2,scale:.985},{opacity:1,x:0,y:0,scale:1,duration:tokens.micro,stagger:.018,ease:tokens.ease,overwrite:true}),
     onComplete:cleanup,
    });
   });
  }catch{cleanup();}
 }
 function source(panel,collapsed,change,{accepted=false,job,onSettled}={}){
  settle('source-mode');settle('source-press');
  const content=env.document.getElementById('capture-content');
  const compact=env.document.getElementById('capture-another');
  if(panel.dataset.collapsed===String(collapsed)){change();return;}
  // Clone in-flight control styles before settling an interrupted expansion.
  const outgoing=collapsed&&available&&!reduced()?{
   clone:content.cloneNode(true),rect:content.getBoundingClientRect(),left:content.offsetLeft,top:content.offsetTop,
  }:null;
  if(outgoing)for(const node of outgoing.clone.querySelectorAll('[id]')){
   const original=env.document.getElementById(node.id);
   if(original&&node.matches?.('input,select,button,.field')&&env.getComputedStyle){
    const style=env.getComputedStyle(original);
    Object.assign(node.style,{width:style.width,height:style.height,font:style.font,padding:style.padding,borderRadius:style.borderRadius,gridColumn:style.gridColumn});
   }
  }
  const duration=phone()?(accepted?tokens.acceptedMobile:tokens.sourceMobile):(accepted?tokens.accepted:tokens.source);
  const controls=node=>[...node.querySelectorAll('.tabs,.source-field,.capture-options .field,.capture-footer')].filter(item=>!item.hidden&&(!item.getClientRects||item.getClientRects().length));
  morph('source',panel,[panel,content,compact],change,{
   duration,ease:tokens.ease,accepted,job,onSettled,
   animate:ghost=>{
    const stagger=phone()?.008:.018;
    if(collapsed&&ghost){
     gsap.to(controls(ghost),{opacity:0,y:-6,duration:tokens.micro*1.5,stagger,ease:tokens.settle});
     gsap.to(ghost.querySelector('.panel-heading'),{opacity:0,duration:tokens.micro,delay:duration-tokens.micro});
     gsap.fromTo(compact,{opacity:0,y:2},{opacity:1,y:0,duration:tokens.micro,delay:duration-tokens.micro,ease:tokens.ease});
    }else if(!collapsed){
     gsap.fromTo(controls(content),{opacity:0,y:phone()?4:6},{opacity:1,y:0,duration:tokens.micro,delay:tokens.micro/2,stagger,ease:tokens.ease});
    }
   },
   ghost:collapsed?()=>{
    // Outgoing content is visual only: no duplicate IDs, focusable controls, or events.
    const clone=outgoing.clone,rect=outgoing.rect;
    clone.removeAttribute('id');clone.setAttribute('aria-hidden','true');clone.inert=true;
    for(const node of clone.querySelectorAll('[id]'))node.removeAttribute('id');
    clone.className+=' source-admission-snapshot';
    Object.assign(clone.style,{width:rect.width+'px',left:outgoing.left+'px',top:outgoing.top+'px'});
    const action=clone.querySelector('.primary');if(action&&accepted){action.disabled=false;action.textContent='Diterima ✓';}
    panel.append(clone);return clone;
   }:undefined,
  });
 }
 function snapshot(element){
  if(!element)return;
  const rect=element.getBoundingClientRect();if(!rect.width)return;
  const style=env.getComputedStyle?.(element);
  const clone=element.cloneNode(true);clone.removeAttribute('id');clone.inert=true;clone.setAttribute('aria-hidden','true');
  for(const node of clone.querySelectorAll('[id]'))node.removeAttribute('id');
  clone.className+=' motion-title-snapshot';Object.assign(clone.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px'});
  if(style)Object.assign(clone.style,{font:style.font,color:style.color,letterSpacing:style.letterSpacing});
  env.document.body.append(clone);return clone;
 }
 function pageReveal(shell,title,parts=[],direction='reader'){
  choreograph('page',shell,done=>gsap.timeline({onComplete:done,defaults:{ease:tokens.ease}})
   .fromTo(title,{clipPath:'inset(0 0 100% 0)',y:direction==='reader'?4:-4},{clipPath:'inset(0 0 0% 0)',y:0,duration:tokens.navigation},0)
   .fromTo(parts.filter(Boolean),{opacity:.65,y:direction==='reader'?6:-6},{opacity:1,y:0,duration:tokens.navigation,stagger:phone()?.015:.025},tokens.micro/2));
 }
 function contextChange(shell,title,change,parts){
  settle('page');const ghost=!reduced()&&gsap?snapshot(title):null;
  change();
  choreograph('page',shell,done=>{
   const timeline=gsap.timeline({onComplete:done,defaults:{ease:tokens.ease}})
    .fromTo(parts().filter(Boolean),{opacity:.65,y:5},{opacity:1,y:0,duration:tokens.navigation},0)
    .fromTo(title,{clipPath:'inset(0 0 100% 0)',y:3},{clipPath:'inset(0 0 0% 0)',y:0,duration:tokens.navigation},0);
   if(ghost)timeline.to(ghost,{opacity:0,y:-3,duration:tokens.micro},0);
  },ghost);
 }
 function followLink(link,event,{shell,origin,row,direction='reader'}={}){
  if(event.defaultPrevented||event.button>0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.hasAttribute?.('download'))return;
  // Preserve native new-tab behavior, including popup blocking and noopener.
  const newTab=link.target==='_blank';
  if(!gsap||reduced()){return;}
  pendingNavigation?.cancel();settle('navigation');
  let finished=false,timer;
  const finish=()=>{
   if(finished)return;finished=true;env.clearTimeout(timer);settle('navigation');pendingNavigation=null;
   if(!newTab){try{env.sessionStorage?.setItem('streamfetch-motion-return',String(Date.now()));}catch{}env.location.assign(link.href);}
  };
  const cancel=()=>{finished=true;env.clearTimeout(timer);settle('navigation');};
  pendingNavigation={finish,cancel};
  if(!newTab)event.preventDefault();
  const ghost=snapshot(origin||link);
  choreograph('navigation',shell,()=>{
    const timeline=gsap.timeline({onComplete:finish,defaults:{ease:tokens.ease}})
     .to(shell,{opacity:.78,y:direction==='reader'?-4:4,duration:tokens.navigation},0);
    if(row)timeline.to(row,{backgroundColor:'#f1f6e7',duration:tokens.micro},0);
    if(ghost)timeline.to(origin||link,{opacity:0,duration:tokens.micro/2},0);
    if(ghost)timeline.fromTo(ghost,{opacity:1,y:0},{opacity:1,y:direction==='reader'?-2:2,duration:tokens.navigation},0);
  },ghost);
  // Keep navigation independent of animation completion/failure.
  timer=env.setTimeout(finish,tokens.navigation*1000+50);
 }
 function returnReveal(shell,title,parts){
  let value;try{value=Number(env.sessionStorage?.getItem('streamfetch-motion-return'));env.sessionStorage?.removeItem('streamfetch-motion-return');}catch{}
  if(value&&Date.now()-value<15000)pageReveal(shell,title,parts,'workspace');
 }
 function island(element,key,phase,change){
  const semanticKey=key+':'+phase;
  const hidden=dismissedKey===semanticKey||phase==='hidden';
  const next=hidden?semanticKey+':hidden':semanticKey;
  const apply=()=>{change();element.hidden=hidden;element.dataset.phase=hidden?'hidden':phase;};
  if(next===islandKey){apply();return;}
  islandKey=next;env.clearTimeout(dismissTimer);
  const targets=[element,...element.children];
  morph('island',element,targets,apply);
  if(phase==='completed'&&!hidden){
   dismissTimer=env.setTimeout(()=>{
    if(islandKey!==next)return;
    dismissedKey=semanticKey;
    island(element,key,phase,change);
   },2600);
  }
 }
 function progress(element,value){
  const previous=progressTweens.get(element);
  if(previous?.value===value)return;
  previous?.tween?.kill();progressTweens.delete(element);
  if(value===null){element.removeAttribute('value');element.removeAttribute('aria-valuenow');return;}
  element.setAttribute('aria-valuenow',value);
  const state={value};progressTweens.set(element,state);
  if(!available||reduced()||!Number.isFinite(Number(element.value))){element.value=value;return;}
  state.tween=gsap.to(element,{value,duration:tokens.micro,ease:tokens.ease,overwrite:true});
 }
 function reset(){
  loginTrailShell=null;stopLoginTrail();
  authState='idle';pendingNavigation?.cancel();pendingNavigation=null;stopLoginMark('normal');
  env.clearTimeout(dismissTimer);islandKey='';dismissedKey='';
  for(const name of [...regions.keys()])settle(name);
  for(const [element,state] of progressTweens){state.tween?.kill();element.value=state.value;}
  progressTweens.clear();
 }
 media?.addEventListener?.('change',()=>{
  syncLoginTrail();
  if(!reduced())return;
  if(loginMarkMotion)applyLoginMark(loginMarkMotion.state);stopLoginMark();
  pendingNavigation?.finish();
  for(const name of [...regions.keys()])settle(name,true);
  for(const [element,state] of progressTweens){state.tween?.kill();element.value=state.value;}
 });
 env.addEventListener?.('resize',()=>{pendingNavigation?.finish();for(const name of [...regions.keys()])settle(name,true);});
 env.addEventListener?.('pagehide',reset);
 trailMedia?.addEventListener?.('change',syncLoginTrail);
 env.addEventListener?.('pageshow',event=>{if(event.persisted){reset();loginTrailShell=env.document.getElementById('login');syncLoginTrail();}});
 return {tokens,source,sourceMode,sourcePress,island,progress,reset,reduced,dialogOpen,dialogClose,toast,historyFilter,loginReveal,loginError,loginSuccess,loginInteract,pageReveal,contextChange,followLink,returnReveal};
});
