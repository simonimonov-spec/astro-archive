"use strict";
// Independent registration plot. Values stay in the supplied report; no RPC.
(() => {
 const clamp=(x,a,b)=>Math.max(a,Math.min(b,x)),minimum=1/64;
 const bounded=(lo,hi)=>{const span=clamp(hi-lo,minimum,1);lo=clamp(lo,0,1-span);return[lo,lo+span];};
 const visibleWarnings=row=>(row.warnings||[]).filter(w=>w.origin==='ai'||row.standardEvaluated===true);
 const warningKinds=row=>({ai:visibleWarnings(row).some(w=>w.origin==='ai'),normal:row.standardEvaluated===true&&(!!row.error||visibleWarnings(row).some(w=>w.origin!=='ai'&&w.type!=='registration'))});
 const inspection=row=>{const values=Object.values(row.inspectionMetrics||{});return values.find(v=>v&&Number.isFinite(v.quality)&&'coverage'in v)||values.find(v=>v&&'coverage'in v)||values.find(v=>v&&Number.isFinite(v.quality))||{};};
 const comparisonText=row=>{const v=inspection(row);return 'coverage'in v?`Сравнена област: ${Number.isFinite(v.coverage)?v.coverage.toFixed(1)+'%':'няма данни'} · ${v.references??0} референции · ${v.matchedStars??0} общи звезди (мин.)\n${v.reason||'Покритието не е увереност или оценка за качество.'}`:'Няма записано AI покритие. Изпълни AI проверката.';};
 function create(canvas,callbacks){
  let spec=null,surface=null,hits=[],selection=null,gesture=null;
  const tooltip=document.createElement('div');tooltip.className='quality-plot-tooltip';tooltip.hidden=true;canvas.after(tooltip);
  canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Диаграма на качеството. Влачене за избор, десен бутон за действия, колелце за приближаване, двойно натискане за 100%.');
  const number=(v,d=3)=>new Intl.NumberFormat('bg',{maximumFractionDigits:d}).format(v);
  const point=e=>{const r=canvas.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};};
  const css=name=>getComputedStyle(document.documentElement).getPropertyValue('--'+name).trim();
  const seriesColor=()=>document.documentElement.dataset.theme==='builtin.red'?css('accent'):'#a981e8';
  const sortedColor=()=>document.documentElement.dataset.theme==='builtin.red'?css('success'):'#45b97a';
  const rows=()=>spec.rows.filter(row=>spec.showExcluded===true||row.decision!=='excluded').map(row=>({row,x:spec.xValue(row),y:spec.yValue(row)})).filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));
  function fullBounds(data){
   if(!data.length)return{x0:0,x1:1,y0:0,y1:1};
   let x0=Infinity,x1=-Infinity,y0=Infinity,y1=-Infinity;
   for(const p of data){x0=Math.min(x0,p.x);x1=Math.max(x1,p.x);y0=Math.min(y0,p.y);y1=Math.max(y1,p.y);}
   // Domain endpoints are the data extrema. Only a flat axis needs a small
   // symmetric extent so coordinate conversion can still divide by its span.
   if(x0===x1){const pad=Math.max(1e-6,Math.abs(x0)*1e-6);x0-=pad;x1+=pad;}
   if(y0===y1){const pad=Math.max(1e-6,Math.abs(y0)*1e-6);y0-=pad;y1+=pad;}
   return{x0,x1,y0,y1};
  }
  function niceStep(span,count){
   const raw=span/Math.max(1,count),exp=Math.pow(10,Math.floor(Math.log10(Math.max(raw,1e-9)))),f=raw/exp;
   return (f<=1?1:f<=2?2:f<=5?5:10)*exp;
  }
  function axisTicks(lo,hi,count){
   if(!(hi>lo))return[lo,hi];
   const step=niceStep(hi-lo,count),out=[lo],first=Math.ceil(lo/step-1e-9)*step,gap=(hi-lo)*.07;
   for(let v=first;v<hi;v+=step){const n=Math.abs(v)<step*1e-9?0:Number(v.toPrecision(12));if(n>lo+gap&&n<hi-gap)out.push(n);}
   out.push(hi);return out;
  }
  function axisNumber(v,lo,hi,base){const digits=Math.min(12,Math.max(base,Math.ceil(-Math.log10(hi-lo))+2));return number(v,digits);}
  function comparisonRows(){
   const source=spec.comparison;
   if(!source||!Array.isArray(source.rows))return[];
   return source.rows.filter(row=>spec.showExcluded===true||row.decision!=='excluded').map(row=>({row,x:source.xValue(row),y:source.yValue(row)})).filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));
  }
  function windowChanged(value){
   const x=bounded(value[0],value[1]),y=bounded(value[2],value[3]);spec.window=[...x,...y];callbacks.window(spec.window);draw();
  }
  function coordinates(x,y){const s=surface;return{x:s.left+(x-s.x0)/(s.x1-s.x0)*s.width,y:s.top+s.height-(y-s.y0)/(s.y1-s.y0)*s.height};}
  function value(p){const s=surface;return{x:s.x0+(p.x-s.left)/s.width*(s.x1-s.x0),y:s.y1-(p.y-s.top)/s.height*(s.y1-s.y0)};}
  function fractions(p){const s=surface,v=value(p);return{x:(v.x-s.full.x0)/(s.full.x1-s.full.x0),y:(v.y-s.full.y0)/(s.full.y1-s.full.y0)};}
  function selectionRect(){if(!selection||!surface)return null;const a=coordinates(selection.x0,selection.y1),b=coordinates(selection.x1,selection.y0);return{x:a.x,y:a.y,w:b.x-a.x,h:b.y-a.y};}
  function selectedBounds(){const chosen=rows().filter(p=>spec.selected.has(p.row.id));if(!chosen.length)return null;return{x0:Math.min(...chosen.map(p=>p.x)),x1:Math.max(...chosen.map(p=>p.x)),y0:Math.min(...chosen.map(p=>p.y)),y1:Math.max(...chosen.map(p=>p.y))};}
  function selectionChanged(){
   const ids=rows().filter(p=>p.x>=selection.x0&&p.x<=selection.x1&&p.y>=selection.y0&&p.y<=selection.y1).map(p=>p.row.id);
   spec.selected=new Set(ids);callbacks.selection(ids);draw();
  }
  function axisAt(p){if(!surface)return null;if(p.y>surface.top+surface.height+30&&p.x>=surface.left&&p.x<=surface.left+surface.width)return'x';if(p.x>surface.left+surface.width+8&&p.y>=surface.top&&p.y<=surface.top+surface.height)return'y';return null;}
  function resizeEdge(p){const r=selectionRect();if(!r)return null;let edge='';if(p.y>=r.y-8&&p.y<=r.y+r.h+8){if(Math.abs(p.x-r.x)<8)edge+='w';else if(Math.abs(p.x-r.x-r.w)<8)edge+='e';}if(p.x>=r.x-8&&p.x<=r.x+r.w+8){if(Math.abs(p.y-r.y)<8)edge+='n';else if(Math.abs(p.y-r.y-r.h)<8)edge+='s';}return edge||null;}
  function draw(){
   if(!spec||canvas.hidden||!canvas.getClientRects().length)return;
   const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;
   const ratio=devicePixelRatio||1,w=rect.width,h=rect.height;canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);
   const c=canvas.getContext('2d');c.setTransform(ratio,0,0,ratio,0,0);c.clearRect(0,0,w,h);
   const data=rows(),extra=comparisonRows(),full=fullBounds(data.concat(extra)),win=spec.window||[0,1,0,1];
   c.font='11px Segoe UI';const ylo=full.y0+(full.y1-full.y0)*win[2],yhi=full.y0+(full.y1-full.y0)*win[3],labelWidth=Math.max(c.measureText(axisNumber(ylo,ylo,yhi,3)).width,c.measureText(axisNumber(yhi,ylo,yhi,3)).width);
   const left=Math.max(w<520?54:62,Math.min(w*.32,Math.ceil(labelWidth)+13)),top=40,right=38,bottom=52,size=Math.max(1,Math.min(w-left-right,h-top-bottom)),width=size,height=size;
   surface={left,top,width,height,full,x0:full.x0+(full.x1-full.x0)*win[0],x1:full.x0+(full.x1-full.x0)*win[1],y0:full.y0+(full.y1-full.y0)*win[2],y1:full.y0+(full.y1-full.y0)*win[3]};
   const s=surface,muted=css('muted'),border=css('border'),text=css('text');
   c.fillStyle=document.documentElement.dataset.theme==='builtin.day'?css('background'):'#000';c.fillRect(left,top,width,height);c.strokeStyle=border;c.lineWidth=1;c.fillStyle=muted;c.font='11px Segoe UI';
   for(const x of axisTicks(s.x0,s.x1,5)){const p=coordinates(x,s.y0);c.beginPath();c.moveTo(p.x,top);c.lineTo(p.x,top+height);c.stroke();c.textAlign='center';c.fillText(axisNumber(x,s.x0,s.x1,2),p.x,top+height+16);}
   for(const y of axisTicks(s.y0,s.y1,5)){const p=coordinates(s.x0,y);c.beginPath();c.moveTo(left,p.y);c.lineTo(left+width,p.y);c.stroke();c.textAlign='right';c.fillText(axisNumber(y,s.y0,s.y1,3),left-8,p.y+4);}
   c.fillStyle=text;c.textAlign='left';c.font='12px Segoe UI';c.fillText(spec.yLabel,left,17);c.textAlign='center';c.fillText(spec.xLabel,left+width/2,top+height+34);
   c.save();c.beginPath();c.rect(left,top,width,height);c.clip();
   const active=data.filter(p=>p.row.decision!=='excluded'),ordered=[...active].sort((a,b)=>a.row.frameIndex-b.row.frameIndex);
   if(spec.indexAxis){
    c.lineWidth=1.35;for(let i=1;i<ordered.length;i++){const a=coordinates(ordered[i-1].x,ordered[i-1].y),b=coordinates(ordered[i].x,ordered[i].y);c.strokeStyle=spec.colorMode==='plot'?seriesColor():spec.color(ordered[i].row);c.beginPath();c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);c.stroke();}
   }
   if(spec.sorted&&spec.indexAxis){
    const sorted=[...ordered].sort((a,b)=>spec.higherBetter?b.y-a.y:a.y-b.y);c.strokeStyle=sortedColor();c.lineWidth=1.5;c.beginPath();
    sorted.forEach((p,i)=>{const q=coordinates(ordered[i].x,p.y);if(i)c.lineTo(q.x,q.y);else c.moveTo(q.x,q.y);});c.stroke();
   }
   if(extra.length){
    const compareColor=spec.comparison.color||'#e0a45c';
    if(spec.indexAxis){c.strokeStyle=compareColor;c.globalAlpha=.72;c.lineWidth=1.35;c.beginPath();let begun=false;for(const p of extra.filter(p=>p.row.decision!=='excluded').sort((a,b)=>a.row.frameIndex-b.row.frameIndex)){const q=coordinates(p.x,p.y);if(!begun){c.moveTo(q.x,q.y);begun=true;}else c.lineTo(q.x,q.y);}c.stroke();c.globalAlpha=1;}
    for(const p of extra){if(p.row.decision==='excluded')continue;const q=coordinates(p.x,p.y);if(q.x<left-7||q.x>left+width+7||q.y<top-7||q.y>top+height+7)continue;c.globalAlpha=p.row.decision==='excluded'?.3:1;c.beginPath();c.arc(q.x,q.y,3.5,0,Math.PI*2);c.fillStyle=css('background');c.fill();c.strokeStyle=compareColor;c.lineWidth=1.6;c.stroke();c.globalAlpha=1;}
   }
   hits=[];
   for(const p of data){
    const q=coordinates(p.x,p.y);if(q.x<left-7||q.x>left+width+7||q.y<top-7||q.y>top+height+7)continue;
    hits.push({x:q.x,y:q.y,row:p.row,vx:p.x,vy:p.y});const col=spec.colorMode==='plot'?seriesColor():spec.color(p.row);
    if(p.row.decision==='excluded')continue;
    if(!spec.indexAxis||active.length<100||spec.selected.has(p.row.id)){c.beginPath();c.arc(q.x,q.y,spec.selected.has(p.row.id)?5:3,0,Math.PI*2);c.fillStyle=col;c.fill();}
    if(spec.selected.has(p.row.id)){c.lineWidth=2;c.strokeStyle=text;c.stroke();}
    if(p.row.id===p.row.reference){c.beginPath();c.arc(q.x,q.y,8,0,Math.PI*2);c.strokeStyle=col;c.lineWidth=2;c.stroke();}
    if(spec.loadedId===p.row.id){c.beginPath();c.moveTo(q.x-8,q.y);c.lineTo(q.x+8,q.y);c.moveTo(q.x,q.y-8);c.lineTo(q.x,q.y+8);c.strokeStyle=text;c.lineWidth=2;c.stroke();}

   }
   const r=selectionRect();if(r){
    c.fillStyle='color-mix(in srgb, '+css('accent')+' 15%, transparent)';c.strokeStyle=css('accent');c.lineWidth=1;c.fillRect(r.x,r.y,r.w,r.h);c.strokeRect(r.x,r.y,r.w,r.h);
    for(const [x,y]of[[r.x,r.y],[r.x+r.w,r.y],[r.x,r.y+r.h],[r.x+r.w,r.y+r.h],[r.x+r.w/2,r.y],[r.x+r.w/2,r.y+r.h],[r.x,r.y+r.h/2],[r.x+r.w,r.y+r.h/2]]){c.fillStyle=css('background');c.fillRect(x-3,y-3,6,6);c.strokeRect(x-3,y-3,6,6);}
   }c.restore();
   // Draw ghost points outside the data clip so extrema retain their full dot.
   for(const p of hits){if(p.row.decision!=='excluded')continue;const q=p;if(q.x<left-.01||q.x>left+width+.01||q.y<top-.01||q.y>top+height+.01)continue;c.globalAlpha=.3;c.beginPath();c.arc(q.x,q.y,3,0,Math.PI*2);c.fillStyle=spec.colorMode==='plot'?seriesColor():spec.color(p.row);c.fill();c.globalAlpha=1;}
   for(const p of extra){if(p.row.decision!=='excluded')continue;const q=coordinates(p.x,p.y);if(q.x<left-.01||q.x>left+width+.01||q.y<top-.01||q.y>top+height+.01)continue;c.globalAlpha=.3;c.beginPath();c.arc(q.x,q.y,3.5,0,Math.PI*2);c.fillStyle=css('background');c.fill();c.strokeStyle=spec.comparison.color||'#e0a45c';c.lineWidth=1.6;c.stroke();c.globalAlpha=1;}
   // Warning badges live in a separate layer, including the top gutter.
   // The numeric Y maximum stays exact; its badge is not clipped by the plot.
   for(const p of hits){if(p.row.decision==='excluded')continue;const q=p;if(q.x<left-.01||q.x>left+width+.01||q.y<top-.01||q.y>top+height+.01)continue;
    if(spec.warnings){const flags=warningKinds(p.row);c.strokeStyle=css('warning');c.fillStyle=css('warning');c.lineWidth=1.5;if(flags.ai){c.strokeRect(q.x-4,q.y-15,8,8);if(flags.normal)c.fillRect(q.x-4,q.y-15,8,8);}else if(flags.normal){c.beginPath();c.moveTo(q.x,q.y-15);c.lineTo(q.x-4,q.y-7);c.lineTo(q.x+4,q.y-7);c.closePath();c.stroke();}}
    if(spec.warnings&&(p.row.warnings||[]).some(w=>w.origin==='ai'&&w.type==='cloud')){c.font='13px Segoe UI Symbol';c.fillStyle=css('warning');c.textAlign='left';c.fillText('☁',q.x+6,q.y-7);}
    if(spec.warnings&&(p.row.warnings||[]).some(w=>w.origin==='ai'&&w.type==='light')){c.font='12px Segoe UI Symbol';c.fillStyle=css('warning');c.textAlign='left';c.fillText('✦',q.x+18,q.y-7);}
   }
   if(r){c.fillStyle=text;c.textAlign='left';c.font='11px Segoe UI';c.fillText(`${spec.selected.size} кадъра · X ${number(selection.x0)}…${number(selection.x1)} · Y ${number(selection.y0)}…${number(selection.y1)}`,left,top-7);}
   // The two red handles constrain each axis to its own 100–6400% window.
   const xbar=top+height+40,ybar=left+width+12;c.strokeStyle=border;c.lineWidth=4;c.lineCap='round';c.beginPath();c.moveTo(left,xbar);c.lineTo(left+width,xbar);c.moveTo(ybar,top);c.lineTo(ybar,top+height);c.stroke();
   c.strokeStyle=css('accent');c.beginPath();c.moveTo(left+win[0]*width,xbar);c.lineTo(left+win[1]*width,xbar);c.moveTo(ybar,top+(1-win[3])*height);c.lineTo(ybar,top+(1-win[2])*height);c.stroke();
   c.fillStyle=css('error');for(const [x,y]of[[left+win[0]*width,xbar],[left+win[1]*width,xbar],[ybar,top+(1-win[3])*height],[ybar,top+(1-win[2])*height]]){c.beginPath();c.arc(x,y,4.5,0,Math.PI*2);c.fill();}
   callbacks.zoom?.(Math.round(100/(win[1]-win[0])),Math.round(100/(win[3]-win[2])));
  }
  function nearest(p){let best=null;for(const item of hits){const d=Math.hypot(item.x-p.x,item.y-p.y);if(d<9&&(!best||d<best.d))best={...item,d};}return best;}
  function tooltipAt(p){
   const hit=nearest(p);if(!hit){tooltip.hidden=true;return;}
   const flags=warningKinds(hit.row),quality=inspection(hit.row).quality;tooltip.textContent=`№ ${hit.row.frameIndex} · ${hit.row.name}\n${hit.row.meta?.night||''}\n${hit.row.decision==='excluded'?'Изключен от подбора':'В подбора'}\n${spec.xLabel}: ${number(spec.xValue(hit.row))}\n${spec.yLabel}: ${number(spec.yValue(hit.row))}\nСтандартна оценка: ${Number.isFinite(hit.row.classicQuality)?number(hit.row.classicQuality,1)+'%':'не е измерена'}\nAI индекс: ${Number.isFinite(quality)?number(quality,1)+'%':'не е измерен'}\n${comparisonText(hit.row)}\n${flags.ai?(flags.normal?'■ Стандартна проверка и AI':'□ AI предупреждение'):flags.normal?'△ Стандартна проверка':''}\n${visibleWarnings(hit.row).filter(w=>w.type!=='registration').map(w=>w.message).join('\n')}`;
   tooltip.hidden=false;tooltip.style.left=(canvas.offsetLeft+clamp(p.x+14,10,Math.max(10,canvas.clientWidth-tooltip.offsetWidth-10)))+'px';tooltip.style.top=(canvas.offsetTop+Math.max(0,p.y-tooltip.offsetHeight-12))+'px';
  }
  canvas.addEventListener('wheel',event=>{event.preventDefault();if(!surface)return;const f=fractions(point(event)),factor=Math.exp(event.deltaY*.0015),win=spec.window||[0,1,0,1],out=[];for(const[i,key]of[[0,'x'],[2,'y']]){const span=clamp((win[i+1]-win[i])*factor,minimum,1),ratio=clamp((f[key]-win[i])/(win[i+1]-win[i]),0,1),lo=f[key]-ratio*span;out.push(...bounded(lo,lo+span));}windowChanged(out);},{passive:false});
  canvas.addEventListener('dblclick',event=>{event.preventDefault();const axis=axisAt(point(event)),win=[...(spec.window||[0,1,0,1])];if(axis){const i=axis==='x'?0:2;win[i]=0;win[i+1]=1;}else win.splice(0,4,0,1,0,1);windowChanged(win);});
  canvas.addEventListener('pointerdown',event=>{
   if(!surface)return;const p=point(event),axis=axisAt(p);if(event.button!==0&&!(event.button===2&&axis))return;event.preventDefault();tooltip.hidden=true;
   const win=[...(spec.window||[0,1,0,1])];
   if(axis){const i=axis==='x'?0:2,f=axis==='x'?(p.x-surface.left)/surface.width:1-(p.y-surface.top)/surface.height;gesture={mode:event.button===2?'axis-pan':'axis-handle',axis,i,start:f,window:win,handle:Math.abs(f-win[i])<Math.abs(f-win[i+1])?i:i+1};}
   else if(event.altKey){gesture={mode:'pan',start:p,window:win};}
   else{const edge=resizeEdge(p);gesture={mode:edge?'resize':'select',edge,start:p,value:value(p),original:selection?{...selection}:null,moved:false};}
   canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove',event=>{
   const p=point(event);if(!gesture){canvas.style.cursor=axisAt(p)?'ew-resize':resizeEdge(p)?'nwse-resize':nearest(p)?'pointer':'crosshair';tooltipAt(p);return;}
   const g=gesture;
   if(g.mode.startsWith('axis')){
    const f=g.axis==='x'?(p.x-surface.left)/surface.width:1-(p.y-surface.top)/surface.height,out=[...g.window];
    if(g.mode==='axis-pan'){const span=g.window[g.i+1]-g.window[g.i],lo=clamp(g.window[g.i]+f-g.start,0,1-span);out[g.i]=lo;out[g.i+1]=lo+span;}
    else if(g.handle===g.i)out[g.i]=clamp(f,0,out[g.i+1]-minimum);else out[g.i+1]=clamp(f,out[g.i]+minimum,1);
    windowChanged(out);return;
   }
   if(g.mode==='pan'){const out=[...g.window];for(const[i,key,sign,length]of[[0,'x',-1,surface.width],[2,'y',1,surface.height]]){const span=out[i+1]-out[i],lo=clamp(out[i]+sign*(p[key]-g.start[key])/length*span,0,1-span);out[i]=lo;out[i+1]=lo+span;}windowChanged(out);return;}
   const v=value(p);g.moved||=Math.hypot(p.x-g.start.x,p.y-g.start.y)>4;
   if(g.mode==='resize'){selection={...g.original};if(g.edge.includes('w'))selection.x0=v.x;if(g.edge.includes('e'))selection.x1=v.x;if(g.edge.includes('n'))selection.y1=v.y;if(g.edge.includes('s'))selection.y0=v.y;}
   else selection={x0:Math.min(g.value.x,v.x),x1:Math.max(g.value.x,v.x),y0:Math.min(g.value.y,v.y),y1:Math.max(g.value.y,v.y)};
   if(selection.x0>selection.x1)[selection.x0,selection.x1]=[selection.x1,selection.x0];
   if(selection.y0>selection.y1)[selection.y0,selection.y1]=[selection.y1,selection.y0];
   selectionChanged();
  });
  canvas.addEventListener('pointerup',event=>{
   if(!gesture)return;const g=gesture;gesture=null;if(g.mode==='select'&&!g.moved){const item=nearest(point(event));if(item){const ids=event.ctrlKey?new Set(spec.selected):new Set();ids.has(item.row.id)&&event.ctrlKey?ids.delete(item.row.id):ids.add(item.row.id);spec.selected=ids;callbacks.selection([...ids]);selection=null;}else{selection=null;spec.selected=new Set();callbacks.selection([]);}}draw();
  });
  canvas.addEventListener('pointercancel',()=>{gesture=null;});canvas.addEventListener('pointerleave',()=>{tooltip.hidden=true;});
  canvas.addEventListener('contextmenu',event=>{event.preventDefault();if(axisAt(point(event)))return;const item=nearest(point(event));if(item&&!spec.selected.has(item.row.id)){spec.selected=new Set([item.row.id]);selection=null;callbacks.selection([...spec.selected]);}callbacks.context(event,item?.row);draw();});
  canvas.addEventListener('keydown',event=>{
   if(!spec)return;
   if(event.key==='Escape'){selection=null;spec.selected=new Set();callbacks.selection([]);draw();}
   else if(event.key==='0'){event.preventDefault();windowChanged([0,1,0,1]);}
   else if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const points=rows().sort((a,b)=>a.row.frameIndex-b.row.frameIndex);if(!points.length)return;const index=points.findIndex(p=>spec.selected.has(p.row.id)),next=clamp(index+(event.key==='ArrowLeft'?-1:1),0,points.length-1);spec.selected=new Set([points[next].row.id]);selection=null;callbacks.selection([...spec.selected]);draw();}
   else if(event.key==='Enter'&&spec.selected.size){event.preventDefault();callbacks.open?.();}
   else if(event.key==='F10'&&event.shiftKey){event.preventDefault();const rect=canvas.getBoundingClientRect();callbacks.context({clientX:rect.left+surface.left+30,clientY:rect.top+surface.top+30});}
  });
  return {
   render(value){const same=spec&&spec.xLabel===value.xLabel&&spec.yLabel===value.yLabel&&spec.reportId===value.reportId&&(spec.comparison?.id||null)===(value.comparison?.id||null);spec=value;if(!same)selection=null;draw();},
   clearRegion(){selection=null;draw();},
   reset(){windowChanged([0,1,0,1]);},
   hasSelection(){return !!selection;},
   zoomSelection(){const b=selection||selectedBounds();if(!b||!surface)return;const f=surface.full;windowChanged([(b.x0-f.x0)/(f.x1-f.x0),(b.x1-f.x0)/(f.x1-f.x0),(b.y0-f.y0)/(f.y1-f.y0),(b.y1-f.y0)/(f.y1-f.y0)]);},
   zoomBounds(b){if(!surface||!b)return;const f=surface.full,out=[];for(const[key,i]of[['x',0],['y',2]]){const span=f[key+'1']-f[key+'0'],lo=(b[key+'0']-f[key+'0'])/span,hi=(b[key+'1']-f[key+'0'])/span,pad=Math.max(minimum/2,(hi-lo)*.06);out.push(lo-pad,hi+pad);}windowChanged(out);},
   zoomRows(ids){const selected=new Set(ids),points=rows().filter(p=>selected.has(p.row.id));if(!points.length)return;this.zoomBounds({x0:Math.min(...points.map(p=>p.x)),x1:Math.max(...points.map(p=>p.x)),y0:Math.min(...points.map(p=>p.y)),y1:Math.max(...points.map(p=>p.y))});},
   legend(){return{data:seriesColor(),sorted:sortedColor()};}
  };
 }
 window.AstroArchiveQualityPlot={create,warningKinds,visibleWarnings,inspection,comparisonText};
})();
