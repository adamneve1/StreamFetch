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
 function choreograph(name,element,animate){
  settle(name);
  if(!gsap||reduced())return;
  const region={element};regions.set(name,region);
  const cleanup=()=>{if(regions.get(name)===region)settle(name);};
  try{region.context=gsap.context(()=>animate(cleanup));}catch{cleanup();}
 }
 function loginReveal(shell){
  choreograph('login',shell,done=>{
   const form=env.document.getElementById('login-form');
   const brand=form.querySelector('.login-brand');
   const fields=[form.querySelector('label'),env.document.getElementById('password'),env.document.getElementById('login-submit')].filter(Boolean);
   const credit=env.document.getElementById('login-credit');
   // One short composition, not a decorative sequence. All controls remain usable.
   gsap.timeline({onComplete:done,defaults:{ease:'power3.out'}})
    .fromTo(form,{opacity:0,scale:.99},{opacity:1,scale:1,duration:.32},0)
    .fromTo(brand,{opacity:0,y:10},{opacity:1,y:0,duration:.34},.02)
    .fromTo(fields,{opacity:0,y:8},{opacity:1,y:0,duration:.3,stagger:.045},.08)
    .fromTo(credit,{opacity:0,y:8},{opacity:1,y:0,duration:.22},.3);
  });
 }
 function loginError(element){
  // A fast response during entrance should settle the form before showing feedback.
  settle('login');
  choreograph('login-error',element,done=>gsap.fromTo(element,{opacity:0,y:3},{opacity:1,y:0,duration:.16,ease:'power2.out',onComplete:done}));
 }
 function loginSuccess(workspace){
  settle('login');settle('login-error');
  // enter() reveals the workspace immediately; this settle never gates authentication.
  choreograph('login-success',workspace,done=>gsap.fromTo(workspace,{opacity:.94,y:2},{opacity:1,y:0,duration:.12,ease:'power2.out',onComplete:done}));
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
 return {source,island,progress,reset,reduced,loginReveal,loginError,loginSuccess};
});
