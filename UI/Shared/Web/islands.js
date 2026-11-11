"use strict";
// One layout controller for the three workspaces. Original controls keep their
// nodes/listeners; resetting unwraps them at their original DOM anchors.
(() => {
  const U=window.AstroArchiveFunctions,{$,node}=U,clone=value=>JSON.parse(JSON.stringify(value));
  const tr=(bg,en)=>window.AstroArchiveI18n.locale.startsWith('en')?en:bg;
  const sections=new Map();let model={version:1,sections:{}},revision=0,saved=0,saving=null,timer;
  const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
  function clean(value){
    const result={version:1,sections:{}};
    for(const key of ['archive','analysis','preparation']){
      const input=value?.sections?.[key];if(!input||!Array.isArray(input.order)||!input.panels)continue;
      if(key==='analysis'&&!input.order.includes('analysis-tools')||key==='preparation'&&!input.order.includes('preparation-status'))continue;
      const order=[...new Set(input.order.filter(id=>typeof id==='string'&&/^[a-z0-9_-]{1,64}$/.test(id)))].slice(0,32),panels=Object.create(null);
      for(const id of order){const p=input.panels[id]||{};panels[id]={span:clamp(Math.round(Number(p.span)||24),3,24)};if(Number.isFinite(p.height))panels[id].height=clamp(Math.round(p.height),140,2400);}
      if(order.length)result.sections[key]={order,panels};
    }return result;
  }
  const snapshot=()=>clone(model);
  function status(text,error=false){for(const s of sections.values()){s.status.textContent=text;s.status.dataset.error=String(error);s.retry.hidden=!error;s.tools.hidden=!s.editing&&!error;}}
  function toggleLabel(s){
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS(icon.namespaceURI,'path');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');
    path.setAttribute('d',(s.editing?'M8 10V7a4 4 0 0 1 7.5-2':'M8 10V7a4 4 0 0 1 8 0v3')+' M6 10h12v10H6z M12 14v3');icon.append(path);
    s.button.replaceChildren(icon,node('span',tr('Местене и размер','Move and resize')),node('span',s.editing?tr('ВКЛ.','ON'):tr('ИЗКЛ.','OFF'),'islands-toggle-state'));
    s.button.setAttribute('aria-pressed',String(s.editing));s.button.title=s.editing?tr('Изключи, за да заключиш местата и размерите. Промените се запазват автоматично.','Turn off to lock positions and sizes. Changes save automatically.'):tr('Включи, за да местиш и оразмеряваш панелите.','Turn on to move and resize panels.');
  }
  async function flush(){
    clearTimeout(timer);if(saving){await saving;if(revision===saved)return;}
    if(revision===saved)return;
    saving=(async()=>{try{while(saved!==revision){const mine=revision;await U.call('ui.layout.save',{layout:snapshot()});saved=mine;}status(tr('Подредбата е запазена.','Layout saved.'));}catch(error){status(tr('Подредбата не е запазена: ','Layout was not saved: ')+error.message,true);throw error;}finally{saving=null;}})();
    return saving;
  }
  function changed(){revision++;status(tr('Запазване на подредбата…','Saving layout…'));clearTimeout(timer);timer=setTimeout(()=>flush().catch(()=>{}),250);}
  function unwrap(s){
    for(const p of s.items){if(!p.frame)continue;p.observer.disconnect();p.body.classList.remove('island-body');p.anchor.replaceWith(p.body);p.frame.remove();p.frame=p.anchor=null;}
    s.root.classList.remove('islands-grid','islands-editing');
  }
  function wrap(s){
    for(const p of s.items){if(p.frame)continue;
      p.anchor=document.createComment('island:'+p.id);p.body.before(p.anchor);
      const frame=node('section',null,'island-frame'),bar=node('div',null,'island-bar'),handle=node('button',null,'island-move'),name=node('span',p.title());
      frame.dataset.island=p.id;bar.append(handle);handle.type='button';handle.append(node('span','⠿','island-grip'),name);handle.title=tr('Влачи за местене · стрелки за пренареждане','Drag to move · arrow keys to reorder');
      for(const [text,delta,label]of [['↑',-1,tr('Нагоре','Move earlier')],['↓',1,tr('Надолу','Move later')]]){const b=node('button',text,'island-order');b.type='button';b.setAttribute('aria-label',label+' · '+p.title());b.onclick=()=>move(s,p,delta);bar.append(b);}
      const size=node('button','◢','island-resize');size.type='button';size.setAttribute('aria-label',tr('Промени размера · ','Resize · ')+p.title());size.title=tr('Влачи за размер · стрелките променят ширина и височина','Drag to resize · arrows change width and height');
      p.body.before(frame);p.body.classList.add('island-body');frame.append(bar,p.body,size);p.frame=frame;p.name=name;
      const visibility=()=>{frame.hidden=p.body.hidden;};p.observer=new MutationObserver(visibility);p.observer.observe(p.body,{attributes:true,attributeFilter:['hidden']});visibility();
      handle.onpointerdown=event=>begin(s,p,event,false);size.onpointerdown=event=>begin(s,p,event,true);
      handle.onkeydown=event=>{const delta=['ArrowLeft','ArrowUp'].includes(event.key)?-1:['ArrowRight','ArrowDown'].includes(event.key)?1:0;if(delta){event.preventDefault();move(s,p,delta);handle.focus();}};
      size.onkeydown=event=>{if(!s.editing||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const rect=frame.getBoundingClientRect();ensure(s);const value=model.sections[s.key].panels[p.id];if(event.key==='ArrowLeft'||event.key==='ArrowRight')value.span=clamp(value.span+(event.key==='ArrowRight'?1:-1),minSpan(s,p),24);else value.height=clamp((value.height||rect.height)+(event.key==='ArrowDown'?32:-32),140,2400);render(s);changed();};
    }
  }
  function minSpan(s,p){const width=s.root.getBoundingClientRect().width;return clamp(Math.ceil(p.minWidth/Math.max(1,width)*24),3,24);}
  function ensure(s){
    if(model.sections[s.key]){const layout=model.sections[s.key];for(const p of s.items){if(!layout.order.includes(p.id))layout.order.push(p.id);layout.panels[p.id]||={span:p.span};}return;}
    const width=s.root.getBoundingClientRect().width;
    model.sections[s.key]={order:s.items.map(p=>p.id),panels:Object.fromEntries(s.items.map(p=>[p.id,{span:width?clamp(Math.round(p.body.getBoundingClientRect().width/width*24),minSpan(s,p),24):p.span}]))};
  }
  function render(s){
    const layout=model.sections[s.key];if(!layout&&!s.editing){unwrap(s);return;}
    wrap(s);s.root.classList.toggle('islands-editing',s.editing);s.root.classList.toggle('islands-grid',!!layout);
    for(const p of s.items)for(const control of p.frame.querySelectorAll('.island-bar button,.island-resize'))control.disabled=!s.editing;
    if(!layout)return;
    const order=[...layout.order.filter(id=>s.items.some(p=>p.id===id)),...s.items.map(p=>p.id).filter(id=>!layout.order.includes(id))];
    const present=[...s.root.children].filter(e=>e.classList.contains('island-frame')).map(e=>e.dataset.island);
    if(present.join(',')!==order.join(','))for(const id of order)s.root.append(s.items.find(p=>p.id===id).frame);
    for(const id of order){const p=s.items.find(p=>p.id===id),value=layout.panels[id]||{span:p.span};p.frame.style.setProperty('--island-span',String(Math.max(minSpan(s,p),value.span)));p.frame.dataset.sized=String(!!value.height);p.frame.style.setProperty('--island-height',(value.height||0)+'px');}
  }
  function move(s,p,delta){
    if(!s.editing)return;
    ensure(s);const order=model.sections[s.key].order,visible=order.filter(id=>{const item=s.items.find(p=>p.id===id);return item&&!item.body.hidden;}),at=visible.indexOf(p.id),to=clamp(at+delta,0,visible.length-1);if(at===to)return;
    const target=visible[to];order.splice(order.indexOf(p.id),1);order.splice(order.indexOf(target)+(delta>0?1:0),0,p.id);render(s);changed();
  }
  function begin(s,p,event,resize){
    if(event.button!==0||!s.editing)return;event.preventDefault();const control=event.currentTarget,origin={x:event.clientX,y:event.clientY},before=snapshot(),wasDirty=revision!==saved;
    let active=false,target=null,after=false,ghost=null,latest=event,raf;
    const rect=p.frame.getBoundingClientRect();control.setPointerCapture(event.pointerId);
    function paint(){raf=null;const dx=latest.clientX-origin.x,dy=latest.clientY-origin.y;if(!active&&Math.hypot(dx,dy)<5)return;
      if(!active){active=true;if(resize){ensure(s);render(s);}else{ghost=node('div',p.title(),'island-drag-preview');document.body.append(ghost);}document.documentElement.classList.add('island-gesture');}
      if(resize){const value=model.sections[s.key].panels[p.id],width=s.root.getBoundingClientRect().width;value.span=clamp(Math.round((rect.width+dx)/Math.max(1,width)*24),minSpan(s,p),24);value.height=clamp(Math.round((rect.height+dy)/8)*8,140,2400);render(s);}
      else{ghost.style.transform=`translate(${latest.clientX+14}px,${latest.clientY+14}px)`;target?.frame.classList.remove('island-drop-target');target=null;
        const frame=document.elementsFromPoint(latest.clientX,latest.clientY).map(e=>e.closest('.island-frame')).find(f=>f&&f.parentElement===s.root&&f!==p.frame);target=s.items.find(item=>item.frame===frame)||null;
        if(target){target.frame.classList.add('island-drop-target');const hit=frame.getBoundingClientRect();after=Math.abs(hit.top-rect.top)<50?latest.clientX>hit.left+hit.width/2:latest.clientY>hit.top+hit.height/2;}
      }
    }
    function update(e){latest=e;if(!raf)raf=requestAnimationFrame(paint);}
    function finish(cancel=false){
      if(raf){cancelAnimationFrame(raf);raf=null;if(!cancel)paint();}
      control.onpointermove=control.onpointerup=control.onpointercancel=control.onlostpointercapture=null;if(control.hasPointerCapture(event.pointerId))control.releasePointerCapture(event.pointerId);
      ghost?.remove();target?.frame.classList.remove('island-drop-target');document.documentElement.classList.remove('island-gesture');document.removeEventListener('keydown',escape);
      if(cancel){model=before;render(s);if(wasDirty)status(tr('Има незапазена подредба.','There is an unsaved layout.'));return;}
      if(!active)return;
      if(!resize&&target){ensure(s);const order=model.sections[s.key].order;order.splice(order.indexOf(p.id),1);order.splice(order.indexOf(target.id)+(after?1:0),0,p.id);render(s);changed();}
      else if(resize)changed();
    }
    const escape=e=>{if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();finish(true);}};document.addEventListener('keydown',escape);
    control.onpointermove=update;control.onpointerup=()=>finish();control.onpointercancel=control.onlostpointercapture=()=>finish(true);
  }
  function editing(s,value){s.editing=value;toggleLabel(s);s.tools.hidden=!value&&s.status.dataset.error!=='true';render(s);}
  function register(key,root,definitions){
    const page=$('page-'+key),heading=page.querySelector('.page-heading'),button=node('button',null,'quiet-button islands-toggle'),tools=node('div',null,'islands-help'),statusNode=node('span'),retry=node('button',tr('Опитай пак','Retry'),'text-button');
    button.type='button';button.id='islands-edit-'+key;button.setAttribute('aria-pressed','false');button.setAttribute('aria-controls',root.id||'islands-root-'+key);if(!root.id)root.id='islands-root-'+key;
    (page.querySelector('.archive-actions')||heading).append(button);tools.hidden=true;retry.type='button';retry.hidden=true;retry.onclick=()=>flush().catch(()=>{});statusNode.setAttribute('role','status');tools.append(node('span',tr('Влачи заглавието за местене и долния десен ъгъл за размер. Подредбата се запазва автоматично.','Drag a title to move; drag the lower-right corner to resize. Layout saves automatically.')),statusNode,retry);root.before(tools);
    const s={key,root,button,tools,status:statusNode,retry,editing:false,items:definitions.map(([id,bg,en,body,minWidth=260,span=24])=>({id,title:()=>tr(bg,en),body,minWidth,span}))};sections.set(key,s);button.onclick=()=>{const wasEditing=s.editing;editing(s,!wasEditing);if(wasEditing)flush().catch(()=>{});};editing(s,false);
    let width=0;new ResizeObserver(()=>{const next=root.getBoundingClientRect().width;if(next&&Math.abs(next-width)>1){width=next;render(s);}}).observe(root);
  }
  register('archive',document.querySelector('.archive-layout'),[
    ['navigation','Навигация и справка','Navigation and totals',document.querySelector('.archive-sidebar'),210,4],
    ['files','Съдържание на архива','Archive contents',document.querySelector('#page-archive .content-panel'),360,20]
  ]);
  register('analysis',$('q-workspace'),[
    ['analysis-plot','Диаграма, таблица и кадри','Chart, table and frames',$('q-plot-panel'),360,18],
    ['analysis-tools','Настройки и подбор','Options and selection',$('q-tools-panel'),260,6]
  ]);
  register('preparation',$('w-workspace'),[
    ['preparation-files','Изпратени файлове','Sent files',$('w-source-panel'),230,8],
    ['preparation-status','Статус на работата','Work status',$('w-status-panel'),230,7],
    ['preparation-results','Нови резултати','New results',$('w-results-panel'),300,9]
  ]);
  const flowVisibility=()=>{for(const key of ['analysis','preparation']){const s=sections.get(key),active=$(`${key}-content`).dataset.phase===(key==='analysis'?'result':'work');s.button.hidden=!active;s.tools.hidden=!active||(!s.editing&&s.status.dataset.error!=='true');}};
  window.addEventListener('astroarchive-flow-phase',flowVisibility);flowVisibility();
  const settings=node('section',null,'custom-block islands-settings'),scope=node('select');scope.id='islands-reset-scope';scope.setAttribute('aria-label',tr('Изглед за възстановяване','Workspace to restore'));
  for(const[value,bg,en]of [['all','Всички основни изгледи','All workspaces'],['archive','Архив','Archive'],['analysis','Анализ','Analysis'],['preparation','Подготовка','Preparation']]){const option=node('option',tr(bg,en));option.value=value;scope.append(option);}
  const resetLabel=()=>{$('ui-layout-reset').textContent=tr('Върни оригиналните места и размери','Restore original positions and sizes')+(scope.value==='all'?'':' · '+scope.selectedOptions[0].textContent);};scope.onchange=resetLabel;
  settings.append(node('h3',tr('Подвижни панели · острови','Movable panels · islands')),node('p',tr('Във всеки основен изглед включи „Местене и размер“. При изключване панелите се заключват. Всяка промяна се запазва автоматично. Връщането на местата и размерите тук е преглед със „Запази“ и „Отказ“.','Turn on Move and resize in each workspace. Turning it off locks the panels. Every change saves automatically. Restoring positions and sizes here is a preview with Save and Cancel.'),'settings-note'),scope);$('customization-editor').prepend(settings);
  window.AstroArchiveIslands={snapshot,flush,
    accept(value){model=clean(value);resetLabel();for(const s of sections.values()){toggleLabel(s);render(s);for(const p of s.items)if(p.name)p.name.textContent=p.title();}},
    restore(scope='all'){if(scope==='all')model.sections={};else delete model.sections[scope];for(const s of sections.values()){s.editing=false;editing(s,false);}},
    resetScope:()=>scope.value
  };
})();
