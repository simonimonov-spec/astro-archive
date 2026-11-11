"use strict";
// Shared appearance editor; Remote Operation only supplies its named transport.
(() => {
  const $ = id => document.getElementById(id);
  const names = {
    background:['Основен фон','Background'],surface:['Панели','Panels'],surfaceRaised:['Повдигнати полета','Raised surfaces'],surfaceHover:['Посочване','Hover'],
    border:['Контури','Borders'],text:['Основен текст','Text'],muted:['Второстепенен текст','Secondary text'],accent:['Акцент','Accent'],
    accentSurface:['Избрани елементи','Selected items'],onAccent:['Текст върху акцента','Text on accent'],success:['Успех','Success'],warning:['Предупреждение','Warning'],error:['Грешка','Error'],errorSurface:['Фон на грешките','Error background']
  };
  const labels={night:['Нощен','Night'],day:['Дневен','Day'],custom:['Персонален','Custom'],red:['Червен · наблюдения','Red · observing']};
  let config, preferences, change;
  const en = () => window.AstroArchiveI18n.locale.startsWith('en');
  const label = pair => pair[en()?1:0];
  const valid = value => /^#[0-9a-f]{6}$/i.test(value);
  function render(data,prefs,onChange){
    config=data;preferences=prefs;change=onChange;
    const cards=$('appearance-themes');cards.replaceChildren();
    for(const theme of data.themes.filter(t=>t.id.startsWith('builtin.'))){
      const key=theme.id.split('.')[1],button=document.createElement('button');button.type='button';button.className='theme-card';button.dataset.theme=theme.id;
      button.setAttribute('aria-pressed',String(theme.id===prefs.themeId));
      const swatches=document.createElement('span');swatches.className='theme-swatches';swatches.setAttribute('aria-hidden','true');
      const colors={...theme.colors,...(key==='custom'?prefs.customColors:{})};
      for(const name of['background','surface','text','accent']){const dot=document.createElement('i');if(valid(colors[name]))dot.style.backgroundColor=colors[name];swatches.append(dot);}
      const text=document.createElement('strong');text.textContent=label(labels[key]||[key,key]);button.append(swatches,text);
      button.onclick=()=>{$('ui-theme').value=theme.id;change({themeId:theme.id});};cards.append(button);
    }
    $('appearance-custom').hidden=prefs.themeId!=='builtin.custom';$('appearance-red').hidden=prefs.themeId!=='builtin.red';
    $('appearance-custom-title').textContent=label(['Твоите цветове','Your colors']);
    $('appearance-custom-note').textContent=label(['Избери цветове или въведи #RRGGBB. Промените се виждат веднага и се запазват с „Запази“.','Choose colors or enter #RRGGBB. Preview immediately; use Save to keep them.']);
    $('appearance-custom-reset').textContent=label(['Върни нощната палитра','Restore night palette']);
    $('appearance-custom-reset').onclick=()=>change({customColors:{}});
    $('appearance-tint-label').textContent=label(['Червен екранен преглед и на изображенията','Red screen tint for images too']);
    $('appearance-red-note').textContent=label(['Снимките остават с истинските си цветове, освен ако включиш оцветяването. То променя само екрана, никога оригиналите или изтеглянията.','Images keep their true colors unless you enable tinting. This affects the screen only, never originals or downloads.']);
    $('appearance-tint').checked=!!prefs.redImageTint;$('appearance-tint').onchange=()=>change({redImageTint:$('appearance-tint').checked});
    $('appearance-shared-note').textContent=label(['Запазената тема е обща за приложението и свързаните браузъри.','The saved theme is shared by the app and connected browsers.']);
    const samsung=/SamsungBrowser/i.test(navigator.userAgent);$('appearance-browser-note').hidden=!samsung;
    $('appearance-browser-note').textContent=label(['Samsung Internet може допълнително да затъмнява сайта. За същите цветове: Настройки на браузъра → Изглед и превъртане на уебстраници → Тъмен режим → изключи принудителния тъмен режим за уеб съдържание. При по-стари версии включи „Използвай тъмната тема на уебсайта“ в Labs.','Samsung Internet can darken websites again. For matching colors: browser Settings → Webpage view and scrolling → Dark mode → turn off Force Dark mode for web content. In older versions enable Use website dark theme in Labs.']);
    const grid=$('appearance-colors');grid.replaceChildren();
    const colors={...data.themes.find(t=>t.id==='builtin.night')?.colors,...prefs.customColors};
    for(const[name,title]of Object.entries(names)){
      const field=document.createElement('label');field.className='appearance-color';const caption=document.createElement('span');caption.textContent=label(title);
      const controls=document.createElement('span');controls.className='appearance-color-inputs';
      const picker=document.createElement('input'),hex=document.createElement('input');picker.type='color';hex.type='text';hex.maxLength=7;hex.spellcheck=false;
      picker.value=hex.value=colors[name]||'#000000';picker.setAttribute('aria-label',label(title));hex.setAttribute('aria-label',label(title)+' #RRGGBB');hex.dataset.color=name;
      // Keep focus while editing; rebuilding the entire editor on each keystroke is disruptive.
      function update(value){hex.setAttribute('aria-invalid',String(!valid(value)));if(!valid(value))return;picker.value=value;preferences.customColors={...preferences.customColors,[name]:value.toLowerCase()};change({customColors:preferences.customColors},false);}
      picker.oninput=()=>{hex.value=picker.value;update(picker.value);};hex.oninput=()=>update(hex.value);controls.append(picker,hex);field.append(caption,controls);grid.append(field);
    }
  }
  function selection(prefs){
    $('appearance-custom').hidden=prefs.themeId!=='builtin.custom';$('appearance-red').hidden=prefs.themeId!=='builtin.red';
    document.querySelectorAll('[data-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.theme===prefs.themeId)));
  }
  window.AstroArchiveAppearance={render,selection,valid:()=>![...document.querySelectorAll('[data-color]')].some(e=>e.getAttribute('aria-invalid')==='true')};
})();
