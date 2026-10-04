/* Focused choreography: authentication, Source admission, and the status island. */
(function(root, factory) {
 if(typeof module==='object'&&module.exports)module.exports=factory;
 else root.StreamFetchMotion=factory(root);
})(typeof window==='undefined'?{}:window, function(env) {
 const {gsap,Flip}=env, media=env.matchMedia?.('(prefers-reduced-motion: reduce)');
 const available=!!(gsap&&Flip), regions=new Map(), progressTweens=new Map();
 let islandKey='',dismissedKey='',dismissTimer;
 if(available)gsap.registerPlugin(Flip);
 const reduced=()=>!!media?.matches;
 function choreograph(name,element,animate,ghost){
  settle(name);
  if(!gsap||reduced())return;
  const region={element,ghost};regions.set(name,region);
  const cleanup=()=>{if(regions.get(name)===region)settle(name);};
  try{region.context=gsap.context(()=>animate(cleanup));}catch{cleanup();}
 }
 function loginReveal(shell){
  choreograph('login',shell,done=>{
   const form=env.document.getElementById('login-form');
   const mark=env.document.getElementById('login-mark'),ring=env.document.getElementById('login-ring');
   const wordmark=env.document.getElementById('login-wordmark'),surface=env.document.getElementById('login-surface');
   const fields=[form.querySelector('label'),env.document.getElementById('password'),env.document.getElementById('login-submit')].filter(Boolean);
   const credit=env.document.getElementById('login-credit');
   // The surface alone is masked: inputs remain in flow, focusable, and usable.
   gsap.timeline({onComplete:done,defaults:{ease:'power3.out'}})
    .fromTo(mark,{opacity:0,scale:.88,x:10,y:6},{opacity:1,scale:1,x:0,y:0,duration:.3},0)
    .fromTo(ring,{scale:1,svgOrigin:'32 32'},{scale:1.075,svgOrigin:'32 32',duration:.12,ease:'power2.out'},.1)
    .to(ring,{scale:1,duration:.18,ease:'power2.inOut'},.22)
    .fromTo(wordmark,{clipPath:'inset(0 100% 0 0)',x:-3},{clipPath:'inset(0 0% 0 0)',x:0,duration:.28},.16)
    .fromTo(surface,{clipPath:'inset(0 0 72% 0 round 12px)',opacity:.5},{clipPath:'inset(0 0 0% 0 round 12px)',opacity:1,duration:.38},.21)
    .fromTo(fields,{opacity:0,y:4},{opacity:1,y:0,duration:.22,stagger:.035},.4)
    .fromTo(credit,{opacity:0,y:4},{opacity:1,y:0,duration:.18},.64);
  });
 }
 function loginError(element){
  // A fast response during entrance should settle the form before showing feedback.
  settle('login');
  choreograph('login-error',element,done=>gsap.timeline({onComplete:done})
   .fromTo(env.document.getElementById('password'),{borderColor:'#dfe5df'},{borderColor:'#b65e55',duration:.16},0)
   .fromTo(element,{opacity:0,y:3},{opacity:1,y:0,duration:.16,ease:'power2.out'},0));
 }
 function loginInteract(){settle('login');settle('login-error');}
 function loginSuccess(workspace){
  settle('login');settle('login-error');
  if(!gsap||reduced())return;
  const form=env.document.getElementById('login-form');
  // A non-interactive visual exit keeps navigation immediate. Never retain passwords.
  const ghost=env.document.getElementById('login')?.hidden?null:form.cloneNode(true);
  if(ghost){
   const rect=form.getBoundingClientRect();ghost.removeAttribute('id');ghost.inert=true;ghost.setAttribute('aria-hidden','true');
   for(const input of ghost.querySelectorAll('input')){input.value='';input.removeAttribute('value');}
   for(const node of ghost.querySelectorAll('[id]'))node.removeAttribute('id');
   ghost.className+=' auth-exit-snapshot';Object.assign(ghost.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px'});
   env.document.body.append(ghost);
  }
  // enter() does not await this timeline, and the snapshot cannot intercept input.
  choreograph('login-success',workspace,done=>{
   const timeline=gsap.timeline({onComplete:done,defaults:{ease:'power2.out'}})
    .fromTo(workspace,{opacity:.96},{opacity:1,duration:.2},0);
   if(ghost)timeline
    .to([...ghost.querySelectorAll('label,input,button')],{opacity:0,y:-3,duration:.12},0)
    .to(ghost.querySelector('.login-surface'),{clipPath:'inset(0 0 72% 0 round 12px)',opacity:0,duration:.18},0)
    .to(ghost.querySelector('.login-brand span'),{clipPath:'inset(0 100% 0 0)',duration:.12},.04)
    .to(ghost.querySelector('.login-logo'),{opacity:0,scale:.96,duration:.1},.1);
  },ghost);
 }
 function settle(name){
  const region=regions.get(name);if(!region)return;
  // Revert only this region's GSAP-owned styles, never arbitrary application styles.
  region.context?.revert();region.ghost?.remove();regions.delete(name);
  delete region.element.dataset.motion;
 }
 function morph(name,element,targets,change,options={}){
  if(!available||reduced()) {settle(name);change();return;}
  // Read the in-flight geometry BEFORE reverting obsolete animation styles.
  let before;
  try{before=Flip.getState(targets,{kill:false,props:'borderRadius,backgroundColor,borderColor'});}
  catch{settle(name);change();return;}
  settle(name);
  const ghost=options.ghost?.();
  change();
  const region={element,ghost};regions.set(name,region);element.dataset.motion='true';
  const cleanup=()=>{if(regions.get(name)===region)settle(name);};
  try{
   region.context=gsap.context(()=>{
    Flip.from(before,{
     duration:options.duration||.38,ease:options.ease||'power3.out',nested:true,prune:true,
     absoluteOnLeave:true,
     onEnter:items=>gsap.fromTo(items,{opacity:0,y:4},{opacity:1,y:0,duration:.2,delay:options.accepted?.12:0}),
     onLeave:items=>gsap.to(items,{opacity:0,duration:.12}),
     onComplete:cleanup,
    });
    if(ghost){
     gsap.timeline().to(ghost,{opacity:.45,duration:.08})
      .to(ghost,{opacity:0,y:-3,duration:.14});
    }
    if(options.job)gsap.fromTo(options.job,{opacity:.4,y:5},{opacity:1,y:0,duration:.3});
   });
  }catch{cleanup();} // The final DOM state must work even when motion is unavailable.
 }
 function source(panel,collapsed,change,{accepted=false,job}={}){
  const content=env.document.getElementById('capture-content');
  const compact=env.document.getElementById('capture-another');
  if(panel.dataset.collapsed===String(collapsed)){change();return;}
  morph('source',panel,[panel,content,compact],change,{
   duration:accepted?.48:.38,ease:'back.out(0.35)',accepted,job,
   ghost:accepted&&collapsed?()=>{
    // Outgoing content is visual only: no duplicate IDs, focusable controls, or events.
    const clone=content.cloneNode(true),rect=content.getBoundingClientRect();
    clone.removeAttribute('id');clone.setAttribute('aria-hidden','true');clone.inert=true;
    for(const node of clone.querySelectorAll('[id]'))node.removeAttribute('id');
    clone.className+=' source-admission-snapshot';
    Object.assign(clone.style,{width:rect.width+'px',left:content.offsetLeft+'px',top:content.offsetTop+'px'});
    const action=clone.querySelector('.primary');if(action){action.disabled=false;action.textContent='Diterima ✓';}
    panel.append(clone);return clone;
   }:undefined,
  });
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
  state.tween=gsap.to(element,{value,duration:.24,ease:'power1.out',overwrite:true});
 }
 function reset(){
  env.clearTimeout(dismissTimer);islandKey='';dismissedKey='';
  for(const name of [...regions.keys()])settle(name);
  for(const [element,state] of progressTweens){state.tween?.kill();element.value=state.value;}
  progressTweens.clear();
 }
 media?.addEventListener?.('change',()=>{
  if(!reduced())return;
  for(const name of [...regions.keys()])settle(name);
  for(const [element,state] of progressTweens){state.tween?.kill();element.value=state.value;}
 });
 env.addEventListener?.('resize',()=>{for(const name of [...regions.keys()])settle(name);});
 return {source,island,progress,reset,reduced,loginReveal,loginError,loginSuccess,loginInteract};
});
