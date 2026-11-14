"use strict";
// Same named UI contract; browsers decompress negotiated gzip automatically.
if(!crypto.randomUUID)crypto.randomUUID=()=>{const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;return [...b].map((v,i)=>([4,6,8,10].includes(i)?'-':'')+v.toString(16).padStart(2,'0')).join('');};
(()=>{
  const listeners=new Set(),base=new URL('.',location.href),inflight=new Map();
  let after=0,stopped=false,key='',polling=false,pinging=false,pollTimer,pingTimer,failures=0,pollFailures=0,bootstrapExpired=false;
  let connection={state:'connecting',rtt:null,mode:'',server:'',checked:0};
  const publish=()=>window.dispatchEvent(new CustomEvent('astroarchive-remote-connection',{detail:{...connection}}));
  async function request(route,options={}){
    const {timeoutMs=route.startsWith('api/upload')?600000:55000,...init}=options,controller=new AbortController();
    const abort=()=>controller.abort();init.signal?.addEventListener('abort',abort,{once:true});if(init.signal?.aborted)abort();
    const timer=setTimeout(abort,timeoutMs);
    try{
      const r=await fetch(new URL(route,base),{...init,signal:controller.signal});
      if(r.status===401){stopped=true;location.replace('./');throw Error('Сесията е прекратена.');}
      if(!r.ok){const e=await r.json().catch(()=>({}));throw Error(e.error||'Връзката е прекъсната.');}
      return await r.json();
    }catch(e){if(controller.signal.aborted)throw Error('Връзката със сървъра се забави. Провери индикатора под информацията за диска и опитай отново.');throw e;}
    finally{clearTimeout(timer);init.signal?.removeEventListener('abort',abort);}
  }
  async function connect(){
    let delay=2000;
    while(!stopped){
      try{const x=await request('api/hello');if(typeof x.key!=='string'||!x.key)throw Error('Невалиден отговор от сървъра.');key=x.key;after=x.eventSequence;connection.mode=x.mode;publish();if(bootstrapExpired)location.reload();return x;}
      catch(error){
        // An expired/unauthorized session navigates to login in request(); it
        // must never be retried as though it were a temporary network fault.
        if(stopped)throw error;
        failures++;connection={...connection,state:failures>1?'offline':'retrying',error:error.message,checked:Date.now()};publish();
        window.dispatchEvent(new CustomEvent('astroarchive-python-offline',{detail:error.message}));
        await new Promise(resolve=>setTimeout(resolve,delay));delay=Math.min(20000,delay*2);
      }
    }
    throw Error('Дистанционната връзка е спряна.');
  }
  const ready=connect();
  const emit=data=>{for(const fn of listeners){try{fn({data});}catch(e){console.error(e);}}};
  function rpc(message){
    // Never queue requests during the initial connection: they could mutate
    // data long after the caller timed out and retried the action.
    if(!key){
      if(message.method!=='app.bootstrap')return Promise.reject(Error('Връзката още не е готова. Опитай отново.'));
      return new Promise((resolve,reject)=>{
        let expired=false;const timer=setTimeout(()=>{expired=true;bootstrapExpired=true;reject(Error('Връзката още не е готова.'));},50000);
        ready.then(()=>{clearTimeout(timer);if(!expired)rpc(message).then(resolve,reject);},error=>{clearTimeout(timer);if(!expired)reject(error);});
      });
    }
    const shared=['python.archive.query','python.settings.get','plugin.archives','plugin.ui.state'].includes(message.method),cacheKey=message.method+':'+JSON.stringify(message.params||{});
    if(shared&&inflight.has(cacheKey))return inflight.get(cacheKey);
    const work=ready.then(()=>request('api/rpc',{method:'POST',timeoutMs:/^(viewer\.|image\.|function.image|plugin\.)/.test(message.method)?300000:55000,headers:{'Content-Type':'application/json','X-AstroArchive-Key':key},body:JSON.stringify(message)})).then(x=>{
      const result=x.result;
      if(result?.rowFields&&result?.rowValues){const fields=result.rowFields;result.rows=result.rowValues.map(row=>Object.fromEntries(fields.map((field,i)=>[field,row[i]])));delete result.rowFields;delete result.rowValues;}
      return x;
    });
    if(shared){inflight.set(cacheKey,work);work.finally(()=>inflight.delete(cacheKey)).catch(()=>{});}return work;
  }
  window.chrome=window.chrome||{};window.chrome.webview={addEventListener(type,fn){if(type==='message')listeners.add(fn);},removeEventListener(type,fn){listeners.delete(fn);},postMessage(message){rpc(message).then(x=>emit({...x,id:message.id})).catch(error=>emit({type:'response',id:message.id,ok:false,error:error.message}));}};
  async function poll(){
    if(stopped||polling)return;polling=true;let more=false;
    try{await ready;const x=await request('api/events?after='+after);for(const e of x.events){after=Math.max(after,e.sequence);emit(e);window.dispatchEvent(new CustomEvent('astroarchive-python-event',{detail:e}));}after=Math.max(after,x.sequence||0);more=!!x.more;pollFailures=0;window.dispatchEvent(new CustomEvent('astroarchive-remote-online'));}
    catch(e){pollFailures++;window.dispatchEvent(new CustomEvent('astroarchive-python-offline',{detail:e.message}));}
    finally{polling=false;if(!stopped)pollTimer=setTimeout(poll,more?200:document.hidden?10000:pollFailures?Math.min(20000,2000*pollFailures):connection.state==='slow'?3000:1500);}
  }
  async function ping(){
    if(stopped||pinging)return;clearTimeout(pingTimer);pinging=true;
    try{await ready;const start=performance.now(),x=await request('api/ping',{timeoutMs:6000});const rtt=Math.round(performance.now()-start);failures=0;connection={state:rtt<400?'good':rtt<1200?'fair':'slow',rtt,mode:x.mode,server:x.server,checked:Date.now()};}
    catch(e){failures++;connection={...connection,state:failures>1?'offline':'retrying',error:e.message,checked:Date.now()};}
    finally{pinging=false;publish();if(!stopped)pingTimer=setTimeout(ping,document.hidden?15000:5000);}
  }
  window.addEventListener('beforeunload',()=>{stopped=true;clearTimeout(pollTimer);clearTimeout(pingTimer);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){clearTimeout(pollTimer);clearTimeout(pingTimer);poll();ping();}});
  window.AstroArchiveRemote={decorateEntry(){},viewerReady(){},ready,connection:()=>({...connection}),checkConnection:ping,request:async(route,options={})=>{if(!key)throw Error('Връзката още не е готова. Опитай отново.');return request(route,{...options,headers:{'X-AstroArchive-Key':key,...options.headers}});},logout:async()=>{if(!key)throw Error('Връзката още не е готова. Опитай отново.');await request('api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-AstroArchive-Key':key},body:'{}'});location.replace('./');}};
  window.AstroArchivePython={call:(method,params={})=>window.AstroArchiveFunctions.call(method,params),async task(method,params={},progress){const started=await this.call(method,params);if(!started?.taskId)return started;for(;;){const t=await this.call('python.task',{id:started.taskId});progress?.(t);if(t.state==='completed')return t.result;if(t.state==='blocked')throw Error(t.problem||'Задачата е спряна.');if(t.state==='cancelled')throw Error('Задачата е отменена.');await new Promise(r=>setTimeout(r,connection.state==='slow'?2000:1000));}},image:id=>window.AstroArchiveFunctions.call('function.image',{id}),navigate:section=>window.AstroArchiveFunctions.navigate(section)};
  poll();ping();
})();
