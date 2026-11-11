"use strict";

// The canvas owns the viewport. The Python service owns linear pixels and display rendering.
(() => {
  const words = {
    title:['Преглед на изображение','Image viewer'], close:['Назад към работата · Esc','Back to workspace · Esc'],
    previous:['Предишен кадър · ←','Previous image · ←'],next:['Следващ кадър · →','Next image · →'],
    fit:['Напасни · F','Fit · F'],one:['Мащаб 1:1 · 1','Scale 1:1 · 1'],rotate:['Завърти 90°','Rotate 90°'],
    flipX:['Огледално по хоризонтала','Flip horizontally'],flipY:['Огледално по вертикала','Flip vertically'],
    panel:['Покажи / скрий настройките','Show / hide controls'],orientation:['Ориентация','Navigator'],
    histogram:['Хистограма','Histogram'],display:['Екранна визуализация','Display'],channel:['Канал','Channel'],
    mode:['Разтягане','Stretch'],auto:['Автоматично','Auto stretch'],linear:['Линейно','Linear'],manual:['Ръчно · MTF','Manual · MTF'],
    mono:['Яркост','Luminance'],red:['Червен','Red'],green:['Зелен','Green'],blue:['Син','Blue'],
    separate:['Независими RGB канали','Independent RGB channels'],black:['Черна граница, %','Black point, %'],
    white:['Бяла граница, %','White point, %'],midtone:['Средни тонове','Midtones'],gamma:['Гама','Gamma'],
    negative:['Негатив','Negative'],smooth:['Плавни пиксели','Smooth pixels'],grid:['Пикселна мрежа','Pixel grid'],
    cross:['Централен кръст','Center crosshair'],reset:['Нулирай изгледа','Reset display'],details:['Файл и FITS хедър','File and FITS header'],
    header:['Покажи FITS хедъра','Show FITS header'],quality:['Разделителна способност','Preview resolution'],
    fast:['Бърз · 1600 px','Fast · 1600 px'],detail:['Подробен · 3200 px','Detailed · 3200 px'],original:['Оригинал · до 16 MP','Original · up to 16 MP'],
    loading:['Зареждане на изображението…','Loading image…'],rendering:['Обновяване на изгледа…','Updating display…'],
    hint:['Колелце: приближаване · влачене: местене · F: напасни · 1: 1:1 · ← →: кадри','Wheel: zoom · drag: pan · F: fit · 1: 1:1 · ← →: images'],
    sampled:['Линейни стойности от прегледа; координати от 0, Y отдолу.','Linear preview values; coordinates from 0, Y from bottom.'],
    preview:['Преглед','Preview'],source:['Оригинал','Source'],median:['Медиана','Median'],mean:['Средно','Mean'],
    note:['Само екранен изглед. Оригиналът не се променя.','Display only. The source is unchanged.'],
    noNext:['Няма следващ кадър в тази посока.','No more images in this direction.'],
    retry:['Опитай отново','Try again'],
    invalid:['Черната граница трябва да е под бялата.','Black point must be below white point.'],
    bayerNote:['Debayer само за цветен RAW; матрицата е от хедъра, ако е налична.','Debayer only for color RAW; pattern comes from the header when available.'],
    histogramNote:['Линейна хистограма с логаритмична височина; статистика от проба на прегледа.','Linear histogram with logarithmic height; statistics sample the preview.'],
    zoom:['Мащаб в проценти от оригинала','Zoom as percent of source'],palette:['Палитра за монохромен канал','Monochrome palette'],
    plus:['Приближи','Zoom in'],minus:['Отдалечи','Zoom out'],
    download:['Изтегли','Download'],
    gray:['Сиво','Gray'],heat:['Топлинна','Heat'],blueGold:['Синьо / златно','Blue / gold'],
  };
  const en = () => !(window.AstroArchiveI18n?.locale || 'bg').toLowerCase().startsWith('bg');
  const t = key => {const full='viewer.'+key,translated=window.AstroArchiveI18n?.t(full);return translated&&translated!==full?translated:words[key]?.[en()?1:0]||key;};
  let call,dialog,canvas,ctx,overview,histogram,stage,frame=null,picture=null,id='',serial=0,renderSerial=0;
  let zoom=1,pan={x:0,y:0},rotation=0,flipX=false,flipY=false,fitting=true,drag=null,animation=0;
  let debounce,pixelTimer,pixelBusy=false,restoreFocus,resize,loading=false,keptTransform=null,pendingDisplay=null;
  const $ = key => dialog.querySelector('#v-'+key);
  const fmt = x => Number.isFinite(x)?x.toLocaleString(en()?'en':'bg',{maximumSignificantDigits:7}):'—';
  const icons={close:'close',previous:'previous',next:'next',fit:'fit',one:'actual',minus:'zoomOut',plus:'zoomIn',rotate:'rotate',flipX:'flipX',flipY:'flipY',panel:'panel',reset:'reset',header:'header',retry:'reset'};
  function button(key){return `<button type="button" class="quiet-button viewer-icon-button${key==='retry'?' viewer-retry':''}" id="v-${key}" data-label="${key}">${window.AstroArchiveIcons.markup(icons[key])}</button>`;}
  function panelState(){const visible=!dialog.classList.contains('viewer-wide');$('panel').setAttribute('aria-pressed',String(visible));$('panel').setAttribute('aria-expanded',String(visible));$('panel').setAttribute('aria-controls','v-sidebar');}
  function control(key,value,min,max,step){return `<label class="viewer-control"><span data-text="${key}"></span><div><input id="v-${key}" type="range" min="${min}" max="${max}" step="${step}" value="${value}"><input id="v-${key}-number" type="number" aria-label="${t(key)}" min="${min}" max="${max}" step="${step}" value="${value}"></div></label>`;}
  function create(){
    dialog=document.createElement('dialog');dialog.className='image-viewer';dialog.setAttribute('aria-labelledby','v-title');
    dialog.innerHTML=`<header class="viewer-heading"><div><p class="eyebrow" id="v-title" data-text="title"></p><h2 id="v-name"></h2><p id="v-meta"></p></div>${button('close','×')}</header>
      <div class="viewer-toolbar" role="toolbar"><div class="viewer-tool-group">${button('previous','←')}${button('next','→')}</div><div class="viewer-tool-group">${button('fit')}${button('one','1:1')}${button('minus','−')}<label class="viewer-zoom"><input id="v-zoom" type="number" min="1" max="6400" step="10" value="100"> %</label>${button('plus','+')}</div><div class="viewer-tool-group">${button('rotate','↻')}${button('flipX','↔')}${button('flipY','↕')}</div>${button('panel','☷')}</div>
      <div class="viewer-workspace"><div id="v-stage" class="viewer-stage" tabindex="0"><canvas id="v-canvas"></canvas><div id="v-loading" class="viewer-loading"><span class="viewer-spinner"></span><span id="v-load-label"></span></div><p id="v-error" class="viewer-error" role="alert" hidden></p>${button('retry')}<div id="v-badge" class="viewer-badge"></div></div>
      <aside id="v-sidebar" class="viewer-sidebar"><div id="v-plugin-tools" hidden></div><section><h3 data-text="orientation"></h3><canvas id="v-overview" class="viewer-overview" role="img" aria-label="${t('orientation')}"></canvas><p class="viewer-muted" id="v-resolution"></p></section>
      <section><h3 data-text="histogram"></h3><canvas id="v-histogram" class="viewer-histogram" role="img" aria-label="${t('histogram')}"></canvas><div class="viewer-hist-range"><span id="v-low">—</span><span id="v-high">—</span></div><p class="viewer-muted" id="v-stats"></p></section>
      <section><h3 data-text="display"></h3><label class="viewer-select"><span data-text="mode"></span><select id="v-mode"><option value="0" data-text="auto"></option><option value="1" data-text="linear"></option><option value="2" data-text="manual"></option><option value="3">Asinh</option><option value="4">Log</option><option value="5">√</option><option value="6">x²</option></select></label>
      <label class="viewer-select"><span data-text="channel"></span><select id="v-channel"><option value="0">RGB</option><option value="1" data-text="mono"></option><option value="2" data-text="red"></option><option value="3" data-text="green"></option><option value="4" data-text="blue"></option></select></label>
      <label class="viewer-check"><input id="v-separate" type="checkbox"><span data-text="separate"></span></label>
      ${control('black',0,0,99.999,.001)}${control('white',100,.001,100,.001)}${control('midtone',.5,.001,.999,.001)}${control('gamma',1,.1,10,.01)}
      <label class="viewer-select"><span data-text="palette"></span><select id="v-palette"><option value="0" data-text="gray"></option><option value="1" data-text="heat"></option><option value="2" data-text="blueGold"></option></select></label>
      <label class="viewer-check"><input id="v-negative" type="checkbox"><span data-text="negative"></span></label>${button('reset')}</section>
      <section><label class="viewer-select"><span data-text="quality"></span><select id="v-quality"><option value="1600" data-text="fast"></option><option value="3200" data-text="detail"></option><option value="0" data-text="original"></option></select></label><div class="viewer-bayer"><label class="viewer-check"><input id="v-debayer" type="checkbox">Debayer</label><select id="v-pattern" aria-label="Bayer"><option value="auto">Автоматично · FITS хедър</option><option>RGGB</option><option>BGGR</option><option>GRBG</option><option>GBRG</option></select></div><p class="viewer-muted" data-text="bayerNote"></p></section>
      <section class="viewer-local-options"><label class="viewer-check"><input id="v-smooth" type="checkbox" checked><span data-text="smooth"></span></label><label class="viewer-check"><input id="v-grid" type="checkbox"><span data-text="grid"></span></label><label class="viewer-check"><input id="v-cross" type="checkbox"><span data-text="cross"></span></label></section>
      <details class="viewer-file"><summary data-text="details"></summary><p id="v-path"></p>${button('header')}<pre id="v-cards" hidden></pre></details><p class="viewer-muted viewer-note" data-text="note"></p></aside></div>
      <footer class="viewer-footer"><span id="v-pixel"></span><span data-text="hint"></span></footer>`;
    document.body.append(dialog);canvas=$('canvas');ctx=canvas.getContext('2d');overview=$('overview');histogram=$('histogram');stage=$('stage');
    if(window.matchMedia('(max-width:720px)').matches)dialog.classList.add('viewer-wide');
    $('retry').hidden=true;panelState();translate();
    $('close').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
    $('fit').onclick=()=>{fit();commitViewport();};$('one').onclick=()=>setZoom(frame.width/frame.previewWidth);
    $('plus').onclick=()=>setZoom(zoom*1.3);$('minus').onclick=()=>setZoom(zoom/1.3);
    $('zoom').onchange=()=>{if(frame)setZoom(Math.max(1,Math.min(6400,Number($('zoom').value)||100))/100*frame.width/frame.previewWidth);};
    $('rotate').onclick=()=>{rotation=(rotation+90)%360;if(fitting)fit();else schedule();commitViewport();};
    for(const key of ['flipX','flipY'])$(key).onclick=()=>{if(key==='flipX')flipX=!flipX;else flipY=!flipY;$(key).setAttribute('aria-pressed',String(key==='flipX'?flipX:flipY));schedule();commitViewport();};
    $('panel').onclick=()=>{const view=transformState();dialog.classList.toggle('viewer-wide');panelState();if(view)restoreTransform(view);commitViewport();};
    $('previous').onclick=()=>neighbor(-1);$('next').onclick=()=>neighbor(1);
    for(const key of ['black','white','midtone','gamma'])for(const suffix of ['', '-number'])$(key+suffix).oninput=()=>{
      const input=$(key+suffix),v=Number(input.value);if(!input.value||!Number.isFinite(v)||v<Number(input.min)||v>Number(input.max))return;
      $(key+(suffix?'':'-number')).value=input.value;renderSoon();};
    for(const key of ['mode','channel','separate','palette','negative','quality','debayer','pattern'])$(key).onchange=renderSoon;
    for(const key of ['smooth','grid','cross'])$(key).onchange=()=>{schedule();window.AstroArchiveSession.edit('viewer',{controls:{[key]:$(key).checked}});};
    $('reset').onclick=()=>{resetControls();rotation=0;flipX=flipY=false;$('flipX').setAttribute('aria-pressed','false');$('flipY').setAttribute('aria-pressed','false');fit();renderSoon();commitViewport();};
    $('header').onclick=async()=>{const requestId=id;try{const result=await call('viewer.header',{id});if(id!==requestId)return;$('cards').textContent=result.cards.join('\n');$('cards').hidden=false;}catch(e){if(id===requestId)error(e.message);}};
    stage.addEventListener('wheel',e=>{if(!picture)return;e.preventDefault();const r=stage.getBoundingClientRect();setZoom(zoom*Math.exp(-Math.max(-150,Math.min(150,e.deltaY))*.0025),{x:e.clientX-r.left,y:e.clientY-r.top});},{passive:false});
    stage.addEventListener('pointerdown',e=>{if(!picture||e.button>2)return;e.preventDefault();stage.focus();stage.setPointerCapture(e.pointerId);drag={id:e.pointerId,x:e.clientX,y:e.clientY,pan:{...pan}};stage.classList.add('dragging');});
    stage.addEventListener('pointermove',e=>{if(drag){pan={x:drag.pan.x+e.clientX-drag.x,y:drag.pan.y+e.clientY-drag.y};fitting=false;schedule();}hover(e);});
    const up=()=>{const changed=!!drag;drag=null;stage.classList.remove('dragging');if(changed)commitViewport();};stage.addEventListener('pointerup',up);stage.addEventListener('pointercancel',up);stage.addEventListener('lostpointercapture',up);
    stage.addEventListener('contextmenu',e=>e.preventDefault());stage.addEventListener('dblclick',()=>{fit();commitViewport();});
    overview.addEventListener('pointerdown',e=>{if(!picture)return;e.preventDefault();overview.setPointerCapture(e.pointerId);locate(e);});overview.addEventListener('pointermove',e=>{if(overview.hasPointerCapture(e.pointerId))locate(e);});
    dialog.addEventListener('keydown',e=>{if(e.ctrlKey||e.metaKey||e.altKey||/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;const actions={f:()=>{fit();commitViewport();},'1':()=>$('one').click(),'+':()=>$('plus').click(),'-':()=>$('minus').click(),ArrowLeft:()=>neighbor(-1),ArrowRight:()=>neighbor(1)};if(actions[e.key]){e.preventDefault();actions[e.key]();}});
    resize=new ResizeObserver(()=>{if(fitting)fit();else if(keptTransform)restoreTransform(keptTransform);else schedule();drawHistogram();});resize.observe(stage);resize.observe(histogram);
  }
  function translate(){if(!dialog)return;for(const node of dialog.querySelectorAll('[data-text]'))node.textContent=t(node.dataset.text);for(const node of dialog.querySelectorAll('[data-label]')){node.title=t(node.dataset.label);node.setAttribute('aria-label',t(node.dataset.label));}$('zoom').setAttribute('aria-label',t('zoom'));histogram.title=t('histogramNote');}
  function resetControls(){for(const [key,value]of Object.entries({mode:frame?.fits?'0':'1',channel:'0',palette:'0',black:0,white:100,midtone:.5,gamma:1})){$(key).value=value;if($(key+'-number'))$(key+'-number').value=value;}$('separate').checked=!!frame?.separateRgb;$('negative').checked=false;}
  function busy(value,kind='rendering'){loading=value;$('loading').hidden=!value;$('load-label').textContent=t(kind);if(frame)enableControls();}
  function enableControls(){
    const auto=Number($('mode').value)===0;for(const key of ['black','white']){$(key).disabled=$(key+'-number').disabled=auto;}
    $('midtone').disabled=$('midtone-number').disabled=Number($('mode').value)!==2;
    $('separate').disabled=!auto||!frame.color;$('channel').disabled=!frame.color;
    $('palette').disabled=frame.color&&Number($('channel').value)===0;
    $('debayer').disabled=!frame.canDebayer;$('pattern').disabled=!frame.canDebayer||!$('debayer').checked;
    $('header').disabled=!frame.fits;for(const key of ['previous','next'])$(key).disabled=!frame.canNavigate||loading;
  }
  function error(message){$('error').textContent=message;$('error').hidden=false;}
  function opening(message){
    if(!dialog)create();if(dialog.open&&id===message.id)return;
    const previous=transformState();if(previous)keptTransform=previous;
    clearTimeout(debounce);clearTimeout(pixelTimer);serial++;renderSerial++;id=message.id;frame=null;picture=null;pendingDisplay=null;
    if(!dialog.open){restoreFocus=document.activeElement;document.documentElement.classList.add('viewer-open');dialog.showModal();}
    $('name').textContent=message.fileName;$('meta').textContent='';$('path').textContent='';$('pixel').textContent=t('sampled');$('cards').hidden=true;$('cards').textContent='';$('error').hidden=true;$('badge').textContent='';
    $('retry').hidden=true;$('retry').onclick=null;
    for(const k of ['resolution','stats','low','high'])$(k).textContent='';for(const k of ['flipX','flipY'])$(k).setAttribute('aria-pressed',String(k==='flipX'?flipX:flipY));
    for(const c of [canvas,overview,histogram])c.getContext('2d').clearRect(0,0,c.width,c.height);
    for(const input of dialog.querySelectorAll('.viewer-sidebar input,.viewer-sidebar select,.viewer-sidebar button'))input.disabled=true;
    for(const k of ['previous','next','fit','one','plus','minus','rotate','flipX','flipY'])$(k).disabled=true;
    busy(true,'loading');stage.focus();
  }
  async function accept(data,first=false){
    if(!dialog?.open||id!==data.id)return;const version=serial,render=renderSerial;
    const image=new Image();image.src=data.url;
    try{await image.decode();}catch(e){if(dialog.open&&id===data.id&&version===serial&&render===renderSerial){busy(false);error(en()?'Cannot load preview.':'Прегледът не може да се зареди.');$('retry').hidden=false;$('retry').onclick=()=>{if(id!==data.id)return;$('retry').hidden=true;$('error').hidden=true;busy(true);accept(data,first);};call('viewer.presented',{id:data.id,shown:false,error:String(e?.message||e).slice(0,1000)+' · '+data.url}).catch(()=>{});}return;}
    if(!dialog.open||id!==data.id||version!==serial||render!==renderSerial)return;
    const old=frame;picture=image;frame=data;
    window.AstroArchiveExtensions?.viewerReady(dialog,data);
    if(!pendingDisplay)displayDocument(data);
    for(const input of dialog.querySelectorAll('.viewer-sidebar input,.viewer-sidebar select,.viewer-sidebar button'))input.disabled=false;
    for(const k of ['fit','one','plus','minus','rotate','flipX','flipY'])$(k).disabled=false;
    $('name').textContent=data.name;$('meta').textContent=[data.objectName,data.date,data.width+' × '+data.height,data.fits?'FITS · '+Math.abs(data.bitpix)+' bit'+(data.bitpix<0?' float':''):'RGB · 8 bit'].filter(Boolean).join('  ·  ');
    $('path').textContent=data.path;$('resolution').textContent=t('preview')+': '+data.previewWidth+' × '+data.previewHeight;
    $('badge').textContent=data.fits?(data.debayer?'Debayer · '+data.bayerPattern:data.color?'RGB':'Mono'):'RGB';
    if(keptTransform)restoreTransform(keptTransform);else if(old&&old.previewWidth!==data.previewWidth)zoom*=old.previewWidth/data.previewWidth;
    $('retry').hidden=true;$('retry').onclick=null;busy(false);enableControls();if(fitting)fit();else schedule();drawHistogram();
    call('viewer.presented',{id:data.id,shown:true,width:image.naturalWidth,height:image.naturalHeight}).catch(()=>{});
    window.AstroArchiveRemote?.viewerReady(dialog,data);
    for(const action of dialog.querySelectorAll('.viewer-toolbar a[download]'))window.AstroArchiveIcons.mount(action,'download',t('download'));
  }
  function close(silent=false){if(!dialog?.open)return;const closing=id,viewState=snapshot();if(viewState)keptTransform=viewState.transform;window.AstroArchiveExtensions?.viewerClosed();id='';serial++;renderSerial++;clearTimeout(debounce);clearTimeout(pixelTimer);dialog.close();document.documentElement.classList.remove('viewer-open');frame=picture=null;cancelAnimationFrame(animation);animation=0;for(const c of [canvas,overview,histogram]){c.width=1;c.height=1;}restoreFocus?.focus?.();if(silent!==true)call('viewer.close',{id:closing,viewState}).catch(()=>{});}
  function renderSoon(){if(!frame)return;pendingDisplay=displaySettings();renderSerial++;$('error').hidden=true;clearTimeout(debounce);enableControls();debounce=setTimeout(render,180);}
  async function render(){
    if(!frame)return;const requestId=id,version=++renderSerial;
    const display=pendingDisplay||displaySettings(),options=display.options;
    if(options.blackPercent>=options.whitePercent){error(t('invalid'));return;}busy(true);
    try{const data=await call('viewer.render',{id:requestId,...display});if(id===requestId&&version===renderSerial){pendingDisplay=null;await accept(data);}}
    catch(e){if(id===requestId&&version===renderSerial){busy(false);if(!e.cancelled)error(e.message);}}
  }
  async function neighbor(delta){if(!frame||loading||!frame.canNavigate)return;const current=id;busy(true,'loading');try{const result=await call('viewer.neighbor',{id,delta});if(id===current){busy(false);if(!result.opened)error(t('noNext'));}}catch(e){if(id===current){busy(false);if(!e.cancelled)error(e.message);}}}
  function matrix(w=stage.clientWidth,h=stage.clientHeight,s=zoom,offset=pan){return new DOMMatrix().translate(w/2+offset.x,h/2+offset.y).rotate(rotation).scale(flipX?-s:s,flipY?-s:s).translate(-picture.width/2,-picture.height/2);}
  function fit(){if(!picture)return;const quarter=rotation%180!==0;zoom=Math.min((stage.clientWidth-36)/(quarter?picture.height:picture.width),(stage.clientHeight-36)/(quarter?picture.width:picture.height));zoom=Math.max(.001,zoom);pan={x:0,y:0};fitting=true;schedule();}
  function setZoom(value,anchor={x:stage.clientWidth/2,y:stage.clientHeight/2}){if(!picture)return;const at=matrix().inverse().transformPoint(anchor);zoom=Math.max(.01,Math.min(64,value*frame.previewWidth/frame.width))*frame.width/frame.previewWidth;const moved=matrix().transformPoint(at);pan.x+=anchor.x-moved.x;pan.y+=anchor.y-moved.y;fitting=false;schedule();commitViewport();}
  window.addEventListener('astroarchive-inspection-decision',()=>schedule());
  function schedule(remember=true){if(remember&&frame&&picture)keptTransform=transformState();if(!dialog?.open||animation)return;animation=requestAnimationFrame(()=>{animation=0;draw();});}
  function surface(c){const r=c.getBoundingClientRect(),dpr=window.devicePixelRatio||1,w=Math.max(1,Math.round(r.width*dpr)),h=Math.max(1,Math.round(r.height*dpr));if(c.width!==w||c.height!==h){c.width=w;c.height=h;}const context=c.getContext('2d');context.setTransform(dpr,0,0,dpr,0,0);context.clearRect(0,0,r.width,r.height);return{context,w:r.width,h:r.height,dpr};}
  function apply(context,m,dpr){context.setTransform(m.a*dpr,m.b*dpr,m.c*dpr,m.d*dpr,m.e*dpr,m.f*dpr);}
  function draw(){
    const{w,h,dpr}=surface(canvas);if(!picture)return;const m=matrix(w,h);apply(ctx,m,dpr);ctx.imageSmoothingEnabled=$('smooth').checked;ctx.drawImage(picture,0,0);if(dialog.querySelector('#q-excluded-overlay')?.hidden===false){ctx.strokeStyle='#e8b781';ctx.lineWidth=1.5/zoom;ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(picture.width,picture.height);ctx.moveTo(picture.width,0);ctx.lineTo(0,picture.height);ctx.stroke();}
    if($('grid').checked&&zoom>=8){const inv=m.inverse(),corners=[{x:0,y:0},{x:w,y:0},{x:0,y:h},{x:w,y:h}].map(p=>inv.transformPoint(p));const x1=Math.max(0,Math.floor(Math.min(...corners.map(p=>p.x)))),x2=Math.min(picture.width,Math.ceil(Math.max(...corners.map(p=>p.x)))),y1=Math.max(0,Math.floor(Math.min(...corners.map(p=>p.y)))),y2=Math.min(picture.height,Math.ceil(Math.max(...corners.map(p=>p.y))));ctx.strokeStyle='#81a6bf66';ctx.lineWidth=1/zoom;ctx.beginPath();for(let x=x1;x<=x2;x++){ctx.moveTo(x,y1);ctx.lineTo(x,y2);}for(let y=y1;y<=y2;y++){ctx.moveTo(x1,y);ctx.lineTo(x2,y);}ctx.stroke();}
    ctx.setTransform(dpr,0,0,dpr,0,0);if($('cross').checked){ctx.strokeStyle='#8dbbdba0';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(w/2-16,h/2);ctx.lineTo(w/2+16,h/2);ctx.moveTo(w/2,h/2-16);ctx.lineTo(w/2,h/2+16);ctx.stroke();}
    $('zoom').value=(zoom*frame.previewWidth/frame.width*100).toFixed(1);drawOverview();
  }
  function overviewMatrix(w,h){const quarter=rotation%180!==0,s=Math.min(w/(quarter?picture.height:picture.width),h/(quarter?picture.width:picture.height));return matrix(w,h,s,{x:0,y:0});}
  function drawOverview(){const{context:c,w,h,dpr}=surface(overview);if(!picture)return;const small=overviewMatrix(w,h);apply(c,small,dpr);c.drawImage(picture,0,0);c.setTransform(dpr,0,0,dpr,0,0);const inv=matrix().inverse(),points=[{x:0,y:0},{x:stage.clientWidth,y:0},{x:stage.clientWidth,y:stage.clientHeight},{x:0,y:stage.clientHeight}].map(p=>small.transformPoint(inv.transformPoint(p)));c.beginPath();points.forEach((p,i)=>i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y));c.closePath();c.fillStyle='#58b9ff17';c.fill();c.strokeStyle='#65c0ff';c.lineWidth=1.5;c.stroke();}
  function locate(e){const r=overview.getBoundingClientRect(),at=overviewMatrix(r.width,r.height).inverse().transformPoint({x:e.clientX-r.left,y:e.clientY-r.top}),screen=matrix().transformPoint(at);pan.x+=stage.clientWidth/2-screen.x;pan.y+=stage.clientHeight/2-screen.y;fitting=false;schedule();commitViewport();}
  function drawHistogram(){if(!histogram)return;const{context:c,w,h}=surface(histogram);if(!frame)return;const data=frame.histogram,colors=data.channels.length===3?['#ff7e8e','#71dbac','#7db6ff']:['#a9c5df'];const peak=Math.log1p(Math.max(1,...data.channels.flat()));for(let k=0;k<data.channels.length;k++){const bins=data.channels[k];c.beginPath();c.moveTo(0,h);bins.forEach((v,i)=>c.lineTo(i/(bins.length-1)*w,h-4-Math.log1p(v)/peak*(h-10)));c.lineTo(w,h);c.closePath();c.fillStyle=colors[k]+'22';c.fill();c.strokeStyle=colors[k];c.lineWidth=1;c.stroke();}$('low').textContent=fmt(data.minimum);$('high').textContent=fmt(data.maximum);$('stats').textContent=t('median')+' '+fmt(data.median)+'  ·  '+t('mean')+' '+fmt(data.mean);}
  function hover(e){if(!picture||!frame||drag||loading)return;const r=stage.getBoundingClientRect(),p=matrix().inverse().transformPoint({x:e.clientX-r.left,y:e.clientY-r.top}),x=Math.floor(p.x),y=Math.floor(p.y);if(x<0||y<0||x>=picture.width||y>=picture.height)return;
    clearTimeout(pixelTimer);const current=id;pixelTimer=setTimeout(async()=>{if(pixelBusy||id!==current)return;pixelBusy=true;try{const result=await call('viewer.pixel',{id:current,x,y});if(id===current&&result.values){const values=result.values.length===3?result.values.map((v,i)=>'RGB'[i]+' '+fmt(v)).join('  ·  '):fmt(result.values[0]);$('pixel').textContent='X '+fmt((x+.5)*frame.width/frame.previewWidth-.5)+' · Y '+fmt(frame.height-(y+.5)*frame.height/frame.previewHeight-.5)+'  ·  '+values+'  ·  '+t('preview');}}catch{}finally{pixelBusy=false;}},70);
  }
  function displaySettings(){
    return {options:{mode:Number($('mode').value),channel:Number($('channel').value),separateRgb:$('separate').checked,blackPercent:Number($('black').value),whitePercent:Number($('white').value),midtone:Number($('midtone').value),gamma:Number($('gamma').value),negative:$('negative').checked,palette:Number($('palette').value)},edge:Number($('quality').value)||0,debayer:$('debayer').checked,pattern:$('pattern').value};
  }
  function displayDocument(data){
    const options={mode:0,channel:0,palette:0,blackPercent:0,whitePercent:100,midtone:.5,gamma:1,negative:false,separateRgb:!!data.separateRgb,...data.options};
    for(const[key,control]of Object.entries({mode:'mode',channel:'channel',palette:'palette',blackPercent:'black',whitePercent:'white',midtone:'midtone',gamma:'gamma',negative:'negative',separateRgb:'separate'})){
      if($(control).type==='checkbox')$(control).checked=options[key];else{$(control).value=options[key];if($(control+'-number'))$(control+'-number').value=options[key];}
    }
    $('quality').value=[0,1600,3200].includes(data.edge)?String(data.edge):'1600';$('debayer').checked=!!data.debayer;$('pattern').value=data.pattern||'auto';
  }
  function transformState(){
    if(!picture||!frame)return null;
    const center=matrix().inverse().transformPoint({x:stage.clientWidth/2,y:stage.clientHeight/2});
    return {zoom:zoom*frame.previewWidth/frame.width,centerX:center.x/picture.width,centerY:center.y/picture.height,panX:pan.x/Math.max(1,stage.clientWidth),panY:pan.y/Math.max(1,stage.clientHeight),rotation,flipX,flipY,fitting};
  }
  function restoreTransform(value){
    if(!picture||!frame||!value)return;rotation=value.rotation||0;flipX=!!value.flipX;flipY=!!value.flipY;fitting=!!value.fitting;
    for(const key of ['flipX','flipY'])$(key).setAttribute('aria-pressed',String(key==='flipX'?flipX:flipY));
    if(fitting){fit();return;}
    zoom=Math.max(.001,Math.min(64,Number(value.zoom)||1)*frame.width/frame.previewWidth);
    if(Number.isFinite(value.centerX)&&Number.isFinite(value.centerY)){
      pan={x:0,y:0};const at=matrix().transformPoint({x:value.centerX*picture.width,y:value.centerY*picture.height});pan={x:stage.clientWidth/2-at.x,y:stage.clientHeight/2-at.y};
    }else pan={x:(value.panX||0)*stage.clientWidth,y:(value.panY||0)*stage.clientHeight};
    keptTransform=transformState();schedule(false);
  }
  function commitViewport(){if(frame&&picture)window.AstroArchiveSession.edit('viewer',{transform:transformState(),panel:!dialog.classList.contains('viewer-wide')});}
  function snapshot(){
    if(!dialog?.open||!frame||!picture)return null;
    let display=pendingDisplay||displaySettings();
    if(!Number.isFinite(display.options.blackPercent)||display.options.blackPercent>=display.options.whitePercent)display={options:frame.options,edge:frame.edge,debayer:frame.debayer,pattern:frame.pattern};
    return {frameId:id,display,controls:Object.fromEntries(['smooth','cross','grid'].map(key=>[key,$(key).checked])),transform:transformState(),panel:!dialog.classList.contains('viewer-wide')};
  }
  window.AstroArchiveSession.register('viewer',{
    read:()=>{const value=snapshot();if(!value)return null;const{display,...presentation}=value;return presentation;},
    async write(value){
      if(!value.open){close(true);return;}const document=value.document;if(!document)return;
      if(!dialog?.open||id!==document.id){opening({id:document.id,fileName:document.name});await accept(document,true);}
      else if(frame?.url!==document.url)await accept(document,false);
      if(!frame)return;
      if(!pendingDisplay)displayDocument(document);
      if(value.panel!==undefined){dialog.classList.toggle('viewer-wide',!value.panel);panelState();}
      if(value.transform)restoreTransform(value.transform);
      for(const[key,v]of Object.entries(value.controls||{}))$(key).checked=v;
      enableControls();schedule();
    }
  });
  window.AstroArchiveViewer={state:snapshot,next(viewerId){if(id!==viewerId||!dialog?.open||loading)return;const next=dialog.querySelector('#v-next');if(!next.disabled)next.click();else error(t('noNext'));},connect(options){call=options.call;},languageChanged:translate,event(message){if(message.name==='viewer.closed'){close(true);return;}if(message.name==='viewer.loading')opening(message);else if(message.name==='viewer.loaded')accept(message.document,true);else if(message.name==='viewer.problem'&&id===message.id){busy(false);error(message.error);}}};
})();
