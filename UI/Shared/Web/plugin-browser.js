"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const t = (key, values) => window.AstroArchiveI18n.t(key, values);
  const node = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  function logo(plugin, large = false) {
    const frame = node("span", "plugin-logo" + (large ? " plugin-logo-large" : ""));
    frame.setAttribute("aria-hidden", "true");
    const fallback = node("img"); fallback.alt = ""; fallback.src = "Assets/plugin-gears.svg";
    frame.append(fallback);
    if (typeof plugin?.logoDataUrl === "string" && /^data:image\/(?:png|jpeg|svg\+xml);base64,/.test(plugin.logoDataUrl)) {
      const image = node("img", "plugin-logo-custom"); image.alt = ""; image.decoding = "async";
      image.addEventListener("load", () => { image.classList.add("is-loaded"); fallback.hidden = true; }, { once: true });
      image.addEventListener("error", () => { image.remove(); fallback.hidden = false; }, { once: true });
      image.src = plugin.logoDataUrl; frame.append(image);
    }
    return frame;
  }
  const state = plugin => plugin.installed === false ? 'Наличен за изтегляне' : t(!plugin.available ? "plugins.unavailable" : plugin.busy ? "plugins.running" : plugin.enabled ? "plugins.enabled" : "plugins.disabled");

  window.AstroArchivePluginBrowser = {
    create(actions) {
      let plugins = [], selectedId = null, detailVersion = 0;

      function actionButton(label, callback, className = "quiet-button") {
        const button = node("button", className, label); button.type = "button";
        button.addEventListener("click", callback); return button;
      }
      function lock(control, plugin, unavailable = false) {
        control.dataset.pluginBusy = String(!!plugin.busy);
        control.dataset.unavailable = String(unavailable || !plugin.available);
      }
      function choose(id, focus = false) {
        if (!plugins.some(plugin => plugin.id === id)) return;
        if (id === selectedId) { if (focus) $("plugin-choice-" + id)?.focus(); return; }
        selectedId = id;
        for (const item of $("ui-plugins").querySelectorAll("[data-plugin-id]")) {
          const selected = item.dataset.pluginId === id;
          item.setAttribute("aria-selected", String(selected)); item.tabIndex = selected ? 0 : -1;
          if (focus && selected) item.focus();
        }
        drawDetails();
      }
      function drawList() {
        const fragment = document.createDocumentFragment();
        for (const plugin of plugins) {
          const item = actionButton("", () => choose(plugin.id), "plugin-choice");
          item.id = "plugin-choice-" + plugin.id; item.dataset.pluginId = plugin.id;
          item.setAttribute("role", "tab"); item.setAttribute("aria-controls", "ui-plugin-details");
          item.setAttribute("aria-selected", String(plugin.id === selectedId)); item.tabIndex = plugin.id === selectedId ? 0 : -1;
          const copy = node("span", "plugin-choice-copy");
          const summary = node("span", "plugin-choice-description", plugin.description); summary.title = plugin.description;
          const status = node("span", "plugin-state", state(plugin)); status.dataset.enabled = String(plugin.enabled);
          copy.append(node("span", "plugin-choice-name", plugin.name), summary, status);
          item.append(logo(plugin), copy); fragment.append(item);
        }
        if (!plugins.length) fragment.append(node("p", "settings-note", t("plugins.empty")));
        $("ui-plugins").replaceChildren(fragment);
        $("ui-plugin-scan-status").textContent = t("plugins.found", { count: plugins.length });
      }
      function drawDetails() {
        const version = ++detailVersion;
        const panel = $("ui-plugin-details"); panel.replaceChildren();
        const plugin = plugins.find(item => item.id === selectedId);
        if (!plugin) {
          panel.setAttribute("aria-labelledby", "plugins-heading");
          const empty = node("div", "plugin-empty"); empty.append(logo(null, true), node("p", "", t("plugins.select")));
          panel.append(empty); actions.controlsChanged(); return;
        }
        panel.setAttribute("aria-labelledby", "plugin-choice-" + plugin.id);
        const heading = node("div", "plugin-details-header"), title = node("div");
        title.append(node("h4", "", plugin.name), node("div", "plugin-meta", t("plugins.version", { version: plugin.version })));
        if (plugin.author) title.append(node("div", "plugin-meta", plugin.author));
        heading.append(logo(plugin, true), title);
        panel.append(heading, node("p", "plugin-description", plugin.description));
        if(plugin.installed === false){
          panel.append(node('p','plugin-meta','Версия: '+plugin.version));
          const download=actionButton('Изтегли от GitHub',()=>actions.download(plugin.id),'primary-button');
          panel.append(download);actions.controlsChanged();return;
        }
        if(plugin.updateAvailable)panel.append(node('p','settings-note','Налична е версия '+plugin.latestVersion+'. За обновяване изтрий текущия плъгин и изтегли новия. Настройките се запазват.'));
        const toggleLabel = node("label", "checkbox-field plugin-toggle"), toggle = node("input");
        toggle.type = "checkbox"; toggle.checked = !!plugin.enabled; lock(toggle, plugin);
        toggle.addEventListener("change", () => actions.toggle(plugin.id, toggle.checked));
        toggleLabel.append(toggle, node("span", "", t("plugins.activation")), node("span", "plugin-state", state(plugin)));
        panel.append(toggleLabel);
        if(plugin.kind==='declarative'){
          const commands=node('div','plugin-capabilities');commands.append(node('strong','','Заявени основни команди'));
          for(const permission of plugin.permissions||[])commands.append(node('p','',permission.label));
          if(!plugin.permissions?.length)commands.append(node('p','','Само собствена логика и визуални полета.'));
          if(plugin.capabilities?.length)commands.append(node('p','','Възможности за основния код: '+plugin.capabilities.map(c=>c.id).join(', ')));
          panel.append(commands);
        }
        if (plugin.contributions?.length) {
          const permissions=node('section','plugin-permissions');
          permissions.append(node('h5','', 'Допълнения към интерфейса'),node('p','settings-note',plugin.uiAllowed?'Разрешени от потребителя. Показват се само докато плъгинът е включен.':'Плъгинът иска разрешение да добави следните части:'));
          const list=node('ul');
          for(const contribution of plugin.contributions){const item=node('li','',({header:'Горен индикатор','viewer-tools':'Преглед на изображение','analysis-tools':'Инструменти за анализ',settings:'Настройки',customization:'Визуални настройки',preparation:'Подготовка',analysis:'Анализ',archive:'Допълнение към архива'}[contribution.slot]||'Раздел')+' · '+contribution.title);item.append(node('small','',contribution.description));list.append(item);
            if(contribution.slot==='archive'&&contribution.archiveRoot&&plugin.uiAllowed&&plugin.enabled){const choose=actionButton('Избери архивна папка…',()=>actions.archive(plugin.id,contribution.id));lock(choose,plugin);item.append(choose);}}
          permissions.append(list);const consent=actionButton(plugin.uiAllowed?'Отнеми разрешението':'Прегледай и разреши…',()=>actions.permission(plugin,!plugin.uiAllowed));lock(consent,plugin,!plugin.enabled);permissions.append(consent);panel.append(permissions);
        }
        if (plugin.problem) panel.append(node("p", "plugin-options-error", plugin.problem));
        if (plugin.busy) panel.append(node("p", "settings-note", t("plugins.runningNote")));
        panel.append(node("div", "plugin-meta plugin-folder-path", plugin.folder));

        for(const contribution of plugin.contributions||[])if(contribution.slot==='settings'){
          const settings=node('section','plugin-options');panel.append(settings);
          if(plugin.enabled&&plugin.uiAllowed&&plugin.available)window.AstroArchiveExtensions.mountSettings(settings,plugin,contribution);
          else settings.append(node('p','settings-note','Включи плъгина и разреши интерфейса му, за да отвориш настройките.'));
        }
        const footer = node("div", "plugin-actions");
        footer.append(node('span','plugin-meta','Версия: '+plugin.version));
        const remove = actionButton('Изтрий плъгина', () => actions.remove(plugin.id), "text-button plugin-remove");
        // A broken module can still be removed.
        remove.dataset.pluginBusy = String(!!plugin.busy); footer.append(remove); panel.append(footer);
        actions.controlsChanged();
      }
      $("ui-plugins").addEventListener("keydown", event => {
        const index = plugins.findIndex(plugin => plugin.id === selectedId);
        const next = event.key === "ArrowDown" ? (index + 1) % plugins.length : event.key === "ArrowUp" ? (index + plugins.length - 1) % plugins.length : event.key === "Home" ? 0 : event.key === "End" ? plugins.length - 1 : -1;
        if (next >= 0 && plugins[next]) { event.preventDefault(); choose(plugins[next].id, true); }
      });
      return {
        render(configuration) {
          const installed=configuration.plugins || [],catalog=configuration.pluginCatalog || [];
          plugins = installed.map(p=>({...p,installed:true,updateAvailable:catalog.find(c=>c.id===p.id)?.updateAvailable,latestVersion:catalog.find(c=>c.id===p.id)?.version}));
          plugins.push(...catalog.filter(c=>!installed.some(p=>p.id===c.id)).map(c=>({...c,installed:false,available:true})));
          if (!plugins.some(plugin => plugin.id === selectedId)) selectedId = plugins[0]?.id || null;
          drawList(); drawDetails();
        }
      };
    }
  };
})();

