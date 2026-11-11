"use strict";
// Adapter for the preserved frontend contract. No filesystem access in JavaScript.
(() => {
  const listeners=new Set(),base=new URL('.',location.href);let after=0,stopped=false,key='',keyHeader='X-AstroArchive-Key',bootstrapExpired=false;
  async function connect(){
    let delay=1000;
    while(!stopped){
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
      try{
        let x;
        try{const response=await fetch(new URL('api/hello',base),{signal:controller.signal});if(!response.ok)throw Error('Локалната връзка не е достъпна.');x=await response.json();}
        finally{clearTimeout(timeout);}
        if(typeof x.key!=='string'||!x.key)throw Error('Невалиден отговор от програмата.');
        key=x.key;after=Math.max(0,Number(x.eventSequence)||0);if(x.app!=='Astro Archive')keyHeader='X-PhotoSorter-Key';if(bootstrapExpired)location.reload();return x;
      }catch(error){
        if(stopped)throw error;
        window.dispatchEvent(new CustomEvent('astroarchive-python-offline',{detail:error.message}));
        await new Promise(resolve=>setTimeout(resolve,delay));delay=Math.min(15000,delay*2);
      }
    }
    throw Error('Локалната връзка е спряна.');
  }
  const ready=connect();
  const emit=data=>{for(const listener of listeners){try{listener({data});}catch(error){console.error(error);}}};
  window.chrome=window.chrome||{};
  window.chrome.webview={addEventListener(type,listener){if(type==='message')listeners.add(listener);},removeEventListener(type,listener){listeners.delete(listener);},postMessage(message){
    // Do not queue commands while hello is unavailable: the caller's timeout
    // could expire before reconnect and a late mutation would surprise users.
    if(!key){
      if(message.method==='app.bootstrap'){
        // Bootstrap is a read-only startup call; expire it before the UI's
        // 60s deadline and reload only the never-initialized page on recovery.
        let expired=false;const timer=setTimeout(()=>{expired=true;bootstrapExpired=true;emit({type:'response',id:message.id,ok:false,error:'Връзката още не е готова.'});},50000);
        ready.then(()=>{clearTimeout(timer);if(!expired)window.chrome.webview.postMessage(message);}).catch(error=>{clearTimeout(timer);if(!expired)emit({type:'response',id:message.id,ok:false,error:error.message});});return;
      }
      emit({type:'response',id:message.id,ok:false,error:'Връзката още не е готова. Опитай отново.'});return;
    }
    ready.then(()=>fetch(new URL('api/rpc',base),{method:'POST',headers:{'Content-Type':'application/json',[keyHeader]:key},body:JSON.stringify(message)})).then(r=>r.json()).then(result=>{
      emit(result);if(message.method==='ui.storage')window.dispatchEvent(new Event('astroarchive-python-settings'));
    }).catch(error=>emit({type:'response',id:message.id,ok:false,error:error.message}));
  }};
  async function poll(){
    if(stopped)return;
    try{await ready;const response=await fetch(new URL('api/events?after='+after,base));if(response.ok){const data=await response.json();for(const event of data.events){after=Math.max(after,event.sequence);emit(event);window.dispatchEvent(new CustomEvent('astroarchive-python-event',{detail:event}));}}}
    catch(error){window.dispatchEvent(new CustomEvent('astroarchive-python-offline',{detail:error.message}));}
    setTimeout(poll,700);
  }
  poll();window.addEventListener('beforeunload',()=>{stopped=true;});
  window.AstroArchivePython={call:(method,params={})=>window.AstroArchiveFunctions.call(method,params),
    async task(method,params={},progress){const started=await this.call(method,params);if(!started?.taskId)return started;
      for(;;){const snapshot=await this.call('python.task',{id:started.taskId});progress?.(snapshot);
        if(snapshot.state==='completed')return snapshot.result;if(snapshot.state==='blocked')throw Error(snapshot.problem||'Задачата е спряна.');if(snapshot.state==='cancelled')throw Error('Задачата е отменена.');await new Promise(resolve=>setTimeout(resolve,500));}},
    image:id=>window.AstroArchiveFunctions.call('function.image',{id}),
    navigate:section=>window.AstroArchiveFunctions.navigate(section)};
})();
