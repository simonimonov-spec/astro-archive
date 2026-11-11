"use strict";
(() => {
 const U=window.AstroArchiveFunctions,A=window.AstroArchiveAnalysis,P=window.AstroArchivePython,{node}=U;
 let dialog=null,body=null,view='list',filter='all',sort='name',direction='asc',query='',page=0,generation=0,timer=null,restoring=false;
 const changed=()=>{if(!restoring)window.AstroArchiveSession?.changed('analysis');};
 const cache=new Map();let queue=Promise.resolve();const pageSize=36;
 const frames=()=>A.frameState().report?.frames||[];
 const currentRows=()=>{const term=query.trim().toLocaleLowerCase();return frames().filter(r=>{
   if(term&&!String(r.name||'').toLocaleLowerCase().includes(term))return false;
   if(filter==='excluded'&&r.decision!=='excluded'||filter==='included'&&r.decision==='excluded'||filter==='ai'&&!window.AstroArchiveQualityPlot.warningKinds(r).ai)return false;return true;
  }).sort((a,b)=>{
   const value=r=>sort==='name'?String(r.name||'').toLocaleLowerCase():sort==='night'?String(r.meta?.night||''):sort==='classic'?A.frameScore(r.id):sort==='ai'?window.AstroArchiveQualityPlot.inspection(r).quality:sort==='aiCoverage'?window.AstroArchiveQualityPlot.inspection(r).coverage:r[sort];
   const x=value(a),y=value(b),xMissing=x==null||typeof x==='number'&&!Number.isFinite(x),yMissing=y==null||typeof y==='number'&&!Number.isFinite(y);
   if(xMissing||yMissing)return xMissing===yMissing?0:xMissing?1:-1;
   const result=typeof x==='number'?x-y:String(x).localeCompare(String(y),'bg');return direction==='asc'?result:-result;
  });};
 function group(start){const state=A.frameState(),chosen=frames().filter(r=>state.selected.has(r.id)).map(r=>r.id),ids=chosen.length?chosen:frames().map(r=>r.id);if(ids.length)A.openInspection(ids,ids.includes(start)?start:ids[0]);}
 function toggle(id){const selected=new Set(A.frameState().selected);selected.has(id)?selected.delete(id):selected.add(id);A.frameSelect([...selected]);render();}
 function thumb(id,token,img){
  if(cache.has(id)){img.src=cache.get(id);return;}
  queue=queue.catch(()=>{}).then(async()=>{if(token!==generation||!dialog?.open||view!=='thumbs')return;const result=await P.call('python.analysis.thumbnail',{id});if(result?.image){cache.set(id,result.image);while(cache.size>100)cache.delete(cache.keys().next().value);if(token===generation&&img.isConnected)img.src=result.image;}}).catch(()=>{if(img.isConnected)img.alt='Прегледът е недостъпен';});
 }
 function render(){
  if(!dialog?.open||!body)return;const host=dialog.querySelector('#q-frame-items'),counter=dialog.querySelector('#q-frame-count'),pager=dialog.querySelector('#q-frame-pages');if(!host)return;
  const rows=currentRows();page=Math.min(page,Math.max(0,Math.ceil(rows.length/pageSize)-1));const slice=rows.slice(page*pageSize,(page+1)*pageSize),state=A.frameState(),token=++generation;host.replaceChildren();host.className=view==='thumbs'?'q-frame-items q-frame-thumbs':'q-frame-items';
  counter.textContent=`${rows.length} показани · ${state.selected.size} маркирани · ${frames().filter(r=>r.decision==='excluded').length} изключени общо`;if(view==='list'){const header=node('div',null,'q-frame-table-header');for(const label of ['Избор','Кадър / вечер','FWHM X','FWHM Y','Roundness','wFWHM','Фон','# Stars','X','Y','Стандартно %','AI %','AI покритие %','Стандартни предупреждения','AI предупреждения','Подбор'])header.append(node('span',label));host.append(header);}
  for(const row of slice){const item=node('article',null,'q-frame-row');item.dataset.excluded=String(row.decision==='excluded');item.dataset.id=row.id;item.tabIndex=0;
   const check=node('input');check.type='checkbox';check.checked=state.selected.has(row.id);check.setAttribute('aria-label','Маркирай '+row.name);check.addEventListener('click',e=>e.stopPropagation());check.onchange=()=>toggle(row.id);item.append(check);
   if(view==='thumbs'){const img=node('img');img.alt='Миниатюра · '+row.name;img.loading='lazy';item.append(img);thumb(row.id,token,img);}
   const meta=node('div',null,'q-frame-meta'),name=node('strong',row.name||'Кадър');name.className='q-frame-name';const score=A.frameScore(row.id),ai=window.AstroArchiveQualityPlot.inspection(row).quality,comparison=window.AstroArchiveQualityPlot.inspection(row);
   meta.append(name,node('small',`${row.meta?.night||'—'} · стандартна ${Number.isFinite(score)?score+'%':'—'} · AI ${Number.isFinite(ai)?ai.toFixed(1)+'%':'—'}`),node('small',row.decision==='excluded'?'Изключен':'В подбора'));meta.append(node('small',window.AstroArchiveQualityPlot.comparisonText(row)));item.append(meta);
   if(view==='list'){const format=v=>Number.isFinite(v)?new Intl.NumberFormat('bg',{maximumFractionDigits:2}).format(v):'—';const warnings=(row.warnings||[]).filter(w=>w.origin==='ai').map(w=>w.message).join(' · ')||'—';for(const value of [row.fwhm,row.fwhmY,row.roundness,row.weightedFwhm,row.background,row.stars,row.shiftX,row.shiftY,score,ai,comparison.coverage]){const cell=node('span',format(value),'q-frame-value');cell.title=window.AstroArchiveQualityPlot.comparisonText(row);item.append(cell);}const standard=window.AstroArchiveQualityPlot.visibleWarnings(row).filter(w=>w.origin!=='ai').map(w=>w.message).join(' · ')||'—',standardCell=node('span',standard,'q-frame-warning');standardCell.title=standard;item.append(standardCell);const cell=node('span',warnings,'q-frame-warning');cell.title=warnings;item.append(cell);}const action=node('button',row.decision==='excluded'?'Върни':'Изключи','quiet-button');action.onclick=e=>{e.stopPropagation();A.frameDecision(row.id,row.decision==='excluded'?'approved':'excluded');};item.append(action);
   item.onclick=e=>{if(e.target.closest('button,input'))return;clearTimeout(timer);timer=setTimeout(()=>toggle(row.id),260);};item.ondblclick=e=>{e.preventDefault();clearTimeout(timer);group(row.id);};item.oncontextmenu=e=>{e.preventDefault();clearTimeout(timer);group(row.id);};item.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();group(row.id);}else if(e.key===' '){e.preventDefault();toggle(row.id);}};host.append(item);
  }
  if(!slice.length)host.append(node('p','Няма кадри за избрания филтър.','function-note'));
  pager.replaceChildren();const back=node('button','←','quiet-button'),next=node('button','→','quiet-button');back.disabled=page===0;next.disabled=(page+1)*pageSize>=rows.length;back.onclick=()=>{page--;render();changed();};next.onclick=()=>{page++;render();changed();};pager.append(back,node('span',rows.length?`${page*pageSize+1}–${page*pageSize+slice.length} от ${rows.length}`:'0 кадъра'),next);
 }
 function open(){
  if(dialog?.open){dialog.focus();return;}if(!frames().length)return;
  U.modal('Кадри от анализа',(content,box)=>{
   dialog=box;body=content;box.classList.add('q-frame-dialog');
   const controls=node('div',null,'q-frame-controls'),viewSelect=node('select');viewSelect.id='q-frame-view';viewSelect.setAttribute('aria-label','Изглед');for(const [v,t] of [['list','Списък'],['thumbs','Миниатюри']])viewSelect.add(new Option(t,v));viewSelect.value=view;viewSelect.onchange=()=>{view=viewSelect.value;page=0;render();changed();};
   const f=node('select');f.id='q-frame-filter';f.setAttribute('aria-label','Филтър');for(const [v,t]of [['all','Всички'],['included','В подбора'],['excluded','Изключени'],['ai','Предупреждение от AI']])f.add(new Option(t,v));f.value=filter;f.onchange=()=>{filter=f.value;page=0;render();changed();};
   const s=node('select');s.id='q-frame-sort';s.setAttribute('aria-label','Подреждане');for(const [v,t]of [['name','Име'],['night','Вечер'],['fwhm','FWHM X'],['fwhmY','FWHM Y'],['roundness','Roundness'],['weightedFwhm','wFWHM'],['shiftX','X Position'],['shiftY','Y Position'],['stars','Брой звезди'],['background','Фон'],['classic','Стандартна оценка'],['ai','AI индекс'],['aiCoverage','AI сравнена област']])s.add(new Option(t,v));s.value=sort;s.onchange=()=>{sort=s.value;page=0;render();changed();};
   const d=node('select');d.id='q-frame-direction';d.setAttribute('aria-label','Посока на подреждане');d.add(new Option('Мин → Макс','asc'));d.add(new Option('Макс → Мин','desc'));d.value=direction;d.onchange=()=>{direction=d.value;render();changed();};
   const search=node('input');search.id='q-frame-search';search.type='search';search.maxLength=200;search.placeholder='Търси кадър';search.value=query;search.oninput=()=>{query=search.value;page=0;render();changed();};controls.append(viewSelect,f,s,d,search);
   const count=node('p','','function-note');count.id='q-frame-count';const hint=node('p','Маркирай с отметка. Двоен клик или десен бутон отваря маркираните; ако няма маркирани — всички.','function-note');const items=node('div');items.id='q-frame-items';const pages=node('div',null,'function-actions');pages.id='q-frame-pages';content.append(controls,count,hint,items,pages);box.addEventListener('close',()=>{dialog=null;body=null;generation++;clearTimeout(timer);changed();});queueMicrotask(render);
  });
  changed();
 }
 let wired=null,oldPrevious=null,oldNext=null,keyboard=null,groupPage=null;
 function restoreViewer(){if(!wired)return;const previous=wired.querySelector('#v-previous'),next=wired.querySelector('#v-next');if(previous&&oldPrevious)previous.onclick=oldPrevious;if(next&&oldNext)next.onclick=oldNext;if(keyboard)wired.removeEventListener('keydown',keyboard,true);wired.querySelector('#q-inspection-group')?.remove();wired.querySelector('#q-inspection-action')?.remove();wired.querySelector('#q-excluded-overlay')?.remove();wired=null;groupPage=null;window.dispatchEvent(new Event('astroarchive-inspection-decision'));}
 function refreshViewer(){
  const box=document.querySelector('dialog.image-viewer[open]'),state=A.frameState();if(!box||!state.report){restoreViewer();return;}
  let current=state.inspection[state.inspectionIndex];if(!state.inspection.length||!current){restoreViewer();return;}
  const row=frames().find(r=>r.id===current);if(!row){restoreViewer();return;}
  if(wired!==box){restoreViewer();wired=box;const prev=box.querySelector('#v-previous'),next=box.querySelector('#v-next');oldPrevious=prev.onclick;oldNext=next.onclick;
   const navigate=delta=>{const s=A.frameState();s.inspectionIndex=Math.max(0,Math.min(s.inspection.length-1,s.inspectionIndex+delta));P.image(s.inspection[s.inspectionIndex]).catch(e=>U.error('analysis',e));};
   prev.onclick=()=>navigate(-1);next.onclick=()=>navigate(1);keyboard=e=>{if(e.ctrlKey||e.metaKey||e.altKey||/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();e.stopImmediatePropagation();navigate(e.key==='ArrowLeft'?-1:1);}};box.addEventListener('keydown',keyboard,true);
   const details=node('details',null,'q-inspection-group');details.id='q-inspection-group';details.open=true;details.append(node('summary','Избрани кадри'),node('div',null,'q-inspection-items'));box.querySelector('#v-sidebar').prepend(details);
   const action=node('button','','quiet-button');action.id='q-inspection-action';box.querySelector('.viewer-toolbar').append(action);
   const overlay=node('div',null,'q-excluded-overlay');overlay.id='q-excluded-overlay';overlay.setAttribute('aria-label','Кадърът е изключен');box.querySelector('#v-stage').append(overlay);
  }
  box.querySelector('#v-previous').disabled=state.inspectionIndex<=0;box.querySelector('#v-next').disabled=state.inspectionIndex>=state.inspection.length-1;
  const details=box.querySelector('#q-inspection-group'),items=details.querySelector('.q-inspection-items');details.querySelector('summary').textContent=`Избрани кадри (${state.inspection.length})`;items.replaceChildren();
  if(groupPage===null)groupPage=Math.floor(state.inspectionIndex/80);groupPage=Math.max(0,Math.min(groupPage,Math.ceil(state.inspection.length/80)-1));const start=groupPage*80;
  if(state.inspection.length>80){const pager=node('div',null,'function-actions'),back=node('button','←','quiet-button'),next=node('button','→','quiet-button');back.disabled=groupPage===0;next.disabled=start+80>=state.inspection.length;back.onclick=()=>{groupPage--;refreshViewer();};next.onclick=()=>{groupPage++;refreshViewer();};pager.append(back,node('span',`${start+1}–${Math.min(start+80,state.inspection.length)} от ${state.inspection.length}`),next);items.append(pager);}
  for(const id of state.inspection.slice(start,start+80)){const f=frames().find(r=>r.id===id);if(!f)continue;const b=node('button',f.name,'text-button');b.dataset.excluded=String(f.decision==='excluded');b.setAttribute('aria-current',String(id===current));b.onclick=()=>{state.inspectionIndex=state.inspection.indexOf(id);P.image(id).catch(e=>U.error('analysis',e));};items.append(b);}
  const action=box.querySelector('#q-inspection-action');action.textContent=row.decision==='excluded'?'Върни в подбора':'Премахни от подбора';action.onclick=()=>A.frameDecision(row.id,row.decision==='excluded'?'approved':'excluded');box.querySelector('#q-excluded-overlay').hidden=row.decision!=='excluded';window.dispatchEvent(new Event('astroarchive-inspection-decision'));
 }
 window.addEventListener('astroarchive-python-event',event=>{if(event.detail.name==='viewer.loaded'){const state=A.frameState(),id=event.detail.document?.sourceId;if(state.inspection.includes(id))state.inspectionIndex=state.inspection.indexOf(id);else if(!document.getElementById('page-analysis')?.hidden&&state.report?.frames.some(r=>r.id===id)){const selected=frames().filter(r=>state.selected.has(r.id)).map(r=>r.id);state.inspection=selected.includes(id)?selected:frames().map(r=>r.id);state.inspectionIndex=state.inspection.indexOf(id);}else{restoreViewer();return;}groupPage=Math.floor(state.inspectionIndex/80);refreshViewer();}else if(event.detail.name==='viewer.closed')restoreViewer();});
 function snapshot(){return{open:!!dialog?.open,view,filter,sort,direction,query,page};}
 function restore(value){restoring=true;try{view=value.view;filter=value.filter;sort=value.sort;direction=value.direction;query=value.query;page=value.page;if(value.open&&!dialog?.open)open();else if(!value.open&&dialog?.open)dialog.close();if(dialog?.open){for(const[id,v]of[['q-frame-view',view],['q-frame-filter',filter],['q-frame-sort',sort],['q-frame-direction',direction],['q-frame-search',query]])dialog.querySelector('#'+id).value=v;render();}}finally{restoring=false;}}
 window.AstroArchiveFrameList={open,refresh:render,refreshViewer,snapshot,restore};
})();
