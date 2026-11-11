"use strict";
// Semantic presentation sync. Applying a snapshot never clicks a button or starts work.
(() => {
  const adapters=new Map(),versions={},saved=new Map(),deferred=new Map(),dirty=new Set(),restoringChannels=new Set(),edits=new Map(),inFlight=new Map(),restoreQueues=new Map();
  const clientId=crypto.randomUUID().replaceAll('-','');let ready=false,restoring=0,sequence=0,timer,pulling=null,sending=false,again=false,decisionEpoch=0;
  const call=(method,params={})=>window.AstroArchivePython.call(method,params);
  function status(message,error=false){const e=document.getElementById('shared-session-status');if(e){e.textContent=message;e.style.color=error?'var(--warning)':'var(--muted)';}}
  function fingerprint(value){return JSON.stringify(value);}
  function apply(channel,record,force=false){
    const next=(restoreQueues.get(channel)||Promise.resolve()).then(()=>applyNow(channel,record,force));restoreQueues.set(channel,next.catch(()=>{}));return next;
  }
  async function applyNow(channel,record,force=false){
    if(!record||!adapters.has(channel))return;
    if(record.revision<(versions[channel]||0)||!force&&record.revision===(versions[channel]||0))return;
    versions[channel]=record.revision;
    if(channel==='analysis'&&(record.value.decisionRevision||0)!==decisionEpoch){
      decisionEpoch=record.value.decisionRevision||0;
      // A pre-decision click must not resurrect the selection after another
      // client has excluded/restored those frames or highlighted new warnings.
      for(const pending of [edits.get(channel),inFlight.get(channel)])if(pending)delete pending.selected;
    }
    const adapter=adapters.get(channel);
    if(record.author===clientId&&!force){saved.set(channel,fingerprint(adapter.read()));return;}
    if(adapter.visible&&!adapter.visible()){deferred.set(channel,record);return;}
    restoring++;restoringChannels.add(channel);
    try{
      await adapter.write(record.value);saved.set(channel,fingerprint(adapter.read()));deferred.delete(channel);
      // Keep unsent/in-flight user choices visible while the server publishes
      // task progress. Applying a task snapshot must not reset its night picker.
      const pending=merge(inFlight.get(channel)||{},edits.get(channel));
      if(channel==='analysis'||channel==='preparation')delete pending.phase;
      if(Object.keys(pending).length){await adapter.write(merge(record.value,pending));dirty.add(channel);}
    }
    finally{restoring--;restoringChannels.delete(channel);if(dirty.size&&!sending){clearTimeout(timer);timer=setTimeout(flush,100);}}
  }
  async function reconcile(){
    if(!ready)return;if(pulling){again=true;return pulling;}
    pulling=(async()=>{do{again=false;const data=await call('ui.session.get',{versions});for(const channel of ['route','archive','analysis','preparation','extensions','viewer'])if(data.channels[channel])await apply(channel,data.channels[channel]);}while(again);status('Обща сесия · синхронизирана');})().catch(e=>status('Обща сесия · '+e.message,true)).finally(()=>{pulling=null;});
    return pulling;
  }
  async function flush(){
    clearTimeout(timer);if(!ready||sending)return;sending=true;let currentChannel=null,currentPatch=null,delay=250;
    try{
      const channels=[...dirty];dirty.clear();
      for(const channel of channels){if(restoringChannels.has(channel)){dirty.add(channel);continue;}const adapter=adapters.get(channel);if(!adapter||adapter.visible&&!adapter.visible()&&!edits.has(channel))continue;
        const base=adapter.read();if(base==null){edits.delete(channel);continue;}currentChannel=channel;currentPatch=edits.get(channel);edits.delete(channel);
        const value=merge(base,currentPatch),baseStamp=fingerprint(base),stamp=fingerprint(value);if(saved.get(channel)===stamp)continue;
        const sentDecisionEpoch=decisionEpoch;
        inFlight.set(channel,currentPatch||{});
        const result=await call('ui.session.publish',{clientId,channel,value,baseRevision:versions[channel]||0,sequence:++sequence});
        if(result.accepted){versions[channel]=result.record.revision;saved.set(channel,stamp);const canonical=Object.fromEntries(Object.keys(value).map(k=>[k,result.record.value[k]]));if((baseStamp!==stamp||fingerprint(canonical)!==stamp)&&fingerprint(adapter.read())===baseStamp)await apply(channel,result.record,true);}
        else{await apply(channel,result.record,true);if(currentPatch){if(channel==='analysis'&&sentDecisionEpoch!==decisionEpoch)delete currentPatch.selected;edits.set(channel,merge(currentPatch,edits.get(channel)));dirty.add(channel);}status('Обща сесия · обновена от друго устройство');}
        inFlight.delete(channel);currentChannel=null;currentPatch=null;
      }
    }catch(e){if(currentChannel){inFlight.delete(currentChannel);dirty.add(currentChannel);if(currentPatch)edits.set(currentChannel,merge(currentPatch,edits.get(currentChannel)));}delay=1500;status('Обща сесия · '+e.message,true);await reconcile();}
    finally{sending=false;if(dirty.size)timer=setTimeout(flush,delay);}
  }
  function merge(base,patch){return {...base,...patch,...(patch?.controls?{controls:{...base?.controls,...patch.controls}}:{})};}
  function difference(before,after){const patch={};for(const[key,value]of Object.entries(after||{})){if(key==='controls'){const controls={};for(const[k,v]of Object.entries(value))if(fingerprint(v)!==fingerprint(before?.controls?.[k]))controls[k]=v;if(Object.keys(controls).length)patch.controls=controls;}else if(fingerprint(value)!==fingerprint(before?.[key]))patch[key]=value;}return patch;}
  function changed(channel){if(!ready||restoringChannels.has(channel)||!adapters.has(channel))return;dirty.add(channel);clearTimeout(timer);timer=setTimeout(flush,250);}
  function edit(channel,patch){if(!ready||!adapters.has(channel))return;edits.set(channel,merge(edits.get(channel),patch));dirty.add(channel);clearTimeout(timer);timer=setTimeout(flush,250);}
  async function activate(channel){const record=deferred.get(channel);if(record)await apply(channel,record,true);}
  async function start(){
    if(ready)return;ready=true;const holder=document.querySelector('.header-disk-copy');
    if(holder&&!document.getElementById('shared-session-status')){const e=document.createElement('small');e.id='shared-session-status';e.setAttribute('role','status');e.style.display='block';e.style.fontSize='10px';e.style.marginTop='5px';holder.append(e);}
    await reconcile();for(const channel of adapters.keys())if(!versions[channel])changed(channel);await flush();
    setInterval(()=>{if(!document.hidden)reconcile();},30000);
  }
  window.addEventListener('astroarchive-python-event',event=>{if(event.detail?.name==='ui.session.changed')setTimeout(reconcile,80);});
  window.addEventListener('astroarchive-flow-phase',event=>changed(event.detail.section));
  window.addEventListener('astroarchive-remote-online',()=>{if(ready&&deferred.size)for(const key of deferred.keys())if(!adapters.get(key)?.visible||adapters.get(key).visible())activate(key);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)reconcile();});
  function capture(event){
    const target=event.target;if(!(target instanceof Element)||target.closest('#settings-dialog,.function-dialog'))return;
    let channel;if(target.closest('.image-viewer'))channel='viewer';else if(target.closest('#analysis-content'))channel='analysis';else if(target.closest('#preparation-content'))channel='preparation';else if(target.closest('#archive-sidebar,.archive-sidebar,.content-panel,.archive-actions'))channel='archive';
    const channels=[channel,target.closest('[data-route]')?'route':null,target.closest('.extension-tabs,#q-plugin-actions,#w-plugin-actions')?'extensions':null].filter(Boolean);
    const before=new Map(channels.map(key=>[key,adapters.get(key)?.read()]));
    // Native controls already contain the new value before capture handlers.
    // Record the actual edited field, not unrelated values from an older view.
    if(channel&&['input','change'].includes(event.type)&&!restoringChannels.has(channel)){
      const controls=before.get(channel)?.controls;
      if(controls&&Object.prototype.hasOwnProperty.call(controls,target.id))edits.set(channel,merge(edits.get(channel),{controls:{[target.id]:controls[target.id]}}));
    }
    // Observe the completed event: a capture-listener microtask can precede target handlers.
    setTimeout(()=>{for(const key of channels){const patch=difference(before.get(key),adapters.get(key)?.read());if(Object.keys(patch).length){edits.set(key,merge(edits.get(key),patch));dirty.add(key);clearTimeout(timer);timer=setTimeout(flush,250);}else changed(key);}},0);
  }
  for(const name of ['click','change','input','pointerup','wheel','keyup'])document.addEventListener(name,capture,true);
  window.AstroArchiveSession={register(channel,adapter){adapters.set(channel,adapter);},changed,edit,activate,flush,reconcile,start,get restoring(){return !!restoring;}};
})();
