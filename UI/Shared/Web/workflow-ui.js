"use strict";
// Shared day picker for analysis and preparation. Filters never discard the basket.
(() => {
  const U=window.AstroArchiveFunctions,{$,node}=U,P=window.AstroArchivePython;
  const i18n = window.AstroArchiveI18n;
  const t = (key, values) => i18n.t('picker.' + key, values);
  const count = (key, value) => t(key, {count: new Intl.NumberFormat(i18n.locale).format(value)});
  // Keep the existing filter value stable for saved sessions.
  const rootFolder = 'В основната папка';
  const option=(value,label)=>{const o=node('option',label);o.value=value;return o;};
  class Picker {
    constructor(host,prefix,onchange){
      this.prefix=prefix;this.host=host;this.onchange=onchange;this.rows=[];this.selected=new Set();this.loaded=false;this.revision=0;this.scope='all';
      try{this.view=localStorage.getItem('astroarchive.picker.view')==='tiles'?'tiles':'rows';}catch{this.view='rows';}
      host.innerHTML=`<div class="flow-picker"><aside class="panel flow-picker-filters"><span class="flow-kicker" data-i18n="picker.dataKicker">ИЗБОР НА ДАННИ</span><h2 data-i18n="picker.fromArchive">От архива</h2><label class="function-field"><span data-i18n="picker.source">Източник</span><select id="${prefix}-kind"></select></label><label class="function-field"><span data-i18n="picker.telescope">Телескоп</span><select id="${prefix}-telescope"></select></label><div class="flow-field-row"><label class="function-field"><span data-i18n="picker.object">Обект</span><select id="${prefix}-object"></select></label><button id="${prefix}-refresh" class="quiet-button icon-button flow-refresh" title="Обнови списъка" aria-label="Обнови списъка" data-i18n-title="picker.refresh" data-i18n-aria-label="picker.refresh"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-2.2L20 8M4 16l2.3 3.2A7 7 0 0 0 17.9 17"/></svg></button></div><p class="function-note" data-i18n="picker.nightHint">Отбележи вечерите вдясно. Изборът се пази, когато сменяш телескопа или обекта.</p><div id="${prefix}-basket" class="flow-basket"></div></aside><section class="panel flow-days-panel"><header class="flow-panel-heading"><div><span class="flow-kicker" data-i18n="picker.nightsKicker">НАБЛЮДАТЕЛНИ НОЩИ</span><h2 data-i18n="picker.chooseNights">Избери вечери</h2></div><div class="function-actions flow-picker-actions"><div class="function-segmented flow-scope" role="group" aria-label="Кои вечери да се показват" data-i18n-aria-label="picker.scope"><button id="${prefix}-scope-all" type="button" aria-pressed="true" data-i18n-text="picker.all">Всички <span class="flow-count" id="${prefix}-count-all">0</span></button><button id="${prefix}-scope-selected" type="button" aria-pressed="false" data-i18n-text="picker.selected">Избрани <span class="flow-count" id="${prefix}-count-selected">0</span></button></div><span class="flow-actions-sep" aria-hidden="true"></span><button id="${prefix}-all" class="text-button" data-i18n="picker.selectAll">Отметни всички</button><button id="${prefix}-none" class="text-button" data-i18n="picker.clear">Изчисти</button><button id="${prefix}-view" type="button" class="quiet-button icon-button flow-view-toggle"></button></div></header><div id="${prefix}-days" class="flow-days"></div><footer class="flow-picker-footer"><div class="flow-count-wrap"><strong id="${prefix}-count"></strong><small id="${prefix}-count-detail" class="flow-count-detail"></small></div><button id="${prefix}-chosen" class="primary-button" disabled data-i18n="picker.confirm">Данните са избрани →</button></footer></section></div>`;
      this.input={};for(const key of ['kind','telescope','object'])this.input[key]=$(prefix+'-'+key);
      this.input.kind.onchange=()=>{this.loaded=false;this.load(true).catch(e=>U.error(prefix==='q'?'analysis':'preparation',e));};
      this.input.telescope.onchange=()=>this.objects();this.input.object.onchange=()=>this.render();
      $(prefix+'-refresh').onclick=()=>this.load(true).catch(e=>U.error(prefix==='q'?'analysis':'preparation',e));
      $(prefix+'-all').onclick=()=>{for(const r of this.visible())this.selected.add(r.id);this.render();};
      $(prefix+'-none').onclick=()=>{this.selected.clear();this.render();};
      $(prefix+'-scope-all').onclick=()=>{this.scope='all';this.render();};
      $(prefix+'-scope-selected').onclick=()=>{this.scope='selected';this.render();};
      $(prefix+'-view').onclick=()=>{this.view=this.view==='tiles'?'rows':'tiles';try{localStorage.setItem('astroarchive.picker.view',this.view);}catch{}const days=$(prefix+'-days');days.dataset.animate='true';clearTimeout(this.animateTimer);this.animateTimer=setTimeout(()=>{delete days.dataset.animate;},450);this.render();};
    }
    async load(refresh=false){
      if(this.loaded&&!refresh)return;const revision=++this.revision,kind=this.requestedArchive||this.input.kind.value;this.requestedArchive=null;
      this.loading = true; this.loadError = null; this.renderLoadState();
      try{
        const settings=await P.call('python.settings.get');if(revision!==this.revision)return;
        this.archives=settings.archives.filter(a=>!a.hidden&&(a.path||a.sharedSource));this.input.kind.replaceChildren(...this.archives.map(a=>option(a.id,i18n.archiveName(a))));
        this.input.kind.value=kind||'frames';if(!this.input.kind.value)this.input.kind.selectedIndex=0;
        if(!this.input.kind.value){this.rows=[];this.selected.clear();this.loaded=true;this.loading=false;this.objects();return;}
        const data=await P.call('python.archive.query',{filters:{archive:this.input.kind.value},view:'picker'});if(revision!==this.revision)return;
        const hadRows=this.rows.length>0;this.rows=data.rows.filter(r=>!['dark','flat','bias'].includes(r.kind));
        if(hadRows){const available=new Set(this.rows.map(r=>r.id));this.selected=new Set([...this.selected].filter(id=>available.has(id)));}
        this.loaded=true;this.loading=false;const tel=this.input.telescope.value;
        this.input.telescope.replaceChildren(option('',t('allTelescopes')),...[...new Set(this.rows.map(r=>this.first(r)))].sort().map(v=>option(v,this.folderMode() && v===rootFolder ? t('rootFolder') : window.AstroArchiveCustomization?.telescope(v)||v)));
        this.input.telescope.value=tel;if(!this.input.telescope.value)this.input.telescope.value='';this.objects();
      }catch(e){if(revision!==this.revision)return;this.loading=false;this.loadError=e.message;this.renderLoadState();throw e;}
    }
    type(){const id=this.input.kind.value;return this.archives?.find(a=>a.id===id)?.type||id;}
    folderMode(){return !!this.archives?.find(a=>a.id===this.input.kind.value)?.managedStructure;}
    folderParts(r){return (r.relativePath||r.relative||r.name||'').split(/[\\/]/).slice(0,-1);}
    first(r){return this.folderMode()?(this.folderParts(r)[0]||rootFolder):r.telescope;}
    second(r){return this.folderMode()?(this.folderParts(r)[1]||rootFolder):r.object;}
    group(r){return this.folderMode()?this.folderParts(r).join('/'):JSON.stringify([r.telescope,r.object,r.night]);}
    visible(){return this.rows.filter(r=>(!this.input.telescope.value||this.first(r)===this.input.telescope.value)&&(!this.input.object.value||this.second(r)===this.input.object.value));}
    objects(){const old=this.input.object.value;this.input.object.replaceChildren(option('',t('allObjects')),...[...new Set(this.rows.filter(r=>!this.input.telescope.value||this.first(r)===this.input.telescope.value).map(r=>this.second(r)))].sort().map(v=>option(v,this.folderMode() && v===rootFolder ? t('rootFolder') : v)));this.input.object.value=old;if(!this.input.object.value)this.input.object.value='';this.render();}
    chosen(){return this.rows.filter(r=>this.selected.has(r.id));}
    // Totals of the chosen files (footer, basket and the Preparation action bar).
    summary(){const rows=this.chosen(),groups=new Set(rows.map(r=>this.group(r))).size;return{files:rows.length,groups,folderMode:this.folderMode(),exposure:rows.reduce((s,r)=>s+(r.totalExposure||0),0),objects:[...new Set(rows.map(r=>r.object).filter(Boolean))]};}
    renderLoadState() {
      $(this.prefix+'-days').textContent = this.loading ? t('loading') : t('loadFailed', {error:this.loadError});
    }
    translate() {
      for (const item of this.input.kind.options) {
        const archive = this.archives?.find(a => a.id === item.value);
        if (archive) item.textContent = i18n.archiveName(archive);
      }
      for (const select of [this.input.telescope, this.input.object]) {
        for (const item of select.options) {
          if (this.folderMode() && item.value === rootFolder) item.textContent = t('rootFolder');
        }
      }
      if (this.loading || this.loadError != null) this.renderLoadState();
      else this.render(false);
    }
    render(notify = true){
      const folder=this.folderMode();
      this.input.telescope.closest('label').querySelector('span').textContent=t(folder?'folderFirst':'telescope');
      this.input.object.closest('label').querySelector('span').textContent=t(folder?'folderSecond':'object');
      if(this.input.telescope.options[0])this.input.telescope.options[0].textContent=t(folder?'allFolders':'allTelescopes');
      if(this.input.object.options[0])this.input.object.options[0].textContent=t(folder?'allSubfolders':'allObjects');
      this.host.querySelector('.flow-panel-heading .flow-kicker').textContent=t(folder?'foldersKicker':'nightsKicker');
      this.host.querySelector('.flow-panel-heading h2').textContent=t(folder?'chooseFolders':'chooseNights');
      this.host.querySelector('.flow-picker-filters .function-note').textContent=t(folder?'folderHint':'nightHint');
      const host=$(this.prefix+'-days');host.replaceChildren();const groups=new Map();
      const groupCount=list=>new Set(list.map(r=>this.group(r))).size;
      const shown=this.scope==='selected'?this.rows.filter(r=>this.selected.has(r.id)):this.visible();
      host.dataset.view=this.view;
      for(const r of shown){const key=this.group(r);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
      for(const rows of [...groups.values()].sort((a,b)=>(b[0].night||'').localeCompare(a[0].night||'')||a[0].object.localeCompare(b[0].object))){
        const r=rows[0],line=node('label',null,'flow-day'),check=node('input');check.type='checkbox';check.dataset.night=r.night;
        const checked=rows.filter(r=>this.selected.has(r.id)).length;check.checked=checked===rows.length;check.indeterminate=checked>0&&checked<rows.length;
        check.onchange=()=>{for(const r of rows)check.checked?this.selected.add(r.id):this.selected.delete(r.id);this.render();};
        const text=node('div'),total=rows.reduce((s,r)=>s+(r.totalExposure||0),0);text.append(node('strong',this.folderMode()?this.folderParts(r).slice(2).join(' / ')||this.folderParts(r).at(-1)||t('rootFolder'):r.night||t('unknownNight')),node('span',this.folderMode()?this.folderParts(r).slice(0,2).join(' / '):r.object+' · '+(window.AstroArchiveCustomization?.telescope(r.telescope)||r.telescope)),node('small',[count('files',rows.length),t('exposure',{time:U.time(total)})].join(' · ')));
        {const tags=window.AstroArchiveTags,keys=new Set(rows.map(x=>x.tag?JSON.stringify(x.tag):'')),mark=keys.size>1?tags?.mixedBadge():tags?.badge(rows[0].tag);if(mark)text.children[1]?.append(' ',mark);} // Beta 1.3 UI: data tag
        line.dataset.selected=String(check.checked||check.indeterminate);line.append(check,node('span','▦','flow-day-icon'),text,node('span',count('frameShort',rows.length),'flow-day-files'),node('span',U.time(total),'flow-day-exposure'));host.append(line);
      }
      if(!host.children.length)host.append(node('p',t(this.scope==='selected'?'emptySelection':'emptyFilters'),'function-empty'));
      $(this.prefix+'-count-all').textContent=U.number(groupCount(this.visible()));$(this.prefix+'-count-selected').textContent=U.number(groupCount(this.chosen()));
      $(this.prefix+'-scope-all').setAttribute('aria-pressed',String(this.scope==='all'));$(this.prefix+'-scope-selected').setAttribute('aria-pressed',String(this.scope==='selected'));
      $(this.prefix+'-all').disabled=this.scope==='selected';
      const viewButton=$(this.prefix+'-view'),toTiles=this.view!=='tiles';viewButton.title=t(toTiles?'tiles':'rows');viewButton.setAttribute('aria-label',viewButton.title);
      viewButton.innerHTML=toTiles?'<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>';
      const selected=this.chosen(),sum=this.summary();
      $(this.prefix+'-count').textContent=selectionSummary(sum);
      $(this.prefix+'-count-detail').textContent=t('exposure',{time:U.time(sum.exposure)});
      $(this.prefix+'-basket').replaceChildren(node('span',t('basket'),'flow-kicker'),node('strong',count('files',sum.files)),node('span',t('exposure',{time:U.time(sum.exposure)})));
      $(this.prefix+'-chosen').disabled=!selected.length;if(notify)this.onchange?.(selected);
    }
    snapshot(){return{archive:this.input.kind.value,telescope:this.input.telescope.value,object:this.input.object.value,ids:[...this.selected]};}
    async restore(value){if(!value)return;if(!this.loaded||this.input.kind.value!==value.archive){this.requestedArchive=value.archive;this.input.kind.value=value.archive;this.loaded=false;await this.load();}this.input.telescope.value=value.telescope||'';this.objects();this.input.object.value=value.object||'';this.selected=new Set(value.ids||[]);this.render();}
    async receive(ids,archive='frames'){this.requestedArchive=archive;this.input.kind.value=archive;this.loaded=false;await this.load(true);const available=new Set(this.rows.map(r=>r.id));this.selected=new Set(ids.filter(id=>available.has(id)));this.render();}
  }
  function phase(section,value){const root=$(section+'-content');root.dataset.phase=value;for(const panel of root.querySelectorAll(':scope > [data-flow-phase]'))panel.hidden=panel.dataset.flowPhase!==value;window.dispatchEvent(new CustomEvent('astroarchive-flow-phase',{detail:{section,phase:value}}));}
  function progress(host,task){host.replaceChildren(node('strong',task.phase||i18n.t('nav.preparation')),node('span',`${Math.round((task.progress||0)*100)}%`));const bar=node('progress');bar.max=1;bar.value=task.progress||0;host.append(bar);}
  function selectionSummary(sum) {
    return [count('files',sum.files),count(sum.folderMode?'folders':'nights',sum.groups)].join(' · ');
  }
  window.AstroArchiveFlow={Picker,option,phase,progress,selectionSummary};
})();
