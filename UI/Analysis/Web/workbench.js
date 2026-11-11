"use strict";
(() => {
 const U=window.AstroArchiveFunctions,{$,node}=U,A=window.AstroArchiveAnalysis,tools=$('q-tools-panel'),plot=$('q-plot-panel');
 const scope=tools.querySelector('section'),finish=$('q-save').closest('section');scope.className='chart-scope-bar';
 finish.className='analysis-finish-bar';finish.querySelector('h3')?.remove();finish.prepend($('q-new'));
 const tabs=node('nav',null,'function-segmented chart-tool-tabs');tabs.id='q-tool-tabs';tabs.setAttribute('aria-label','Инструменти за анализа');
 const control=node('select');control.id='q-tool-view';control.hidden=true;
 const panels=node('div',null,'chart-tool-panels'),views={};
 for(const[id,label]of[['chart','Диаграма'],['quality','Качество'],['export','Изнасяне']]){
  const o=node('option',label);o.value=id;control.append(o);const b=node('button',label);b.type='button';b.dataset.tool=id;b.onclick=()=>{control.value=id;refresh();window.AstroArchiveSession.changed('analysis');};tabs.append(b);const panel=node('div');panel.dataset.toolPanel=id;views[id]=panel;panels.append(panel);
 }
 tools.prepend(tabs,control,panels);
 const chart=$('q-chart-tools');views.chart.append(scope,chart);chart.querySelector('h3')?.remove();
 const warningToggle=$('q-plot-warnings').closest('label');warningToggle.classList.add('chart-warning-toggle');warningToggle.querySelector('span').textContent='Показвай знаците за проблеми';warningToggle.title='Показва или скрива стандартните и AI знаци върху диаграмата. Оценките и подборът се запазват.';plot.querySelector('.flow-view-buttons').append(warningToggle);
 const advanced=node('label',null,'checkbox-field advanced-unlock'),check=node('input');check.type='checkbox';check.id='q-advanced';advanced.append(check,node('span','Разширени настройки · избор на осите'));chart.querySelector('#q-preset').closest('label').after(advanced);
 const description=node('p','','function-note');description.id='q-preset-description';advanced.before(description);
 function describe(){const x=$('q-x').selectedOptions[0]?.textContent||'№ кадър',y=$('q-metric').selectedOptions[0]?.textContent||'FWHM';description.textContent='Хоризонтално: '+x+'. Вертикално: '+y+'. Натисни „Разширени настройки“, за да избереш осите ръчно.';}
 for(const id of ['q-preset','q-x','q-metric'])$(id).addEventListener('change',()=>setTimeout(describe,0));
 const library=node('button','Какво означават показателите?','text-button');library.onclick=()=>U.modal('Библиотека на показателите',body=>{body.append(node('p','Показателите са измервания, а не окончателна присъда за кадъра.','function-note'));U.table(body,[{title:'Показател',value:r=>r.label},{title:'Какво измерва',value:r=>r.meaning},{title:'Как се чете',value:r=>r.reading}],window.AstroArchiveAnalysisMetrics.items);});views.chart.append(library);
 const zoom=node('section',null,'chart-zoom-controls');zoom.append(node('h3','Приближаване'));
 for(const[id,label,action]of[['q-fit','Напасни',()=>A.fit()],['q-maximize','Максимален',()=>{plot.classList.toggle('chart-maximized');A.fit();}],['q-zoom-selection','Маркираното',()=>A.zoomSelection()],['q-zoom-region','Област…',()=>A.region()]]){const b=node('button',label,'quiet-button');b.id=id;b.onclick=action;zoom.append(b);}
 const night=node('select');night.id='q-zoom-night';night.setAttribute('aria-label','Приближи до вечер');night.onchange=()=>A.zoomNight(night.value);zoom.append(night);views.chart.append(zoom);
 const exit=node('button','Върни размера','quiet-button');exit.id='q-exit-max';exit.onclick=()=>{plot.classList.remove('chart-maximized');A.fit();};plot.querySelector('header').append(exit);
 const coverage=node('section',null,'flow-tool-group');coverage.append(node('h3','Покритие'),$('q-cover'),$('q-noise'));views.chart.append(coverage,$('q-star-tools'));
 const modeTabs=node('nav',null,'function-segmented chart-tool-tabs');modeTabs.setAttribute('aria-label','Метод за качество');const qualityMode=node('select');qualityMode.id='q-quality-mode';qualityMode.hidden=true;
 for(const[id,label]of[['classic','Стандартно'],['ai','AI']]){qualityMode.add(new Option(label,id));const b=node('button',label);b.type='button';b.dataset.qualityMode=id;b.onclick=()=>{qualityMode.value=id;refresh();window.AstroArchiveSession.changed('analysis');};modeTabs.append(b);}
 const aiPanel=node('section',null,'flow-tool-group');aiPanel.id='q-ai-panel';aiPanel.append(node('h3','AI проверка'),node('p','Проверява по заявка за възможни облаци, следи и препятствия. Резултатите са предложения за проверка.','function-note'),$('q-plugin-tools'));
 const availability=node('div',null,'py-notice');availability.id='q-ai-availability';availability.setAttribute('role','status');
 const availabilityText=node('p','','function-note'),pluginSettings=node('button','Отвори плъгините','quiet-button');pluginSettings.id='q-ai-plugin-settings';pluginSettings.onclick=()=>window.AstroArchivePreferences.open('plugins');availability.append(availabilityText,pluginSettings);aiPanel.append(availability);
 function refreshAIAvailability(){
  const status=window.AstroArchiveExtensions?.toolsStatus('analysis-tools','frame-inspector-ai')||{reason:'loading'},remote=!!window.AstroArchiveRemote;
  availability.hidden=status.reason==='ready';pluginSettings.hidden=remote||status.reason==='loading';
  const instructions=remote?'Включи AI добавката и разреши интерфейса ѝ от Настройки → Плъгини на главния компютър.':'Отвори Настройки → Плъгини, включи AI добавката и разреши нейния интерфейс.';
  availabilityText.textContent=status.reason==='loading'?'Зареждат се AI контролите…':status.reason==='permission'?'AI плъгинът е включен, но няма разрешение да показва контролите си. '+instructions:status.reason==='error'?'AI плъгинът не е зареден: '+status.message+' '+instructions:status.reason==='missing'?'Няма налична AI добавка за този изглед. '+instructions:'AI проверката е изключена. '+instructions;
 }
 window.addEventListener('astroarchive-extensions-rendered',refreshAIAvailability);
 const ai=node('button','Избери според AI','quiet-button');ai.id='q-ai-worst';ai.onclick=()=>A.selectAI();aiPanel.append(ai);views.quality.append(modeTabs,qualityMode);
 const classic=node('section',null,'flow-tool-group');classic.id='q-classic-quality';classic.append(node('h3','Стандартна оценка'),node('p','Обединява процентната оценка с проверките за фокус, удължени звезди, малко звезди, фон и следи. Сравнява еднакъв телескоп, обект, вечер, филтър и експозиция. Индексът е относителен, не вероятност.','function-note'));
 const cutoff=node('input');cutoff.type='number';cutoff.min='0';cutoff.max='100';cutoff.step='1';cutoff.id='q-quality-threshold';const cutoffLabel=node('label',null,'function-field');cutoffLabel.append(node('span','Предложи за изключване под [%]'),cutoff);classic.append(cutoffLabel);
 const details=node('details');details.append(node('summary','Тежести на показателите'));for(const[key,label]of[['fwhm','FWHM X'],['fwhmY','FWHM Y'],['roundness','Кръглост'],['stars','Брой звезди'],['background','Фон']]){const l=node('label',null,'function-field'),v=node('input');v.type='number';v.min='0';v.max='100';v.step='1';v.id='q-weight-'+key;l.append(node('span',label+' · %'),v);details.append(l);}classic.append(details);
 details.append(node('p','Дяловете се нормализират до общо 100%. След промяна пусни проверката отново.','function-note'));const runClassic=node('button','Изпълни стандартната проверка','primary-button');runClassic.id='q-run-classic';runClassic.onclick=()=>A.runClassic();classic.append(runClassic);const choose=node('button','Избери според стандартната оценка','quiet-button');choose.id='q-classic-worst';choose.onclick=()=>A.selectClassic();classic.append(choose);views.quality.append(classic);
 const note=node('p','','function-note');note.id='q-quality-status';classic.append(note,$('q-notice'));$('q-worst').hidden=true;views.quality.append(aiPanel,$('q-decision-tools'));
 const edit=()=>A.saveQuality();cutoff.addEventListener('change',edit);for(const input of details.querySelectorAll('input'))input.addEventListener('change',edit);
 views.export.append($('q-saved-tools'),finish);
 const list=node('button','☷  Всички кадри','quiet-button');list.id='q-open-list';list.type='button';list.title='Отвори списъка с анализираните кадри';list.onclick=()=>A.openFrameList();tools.append(list);
 // Existing view controls are retained for saved/session/coverage flows but not duplicated in the chart header.
 for(const b of document.querySelectorAll('[data-q-view="table"],[data-q-view="gallery"]'))b.hidden=true;
 check.onchange=()=>{A.customize();refresh();};document.addEventListener('keydown',e=>{if(e.key==='Escape')plot.classList.remove('chart-maximized');});
 function refresh(){
  const legacy={basic:'chart',advanced:'chart',ai:'quality',coverage:'chart',stars:'chart'};if(legacy[control.value])control.value=legacy[control.value];if(!['chart','quality','export'].includes(control.value))control.value='chart';
  for(const b of tabs.querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.tool===control.value));for(const[id,panel]of Object.entries(views))panel.hidden=id!==control.value;
  $('q-x').closest('label').hidden=$('q-metric').closest('label').hidden=!check.checked;$('q-star-tools').hidden=!A.hasStars?.();classic.hidden=qualityMode.value!=='classic';aiPanel.hidden=qualityMode.value!=='ai';for(const b of modeTabs.querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.qualityMode===qualityMode.value));describe();
 }
 window.AstroArchiveAnalysisWorkbench={refresh};refresh();refreshAIAvailability();
})();
