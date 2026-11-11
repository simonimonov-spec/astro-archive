"use strict";
// Fixed declarative controls. Plugin code never becomes HTML or JavaScript here.
(() => {
  const U=window.AstroArchiveFunctions,{$,node}=U;
  const active={analysis:null,preparation:null},mounts=new Map(),values=new Map(),publishedDefaults=new Map(),clientValues=new Map();
  let plugins=[],api,loaded=false,viewerContext=null,viewerHost=null;
  const key=(p,c)=>p.id+':'+c.id;
  const approved=slot=>plugins.filter(p=>p.enabled&&p.available&&p.uiAllowed).flatMap(p=>(p.contributions||[]).filter(c=>c.slot===slot&&(!c.clients||c.clients.includes(window.AstroArchiveRemote?'remote':'desktop'))).map(c=>({p,c}))).sort((a,b)=>(a.c.order||0)-(b.c.order||0));
  const color=name=>getComputedStyle(document.documentElement).getPropertyValue('--'+name).trim();
  function layout(){
    if(!$('header-extensions')&&document.querySelector('.header-disk-copy')){const host=node('div');host.id='header-extensions';host.hidden=true;document.querySelector('.header-disk-copy').append(host);}
    for(const slot of ['analysis','preparation']){
      const base=$(slot+'-content');
      if(!$(slot+'-extension-tabs')){const menu=node('nav',null,'function-segmented extension-tabs');menu.id=slot+'-extension-tabs';menu.hidden=true;base.before(menu);}
      if(!$(slot+'-extensions')){const host=node('div');host.id=slot+'-extensions';host.hidden=true;base.after(host);}
    }
    if(!$('archive-extensions')){const host=node('div');host.id='archive-extensions';$('page-archive').append(host);}
    if(!$('preparation-activity')){const host=node('section',null,'preparation-activity');host.id='preparation-activity';const core=$('w-history-area');if(core)host.append(core);const plugins=node('div');plugins.id='preparation-plugin-activity';host.append(plugins);$('page-preparation').append(host);}
    if(!$('extension-permission-notice')){
      const notice=node('div',null,'extension-notice');notice.id='extension-permission-notice';notice.hidden=true;notice.append(node('span'));
      const button=node('button','Прегледай разрешенията','quiet-button');button.onclick=()=>window.AstroArchivePreferences.open('plugins');notice.append(button);$('main').prepend(notice);
    }
  }
  function fields(p,c){const id=key(p,c);if(!values.has(id))values.set(id,new Map());return values.get(id);}
  function readValues(p,c){return Object.fromEntries(fields(p,c));}
  const actionQueues=new Map(),saveTimers=new Map(),returnTasks=new Set();
  async function returnToPicker(){await window.AstroArchivePreparation?.newSelection?.();select('preparation',null,false);}
  async function leavePreparation(p,c){for(const[id,pending]of [...saveTimers])if(id.startsWith(key(p,c)+':')){clearTimeout(pending.timer);pending.flush();}await actionQueues.get(p.id);await returnToPicker();}
  function finishNavigation(tasks){for(const task of tasks||[])if(returnTasks.has(task.id)&&task.state!=='running'){returnTasks.delete(task.id);if(task.state==='completed')returnToPicker().catch(error=>U.error('preparation',error));}}
  function action(p,c,e,row,itemId){
    const parameters={pluginId:p.id,contributionId:c.id,element:e.id,values:readValues(p,c),row,itemId,context:selection(c.slot)};
    const perform=async()=>{try{
      const response=await U.call('plugin.ui.input',parameters);
      if(response?.returnToPreparation===true&&c.slot==='preparation'&&typeof response.taskId==='string'){returnTasks.add(response.taskId);U.call('activity.state').then(state=>finishNavigation(state.tasks)).catch(()=>{});}
      if(response?.openContribution){const target=approved('preparation').find(x=>x.p.id===p.id&&x.c.id===response.openContribution);if(!target)throw Error('Непознато поле за връщане.');select('preparation',key(target.p,target.c),false);window.AstroArchivePython.navigate('preparation');}
      if(response?.archiveSettings)window.AstroArchiveCustomization?.archiveUpdated(response.archiveSettings);
      if(response?.copyText)await navigator.clipboard.writeText(response.copyText);
      if(response?.navigation){window.AstroArchiveArchiveView.navigate(response.navigation.archiveId,response.navigation.relativePath);window.AstroArchivePython.navigate('archive');}
      if(response?.advanceViewer===true&&p.id==='ai-training'&&c.id==='rating'&&c.slot==='viewer-tools'&&viewerContext?.viewerId===parameters.context.viewerId)await window.AstroArchiveViewer?.next(parameters.context.viewerId);
      if(response?.message)window.dispatchEvent(new CustomEvent('astroarchive-plugin-message',{detail:{plugin:p.id,message:response.message}}));
    }catch(error){U.modal(p.name,body=>body.append(node('p',error.message,'error-banner')));}};
    const next=(actionQueues.get(p.id)||Promise.resolve()).then(perform);actionQueues.set(p.id,next.catch(()=>{}));return next;
  }
  function selection(slot){if(slot==='viewer-tools')return viewerContext||{};return (slot.startsWith('preparation')?window.AstroArchivePreparation:window.AstroArchiveAnalysis)?.selection?.(slot)||{};}
  function chart(element,host){
    const wrap=node('section',null,'extension-chart'),heading=node('strong',element.label||'Диаграма');
    const canvas=node('canvas');canvas.setAttribute('role','img');canvas.setAttribute('aria-label',element.label||'Диаграма на добавката');
    const legend=node('div',null,'py-legend');wrap.append(heading,canvas,legend);host.append(wrap);
    const series=element.series||[],palette=[color('accent'),color('success'),color('warning'),color('error'),'#b69dff','#6ed5d1'];
    for(const [i,s]of series.entries()){const label=node('span',s.name);label.style.color=s.color||palette[i%palette.length];legend.append(label);}
    let attached=false;const observer=new ResizeObserver(draw);observer.observe(canvas);
    function draw(){
      if(!canvas.isConnected){if(attached)observer.disconnect();return;}attached=true;
      const w=canvas.getBoundingClientRect().width;if(!w)return;const h=330,ratio=devicePixelRatio||1;canvas.width=w*ratio;canvas.height=h*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
      const m={l:62,r:24,t:20,b:52},pw=w-m.l-m.r,ph=h-m.t-m.b;let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
      for(const s of series)for(const p of s.points){xmin=Math.min(xmin,p.x);xmax=Math.max(xmax,p.x);ymin=Math.min(ymin,p.y);ymax=Math.max(ymax,p.y);}
      if(!Number.isFinite(xmin)){ctx.fillStyle=color('muted');ctx.fillText('Няма данни за диаграмата.',20,45);return;}
      if(element.chartType==='bar'){ymin=Math.min(0,ymin);xmin-=.5;xmax+=.5;}if(xmin===xmax){xmin-=.5;xmax+=.5;}if(ymin===ymax){ymin-=.5;ymax+=.5;}
      const px=x=>m.l+(x-xmin)/(xmax-xmin)*pw,py=y=>m.t+ph-(y-ymin)/(ymax-ymin)*ph;
      ctx.font='12px Segoe UI';ctx.strokeStyle=color('border');ctx.fillStyle=color('muted');
      for(let i=0;i<=4;i++){const x=m.l+pw*i/4,y=m.t+ph*i/4;ctx.beginPath();ctx.moveTo(x,m.t);ctx.lineTo(x,m.t+ph);ctx.moveTo(m.l,y);ctx.lineTo(m.l+pw,y);ctx.stroke();ctx.fillText(U.number(xmin+(xmax-xmin)*i/4,1),x-12,h-29);ctx.fillText(U.number(ymax-(ymax-ymin)*i/4,2),2,y+4);}
      ctx.fillText(element.xLabel||'',m.l+pw/2,h-6);ctx.fillText(element.yLabel||'',m.l,13);
      ctx.save();ctx.beginPath();ctx.rect(m.l,m.t,pw,ph);ctx.clip();
      for(const [i,s]of series.entries()){
        ctx.strokeStyle=ctx.fillStyle=s.color||palette[i%palette.length];ctx.lineWidth=2;ctx.beginPath();
        s.points.forEach((p,j)=>{const x=px(p.x),y=py(p.y);if(element.chartType==='bar'){const width=Math.min(36,pw/Math.max(2,s.points.length*2));ctx.fillRect(x-width/2,y,width,py(0)-y);}else if(element.chartType==='scatter'){ctx.moveTo(x+4,y);ctx.arc(x,y,4,0,Math.PI*2);}else j?ctx.lineTo(x,y):ctx.moveTo(x,y);});
        element.chartType==='scatter'?ctx.fill():ctx.stroke();
      }ctx.restore();
    }
    draw();
  }
  function controls(p,c,elements,host,imageNodes=new Map()){
    const saved=fields(p,c);
    if(!publishedDefaults.has(key(p,c)))publishedDefaults.set(key(p,c),new Map());const previous=publishedDefaults.get(key(p,c));
    for(const declared of elements){
      const e={...declared,...clientValues.get(key(p,c))?.get(declared.id)};
      if(e.visible===false)continue;const box=node('div',null,'extension-control');box.dataset.element=e.id;if(e.span===2)box.classList.add('extension-span');
      if(e.type==='group'){box.classList.add('extension-group');box.append(node('h3',e.label||''));const grid=node('div',null,'extension-grid');box.append(grid);controls(p,c,e.children||[],grid,imageNodes);}
      else if(e.type==='text')box.append(node('p',e.value??e.label??'','extension-tone-'+(e.tone||'normal')));
      else if(e.type==='metric'){box.classList.add('extension-metric');box.dataset.tone=e.tone||'normal';box.append(node('small',e.label),node('strong',String(e.value??'—')+(e.unit?' '+e.unit:''),'extension-tone-'+(e.tone||'normal')));}
      else if(e.type==='button'){
        const button=node('button',e.label||'Изпълни',e.tone==='primary'?'primary-button':'quiet-button');button.disabled=!!e.disabled;button.dataset.unavailable=String(!!e.disabled);
        button.onclick=async()=>{for(const[id,pending]of [...saveTimers])if(id.startsWith(key(p,c)+':')){clearTimeout(pending.timer);pending.flush();}button.disabled=true;try{await action(p,c,e);}finally{if(button.isConnected)button.disabled=!!e.disabled;button.dataset.unavailable=String(!!e.disabled);}};box.append(button);
      }else if(['input','textarea','number','checkbox','select'].includes(e.type)){
        const published=JSON.stringify(e.value);if(previous.has(e.id)&&previous.get(e.id)!==published)saved.delete(e.id);previous.set(e.id,published);
        const label=node('label',null,e.type==='checkbox'?'checkbox-field':'function-field');const input=node(e.type==='select'?'select':e.type==='textarea'?'textarea':'input');input.dataset.pluginField=e.id;input.setAttribute('aria-label',e.label||e.id);input.disabled=!!e.disabled;input.dataset.unavailable=String(!!e.disabled);
        if(e.type==='select')for(const choice of e.options||[]){const option=node('option',choice.label);option.value=choice.value;input.append(option);}
        else if(e.type==='textarea'){input.rows=9;input.className='extension-multiline';}else input.type=e.type==='input'?'text':e.type;
        if(e.placeholder)input.placeholder=e.placeholder;
        for(const property of ['min','max','step','maxLength'])if(e[property]!=null)input[property]=e[property];
        const value=saved.has(e.id)?saved.get(e.id):e.value??(e.type==='checkbox'?false:e.type==='number'?Math.max(e.min??-Infinity,Math.min(e.max??Infinity,0)):e.type==='select'?e.options?.[0]?.value??'':'');
        if(e.type==='checkbox')input.checked=!!value;else input.value=String(value);
        const capture=()=>saved.set(e.id,e.type==='checkbox'?input.checked:e.type==='number'?input.valueAsNumber:input.value);
        const timerKey=key(p,c)+':'+e.id;capture();input.oninput=()=>{capture();if(e.command&&['input','textarea','number'].includes(e.type)){clearTimeout(saveTimers.get(timerKey)?.timer);if(e.type!=='number'||Number.isFinite(input.valueAsNumber)){const flush=()=>{saveTimers.delete(timerKey);action(p,c,e);};saveTimers.set(timerKey,{timer:setTimeout(flush,400),flush});}}};input.onchange=()=>{clearTimeout(saveTimers.get(timerKey)?.timer);saveTimers.delete(timerKey);capture();if(e.command)action(p,c,e);};
        if(e.type==='checkbox')label.append(input,node('span',e.label));else label.append(node('span',e.label),input);box.append(label);
      }else if(e.type==='list'){
        box.classList.add('extension-span');if(e.label)box.append(node('h3',e.label));const list=node('ul',null,'extension-list');box.append(list);
        for(const item of e.items||[]){const row=node('li',null,'extension-list-item'),copy=node('div',null,'extension-list-copy');row.dataset.itemId=item.id;copy.append(node('strong',item.label||item.id));if(item.description)copy.append(node('small',item.description));row.append(copy);
          if(item.status)row.append(node('span',item.status,'extension-list-status extension-tone-'+(item.tone||'normal')));
          if(e.command){const button=node('button',e.actionLabel||'Изпълни','quiet-button');button.disabled=!!e.disabled||!!item.disabled;button.dataset.unavailable=String(button.disabled);button.setAttribute('aria-label',(e.actionLabel||'Изпълни')+' · '+(item.label||item.id));button.onclick=async()=>{button.disabled=true;try{await action(p,c,e,undefined,item.id);}finally{if(button.isConnected)button.disabled=!!e.disabled||!!item.disabled;}};row.append(button);}list.append(row);}
        if(!e.items?.length)list.append(node('li','Списъкът е празен.','settings-note'));
      }else if(e.type==='table'){
        box.classList.add('extension-span');box.append(node('h3',e.label||''));const table=node('div');box.append(table);
        U.table(table,e.columns.map(column=>({title:column.label,value:row=>column.format==='number'?U.number(row[column.key]):column.format==='exposure'?U.time(row[column.key]):row[column.key]})),e.rows,e.command?row=>action(p,c,e,e.rows.indexOf(row)):undefined);
      }else if(e.type==='console'){box.classList.add('extension-span');box.append(node('h3',e.label||'Конзола'),node('pre',e.value||'','extension-console'));
      }else if(e.type==='image'){box.classList.add('extension-span');const src=e.assetId?'plugin-image/'+encodeURIComponent(p.id)+'/'+e.assetId:e.value,old=imageNodes.get(e.id),img=old?.getAttribute('src')===src?old:node('img');if(img.getAttribute('src')!==src)img.src=src;img.dataset.uiImage=e.id;img.loading='lazy';img.decoding='async';img.alt=e.label||'Резултат';img.className='extension-result-image';box.append(img);
      }else if(e.type==='chart'){box.classList.add('extension-span');chart(e,box);}
      if(e.help)box.append(node('small',e.help,'settings-note'));host.append(box);
    }
  }
  function mount(container,p,c){
    const id=key(p,c);if(!mounts.has(id))mounts.set(id,new Set());mounts.get(id).add(container);renderDocument(container,p,c);
  }
  function renderDocument(container,p,c){
    const imageNodes=new Map([...container.querySelectorAll('[data-ui-image]')].map(img=>[img.dataset.uiImage,img]));
    const known=new Set();const visit=items=>{for(const e of items){if(e.type==='group')visit(e.children||[]);else if(['input','textarea','number','select','checkbox'].includes(e.type))known.add(e.id);}};visit(c.elements||[]);
    for(const id of fields(p,c).keys())if(!known.has(id))fields(p,c).delete(id);
    const focused=container.contains(document.activeElement)?document.activeElement.dataset.pluginField:null;const offsets=new Map([...container.querySelectorAll('.extension-console,[data-element]')].map(e=>[e.dataset.element||'console',e.scrollTop]));
    container.replaceChildren();const title=node('header',null,'extension-heading');if(p.logoDataUrl){const logo=node('img');logo.src=p.logoDataUrl;logo.alt='';logo.className='extension-logo';title.append(logo);}title.append(node('span',p.name+' · ПЛЪГИН','eyebrow'),node('h2',c.title));if(c.description)title.append(node('p',c.description,'settings-note'));container.append(title);
    const grid=node('div',null,'extension-grid'+(c.layout==='workbench'?' extension-workbench':''));container.append(grid);controls(p,c,c.elements||[],grid,imageNodes);for(const e of container.querySelectorAll('.extension-console,[data-element]'))e.scrollTop=offsets.get(e.dataset.element||'console')||0;if(focused){const input=[...container.querySelectorAll('[data-plugin-field]')].find(e=>e.dataset.pluginField===focused);input?.focus({preventScroll:true});}
    if(!c.elements?.length)grid.append(node('p','Добавката още не е предоставила данни.','settings-note'));
  }
  function select(slot,id,notify=true){
    active[slot]=id;$(slot+'-content').hidden=!!id;const host=$(slot+'-extensions');host.hidden=!id;host.replaceChildren();
    for(const button of $(slot+'-extension-tabs').querySelectorAll('button'))button.setAttribute('aria-pressed',String((button.dataset.extensionKey||null)===id));
    const current=approved(slot).find(x=>key(x.p,x.c)===id);if(current){if(slot==='preparation'){const back=node('button','← Подбор на данни','flow-back');back.onclick=()=>leavePreparation(current.p,current.c).catch(error=>U.error(slot,error));host.append(back);}const content=node('section',null,'declarative-plugin');host.append(content);mount(content,current.p,current.c);if(notify&&current.c.activate)U.call('plugin.ui.activate',{pluginId:current.p.id,contributionId:current.c.id,context:selection(slot)}).catch(error=>U.error(slot,error));}
    if(!id)window.AstroArchiveFunctions.show(slot).then(()=>window.AstroArchiveSession?.activate(slot)).catch(error=>U.error(slot,error));
    if(notify)window.AstroArchiveSession?.changed('extensions');
  }
  function renderActivities(){const activity=$('preparation-plugin-activity');activity.replaceChildren();for(const{p,c}of approved('preparation-activity')){if(!c.elements?.length)continue;const section=node('section',null,'panel declarative-plugin');activity.append(section);mount(section,p,c);}}
  function render(){
    layout();
    renderHeader();
    for(const slot of ['analysis','preparation']){
      const menu=$(slot+'-extension-tabs'),entries=approved(slot);menu.replaceChildren();menu.hidden=slot==='preparation'||!entries.length;
      const base=node('button',slot==='analysis'?'Основен анализ':'Основна подготовка');base.onclick=()=>select(slot,null);menu.append(base);
      for(const {p,c}of entries){const button=node('button',c.title);button.dataset.extensionKey=key(p,c);button.onclick=()=>select(slot,key(p,c));menu.append(button);}
      const actionHost=$(slot==='analysis'?'q-plugin-actions':'w-plugin-actions');
      if(actionHost){actionHost.replaceChildren();actionHost.hidden=!entries.length;for(const{p,c}of entries){const button=node('button',null,'flow-card');button.append(node('span','ПЛЪГИН','extension-plugin-badge'));if(p.logoDataUrl){const img=node('img');img.src=p.logoDataUrl;img.alt='';img.className='extension-card-logo';button.append(img);}button.append(node('strong',c.title),node('small',p.name+' · '+(c.description||'Добавка')));button.onclick=()=>select(slot,key(p,c));actionHost.append(button);}}
      const selected=entries.some(x=>key(x.p,x.c)===active[slot])?active[slot]:null;
      if(selected)select(slot,selected,false);else{$(slot+'-content').hidden=false;$(slot+'-extensions').hidden=true;active[slot]=null;base.setAttribute('aria-pressed','true');}
    }
    renderActivities();
    const archive=$('archive-extensions');archive.replaceChildren();
    for(const{p,c}of approved('archive')){const section=node('section',null,'panel declarative-plugin');archive.append(section);mount(section,p,c);}
    const analysisTools=$('q-plugin-tools');if(analysisTools){analysisTools.replaceChildren();for(const{p,c}of approved('analysis-tools')){const section=node('section',null,'analysis-plugin-tool');analysisTools.append(section);mount(section,p,c);}analysisTools.hidden=!analysisTools.children.length;}
    const custom=$('custom-plugin-panels');if(custom){custom.replaceChildren();for(const{p,c}of approved('customization').filter(x=>!x.c.archiveKey)){const section=node('section',null,'declarative-plugin');custom.append(section);mount(section,p,c);}if(!custom.children.length)custom.append(node('p','Няма други включени добавки с визуални настройки. Настройките на плъгин архивите са при архивните места.','settings-note'));}
    const pending=plugins.filter(p=>p.enabled&&!p.uiAllowed&&p.contributions?.length),notice=$('extension-permission-notice');notice.hidden=!pending.length;notice.querySelector('span').textContent=pending.map(p=>p.name).join(', ')+' иска разрешение да добави полета към интерфейса.';
    for(const[id,holders]of mounts){for(const holder of holders)if(!holder.isConnected)holders.delete(holder);if(!holders.size)mounts.delete(id);}
    for(const id of values.keys())if(!plugins.some(p=>p.enabled&&id.startsWith(p.id+':'))){values.delete(id);publishedDefaults.delete(id);}
    for(const id of clientValues.keys())if(!plugins.some(p=>p.enabled&&p.uiAllowed&&id.startsWith(p.id+':')))clientValues.delete(id);
    renderViewer(true);
    window.dispatchEvent(new Event('astroarchive-extensions-rendered'));
  }
  function renderViewer(notify=false){
    if(!viewerHost?.isConnected)return;
    viewerHost.replaceChildren();
    for(const{p,c}of approved('viewer-tools')){
      const section=node('section',null,'viewer-plugin-tool');viewerHost.append(section);mount(section,p,c);
      if(notify&&c.activate&&viewerContext?.viewerId)U.call('plugin.ui.activate',{pluginId:p.id,contributionId:c.id,context:{...viewerContext}}).catch(error=>{section.replaceChildren(node('p',error.message,'settings-note'));});
    }
    if(loaded&&!approved('viewer-tools').some(({p,c})=>p.id==='ai-training'&&c.id==='rating')){
      const plugin=plugins.find(p=>p.id==='ai-training'),section=node('section',null,'viewer-plugin-tool');
      section.append(node('h3','Ръчна оценка на кадъра'));
      const reason=!plugin?'Добавката AI Training не е намерена.':plugin.problem||(!plugin.enabled?'Добавката AI Training е изключена.':!plugin.uiAllowed?'Разреши интерфейса на AI Training, за да оценяваш кадъра тук.':'Ръчната оценка в момента не е достъпна.');
      section.append(node('p',reason,'extension-tone-warning'));
      if(!window.AstroArchiveRemote){const button=node('button','Настройки на добавките','quiet-button');button.onclick=()=>window.AstroArchivePreferences.open('plugins');section.append(button);}
      viewerHost.append(section);
    }
    viewerHost.hidden=!viewerHost.children.length;
  }
  function renderHeader(){const host=$('header-extensions');if(!host)return;host.replaceChildren();for(const{p,c}of approved('header')){const section=node('section');section.dataset.plugin=p.id;section.dataset.contribution=c.id;controls(p,c,c.elements||[],section);host.append(section);}host.hidden=!host.children.length;}
  window.AstroArchiveSession.register('extensions',{read:()=>({...active}),write:async value=>{for(const slot of ['analysis','preparation']){const id=value[slot]||null;if(active[slot]===id)continue;if(id&&!approved(slot).some(x=>key(x.p,x.c)===id))continue;select(slot,id,false);}}});
  window.AstroArchiveExtensions={
    viewerReady(dialog,data){const changed=viewerContext?.viewerId!==data.id;viewerContext={ids:data.sourceId?[data.sourceId]:[],stage:'viewer',viewerId:data.id};viewerHost=dialog.querySelector('#v-plugin-tools');if(changed)renderViewer(true);},
    viewerClosed(){viewerContext=null;if(viewerHost)viewerHost.replaceChildren();viewerHost=null;},
    connect(bindings){api=bindings;},update(value){plugins=value||[];loaded=true;render();},
    toolsStatus(slot,pluginId=null){
      if(!loaded)return{reason:'loading'};
      const declared=plugins.filter(p=>(!pluginId||p.id===pluginId)&&p.contributions?.some(c=>c.slot===slot&&(!c.clients||c.clients.includes(window.AstroArchiveRemote?'remote':'desktop'))));
      if(approved(slot).some(({p})=>!pluginId||p.id===pluginId))return{reason:'ready'};
      const failed=declared.find(p=>p.problem);
      if(failed)return{reason:'error',message:failed.problem};
      if(declared.some(p=>p.enabled&&!p.uiAllowed))return{reason:'permission'};
      return{reason:declared.length?'disabled':'missing'};
    },
    mountSettings(container,p,c){if(p.enabled&&p.uiAllowed&&p.available)mount(container,p,c);},
    mountArchiveSettings(container,archive){for(const{p,c}of approved('customization').filter(x=>x.p.id===archive.ownerPlugin&&x.c.archiveKey===archive.pluginArchiveKey)){const section=node('section',null,'plugin-archive-options');container.append(section);mount(section,p,c);}},
    setClientValues(pluginId,contributionId,updates){
      const item=approved('header').find(x=>x.p.id===pluginId&&x.c.id===contributionId);if(!item)return false;
      if(!updates||typeof updates!=='object'||Array.isArray(updates))throw Error('Невалидни локални UI стойности.');const mapped=new Map();
      for(const[id,value]of Object.entries(updates)){const element=item.c.elements?.find(e=>e.id===id&&['metric','text'].includes(e.type));if(!element||!value||Object.keys(value).some(k=>!['value','tone','unit'].includes(k))||typeof value.value!=='string'||value.value.length>4000||value.tone&&!['normal','success','warning','error','primary'].includes(value.tone)||value.unit!=null&&(typeof value.unit!=='string'||value.unit.length>100))throw Error('Непозволени локални UI данни.');mapped.set(id,{...value});}
      clientValues.set(key(item.p,item.c),mapped);renderHeader();return true;
    },
    select,isActive:slot=>!!active[slot],
    broadcast(){},theme(){for(const[id,holders]of mounts){const item=plugins.flatMap(p=>(p.contributions||[]).map(c=>({p,c}))).find(x=>key(x.p,x.c)===id);if(item)for(const holder of holders)if(holder.isConnected)renderDocument(holder,item.p,item.c);}}
  };
  window.addEventListener('astroarchive-python-event',async event=>{
    const data=event.detail;
    if(data.name==='activity.changed')finishNavigation(data.snapshot?.tasks);
    if(data.name==='plugins.changed'){
      try{const state=await U.call('plugin.ui.state');window.AstroArchiveExtensions.update(state.plugins);}catch(error){console.warn(error.message);}
    }
    if(data.name==='plugin.ui.changed'){
      const plugin=plugins.find(p=>p.id===data.plugin&&p.uiAllowed&&p.enabled&&p.uiRevision===data.revision),c=plugin?.contributions.find(c=>c.id===data.contribution);
      if(c){c.elements=data.elements;if(c.slot==='header')renderHeader();if(c.slot==='preparation-activity')renderActivities();else for(const holder of mounts.get(key(plugin,c))||[])if(holder.isConnected)renderDocument(holder,plugin,c);}
    }
    if(data.name==='plugin.ui.removed'){plugins=plugins.map(p=>p.id===data.plugin?{...p,enabled:false}:p);render();}
  });
  window.addEventListener('astroarchive-locations-saved',event=>{for(const archive of event.detail.archives||[])for(const{p,c}of approved('customization').filter(x=>x.p.id===archive.ownerPlugin&&x.c.archiveKey===archive.pluginArchiveKey&&x.c.activate))U.call('plugin.ui.activate',{pluginId:p.id,contributionId:c.id,context:{archiveId:archive.id}}).catch(error=>console.warn(error.message));});
  let messageTimer;
  window.addEventListener('astroarchive-plugin-message',event=>{let status=$('extension-message');if(!status){status=node('div',null,'extension-message');status.id='extension-message';status.setAttribute('role','status');document.body.append(status);}status.textContent=event.detail.message;status.hidden=false;clearTimeout(messageTimer);messageTimer=setTimeout(()=>{status.hidden=true;},4500);});
  layout();
})();
