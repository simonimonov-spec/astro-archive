// Beta 1.3 UI
"use strict";
(() => {
  const imageType = 'application/x-astroarchive-image';
  const paneType = 'application/x-astroarchive-viewer-pane';
  const maxPanes = 8, maxGroups = 8;
  const labels = {
    title:['Сравнение на снимки','Compare images'], newGroup:['Нов преглед','New viewer'],
    add:['Добави снимка','Add image'], addHint:['Пусни снимка върху този бутон или избери от архива.','Drop an image on this button or choose from the archive.'],
    files:['Снимки от текущия изглед в архива','Images in the current archive view'],
    noFiles:['Отвори папка със снимки в Архива или довлечи снимка от таблицата.','Open an image folder in the Archive or drag an image from the table.'],
    choose:['Избери снимка','Choose an image'], position:['Позиция спрямо снимката','Position relative to image'],
    left:['Отляво','Left'],right:['Отдясно','Right'], top:['Отгоре','Above'],bottom:['Отдолу','Below'],
    minimize:['Прибери прегледа','Minimize viewer'], maximize:['Разшири / възстанови прегледа','Maximize / restore viewer'],
    detach:['Отдели в прозорец','Open in a separate window'],dock:['Върни в приложението','Dock in the application'],
    close:['Затвори прегледа','Close viewer'], closeImage:['Затвори снимката','Close image'],
    hideImage:['Скрий снимката','Hide image'], hiddenImages:['Скрити снимки','Hidden images'],
    closePicker:['Затвори избора на снимка','Close image picker'],
    shelf:['Отворени прегледи','Open viewers'], move:['Премести снимката спрямо друга','Move image relative to another'],
    settingsSide:['Премести настройките от другата страна','Move controls to the other side'],
    up:['Премести панела нагоре','Move panel up'],down:['Премести панела надолу','Move panel down'],
    options:['Изглед и мащаб','Display and scale'], local:['Елементи на изображението','Image overlays'],
    limit:['Достигнат е броят отворени прегледи или снимки.','The maximum number of open viewers or images has been reached.'],
    blocked:['Прозорецът не може да се отвори. Прегледът остава в приложението.','The window could not open. The viewer stays in the application.'],
    loadError:['Снимката не може да се добави за сравнение.','The image could not be added for comparison.'],
  };
  const t = key => labels[key][(window.AstroArchiveI18n?.locale||'bg').startsWith('bg') ? 0 : 1];
  const directions = ['left','right','top','bottom'];
  const groups = new Set();
  let api, shelf, active, unloading = false, nextPane = 0;
  const node = (tag, className, text) => {
    const el=document.createElement(tag);
    if(className)el.className=className;
    if(text!==undefined)el.textContent=text;
    return el;
  };
  function button(key, action, icon) {
    const el=node('button', 'quiet-button', icon ? undefined : t(key));
    el.type='button';el.dataset.viewerLabel=key;el.onclick=action;
    if(icon)window.AstroArchiveIcons.mount(el,icon,t(key));
    return el;
  }
  function translate(root) {
    for (const el of root.querySelectorAll('[data-viewer-label]')) {
      const label=t(el.dataset.viewerLabel);
      if(el.dataset.appIcon){el.title=label;el.setAttribute('aria-label',label);}
      else el.textContent=label;
    }
  }

  function file(entry, archiveId) {
    if(entry.isLink || !['fits','image'].includes(entry.kind))return null;
    return {name:entry.name, source:{archiveId:entry.archiveId||archiveId, relativePath:entry.relativePath}};
  }
  function attachArchiveDrag(element, entry, archiveId) {
    if(window.AstroArchiveRemote || !window.chrome?.webview)return;
    const image=file(entry,archiveId);
    if(!image)return;
    element.draggable=true;
    element.addEventListener('dragstart',event=>writeDrag(event,imageType,image,'copy'));
  }
  function writeDrag(event,type,value,effect) {
    event.dataTransfer.setData(type,JSON.stringify(value));
    event.dataTransfer.effectAllowed=effect;
  }
  function dropTarget(button, onImage, onPane) {
    const type = data => data.types.includes(imageType) ? imageType
      : onPane && data.types.includes(paneType) ? paneType : null;
    button.addEventListener('dragover',event=>{
      const format=type(event.dataTransfer);
      if(!format)return;
      event.preventDefault();
      event.dataTransfer.dropEffect=format===imageType?'copy':'move';
      button.classList.add('viewer-drop-active');
    });
    button.addEventListener('dragleave',()=>button.classList.remove('viewer-drop-active'));
    button.addEventListener('drop',event=>{
      button.classList.remove('viewer-drop-active');
      const format=type(event.dataTransfer);
      if(!format)return;
      event.preventDefault();event.stopPropagation();
      let data;
      try{data=JSON.parse(event.dataTransfer.getData(format));}catch{return;}
      if(format===paneType && Number.isInteger(data))onPane(data);
      else if(typeof data?.name==='string' && typeof data.source?.archiveId==='string'
              && typeof data.source?.relativePath==='string')onImage(data);
    });
  }

  function panelSections(dialog) {
    const sidebar=dialog.querySelector('[data-viewer-id="sidebar"]');
    const sections=[...sidebar.querySelectorAll(':scope > section')];
    for (const original of sections) {
      const title=original.querySelector('h3, .viewer-select > span');
      const key=title?.dataset.text || 'local';
      const section=node('div','viewer-setting-section');
      const details=node('details');
      details.open=true;
      const head=node('div','viewer-setting-heading');
      const summary=node('summary');
      if(title?.tagName==='H3'){summary.dataset.text=key;title.remove();}
      else if(key==='quality')summary.dataset.viewerLabel='options';
      else summary.dataset.viewerLabel='local';
      const controls=node('span','viewer-setting-order');
      for (const [direction, icon] of [[-1,'up'],[1,'down']]) {
        controls.append(button(icon,()=>{
          const sibling=direction<0?section.previousElementSibling:section.nextElementSibling;
          if(sibling?.classList.contains('viewer-setting-section')){
            sidebar.insertBefore(direction<0?section:sibling,direction<0?sibling:section);
          }
        },icon));
      }
      details.append(summary,original);
      head.append(controls);
      section.append(details,head);
      sidebar.insertBefore(section,sidebar.querySelector('.viewer-file'));
    }
    // The controls remain outside the native summary so Enter toggles only the panel.
    const side=button('settingsSide',()=>{
      dialog.classList.toggle('viewer-settings-left');
    }, 'panel');
    side.classList.add('viewer-settings-side');
    sidebar.prepend(side);
  }

  function connect(options) {
    api=options;
    if(window.AstroArchiveRemote || !window.chrome?.webview)return;
    shelf=node('nav','viewer-shelf');
    shelf.setAttribute('aria-label',t('shelf'));
    shelf.hidden=true;
    document.body.append(shelf);
    window.addEventListener('beforeunload',()=>{
      unloading=true;
      for(const group of groups)group.popup?.close();
    });
  }
  function show(group) {
    if(group.popup){
      native(group,'focus').then(()=>group.popup?.focus()).catch(error=>report(group,error.message));
      return;
    }
    if(active && active!==group)active.root.hidden=true;
    active=group;group.root.hidden=false;
    drawShelf();
  }
  function hide(group) {
    if(group.popup){native(group,'minimize').catch(error=>report(group,error.message));return;}
    group.root.hidden=true;
    if(active===group)active=null;
    drawShelf();
  }
  function drawShelf() {
    if(!shelf)return;
    shelf.replaceChildren(button('newGroup',()=>createGroup(), 'add'));
    shelf.hidden=!groups.size;
    document.documentElement.classList.toggle('viewer-shelf-open',groups.size>0);
    for (const group of groups) {
      const item=node('button','quiet-button viewer-shelf-item',group.name||t('title'));
      item.type='button';item.title=group.name||t('title');
      item.setAttribute('aria-pressed',String(group===active));
      item.onclick=()=>group===active?hide(group):show(group);
      shelf.append(item);
    }
  }
  function report(group,message) {
    group.status.textContent=message;
    group.status.hidden=!message;
  }

  function createGroup() {
    if(!shelf || groups.size>=maxGroups){if(active)report(active,t('limit'));return null;}
    const root=node('section','viewer-comparison');
    const heading=node('header','viewer-comparison-heading');
    const title=node('h2',null,t('title'));
    const controls=node('div','viewer-comparison-actions');
    const body=node('div','viewer-comparison-body');
    const grid=node('div','viewer-comparison-grid');
    const picker=node('aside','viewer-image-picker');
    const status=node('p','viewer-comparison-status');
    const hiddenImages=node('div','viewer-hidden-images'), hiddenPanes=node('div');
    hiddenImages.hidden=hiddenPanes.hidden=true;
    hiddenImages.setAttribute('aria-label',t('hiddenImages'));
    const group={root,title,grid,picker,status,hiddenImages,hiddenPanes,tree:null,panes:new Map(),name:'',popup:null,token:null};
    const add=button('add',()=>pick(group), 'add');
    dropTarget(add,image=>pick(group,null,image),key=>placeDraggedPane(group,firstPane(group),key));
    group.detach=button('detach',()=>openWindow(group),'detach');
    group.dock=button('dock',()=>returnToApp(group,true),'dock');
    group.dock.hidden=true;
    controls.append(add,group.detach,group.dock,
      button('minimize',()=>hide(group),'minimize'),
      button('maximize',()=>{
        if(group.popup)native(group,'maximize').catch(error=>report(group,error.message));
        else root.classList.toggle('viewer-comparison-expanded');
      },'maximize'),button('close',()=>closeGroup(group),'close'));
    heading.append(title,controls);
    status.hidden=true;status.setAttribute('role','status');
    body.append(grid,picker);root.append(heading,status,hiddenImages,body,hiddenPanes);
    document.body.append(root);groups.add(group);show(group);pick(group);
    return group;
  }
  function closeGroup(group) {
    if (group.popup) returnToApp(group,false);
    for(const pane of group.panes.values())pane.viewer.destroy();
    group.panes.clear();
    group.root.remove();groups.delete(group);
    if(active===group)active=null;
    drawShelf();
  }

  function branch(target, pane, direction) {
    const vertical=direction==='top'||direction==='bottom';
    return {vertical, children:direction==='left'||direction==='top'?[pane,target]:[target,pane]};
  }
  function replace(tree, target, value) {
    if(tree===target)return value;
    if(!tree?.children)return tree;
    const [a,b]=tree.children.map(child=>replace(child,target,value));
    return a && b ? {...tree,children:[a,b]} : a||b;
  }
  function render(group) {
    function element(tree) {
      if(!tree.children)return tree.hidden?null:tree.element;
      const children=tree.children.map(element).filter(Boolean);
      if(children.length<2)return children[0]||null;
      const split=group.root.ownerDocument.createElement('div');
      split.className='viewer-split';
      if(tree.vertical)split.classList.add('viewer-split-vertical');
      split.append(...children);
      return split;
    }
    const content=group.tree&&element(group.tree);
    group.grid.replaceChildren(...(content?[content]:[]));
    const hidden=[...group.panes.values()].filter(pane=>pane.hidden);
    group.hiddenPanes.replaceChildren(...hidden.map(pane=>pane.element));
    group.hiddenImages.hidden=!hidden.length;
    group.hiddenImages.replaceChildren(...hidden.map(pane=>{
      const restore=node('button','quiet-button viewer-shelf-item',pane.name);
      restore.type='button';restore.title=pane.name;
      restore.onclick=()=>{pane.hidden=false;render(group);pane.element.querySelector('.viewer-stage')?.focus();};
      return restore;
    }));
  }
  function move(group, pane, target, direction) {
    if(!pane || !target || pane===target || !group.panes.has(pane.key) || !group.panes.has(target.key))return;
    group.tree=replace(replace(group.tree,pane,null),target,branch(target,pane,direction));
    render(group);
    group.picker.hidden=true;
  }
  function removePane(group,pane) {
    pane.viewer.destroy();
    group.panes.delete(pane.key);
    group.tree=replace(group.tree,pane,null);
    render(group);
    if(!group.tree)closeGroup(group);
    else if(!group.picker.hidden)pick(group);
  }
  async function addPane(group, image, target, direction='right', presentation) {
    if(group.panes.size>=maxPanes){report(group,t('limit'));return false;}
    report(group,'');
    target=target&&group.panes.has(target.key)?target:firstPane(group);
    const pane={key:++nextPane, element:node('div','viewer-tile'), name:image.name};
    const callbacks={
      close:()=>removePane(group,pane),
      hide:()=>{
        pane.hidden=true;
        render(group);
        group.hiddenImages.lastElementChild?.focus();
        if(!group.picker.hidden)pick(group);
      },
      choose:()=>pick(group,pane),
      add:image=>pick(group,pane,image),
      move:key=>placeDraggedPane(group,pane,key),
      drag:event=>writeDrag(event,paneType,pane.key,'move'),
      ready:data=>{pane.name=data.name;},
    };
    pane.viewer=window.AstroArchiveViewer.createPane(pane.element,callbacks);
    pane.viewer.connect(api);
    group.panes.set(pane.key,pane);
    group.tree=group.tree?replace(group.tree,target,branch(target,pane,direction)):pane;
    if(!group.name){
      group.name=image.name;group.title.textContent=image.name;group.title.title=image.name;drawShelf();
    }
    group.picker.hidden=true;
    render(group);
    return pane.viewer.open(image.source,image.name,presentation);
  }
  function select(label,values) {
    const field=node('label','viewer-picker-field',label), input=node('select');
    for(const [value,text] of values)input.add(new Option(text,value));
    field.append(input);
    return {field,input};
  }
  function firstPane(group) {
    return [...group.panes.values()].find(pane=>!pane.hidden) || group.panes.values().next().value;
  }
  function placeDraggedPane(group, target, key) {
    const ownPane=group.panes.get(key);
    if(ownPane){
      if(ownPane!==target)pick(group,target,null,ownPane);
      return;
    }
    for(const other of groups){
      const pane=other.panes.get(key), frame=pane?.viewer.current();
      if(frame){
        pick(group,target,{name:frame.name,source:{sourceFrameId:frame.id},presentation:pane.viewer.state()});
        return;
      }
    }
  }
  function pick(group, target, dropped, moving) {
    if(!groups.has(group))return;
    target=target&&group.panes.has(target.key)?target:firstPane(group);
    const caption=node('h3',null,t(moving?'move':'add'));
    const header=node('div','viewer-picker-heading');
    header.append(caption,button('closePicker',()=>{group.picker.hidden=true;},'close'));
    const choices=select(t('position'),directions.map(direction=>[direction,t(direction)]));
    choices.input.value='right';
    const status=node('p','settings-note');
    if(target)status.textContent=target.name;
    group.picker.replaceChildren(header);
    if(target)group.picker.append(status,choices.field);
    if(moving || dropped){
      group.picker.append(node('p','viewer-picker-filename',(moving||dropped).name));
      group.picker.append(button(moving?'move':'add',()=>moving
        ? move(group,moving,target,choices.input.value)
        : addPane(group,dropped,target,choices.input.value,dropped.presentation)));
    }else{
      const list=node('div','viewer-picker-list');
      list.setAttribute('aria-label',t('files'));
      for(const image of api.files()){
        const item=node('button','quiet-button',image.name);
        item.type='button';item.title=image.name;item.draggable=true;
        item.onclick=()=>addPane(group,image,target,choices.input.value);
        item.addEventListener('dragstart',event=>writeDrag(event,imageType,image,'copy'));
        list.append(item);
      }
      if(!list.childElementCount)list.append(node('p','settings-note',t('noFiles')));
      group.picker.append(list);
      if(target && group.panes.size>1){
        const other=select(t('move'),[[0,t('choose')],...[...group.panes.values()]
          .filter(pane=>pane!==target&&!pane.hidden).map(pane=>[pane.key,pane.name])]);
        group.picker.append(other.field,button('move',()=>move(group,group.panes.get(Number(other.input.value)),target,choices.input.value)));
      }
    }
    group.picker.hidden=false;
    if(dropped || moving)group.picker.querySelector('button:last-child')?.focus();
  }

  function native(group,action) {
    return api.call('viewer.window',{token:group.token,action});
  }
  function copyTheme(target) {
    const root=document.documentElement, other=target.documentElement;
    other.style.cssText=root.style.cssText;
    for(const key of ['theme','animations','imageTint'])if(root.dataset[key]!==undefined)other.dataset[key]=root.dataset[key];
    other.lang=root.lang;
  }
  function returnToApp(group, showGroup) {
    const popup=group.popup, token=group.token;
    group.popup=null;group.token=null;
    group.themeObserver?.disconnect();
    group.themeObserver=null;
    document.body.append(group.root);
    for(const pane of group.panes.values())pane.viewer.refresh();
    group.detach.hidden=false;group.dock.hidden=true;
    if(popup && !popup.closed)popup.close();
    if(token)api.call('viewer.window',{token,action:'forget'}).catch(()=>{});
    if(showGroup){show(group);window.focus();}
    else {group.root.hidden=true;drawShelf();}
  }
  async function openWindow(group) {
    if(group.popup)return;
    const token=crypto.randomUUID().replaceAll('-','');
    const popup=window.open('',token,'popup,width=1024,height=680,resizable=yes');
    if(!popup){report(group,t('blocked'));return;}
    group.popup=popup;group.token=token;
    try{
      const doc=popup.document;
      const base=doc.createElement('base');base.href=document.baseURI;doc.head.append(base);
      for(const link of document.querySelectorAll('link[rel="stylesheet"],link[rel="icon"]'))doc.head.append(link.cloneNode());
      doc.title=document.title+' · '+token;
      doc.body.classList.add('viewer-popup');
      copyTheme(doc);
      group.themeObserver=new MutationObserver(()=>copyTheme(doc));
      group.themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['style','data-theme','data-animations','data-image-tint','lang']});
      popup.addEventListener('pagehide',()=>{
        if(!unloading && group.popup===popup)returnToApp(group,false);
      });
      if(active===group)active=null;
      doc.body.append(group.root);group.root.hidden=false;
      for(const pane of group.panes.values())pane.viewer.refresh();
      group.detach.hidden=true;group.dock.hidden=false;
      drawShelf();
      await native(group,'bind');
      if(group.popup!==popup)return;
      doc.title='Astro Archive · '+(group.name||t('title'));
      popup.focus();
    }catch(error){
      if(group.popup!==popup)return;
      returnToApp(group,true);
      report(group,error.message||t('blocked'));
    }
  }

  async function compareCurrent(viewer, target, dropped, add) {
    const data=viewer.current();
    if(!data || add.disabled)return;
    const group=createGroup();
    if(!group)return;
    add.disabled=true;
    try{
      const ready=await addPane(group,{name:data.name,source:{sourceFrameId:data.id}},null,'right',viewer.state());
      if(!ready){
        closeGroup(group);
        const error=target.querySelector('.viewer-error');error.textContent=t('loadError');error.hidden=false;
        return;
      }
      viewer.close();
      pick(group,null,dropped);
    }finally{add.disabled=false;}
  }
  function configureViewer(dialog, prefix, callbacks) {
    dialog.classList.add('viewer-workbench-controls');
    panelSections(dialog);
    const heading=dialog.querySelector('.viewer-heading');
    const close=dialog.querySelector('#'+prefix+'close');
    const actions=node('div','viewer-comparison-actions');
    const add=button('add',()=>callbacks?callbacks.choose():compareCurrent(window.AstroArchiveViewer,dialog,null,add),'add');
    add.classList.add('viewer-add-image');
    add.title=t('addHint');
    dropTarget(add,image=>callbacks?callbacks.add(image):compareCurrent(window.AstroArchiveViewer,dialog,image,add),callbacks?.move);
    heading.append(actions);actions.append(add);
    if(callbacks)actions.append(button('hideImage',callbacks.hide,'minimize'));
    actions.append(close);
    if(callbacks){
      const title=heading.querySelector('h2');
      title.draggable=true;
      title.addEventListener('dragstart',callbacks.drag);
      close.dataset.label='closeImage';
    }
    translate(dialog);
    add.title=t('addHint');
  }
  function languageChanged() {
    const primary=document.querySelector('.image-viewer:not(.viewer-tile .image-viewer)');
    if(primary)translate(primary);
    for(const group of groups){
      translate(group.root);
      for(const pane of group.panes.values())pane.viewer.languageChanged();
    }
    if(shelf){shelf.setAttribute('aria-label',t('shelf'));drawShelf();}
  }

  function openImage(image) {
    const group=createGroup();
    if(group && image)addPane(group,image);
  }

  window.AstroArchiveViewerWorkbench={connect,configureViewer,attachArchiveDrag,file,languageChanged,openImage};
})();
