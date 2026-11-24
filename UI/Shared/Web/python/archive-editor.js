"use strict";
(() => {
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,action)=>{const b=node('button',text,'quiet-button');b.type='button';b.onclick=()=>Promise.resolve(action()).catch(e=>window.AstroArchiveFunctions.error('archive',e));return b;};
  const option=(value,title)=>new Option(title,value), clone=x=>structuredClone(x);
  const t=window.AstroArchiveI18n.t;
  const titleKeys={"name":"table.name","frames":"archive.framesLabel","files":"archive.filesLabel","stacks":"archive.stacksLabel","exposure":"archive.field.exposure","totalExposure":"analysis.totalExposure","size":"table.size","realTelescope":"archive.field.realTelescope","object":"picker.object","telescope":"picker.telescope","night":"table.observingNight","observed":"archive.field.observed","created":"archive.field.created","filter":"archive.field.filter","sensor":"archive.field.sensor","resolution":"archive.field.resolution","sourceStackCount":"archive.field.sourceStackCount","telescopes":"archive.field.telescopes","kind":"table.type"};
  const fieldTitle=(key,fallback=key)=>Object.hasOwn(titleKeys,key)?t(titleKeys[key]):fallback;
  const format=value=>window.AstroArchiveFunctions.number(value,0);
  const kinds=new Set(['frames','stacks','photos']);
  const common=['size','object','filter','sensor','resolution','kind'];
  const scoped={frames:['frames','exposure','totalExposure','observed','night','telescope','realTelescope','created','files'],stacks:['stacks','frames','totalExposure','observed','night','created','telescope','realTelescope','files'],photos:['created','sourceStackCount','telescopes','files']};
  const archiveKind=archive=>kinds.has(archive.id)?archive.id:kinds.has(archive.type)?archive.type:'frames';
  function informationFields(kind,folder){const allowed=new Set([...common,...scoped[kind]]);if(!folder){for(const key of ['files','stacks','realTelescope'])allowed.delete(key);if(kind==='frames'){allowed.delete('frames');allowed.delete('totalExposure');}}return Object.keys(titleKeys).filter(key=>allowed.has(key)).map(key=>[key,fieldTitle(key)]);}
  const call=(method,params)=>window.AstroArchiveFunctions.call(method,params);
  function input(label,value,onchange){const wrap=node('label',null,'settings-field'),e=node('input');e.type='text';e.value=value??'';e.maxLength=512;e.setAttribute('aria-label',label);e.oninput=()=>onchange(e.value);wrap.append(node('span',label),e);return {wrap,e};}
  // The shared-source option exists only while an enabled plugin provides it.
  function usesSharedSource(id){return ['frames','stacks','photos'].includes(id)&&!!window.AstroArchiveExtensions?.hasCapability?.('archives.sharing.source');}
  async function chooseShared(archive, apply, {confirm=false}={}){
    const U=window.AstroArchiveFunctions, choices=await call('archive.shared.choices',{archiveId:archive.id});
    if(choices.length===1&&!confirm){await apply(choices[0]);return;}
    U.modal(t('archive.chooseShared'),(body,dialog)=>{
      if(!choices.length){body.append(node('p',t('archive.sharedNeedHost')));return;}
      for(const source of choices){
        const pick=button([source.name,source.host].filter(Boolean).join(' · '),async()=>{await apply(source);dialog.close();});
        body.append(pick);
      }
    });
  }
  function sourceLocation(actions,archive,apply){
    const extensions=window.AstroArchiveExtensions;
    if(usesSharedSource(archive.id)&&!extensions.hasAnchor('archive.location',archive.id,'archives.sharing.source')){
      actions.append(button(t('archive.chooseSharedShort'),()=>chooseShared(archive,apply,{confirm:true})));
    }
    extensions?.anchor(actions,'archive.location',{archiveId:archive.id,applySharedSource:apply});
  }
  function render(host,archive,changed,assets=[]){
    const card=node('section',null,'archive-schema-card');host.append(card);let sample;const kind=archiveKind(archive),expandedLevels=new WeakSet();card.dataset.archiveId=archive.id;card.dataset.archiveKind=kind;
    const status=node('p',null,'settings-note'),head=node('div',null,'archive-schema-heading'),name=input(t('archive.name'),archive.name,v=>{archive.name=v;changed();}),path=input(t('archive.location'),archive.path,v=>{archive.path=v;delete archive.sharedSource;path.e.placeholder='';changed();});
    const setPath=()=>{path.e.value=archive.path||'';path.e.placeholder=archive.sharedSource?t('archive.sharedPrefix',{name:archive.sharedSource.name}):'';};
    setPath();
    const actions=node('div',null,'archive-location-actions');
    actions.append(button(t('archive.chooseLocation'),async()=>{const chosen=await call('customization.folder.choose');if(chosen?.path){archive.path=chosen.path;delete archive.sharedSource;setPath();changed();}}));
    sourceLocation(actions,archive,source=>{archive.path='';archive.sharedSource=source;setPath();changed();});head.append(name.wrap,path.wrap,actions);const fileFormula=input(t('archive.fileFormula'),archive.structure?.fileFormula||'{filename}',v=>{if(archive.structure){archive.structure.fileFormula=v;changed();}});fileFormula.wrap.classList.add('archive-file-formula');fileFormula.e.disabled=!archive.structure;card.append(head,status,fileFormula.wrap);
    const details=node('details',null,'archive-schema-options'),summary=node('summary',t('archive.structure')),body=node('div');details.append(summary,body);card.append(details);
    async function load(){status.textContent=t('archive.indexLoading');sample=await call('archive.structure.sample',{archiveId:archive.id}).catch(()=>null);
      if(!sample){sample=await call('archive.structure.sample',{archiveId:'frames'});sample.sample={};sample.hasSample=false;sample.fields=sample.fields.filter(f=>Object.hasOwn(titleKeys,f.key)||['filename','basename','extension'].includes(f.key)).map(f=>({...f,value:null}));sample.structure=sample.presets[archive.type||'frames'];}
      archive.structure ||= clone(sample.structure);archive.type=kind;sample.fields=sample.fields.filter(f=>!Object.hasOwn(titleKeys,f.key)||common.includes(f.key)||scoped[kind].includes(f.key));draw();status.textContent=sample.hasSample?t('archive.indexSample',{name:sample.sampleName}):t('archive.noSample');
    }
    details.addEventListener('toggle',()=>{if(details.open&&!sample)load().catch(e=>status.textContent=e.message);});
    function draw(){body.replaceChildren();const layout=archive.structure;fileFormula.e.disabled=false;fileFormula.e.value=layout.fileFormula;body.append(node('p',t('archive.customStructure',{archive:t('archive.'+kind)}),'schema-info'),button(t('archive.restoreStructure'),()=>{archive.structure=clone(sample.presets[kind]);changed();draw();}));
      const help=node('details'),helpTitle=node('summary',t('archive.fieldHelp')),table=node('table'),head=node('tr');head.append(node('th',t('archive.field')),node('th',t('archive.fieldMeaning')),node('th',t('archive.example')));table.append(head);
      for(const f of sample.fields){const tr=node('tr');tr.append(node('td','{'+f.key+'}'),node('td',fieldTitle(f.key,f.title)),node('td',f.value==null?'—':String(f.value)));table.append(tr);}help.append(helpTitle,node('p',t('archive.formulaHelp')),table);body.append(help);
      layout.levels.forEach((level,index)=>{const group=node('details',null,'archive-schema-level');group.open=expandedLevels.has(level);group.addEventListener('toggle',()=>{if(group.open)expandedLevels.add(level);else expandedLevels.delete(level);});group.style.setProperty('--level',index);const title=node('summary',`${index+1}. ${fieldTitle(level.field,level.field||t('archive.archiveFolder'))}`),line=node('div',null,'archive-schema-line'),field=node('select');field.setAttribute('aria-label',t('archive.groupLevel',{level:format(index+1)}));field.append(...sample.fields.map(f=>option(f.key,fieldTitle(f.key,f.title)+' · '+f.key)));field.value=level.field;field.onchange=()=>{level.field=field.value;level.formula='{'+field.value+'}';changed();draw();};
        const formula=input(t('archive.folderFormula',{level:format(index+1)}),level.formula,v=>{level.formula=v;changed();}),icons=node('select');icons.setAttribute('aria-label',t('archive.levelIcon',{level:format(index+1)}));icons.append(...[['telescope','picker.telescope'],['object','picker.object'],['day','analysis.night'],['folder','archive.archiveFolder'],['fits','type.frame'],['image','prepare.image']].map(([key,label])=>option(key,t(label))),...assets.map((a,i)=>option(a.fileName,t('archive.customIcon',{count:format(i+1)}))));icons.value=level.icon||'folder';icons.onchange=()=>{level.icon=icons.value;changed();};
        const move=delta=>{const target=index+delta;if(target<0||target>=layout.levels.length)return;[layout.levels[index],layout.levels[target]]=[layout.levels[target],layout.levels[index]];changed();draw();};const up=button('↑',()=>move(-1)),down=button('↓',()=>move(1));up.disabled=index===0;down.disabled=index===layout.levels.length-1;
        title.append(node('small',level.columns.map(k=>fieldTitle(k)).join(' · '),'schema-info'));line.append(field,icons,up,down,button(t('archive.removeLevel'),()=>{if(layout.levels.length>1){layout.levels.splice(index,1);changed();draw();}}));group.append(title,line,formula.wrap,node('p',t('archive.levelInformation'),'schema-info'));const fields=node('div');group.append(fields);const fillFields=()=>window.AstroArchiveLayoutEditor.group(fields,informationFields(kind,true),level.columns,v=>{level.columns=v;changed();fillFields();});fillFields();body.append(group);
      });
      const add=button(t('archive.addLevel'),()=>{if(layout.levels.length<8){layout.levels.push({field:'filter',formula:'{filter}',icon:'folder',columns:['files','frames','totalExposure','size']});changed();draw();}});body.append(add);
      const fileDetails=node('details',null,'archive-file-information'),fileSummary=node('summary',t('archive.fileInformation'));fileSummary.append(node('small',layout.fileColumns.map(k=>fieldTitle(k)).join(' · '),'schema-info'));fileDetails.append(fileSummary);body.append(fileDetails);const fields=node('div');fileDetails.append(fields);const fillFileFields=()=>window.AstroArchiveLayoutEditor.group(fields,informationFields(kind,false),layout.fileColumns,v=>{layout.fileColumns=v;changed();fillFileFields();});fillFileFields();
      const preview=node('p',null,'schema-preview');body.append(button(t('archive.showExampleNames'),async()=>{const value=await call('archive.structure.preview',{archiveId:archive.id,structure:layout});preview.textContent=value.names.join(' → ');}),preview);
      if(archive.managedStructure)body.append(node('p',t('archive.managedStructure'),'settings-note'));
      else body.append(node('p',t('archive.structureHint'),'settings-note'),button(t('archive.restructure'),()=>previewEdit(archive.id,'',{},true)));
    }
    return card;
  }
  function showPreview(data,{title=t('archive.previewChanges'),action=t('archive.confirmSave'),onSaved}={}) {
    const U=window.AstroArchiveFunctions;
    return U.modal(title,(body,dialog)=>{
      body.append(node('p',data.backup?t('archive.moveSummary',{files:format(data.count),moves:format(data.moves)}):t('archive.moveEmpty'),'schema-preview'));
      if(data.merges.length)body.append(node('p',t('archive.mergeWith',{names:data.merges.join(', ')}),'schema-merge'));
      if(data.backup)body.append(node('p',t('archive.originalBackup'),'function-note'));
      if(data.examples.length<data.count)body.append(node('p',t('archive.showingExamples',{count:format(data.examples.length)}),'function-note'));
      const rows=node('div');
      body.append(rows);
      const columns=[{title:t('archive.before'),value:r=>r.before},{title:t('archive.after'),value:r=>r.after}];
      if(data.examples.some(row=>row.fields?.length)){
        columns.push({title:t('archive.metadataChanges'),value:row=>row.fields.map(field=>`${field.name}: ${field.before??'—'} → ${field.after}`).join(' · ')||'—'});
      }
      U.table(rows,columns,data.examples);
      const save=button(action,async()=>{
        save.disabled=true;
        try{
          const result=await window.AstroArchivePython.task('archive.metadata.commit',{token:data.token,confirmed:true});
          dialog.close();
          onSaved?.();
          U.modal(t('archive.changesSaved'),content=>{
            content.append(node('p',result.movedFolder?t('archive.folderMoved'):t('archive.filesUpdated',{count:format(result.updated)})));
            if(result.backup)content.append(node('p',t('archive.backup',{path:result.backup}),'function-note'));
          });
        }finally{save.disabled=false;}
      });
      save.className='primary-button';
      body.append(save);
    });
  }
  async function previewEdit(archiveId,relativePath,values={},restructure=false) {
    showPreview(await call('archive.metadata.preview',{archiveId,relativePath,values,restructure}));
  }
  async function renameBatch(archiveId,entry) {
    const U=window.AstroArchiveFunctions;
    const {structure}=await call('archive.structure.sample',{archiveId});
    const depth=entry.relativePath.split(/[\\/]/).filter(Boolean).length;
    U.modal(t('archive.namingIn',{name:entry.name}),(body,dialog)=>{
      const folders=structure.levels.slice(depth).map((level,index)=>input(
        t('archive.subfolder',{level:format(depth+index+1)})+' · '+fieldTitle(level.field),level.formula,()=>{}));
      const file=input(t('archive.fileNames'),structure.fileFormula||'{filename}',()=>{});
      body.append(node('p',t('archive.batchFormulaHint'),'function-note'),
        ...folders.map(field=>field.wrap),file.wrap);
      body.append(button(t('archive.previewNames'),async()=>{
        const data=await call('archive.entries.renamePreview',{archiveId,relativePath:entry.relativePath,
          formulas:folders.map(field=>field.e.value),fileFormula:file.e.value});
        dialog.close();showPreview(data,{title:t('archive.previewNames'),action:t('archive.rename')});
      }));
    });
  }
  async function edit(archiveId,entry){const U=window.AstroArchiveFunctions,data=await call('archive.metadata.describe',{archiveId,relativePath:entry.relativePath});U.modal(t('archive.editInformation'),(body,dialog)=>{
    body.append(node('p',t('archive.editHint',{name:entry.name,count:format(data.count)}),'function-note'));const inputs={};
    for(const f of data.fields){const row=node('div',null,'metadata-edit-row'),enabled=node('input');enabled.type='checkbox';enabled.setAttribute('aria-label',t('archive.changeField',{field:fieldTitle(f.key,f.title)}));const value=data.values[f.key],control=input(fieldTitle(f.key,f.title),Array.isArray(value)?value.join('; '):value==null?'':value,()=>{}),hint=node('small',null,'schema-merge');inputs[f.key]={enabled,input:control.e};const list=node('datalist');list.id='meta-'+f.key+'-'+crypto.randomUUID();list.append(...(data.suggestions[f.key]||[]).map(v=>option(v,v)));control.e.setAttribute('list',list.id);control.e.oninput=()=>{enabled.checked=true;hint.textContent=(data.suggestions[f.key]||[]).some(v=>v.localeCompare(control.e.value,undefined,{sensitivity:'accent'})===0)?t('archive.existingGroup'):'';};row.append(enabled,control.wrap,hint,list);body.append(row);}
    body.append(button(t('archive.previewBeforeSave'),async()=>{const values={};for(const[k,v]of Object.entries(inputs))if(v.enabled.checked)values[k]=v.input.value;await previewEdit(archiveId,entry.relativePath,values);dialog.close();}));
  });}
  async function sourceNights(id){const U=window.AstroArchiveFunctions,nights=await call('archive.source.nights',{id});U.modal(t('archive.sourceObservingNights'),(body,dialog)=>{if(!nights.length)body.append(node('p',t('archive.noSourceFrames')));for(const night of nights)body.append(button(night.name,()=>{dialog.close();window.AstroArchiveArchiveView.navigate(night.archiveId,night.relativePath);}));});}
  window.AstroArchiveArchiveSchema={render,edit,previewEdit,showPreview,renameBatch,fieldTitle,sourceNights,chooseShared,usesSharedSource,sourceLocation};
})();
