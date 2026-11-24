// Beta 1.3 UI
"use strict";
(() => {
  const U=window.AstroArchiveFunctions, P=window.AstroArchivePython;
  let menu, anchor, clipboard;
  const text=(bg,en)=>U.tr(bg,en);
  const api=()=>window.AstroArchiveArchiveView;
  const directory=entry=>['folder','archive'].includes(entry.kind);
  const image=entry=>['fits','image'].includes(entry.kind);
  const scope=(archiveId,entry)=>({archiveId,relativePath:entry.relativePath||''});

  function dismiss(restoreFocus=false) {
    if(!menu?.matches(':popover-open'))return;
    menu.hidePopover();
    if(restoreFocus)anchor?.focus();
  }

  function ensureMenu() {
    if(menu)return;
    menu=U.node('div',null,'archive-context-menu');
    menu.setAttribute('popover','manual');
    menu.setAttribute('role','menu');
    menu.tabIndex=-1;
    document.body.append(menu);
    document.addEventListener('pointerdown',event=>{if(!menu.contains(event.target))dismiss();});
    document.addEventListener('contextmenu',event=>{if(!event.defaultPrevented)dismiss();});
    document.addEventListener('scroll',event=>{if(!menu.contains(event.target))dismiss();},true);
    window.addEventListener('resize',()=>dismiss());
    menu.addEventListener('keydown',event=>{
      const buttons=[...menu.querySelectorAll('button:not(:disabled)')];
      const index=buttons.indexOf(document.activeElement);
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismiss(true);return;}
      if(event.key==='Tab'){dismiss(true);return;}
      if(event.key==='ArrowDown'||event.key==='ArrowUp'){
        event.preventDefault();
        const step=event.key==='ArrowDown'?1:-1;
        buttons.at(index<0?(step>0?0:-1):(index+buttons.length+step)%buttons.length)?.focus();
      }else if(event.key==='Home'||event.key==='End'){
        event.preventDefault();buttons.at(event.key==='Home'?0:-1)?.focus();
      }else if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
        const files=[...menu.querySelectorAll('.archive-context-file-actions button:not(:disabled)')];
        const here=files.indexOf(document.activeElement);
        if(here<0)return;
        event.preventDefault();
        files[(here+files.length+(event.key==='ArrowRight'?1:-1))%files.length]?.focus();
      }
    });
  }

  function command(label, action, icon, enabled=true) {
    const button=U.node('button',null,'archive-context-command');
    button.type='button';button.disabled=!enabled;
    button.setAttribute('role','menuitem');
    button.tabIndex=-1;
    const labelNode=U.node('span',label);
    if(icon)button.innerHTML=window.AstroArchiveIcons.markup(icon);
    button.append(labelNode);
    button.onclick=async()=>{
      dismiss();
      try{await action();}catch(error){U.error('archive',error);}
    };
    return button;
  }

  function modalAction(label,action) {
    const button=U.node('button',label,'quiet-button');
    button.type='button';
    button.onclick=async()=>{
      try{await action();}catch(error){U.error('archive',error);}
    };
    return button;
  }

  function renameFile(archiveId,entry) {
    U.modal(text('Преименувай','Rename'),(body,dialog)=>{
      const label=U.node('label',null,'function-field');
      const field=U.node('input');field.value=entry.name;
      label.append(U.node('span',text('Име на файла','File name')),field);
      const problem=U.node('p',null,'function-note');
      problem.setAttribute('role','alert');
      const save=modalAction(text('Запази името','Save name'),async()=>{
        const name=field.value.trim();
        const extension=entry.name.slice(entry.name.lastIndexOf('.'));
        if(image(entry)&&!name.toLowerCase().endsWith(extension.toLowerCase())){
          problem.textContent=text('Запази разширението '+extension+'.','Keep the '+extension+' extension.');
          return;
        }
        save.disabled=true;
        try{
          await P.call('python.archive.rename',{...scope(archiveId,entry),name});
          dialog.close();
        }catch(error){problem.textContent=error.message;}
        finally{save.disabled=false;}
      });
      body.append(label,problem,save);
    });
  }

  async function tag(archiveId,entry,archive) {
    const current=entry.details?.tag;
    const value=await window.AstroArchiveTags.chooseTag({
      stack:entry.details?.kind==='stack'||(archive?.type||archiveId)==='stacks',
      current:current==='mixed'?null:current,
      title:text('Етикет · ','Tag · ')+entry.name,
      allowClear:true
    });
    if(value===undefined)return;
    await P.task('archive.tag.set',{archiveId,relativePaths:[entry.relativePath||''],tag:value});
  }

  async function prepare(archiveId,entry) {
    const result=await P.call('python.archive.query',{filters:{archive:archiveId,relative:entry.relativePath||''}});
    const ids=result.rows.map(row=>row.id);
    if(!ids.length)throw new Error(text('В избраното място няма кадри или снимки.','The selection contains no images.'));
    await window.AstroArchivePreparation.receive(ids,archiveId);
    if(!window.AstroArchivePreparation.selection().ids.length){
      throw new Error(text('Няма изображения, които могат да се изберат за подготовка.','No images in this selection can be prepared.'));
    }
    P.navigate('preparation');
  }

  async function information(archiveId,entry,shared) {
    const data=directory(entry)||image(entry)?await P.call('archive.metadata.describe',scope(archiveId,entry)):null;
    U.modal(text('Информация · ','Information · ')+entry.name,(body,dialog)=>{
      body.append(U.node('p',entry.relativePath||entry.name,'archive-context-path'));
      if(data){
        const summary=text('Изображения: ','Images: ')+data.count+' · '+U.number(data.totals.bytes/1048576,2)+' MB';
        body.append(U.node('p',summary,'function-note'));
        const host=U.node('div');body.append(host);
        const rows=data.fields.filter(field=>data.values[field.key]!=null&&data.values[field.key]!=='');
        if(rows.length)U.table(host,[
          {title:text('Поле','Field'),value:field=>field.title},
          {title:text('Стойност','Value'),value:field=>String(data.values[field.key])}
        ],rows);
        if(!shared&&data.count)body.append(modalAction(text('Редактирай информацията…','Edit information…'),async()=>{
          await window.AstroArchiveArchiveSchema.edit(archiveId,entry);dialog.close();
        }));
      }
      if(entry.kind==='archive'&&!shared){
        for(const [folder,label] of [[false,text('Внеси файлове…','Import files…')],[true,text('Внеси папка…','Import folder…')]]){
          body.append(modalAction(label,()=>{dialog.close();return window.AstroArchiveTags.importWithTags({archiveId,folder});}));
        }
      }
      if(image(entry)&&!entry.isLink){
        body.append(modalAction(text('Преглед','Preview'),()=>{
          dialog.close();return entry.id?P.image(entry.id):P.call('image.open',scope(archiveId,entry));
        }));
      }
      if(entry.id&&(entry.details?.sourceFiles?.length||entry.details?.kind==='stack')){
        body.append(modalAction(text('Изходни кадри и стакове','Source frames and stacks'),()=>{
          dialog.close();return P.provenance(entry.id);
        }));
      }
      if(entry.id&&entry.details?.kind==='stack')body.append(modalAction(text('Намери изходните нощи','Find source nights'),()=>{
        dialog.close();return window.AstroArchiveArchiveSchema.sourceNights(entry.id);
      }));
    });
  }

  async function paste(archiveId,entry) {
    const source=clipboard;
    if(!source)return;
    const target=directory(entry)?entry.relativePath||'':entry.relativePath.split(/[\\/]/).slice(0,-1).join('/');
    const data=await P.call('archive.entries.movePreview',{archiveId:source.archiveId,relativePath:source.relativePath,
      targetArchiveId:archiveId,targetRelativePath:target});
    window.AstroArchiveArchiveSchema.showPreview(data,{
      title:text('Преглед преди поставяне','Preview move'),
      action:text('Премести файловете','Move files'),
      onSaved:()=>{if(clipboard===source)clipboard=null;}
    });
  }

  async function remove(archiveId,entry) {
    const title=text('Премести в кошчето','Move to Trash');
    const prompt=directory(entry)
      ? text('Изображенията в „'+entry.name+'“ и подпапките ѝ ще отидат в кошчето.','Move the images inside “'+entry.name+'” and its subfolders to Trash?')
      : text('„'+entry.name+'“ ще отиде в кошчето.','Move “'+entry.name+'” to Trash?');
    if(!await U.confirm(title,prompt,title))return;
    await P.task('archive.entries.delete',{...scope(archiveId,entry),confirmed:true});
    if(clipboard?.archiveId===archiveId&&clipboard.relativePath===entry.relativePath)clipboard=null;
    const current=api().current();
    if(directory(entry)&&current.archiveId===archiveId){
      const parent=(entry.relativePath||'').replaceAll('\\','/');
      if(current.path.replaceAll('\\','/')===parent||current.path.replaceAll('\\','/').startsWith(parent+'/')){
        api().navigate(archiveId,parent.split('/').slice(0,-1).join('/'));
      }
    }
  }

  function open(event,archiveId,entry,archive) {
    if(!archiveId||!entry||window.AstroArchiveRemote&&!image(entry)&&!directory(entry))return;
    event.preventDefault();
    dismiss();
    ensureMenu();
    anchor=event.currentTarget;
    const keyboard=event.type==='keydown'||event.clientX===0&&event.clientY===0;
    const valid=!entry.isLink;
    const shared=Boolean(archive?.sharedSource||window.AstroArchiveRemote);
    const folder=directory(entry), enabled=valid&&(folder||image(entry));
    const writable=enabled&&!shared;
    const movable=writable&&entry.kind!=='archive';
    const actions=U.node('div',null,'archive-context-file-actions');
    actions.setAttribute('role','group');
    actions.setAttribute('aria-label',text('Действия с файлове','File actions'));
    actions.append(
      command(text('Изрежи','Cut'),()=>{clipboard=scope(archiveId,entry);},'cut',movable),
      command(text('Постави','Paste'),()=>paste(archiveId,entry),'paste',writable&&Boolean(clipboard)),
      command(text('Изтрий','Delete'),()=>remove(archiveId,entry),'trash',movable)
    );
    menu.setAttribute('aria-label',entry.name);
    menu.replaceChildren(
      command(folder?text('Промени именуването…','Change naming…'):text('Преименувай…','Rename…'),
        ()=>folder?window.AstroArchiveArchiveSchema.renameBatch(archiveId,entry):renameFile(archiveId,entry),'rename',writable),
      command(text('Промени етикета…','Change data tag…'),()=>tag(archiveId,entry,archive),'tag',writable),
      command(text('Подготовка','Preparation'),()=>prepare(archiveId,entry),'prepare',enabled),
      command(text('Информация','Information'),()=>information(archiveId,entry,shared),'info',valid),
      actions
    );
    menu.style.left=menu.style.top='0px';
    menu.showPopover();
    const box=anchor.getBoundingClientRect();
    const bounds=menu.getBoundingClientRect();
    const x=keyboard?box.left:event.clientX, y=keyboard?box.bottom:event.clientY;
    menu.style.left=Math.max(0,Math.min(x,window.innerWidth-bounds.width))+'px';
    menu.style.top=Math.max(0,Math.min(y,window.innerHeight-bounds.height))+'px';
    if(keyboard)menu.querySelector('button:not(:disabled)')?.focus({preventScroll:true});
    else menu.focus({preventScroll:true});
  }

  window.AstroArchiveArchiveMenu={
    attach(element,resolve) {
      function display(event){
        const target=resolve(event);
        if(target)open(event,target.archiveId,target.entry,target.archive);
      }
      element.addEventListener('contextmenu',display);
      element.addEventListener('keydown',event=>{
        if((event.key==='ContextMenu'||event.shiftKey&&event.key==='F10')&&event.target===element)display(event);
      });
    }
  };
})();
