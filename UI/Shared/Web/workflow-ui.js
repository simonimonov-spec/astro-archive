"use strict";
// Shared day picker for analysis and preparation. Filters never discard the basket.
(() => {
  const U=window.AstroArchiveFunctions,{$,node}=U,P=window.AstroArchivePython;
  const option=(value,label)=>{const o=node('option',label);o.value=value;return o;};
  class Picker {
    constructor(host,prefix,onchange){
      this.prefix=prefix;this.host=host;this.onchange=onchange;this.rows=[];this.selected=new Set();this.loaded=false;this.revision=0;
      host.innerHTML=`<div class="flow-picker"><aside class="panel flow-picker-filters"><span class="flow-kicker">ИЗБОР НА ДАННИ</span><h2>От архива</h2><label class="function-field"><span>Източник</span><select id="${prefix}-kind"></select></label><label class="function-field"><span>Телескоп</span><select id="${prefix}-telescope"></select></label><label class="function-field"><span>Обект</span><select id="${prefix}-object"></select></label><button id="${prefix}-refresh" class="text-button">↻ Обнови списъка</button><p class="function-note">Отбележи вечерите вдясно. Изборът се пази, когато сменяш телескопа или обекта.</p><div id="${prefix}-basket" class="flow-basket"></div></aside><section class="panel flow-days-panel"><header class="flow-panel-heading"><div><span class="flow-kicker">НАБЛЮДАТЕЛНИ НОЩИ</span><h2>Избери вечери</h2></div><div class="function-actions"><button id="${prefix}-all" class="text-button">Всички показани</button><button id="${prefix}-none" class="text-button">Изчисти избора</button></div></header><div id="${prefix}-days" class="flow-days"></div><footer class="flow-picker-footer"><strong id="${prefix}-count"></strong><button id="${prefix}-chosen" class="primary-button" disabled>Данните са избрани →</button></footer></section></div>`;
      this.input={};for(const key of ['kind','telescope','object'])this.input[key]=$(prefix+'-'+key);
      this.input.kind.onchange=()=>{this.loaded=false;this.load(true).catch(e=>U.error(prefix==='q'?'analysis':'preparation',e));};
      this.input.telescope.onchange=()=>this.objects();this.input.object.onchange=()=>this.render();
      $(prefix+'-refresh').onclick=()=>this.load(true).catch(e=>U.error(prefix==='q'?'analysis':'preparation',e));
      $(prefix+'-all').onclick=()=>{for(const r of this.visible())this.selected.add(r.id);this.render();};
      $(prefix+'-none').onclick=()=>{this.selected.clear();this.render();};
    }
    async load(refresh=false){
      if(this.loaded&&!refresh)return;const revision=++this.revision,kind=this.requestedArchive||this.input.kind.value;this.requestedArchive=null;
      $(this.prefix+'-days').textContent='Зареждане на запазената информация…';
      try{
        const settings=await P.call('python.settings.get');if(revision!==this.revision)return;
        this.archives=settings.archives.filter(a=>a.path&&!a.hidden);this.input.kind.replaceChildren(...this.archives.map(a=>option(a.id,a.name)));
        this.input.kind.value=kind||'frames';if(!this.input.kind.value)this.input.kind.selectedIndex=0;
        if(!this.input.kind.value){this.rows=[];this.selected.clear();this.loaded=true;this.objects();return;}
        const data=await P.call('python.archive.query',{filters:{archive:this.input.kind.value},view:'picker'});if(revision!==this.revision)return;
        const hadRows=this.rows.length>0;this.rows=data.rows.filter(r=>!['dark','flat','bias'].includes(r.kind));
        if(hadRows){const available=new Set(this.rows.map(r=>r.id));this.selected=new Set([...this.selected].filter(id=>available.has(id)));}
        this.loaded=true;const tel=this.input.telescope.value;
        this.input.telescope.replaceChildren(option('','Всички телескопи'),...[...new Set(this.rows.map(r=>this.first(r)))].sort().map(v=>option(v,window.AstroArchiveCustomization?.telescope(v)||v)));
        this.input.telescope.value=tel;if(!this.input.telescope.value)this.input.telescope.value='';this.objects();
      }catch(e){$(this.prefix+'-days').textContent='Списъкът не можа да се зареди: '+e.message;throw e;}
    }
    type(){const id=this.input.kind.value;return this.archives?.find(a=>a.id===id)?.type||id;}
    folderMode(){return !!this.archives?.find(a=>a.id===this.input.kind.value)?.managedStructure;}
    folderParts(r){return (r.relativePath||r.relative||r.name||'').split(/[\\/]/).slice(0,-1);}
    first(r){return this.folderMode()?(this.folderParts(r)[0]||'В основната папка'):r.telescope;}
    second(r){return this.folderMode()?(this.folderParts(r)[1]||'В основната папка'):r.object;}
    group(r){return this.folderMode()?this.folderParts(r).join('/'):JSON.stringify([r.telescope,r.object,r.night]);}
    visible(){return this.rows.filter(r=>(!this.input.telescope.value||this.first(r)===this.input.telescope.value)&&(!this.input.object.value||this.second(r)===this.input.object.value));}
    objects(){const old=this.input.object.value;this.input.object.replaceChildren(option('','Всички обекти'),...[...new Set(this.rows.filter(r=>!this.input.telescope.value||this.first(r)===this.input.telescope.value).map(r=>this.second(r)))].sort().map(v=>option(v,v)));this.input.object.value=old;if(!this.input.object.value)this.input.object.value='';this.render();}
    chosen(){return this.rows.filter(r=>this.selected.has(r.id));}
    render(){
      const folder=this.folderMode();this.input.telescope.closest('label').querySelector('span').textContent=folder?'Папка · първо ниво':'Телескоп';this.input.object.closest('label').querySelector('span').textContent=folder?'Папка · второ ниво':'Обект';if(this.input.telescope.options[0])this.input.telescope.options[0].textContent=folder?'Всички папки':'Всички телескопи';if(this.input.object.options[0])this.input.object.options[0].textContent=folder?'Всички подпапки':'Всички обекти';this.host.querySelector('.flow-panel-heading h2').textContent=folder?'Избери комплекти / папки':'Избери вечери';this.host.querySelector('.flow-picker-filters .function-note').textContent=folder?'Подборът следва папките на този архив. Всеки комплект и вложена папка се избират отделно.':'Отбележи вечерите вдясно. Изборът се пази, когато сменяш телескопа или обекта.';
      const host=$(this.prefix+'-days');host.replaceChildren();const groups=new Map();
      for(const r of this.visible()){const key=this.group(r);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
      for(const rows of [...groups.values()].sort((a,b)=>(b[0].night||'').localeCompare(a[0].night||'')||a[0].object.localeCompare(b[0].object))){
        const r=rows[0],line=node('label',null,'flow-day'),check=node('input');check.type='checkbox';check.dataset.night=r.night;
        const count=rows.filter(r=>this.selected.has(r.id)).length;check.checked=count===rows.length;check.indeterminate=count>0&&count<rows.length;
        check.onchange=()=>{for(const r of rows)check.checked?this.selected.add(r.id):this.selected.delete(r.id);this.render();};
        const text=node('div'),total=rows.reduce((s,r)=>s+(r.totalExposure||0),0);text.append(node('strong',this.folderMode()?this.folderParts(r).slice(2).join(' / ')||this.folderParts(r).at(-1)||'В основната папка':r.night||'Неизвестна нощ'),node('span',this.folderMode()?this.folderParts(r).slice(0,2).join(' / '):r.object+' · '+(window.AstroArchiveCustomization?.telescope(r.telescope)||r.telescope)),node('small',`${rows.length} файла · ${U.time(total)} експозиция`));
        line.dataset.selected=String(check.checked);line.append(check,node('span','▦','flow-day-icon'),text);host.append(line);
      }
      if(!host.children.length)host.append(node('p','Няма данни за тези филтри. Провери архивните папки и обнови архива.','function-empty'));
      const selected=this.chosen(),groupsCount=new Set(selected.map(r=>this.group(r))).size;
      $(this.prefix+'-count').textContent=`${selected.length} файла · ${groupsCount} ${this.folderMode()?'папки':'вечери'}`;
      $(this.prefix+'-basket').replaceChildren(node('span','ОБЩ ИЗБОР','flow-kicker'),node('strong',selected.length+' файла'),node('span',U.time(selected.reduce((s,r)=>s+(r.totalExposure||0),0))+' обща експозиция'));
      $(this.prefix+'-chosen').disabled=!selected.length;this.onchange?.(selected);
    }
    snapshot(){return{archive:this.input.kind.value,telescope:this.input.telescope.value,object:this.input.object.value,ids:[...this.selected]};}
    async restore(value){if(!value)return;if(!this.loaded||this.input.kind.value!==value.archive){this.requestedArchive=value.archive;this.input.kind.value=value.archive;this.loaded=false;await this.load();}this.input.telescope.value=value.telescope||'';this.objects();this.input.object.value=value.object||'';this.selected=new Set(value.ids||[]);this.render();}
    async receive(ids,archive='frames'){this.requestedArchive=archive;this.input.kind.value=archive;this.loaded=false;await this.load(true);const available=new Set(this.rows.map(r=>r.id));this.selected=new Set(ids.filter(id=>available.has(id)));this.render();}
  }
  function phase(section,value){const root=$(section+'-content');root.dataset.phase=value;for(const panel of root.querySelectorAll(':scope > [data-flow-phase]'))panel.hidden=panel.dataset.flowPhase!==value;window.dispatchEvent(new CustomEvent('astroarchive-flow-phase',{detail:{section,phase:value}}));}
  function progress(host,task){host.replaceChildren(node('strong',task.phase||'Подготовка'),node('span',`${Math.round((task.progress||0)*100)}%`));const bar=node('progress');bar.max=1;bar.value=task.progress||0;host.append(bar);}
  window.AstroArchiveFlow={Picker,option,phase,progress};
})();
