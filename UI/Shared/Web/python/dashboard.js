"use strict";
(() => {
  const U=window.AstroArchiveFunctions,{$,node,number:n,time,table}=U,P=window.AstroArchivePython;
  const i18n=window.AstroArchiveI18n,t=i18n.t;
  function localized(tag,key,className){
    const element=node(tag,t(key),className);
    element.dataset.i18n=key;
    return element;
  }
  const button=(label,fn,cls='quiet-button')=>{const b=node('button',label,cls);b.type='button';b.onclick=()=>Promise.resolve(fn()).catch(e=>U.error('archive',e));return b;};
  const field=(label,type='text',value='')=>{const wrap=node('label',null,'function-field'),input=node('input');input.type=type;input.value=value;wrap.append(node('span',label),input);return {wrap,input};};
  const option=(value,label)=>{const e=node('option',label);e.value=value;return e;};
  const filters=node('section',null,'panel py-archive-filters');filters.hidden=true;
  const controls=node('div',null,'function-filters'),stats=node('section',null,'folder-summary py-filter-stats');stats.hidden=true;stats.dataset.i18nAriaLabel='archive.filterStatistics';stats.setAttribute('aria-label',t('archive.filterStatistics'));
  const stat=(label,className)=>{const row=node('div',null,'summary-stat'),value=node('strong','—',className);row.append(localized('span',label),value);stats.append(row);return value;};
  stats.append(localized('span','archive.filterHeading','py-filter-heading'));
  const frameCount=stat('archive.framesLabel','py-filter-frames'),exposure=stat('analysis.totalExposure','py-filter-exposure'),fileNote=node('small',null,'py-filter-note'),warning=localized('small','archive.incompleteExposure','py-filter-note py-filter-warning');warning.hidden=true;stats.append(fileNote,warning);
  const filterInputs={};for(const[key,label]of [['archive','nav.archive'],['telescope','picker.telescope'],['object','picker.object'],['night','analysis.night'],['tag','tag.label'],['since','archive.recent']]){const wrap=node('label',null,'function-field'),select=node('select');select.id='py-filter-'+key;wrap.append(localized('span',label),select);controls.append(wrap);filterInputs[key]=select;}
  function translatedOption(value,key){
    const element=localized('option',key);element.value=value;return element;
  }
  function archiveOption(archive){
    const element=option(archive.id,i18n.archiveName(archive));
    element.archive=archive;
    return element;
  }
  filterInputs.archive.append(...[['frames','archive.framesLabel'],['stacks','archive.stacksLabel'],['photos','archive.photosLabel']].map(([value,key])=>translatedOption(value,key)));
  filterInputs.tag.append(...[['','picker.all'],['astro','tag.astro'],['photometry','archive.photometryTags'],['variable','tag.variable'],['exoplanet','tag.exoplanet'],['other','tag.other'],['none','tag.none']].map(([value,key])=>translatedOption(value,key))); // Beta 1.3 UI
    const sinceOptions = [translatedOption('','picker.all'),translatedOption('1','archive.recentHours'),option('7',''),option('30','')];
  filterInputs.since.append(...sinceOptions);
  let statistics;
  function renderStatistics(s){
    frameCount.textContent=n(s.frames,0);exposure.textContent=time(s.exposure);
    fileNote.textContent=t('picker.files',{count:n(s.files,0)})+' · '+n(s.bytes/1073741824)+' GB';
    warning.hidden=!s.exposureUnknown;
  }
  P.translateArchiveDashboard=()=>{
    for(const item of sinceOptions.slice(2))item.textContent=t('archive.recentDays',{days:n(Number(item.value),0)});
    for(const item of filterInputs.archive.options)if(item.archive)item.textContent=i18n.archiveName(item.archive);
    if(statistics)renderStatistics(statistics);
  };
  P.translateArchiveDashboard();
  filters.append(localized('h3','archive.filterTitle','archive-filter-title'),controls);const sidebar=document.querySelector('#page-archive .archive-sidebar');sidebar.prepend(filters);sidebar.append(stats);
  async function facets(){const value=await P.call('python.archive.facets',{filters:{archive:filterInputs.archive.value}});for(const key of ['telescope','object','night']){const old=filterInputs[key].value;filterInputs[key].replaceChildren(translatedOption('','picker.all'),...value[key].map(v=>option(v,v)));filterInputs[key].value=old;}await query();}
  async function query(){const chosen={};for(const[key,input]of Object.entries(filterInputs)){input.closest('label').dataset.active=String(!!input.value);if(input.value)chosen[key]=key==='since'?Date.now()/1000-Number(input.value)*86400:input.value;}await window.AstroArchiveArchiveView.filter(chosen);}
  for(const[key,input]of Object.entries(filterInputs))input.onchange=()=>Promise.resolve(key==='archive'?facets():query()).catch(e=>U.error('archive',e));
  const toggle=button(t('archive.filters'),async()=>{const active=filters.hidden;filters.hidden=stats.hidden=!active;sidebar.classList.toggle('filter-mode',active);toggle.setAttribute('aria-pressed',String(active));if(active){const settings=await P.call('python.settings.get'),selected=window.AstroArchiveArchiveView.current().archiveId;filterInputs.archive.replaceChildren(...settings.archives.filter(a=>a.path&&!a.hidden).map(archiveOption));filterInputs.archive.value=selected;if(!filterInputs.archive.value)filterInputs.archive.selectedIndex=0;await facets();}else await window.AstroArchiveArchiveView.filter(null);});toggle.id='archive-filter-toggle';toggle.setAttribute('aria-pressed','false');
  window.addEventListener('astroarchive-shared-archive',event=>{const chosen=event.detail.filters,active=!!chosen;filters.hidden=stats.hidden=!active;sidebar.classList.toggle('filter-mode',active);toggle.setAttribute('aria-pressed',String(active));if(chosen)for(const[key,input]of Object.entries(filterInputs)){const value=key==='since'?(chosen.since?String(Math.round((Date.now()/1000-chosen.since)/86400)):''):chosen[key]||'';if(![...input.options].some(o=>o.value===value))input.append(option(value,value));input.value=value;input.closest('label').dataset.active=String(!!value);}});
  window.addEventListener('astroarchive-archive-filter-reset',()=>{filters.hidden=stats.hidden=true;sidebar.classList.remove('filter-mode');toggle.setAttribute('aria-pressed','false');});
  window.addEventListener('astroarchive-archive-filter-statistics',event=>{statistics=event.detail;renderStatistics(statistics);});
  const iconize=(b,key,paths)=>{b.classList.add('archive-icon-button');b.title=t(key);b.setAttribute('aria-label',t(key));b.dataset.i18nTitle=key;b.dataset.i18nAriaLabel=key;b.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+paths+'</svg>';b.append(localized('span',key,'sr-only'));return b;};
  document.querySelector('.archive-actions').append(iconize(toggle,'archive.filters','<path d="M3 5h18l-7 8.5V19l-4 2v-7.5z"/>'),iconize(button(t('archive.trash'),()=>trash()),'archive.trash','<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'));
  const importButton=$('archive-import'),importWrap=node('div',null,'archive-import-choice'),importMenu=node('div',null,'archive-import-menu');importButton.before(importWrap);importWrap.append(importButton,importMenu);importMenu.id='archive-import-menu';importMenu.hidden=true;importMenu.setAttribute('role','menu');importMenu.dataset.i18nAriaLabel='archive.importChoice';importMenu.setAttribute('aria-label',t('archive.importChoice'));
  const closeImport=(focus=false)=>{importMenu.hidden=true;importButton.setAttribute('aria-expanded','false');if(focus)importButton.focus();};
  for(const[folder,title,description]of [[false,'archive.importFiles','archive.importFilesHint'],[true,'archive.importFolder','archive.importFolderHint']]){const item=button('',async()=>{closeImport();const current=window.AstroArchiveArchiveView.current();await window.AstroArchiveTags.importWithTags({folder,archiveId:current.entered&&current.shared?current.archiveId:undefined});});item.setAttribute('role','menuitem');item.append(window.AstroArchiveArchiveIcons.create(folder?'folder':'fits'),localized('span',title),localized('small',description));importMenu.append(item);}
  function openImport(){importMenu.hidden=false;importButton.setAttribute('aria-expanded','true');const rect=importButton.getBoundingClientRect();importMenu.style.left=Math.max(12,Math.min(rect.left,innerWidth-importMenu.offsetWidth-12))+'px';importMenu.style.top=Math.max(12,Math.min(rect.bottom+8,innerHeight-importMenu.offsetHeight-12))+'px';importMenu.querySelector('button').focus();}
  importButton.onclick=()=>importMenu.hidden?openImport():closeImport();importButton.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();openImport();}});
  importMenu.onkeydown=event=>{const items=[...importMenu.querySelectorAll('button')],at=items.indexOf(document.activeElement);if(event.key==='Escape'){event.preventDefault();closeImport(true);}else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();items[(at+(event.key==='ArrowDown'?1:items.length-1))%items.length].focus();}};
  document.addEventListener('pointerdown',event=>{if(!importWrap.contains(event.target))closeImport();});document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!importMenu.hidden)closeImport(true);});window.addEventListener('resize',()=>closeImport());
  async function scan(){
    const settings=await P.call('python.settings.get'),archives=settings.archives.filter(a=>!a.hidden&&(a.path||a.sharedSource)),current=window.AstroArchiveArchiveView.current(),chosen=current.entered?archives.filter(a=>a.id===current.archiveId):archives;
    if(!chosen.length)throw Error('Първо избери архивните папки от Настройки.');
    const results=[];for(const archive of chosen){try{const result=await P.task('python.archive.scan',{archiveId:archive.id});results.push({archive:archive.name,...result});}catch(error){results.push({archive:archive.name,problem:error.message});}}
    window.dispatchEvent(new CustomEvent('astroarchive-python-index-changed'));if(!filters.hidden)await facets();
    if(!results.some(r=>r.problem||r.errors?.length||r.missingMetadataFiles))return results;
      U.modal('Проверка на информацията · резултат',content=>{for(const result of results){content.append(node('h3',result.archive));if(result.problem){content.append(node('p',result.problem,'error-banner'));continue;}if(result.shared){content.append(node('p',`${n(result.files,0)} файла в обновения каталог`));continue;}content.append(node('p',`${n(result.files,0)} файла проверени · ${n(result.read,0)} хедъра прочетени · ${n(result.errors.length,0)} грешки · ${n(result.missingMetadataFiles,0)} с непълна информация`),node('p','Индекс: '+result.cacheFile,'function-note'));const list=node('ul');for(const error of result.errors.slice(0,20))list.append(node('li',error.path+' · '+error.problem));const labels={totalExposure:'липсва записана експозиция',frames:'неизвестен брой кадри в стака',object:'неизвестен обект',telescope:'неизвестен телескоп',night:'неизвестна нощ',dimensions:'неизвестни размери'};for(const missing of result.missingExamples||[])list.append(node('li',missing.path+' · '+missing.fields.map(f=>labels[f]||f).join(', ')));content.append(list);if(result.missingMetadataFiles)content.append(node('p','Липсващите в самия файл стойности са отбелязани като неизвестни. Общата експозиция събира известните стойности.','function-note'));}});
    return results;
  }
  P.refreshArchives=scan;
  async function trash(){const data=await P.call('python.trash.list');U.modal(t('archive.trashTitle'),(body,dialog)=>{const list=node('div');body.append(node('p',t('archive.trashHint'),'function-note'),list);
    table(list,[{title:t('type.file'),value:r=>r.originalName},{title:t('archive.trashOriginal'),value:r=>r.file.relative},{title:'',value:r=>button(t('file.preview'),async()=>{dialog.close();await P.call('python.trash.preview',{id:r.id});})},{title:'',value:r=>button(t('archive.trashRestore'),async()=>{await P.call('python.trash.restore',{id:r.id});dialog.close();await trash();})}],data);
    body.append(button(t('archive.trashEmpty'),async()=>{if(await U.confirm(t('archive.trashConfirmTitle'),t('archive.trashConfirm',{count:n(data.filter(r=>r.state==='trashed').length,0)}),t('archive.trashDelete'))){await P.call('python.trash.empty',{confirmed:true});dialog.close();}}));});}
  async function settingsDialog(){const configuration=await P.call('python.settings.get');let archives=structuredClone(configuration.archives),programs=structuredClone(configuration.programs);
    U.modal(t('archive.settingsTitle'),(body,dialog)=>{
      const error=node('p',null,'error-banner');error.hidden=true;body.append(error,node('h3',t('archive.archives')));const archiveList=node('div');body.append(archiveList);
      function renderArchives(){
        archiveList.replaceChildren();
        for(const a of archives.filter(a=>!a.hidden)){
          const row=node('div',null,'py-settings-row'),name=field(t('table.name'),'text',a.name),path=field(t('archive.archiveFolder'),'text',a.path);
          const source=window.AstroArchiveArchiveSchema, actions=node('div',null,'archive-location-actions');
          const setPath=()=>{path.input.value=a.path||'';path.input.placeholder=a.sharedSource?t('archive.sharedPrefix',{name:a.sharedSource.name}):'';};
          setPath();
          name.input.oninput=()=>a.name=name.input.value;
          path.input.oninput=()=>{a.path=path.input.value;delete a.sharedSource;path.input.placeholder='';};
          actions.append(button(t('archive.chooseLocation'),async()=>{const {path:chosen}=await P.call('python.settings.choose',{kind:'folder'});if(chosen){a.path=chosen;delete a.sharedSource;setPath();}}));
          source.sourceLocation(actions,a,selected=>{a.path='';a.sharedSource=selected;setPath();});row.append(name.wrap,path.wrap,actions);archiveList.append(row);
        }
      }
      renderArchives();body.append(button(t('archive.addArchive'),()=>{archives.push({id:'library-'+crypto.randomUUID().replaceAll('-',''),name:t('archive.newArchive'),path:''});renderArchives();}));
      body.append(node('h3',t('archive.programs')));const programList=node('div');body.append(programList);
      function renderPrograms(){programList.replaceChildren();for(const p of programs){const row=node('div',null,'py-settings-program');const name=field(t('table.name'),'text',p.name),exe=field(t('archive.executable'),'text',p.executable),folder=field(t('archive.workingFolder'),'text',p.workingFolder);name.input.oninput=()=>p.name=name.input.value;exe.input.oninput=()=>p.executable=exe.input.value;folder.input.oninput=()=>p.workingFolder=folder.input.value;
        row.append(name.wrap,exe.wrap,button(t('archive.chooseProgram'),async()=>{p.executable=(await P.call('python.settings.choose',{kind:'executable'})).path;exe.input.value=p.executable;}),folder.wrap,button(t('archive.chooseWorkingFolder'),async()=>{p.workingFolder=(await P.call('python.settings.choose',{kind:'folder'})).path;folder.input.value=p.workingFolder;}),button(t('plugins.remove'),()=>{programs=programs.filter(v=>v!==p);renderPrograms();}));programList.append(row);}}
      renderPrograms();body.append(button(t('archive.addProgram'),()=>{programs.push({id:crypto.randomUUID().replaceAll('-',''),name:t('archive.newProgram'),executable:'',workingFolder:''});renderPrograms();}));
      body.append(node('h3',t('archive.performance')));const parallel=node('div',null,'function-inline'),workers=field(t('archive.workers'),'number',configuration.workers),memory=field(t('archive.memory'),'number',configuration.memoryMb);parallel.append(workers.wrap,memory.wrap);body.append(parallel);
      const aliases=field(t('archive.aliases'),'text',JSON.stringify(configuration.aliases));body.append(aliases.wrap);
      const actions=node('div',null,'function-actions');actions.append(button(t('settings.save'),async()=>{try{const saved=await P.call('python.settings.save',{archives,programs,aliases:JSON.parse(aliases.input.value||'{}'),workers:Number(workers.input.value),memoryMb:Number(memory.input.value)});window.AstroArchiveCustomization.locationsSaved(saved.archives);dialog.close();}catch(e){error.hidden=false;error.textContent=e.message;}},'primary-button'),button(t('archive.importSettings'),async()=>{const result=await P.call('python.settings.import');if(!result.cancelled){dialog.close();await settingsDialog();}}));body.append(actions);
    });
  }
  window.addEventListener('astroarchive-python-settings',()=>settingsDialog().catch(e=>U.error('archive',e)));
  const appearance=$('settings-panel-appearance'),storageButton=button(t('archive.storageSettings'),settingsDialog);storageButton.dataset.i18n='archive.storageSettings';storageButton.setAttribute('data-storage-settings','');appearance.append(storageButton);
  const legacy=document.querySelector('.legacy-debug');if(legacy)legacy.hidden=true;
  window.addEventListener('astroarchive-python-index-changed',()=>{window.AstroArchiveAnalysis.reset();window.AstroArchivePreparation.reset();});
  window.AstroArchivePython.settings=settingsDialog;
  P.provenance=async id=>{const graph=await P.call('archive.provenance',{id});U.modal(t('archive.provenance'),body=>{const visit=(row,depth=0)=>{const section=node('div');section.style.marginLeft=Math.min(depth,5)*18+'px';section.append(node('strong',row.name||row.id),node('p',row.relativePath||t('archive.missingSource'),'function-note'));if(row.archiveId){section.append(button(t('archive.toFolder'),()=>{document.querySelectorAll('.function-dialog').forEach(d=>d.close());window.AstroArchiveArchiveView.navigate(row.archiveId,row.relativePath.split('/').slice(0,-1).join('/'));P.navigate('archive');}),button(t('file.preview'),()=>P.image(row.id)));}body.append(section);for(const child of row.sources||[])visit(child,depth+1);};visit(graph);});};
  window.addEventListener('astroarchive-python-event',event=>{if(event.detail.name==='archive.changed'){window.AstroArchiveAnalysis.reset();window.AstroArchivePreparation.reset();}});
})();
