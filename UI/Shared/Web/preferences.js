"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const i18n = window.AstroArchiveI18n;
  const t = (key, values) => i18n.t(key, values);
  const defaults = () => ({ themeId: "builtin.night", customColors: {}, redImageTint: false, languageId: i18n.defaultLanguage, backgroundMode: "theme", backgroundColor: "#0c111a", backgroundImage: null, animations: true, customization: window.AstroArchiveCustomization?.defaults(), islands: {version:1,sections:{}} });
  const appearanceOnly = () => !!window.AstroArchiveRemote;
  const appearanceFields = ['themeId','customColors','redImageTint','backgroundMode','backgroundColor','animations'];
  let refreshingAppearance = null;
  const cssNames = { background: "background", surface: "surface", surfaceRaised: "surface-raised", surfaceHover: "surface-hover", border: "border", text: "text", muted: "muted", accent: "accent", accentSurface: "accent-surface", onAccent: "on-accent", success: "success", warning: "warning", error: "error", errorSurface: "error-surface" };
  let bindings, configuration, draft = defaults(), imagePreview = null;
  let busy = false, generation = 0, languageStamp = "", currentImage = null;
  let messageKey = "", messageTone = "info";
  let selectedSettingsTab = "appearance";
  let pluginUpdatePending = false;
  let catalogChecked = false;
  const pluginBrowser = window.AstroArchivePluginBrowser.create({
    controlsChanged: () => setBusy(busy),
    download: id => mutatePlugins('ui.plugins.download',{id}),
    update: updatePlugin,
    toggle: async (id, enabled) => {const plugin=configuration.plugins.find(p=>p.id===id);if(enabled&&!await window.AstroArchiveFunctions.confirm(t('plugins.enableTitle'),t('plugins.enableBody',{name:i18n.pluginText(plugin,plugin.name),commands:(plugin.permissions||[]).map(i18n.permissionText).join('; ')||t('plugins.noCoreCommands')}),t('plugins.enable'))){drawPlugins();return;}return mutatePlugins("ui.plugins.toggle", { id, enabled, confirmed:true, revision:plugin.uiRevision });},
    remove: async id => {if(await window.AstroArchiveFunctions.confirm(t('plugins.removeTitle'),t('plugins.removeBody'),t('plugins.remove')))return mutatePlugins("ui.plugins.remove", { id, confirmed:true });},
      permission: async (plugin, allowed) => {if(allowed&&!await window.AstroArchiveFunctions.confirm(t('plugins.permissionTitle'),t('plugins.permissionBody',{name:i18n.pluginText(plugin,plugin.name),areas:plugin.contributions.map(c=>i18n.pluginText(plugin,c.title)).join(', ')}),t('plugins.allow')))return;return mutatePlugins("ui.plugins.permission", { id:plugin.id, revision:plugin.uiRevision, allowed });},
      archive: (id, contributionId) => mutatePlugins("ui.plugins.archive", { id, contributionId })
  });

  function offlineConfiguration() {
    return { preferences: defaults(), themes: [{ id: "builtin.night", name: "theme.night", colors: {} }], plugins: [], problems: [], backgroundDataUrl: null };
  }

  function apply(preferences, background) {
    if (!configuration) return;
    const baseline = configuration.themes.find(theme => theme.id === "builtin.night")?.colors || {};
    const theme = configuration.themes.find(item => item.id === preferences.themeId) || configuration.themes[0];
    const colors = { ...baseline, ...theme.colors, ...(preferences.themeId === 'builtin.custom' ? preferences.customColors : {}) };
    const root = document.documentElement;
    root.dataset.theme = preferences.themeId;
    root.dataset.imageTint = preferences.redImageTint ? 'on' : 'off';
    for (const [name, propertyName] of Object.entries(cssNames)) {
      const value = colors[name], property = "--" + propertyName;
      if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) root.style.removeProperty(property);
      else if (root.style.getPropertyValue(property) !== value) root.style.setProperty(property, value);
    }
    const backgroundColor = getComputedStyle(root).getPropertyValue('--background').trim();
    if (/^#[0-9a-f]{6}$/i.test(backgroundColor)) {
      const rgb = backgroundColor.slice(1).match(/.{2}/g).map(value => parseInt(value, 16));
      const scheme = (rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722) > 150 ? "only light" : "only dark";
      root.style.colorScheme = scheme;
      document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', scheme);
    }
    root.dataset.animations = preferences.animations ? "on" : "off";
    window.AstroArchiveCustomization?.accept(preferences.customization, configuration.icons || []);
    window.AstroArchiveExtensions?.theme();
    window.AstroArchiveMonitoring?.theme();
    const mode = preferences.themeId === 'builtin.red' || preferences.backgroundMode === "image" && !background ? "theme" : preferences.backgroundMode;
    root.dataset.background = mode;
    root.style.setProperty("--canvas-color", mode === "color" ? preferences.backgroundColor : backgroundColor || "#0c111a");
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', mode === "color" ? preferences.backgroundColor : backgroundColor || "#0c111a");
    const validImage = mode === "image" && typeof background === "string" && background.startsWith("data:image/jpeg;base64,") ? background : null;
    if (validImage !== currentImage) {
      currentImage = validImage;
      if (validImage) $("background-image").src = validImage;
      else $("background-image").removeAttribute("src");
    }
    $("background-image").hidden = !validImage;
    const stamp = generation + ":" + preferences.languageId;
    if (stamp !== languageStamp) {
      languageStamp = stamp;
      i18n.select(configuration.languages.find(item => item.id === preferences.languageId));
      bindings?.languageChanged();
      translateOptions();
      drawPlugins();
      drawProblems();
      renderPluginStatus();
      renderUpdateStatus();
      if (updateTransfer.phase!=='idle') drawUpdateDownload(updateTransfer);
      if (messageKey) message(t(messageKey), messageTone);
    }
    window.AstroArchiveIslands?.accept(preferences.islands);
  }

  function accept(data, preserveDraft = false) {
    const previous = preserveDraft ? { ...draft } : null;
    const previousImage = imagePreview;
    const source = data || offlineConfiguration();
    const languages = new Map(i18n.languages.map(language => [language.id, language]));
    for (const language of source.languages || []) {
      const builtin = languages.get(language.id);
      languages.set(language.id, builtin ? { ...builtin, messages: language.messages } : language);
    }
    configuration = { ...source, languages: [...languages.values()] };
    generation++;
    draft = previous || { ...configuration.preferences };
    if (!configuration.themes.some(theme => theme.id === draft.themeId)) draft.themeId = configuration.preferences.themeId;
    if (!languages.has(draft.languageId)) draft.languageId = languages.has(source.preferences.languageId) ? source.preferences.languageId : defaults().languageId;
    imagePreview = preserveDraft ? previousImage : configuration.backgroundDataUrl;
    apply(draft, imagePreview);
    bindings?.pluginsChanged(configuration.plugins);
    fillControls();
  }

  function renderPluginStatus() {
    $("ui-plugin-folder-path").textContent = configuration.plugins?.length ? configuration.pluginsFolder : t('plugins.folderEmpty');
    const catalogKey = configuration.catalogProblem ? 'plugins.catalogOffline' : catalogChecked ? 'plugins.catalogChecked' : 'plugins.catalogInfo';
    $("ui-plugin-catalog-status").textContent = t(catalogKey);
  }

  function option(select, value, text) {
    const node = document.createElement("option"); node.value = value; node.textContent = text; select.append(node);
  }
  function translateOptions() {
    for (const item of $("ui-theme").options) {
      const theme = configuration.themes.find(value => value.id === item.value);
      if (theme) item.textContent = theme.id.startsWith("builtin.") ? t(theme.name) : theme.name;
    }
    $("ui-image-state").textContent = t(draft.backgroundImage ? "settings.imageReady" : "settings.noImage");
  }
  function fillLanguages() {
    const choices = document.createDocumentFragment();
    for (const language of configuration.languages) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "ui-language";
      input.value = language.id;
      input.checked = language.id === draft.languageId;
      const caption = document.createElement("span");
      if (language.flag !== undefined) {
        const flag = document.createElement("span");
        flag.className = "language-flag";
        flag.style.backgroundPositionX = `${language.flag * 100 / (i18n.languages.length - 1)}%`;
        flag.setAttribute("aria-hidden", "true");
        caption.append(flag);
      }
      const name = document.createElement("span");
      name.lang = language.locale;
      name.textContent = language.name;
      caption.append(name);
      label.append(input, caption);
      choices.append(label);
    }
    $("ui-language-options").replaceChildren(choices);
  }
  function fillControls() {
    $("ui-theme").replaceChildren();
    for (const theme of configuration.themes) option($("ui-theme"), theme.id, theme.id.startsWith("builtin.") ? t(theme.name) : theme.name);
    fillLanguages();
    $("ui-theme").value = draft.themeId;
    $("ui-background-mode").value = draft.backgroundMode;
    $("ui-background-color").value = $("ui-background-hex").value = draft.backgroundColor;
    $("ui-background-hex").removeAttribute("aria-invalid");
    $("ui-animations").checked = draft.animations;
    window.AstroArchiveAppearance?.render(configuration, draft, editAppearance);
    updateBackgroundControls();
    translateOptions();
    setBusy(busy);
  }
  function updateBackgroundControls() {
    $("ui-color-options").hidden = draft.backgroundMode !== "color";
    $("ui-image-options").hidden = draft.backgroundMode !== "image";
  }
  function message(text, tone = "info") {
    $("settings-message").textContent = text;
    $("settings-message").dataset.tone = tone;
    $("settings-message").hidden = !text;
  }
  function notice(key, tone = "info") { messageKey = key; messageTone = tone; message(t(key), tone); }
  function report(error) { messageKey = ""; message(error.message, "error"); bindings?.log(error.message, "error"); }
  function setBusy(value) {
    busy = value;
    for (const control of $("settings-dialog").querySelectorAll("button, input, select, textarea")) control.disabled = (value && control.getAttribute("role") !== "tab") || control.dataset.pluginBusy === "true" || control.dataset.unavailable === "true" || control.dataset.customRequired === "true";
    if (!bindings?.desktop) for (const id of ["ui-save", "ui-plugin-install", "ui-plugin-folder", "ui-plugin-scan", "ui-plugin-catalog", "app-update-check", "ui-choose-background"]) $(id).disabled = true;
    if(!configuration?.plugins?.length)$("ui-plugin-folder").disabled=true;
    if (!bindings?.desktop) for (const control of $("settings-dialog").querySelectorAll("[data-debug-section], [data-storage-settings]")) control.disabled = true;
    if (!bindings?.desktop) for (const control of $("customization-editor").querySelectorAll("button,input,select,textarea")) control.disabled = true;
    if (appearanceOnly()) $("ui-choose-background").disabled = true;
    $('app-update-download').disabled=value||!bindings?.desktop||!updateDownloadable||updateTransfer.phase==='downloading';
    $('app-update-stop').disabled=value||!bindings?.desktop;
    $('app-update-folder').disabled=value||!bindings?.desktop;
    $("settings-dialog").setAttribute("aria-busy", String(value));
  }
  function drawPlugins() {
    pluginBrowser.render(configuration);
  }
  function showTab(tab, scan = false) {
    if (appearanceOnly()) tab = 'appearance';
    if (!["appearance", "customization", "language", "plugins", "diagnostics"].includes(tab)) return;
    const dialog = $("settings-dialog");
    window.AstroArchiveMotion.measure(dialog, () => {
      selectedSettingsTab = tab;
      showTabContent(dialog, tab, scan);
    });
  }
  function showTabContent(dialog, tab, scan) {
    dialog.dataset.settingsTab = tab;
    for (const button of document.querySelectorAll("[data-settings-tab]")) {
      const selected = button.dataset.settingsTab === tab;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
      $("settings-panel-" + button.dataset.settingsTab).hidden = !selected;
    }
    const readOnly = tab === "plugins" || tab === "diagnostics";
    $("ui-save").hidden = $("ui-reset").hidden = readOnly;
    $("ui-cancel").dataset.i18n = readOnly ? "settings.done" : "settings.cancel";
    $("ui-cancel").textContent = t($("ui-cancel").dataset.i18n);
    if (tab === "plugins" && scan) scanPlugins();
    if (tab === "diagnostics") window.AstroArchiveActivity.showDiagnostics();
    if (tab === "customization") window.AstroArchiveCustomization?.loadCatalog();
    window.AstroArchiveMonitoring?.diagnosticsVisible(tab === "diagnostics");
  }
  async function scanPlugins() {
    if (busy || !bindings?.desktop) return;
    setBusy(true); notice("plugins.scanning");
    try { accept(await bindings.call("ui.plugins.scan"), true); notice("plugins.changed"); }
    catch (error) { report(error); }
    finally { setBusy(false); }
  }
  function pluginUpdateMessages() {
    const fallback = t('plugins.updateErrorDetail');
    return (configuration.pluginUpdateNotices || (configuration.pluginUpdateProblems || []).map(() => ({}))).map(notice => {
      const problem = i18n.errorText(notice, fallback);
      if (!notice.plugin) return problem;
      const plugin = configuration.plugins.find(item => item.id === notice.plugin);
      const name = plugin ? i18n.pluginText(plugin, plugin.name) : notice.plugin;
      return t('plugins.updateProblemFor', { name, problem });
    });
  }
  function drawProblems() {
    const otherProblems = (configuration.problems || []).slice(configuration.pluginUpdateProblems?.length || 0);
    const problems = [...pluginUpdateMessages(), ...otherProblems];
    $("ui-plugin-problems").hidden = !problems.length;
    $("ui-plugin-problem-list").replaceChildren(...problems.map(problem => {
      const item = document.createElement("li");
      item.textContent = problem;
      return item;
    }));
  }
  function pluginUpdateValues(plugin) {
    const installed = configuration.plugins.find(item => item.id === plugin.id);
    return { ...plugin, name: i18n.pluginText(installed, plugin.name) };
  }
  async function mutatePlugins(method, params = {}) {
    if (busy || !bindings.desktop) return;
    setBusy(true); notice("plugins.busy");
    try {
      // Plugin activation is immediate; don't leave an unrelated preview active.
      const restored = await bindings.call("ui.cancelPreview");
      const data = await bindings.call(method, params);
      if (data) { accept(data); notice(method === "ui.plugins.install" ? "plugins.installed" : "plugins.changed"); }
      else { accept(restored); messageKey = ""; message(""); }
    } catch (error) { report(error); }
    finally { setBusy(false); }
  }
  async function updatePlugin(plugin) {
    if (busy || pluginUpdatePending || !bindings?.desktop || appearanceOnly()) return;
    const ui = window.AstroArchiveFunctions;
    pluginUpdatePending = true;
    let accepted = false, updating = false;
    try {
      if (!await ui.confirm(t('plugins.update'), t('plugins.updateConfirm', pluginUpdateValues(plugin)), t('plugins.updateRestart'))) return;
      if (busy) return;
      updating = true;
      setBusy(true);
      let status, dialog;
      const showProgress = (key, stopping = false) => {
        status = ui.node('p', t(key));
        status.setAttribute('role', 'status');
        dialog = ui.modal(t('plugins.update'), (body, box) => {
          body.append(status);
          if (stopping) {
            box.querySelector('.panel-heading button').disabled = true;
            box.addEventListener('cancel', event => event.preventDefault());
          }
        });
      };
      showProgress('plugins.updateDownloading');
      try {
        const params = { id: plugin.id, commit: plugin.commit, confirmed: true };
        let result;
        try {
          result = await bindings.call('ui.plugins.update', params);
        } catch (error) {
          if (error.errorKey !== 'diagnostics.restartBusy') throw error;
          dialog.close();
          if (!await confirmStoppingTasks(ui)) return;
          showProgress('plugins.updateStoppingTasks', true);
          result = await bindings.call('ui.plugins.update', { ...params, cancelWork: true });
        }
        if (result?.accepted !== true) throw Object.assign(new Error(), { errorKey: 'diagnostics.restartFailed' });
        accepted = true;
        status.textContent = t('diagnostics.restarting');
        setTimeout(() => window.close(), 1200);
      } catch (error) {
        dialog.close();
        const detail = i18n.errorText(error, t('plugins.updateErrorDetail'));
        ui.modal(t('plugins.updateFailed'), body => body.append(ui.node('p', detail, 'error-banner')));
      }
    } finally {
      if (!accepted) {
        pluginUpdatePending = false;
        if (updating) setBusy(false);
      }
    }
  }

  function confirmStoppingTasks(ui) {
    return new Promise(resolve => {
      let stop = false;
      const dialog = ui.modal(t('plugins.updateActiveTasks'), (body, box) => {
        body.append(ui.node('p', t('plugins.updateActiveTasksDetail')));
        const actions = ui.node('div', null, 'function-actions');
        const skip = ui.node('button', t('plugins.updateSkipNow'), 'quiet-button');
        const proceed = ui.node('button', t('plugins.updateStopTasks'), 'primary-button');
        skip.type = proceed.type = 'button';
        skip.autofocus = true;
        skip.onclick = () => box.close();
        proceed.onclick = () => { stop = true; box.close(); };
        actions.append(skip, proceed);
        body.append(actions);
      });
      dialog.addEventListener('close', () => resolve(stop), { once: true });
    });
  }

  async function checkPluginUpdates() {
    let updates = [];
    try {
      const result = await bindings.call('ui.plugins.catalog');
      configuration.pluginCatalog = result.pluginCatalog;
      configuration.catalogProblem = result.catalogProblem;
      catalogChecked = true;
      drawPlugins();
      renderPluginStatus();
      if (!result.catalogProblem) updates = result.pluginCatalog.filter(plugin => plugin.updateAvailable);
    } catch (_) { /* A failed startup request can be retried from Settings. */ }
    const problems = pluginUpdateMessages();
    if (!updates.length && !problems.length) return;
    const ui = window.AstroArchiveFunctions;
    ui.modal(t(updates.length ? 'plugins.updatesTitle' : 'plugins.updateFailed'), body => {
      for (const problem of problems) body.append(ui.node('p', problem, 'error-banner'));
      for (const plugin of updates) {
        const row = ui.node('div', null, 'plugin-actions');
        const button = ui.node('button', t('plugins.update'), 'primary-button');
        button.type = 'button';
        button.addEventListener('click', () => updatePlugin(plugin));
        row.append(ui.node('span', t('plugins.updateNotice', pluginUpdateValues(plugin))), button);
        body.append(row);
      }
    });
  }
  function preview() {
    if (busy) return;
    const color = $("ui-background-hex").value;
    if (!/^#[0-9a-f]{6}$/i.test(color)) { $("ui-background-hex").setAttribute("aria-invalid", "true"); return; }
    $("ui-background-hex").removeAttribute("aria-invalid");
    const changedTheme = draft.themeId !== $("ui-theme").value;
    if (changedTheme) $("ui-background-mode").value = 'theme';
    draft = { ...draft, themeId: $("ui-theme").value,
      backgroundMode: $("ui-background-mode").value, backgroundColor: color.toLowerCase(), animations: $("ui-animations").checked,
      customization: window.AstroArchiveCustomization?.snapshot(), islands: window.AstroArchiveIslands?.snapshot() || draft.islands };
    updateBackgroundControls(); apply(draft, imagePreview); notice("settings.previewing");
    window.AstroArchiveAppearance?.selection(draft);
  }
  function editAppearance(patch, redraw = true) {
    if (busy) return;
    draft = {...draft, ...patch, ...(patch.themeId ? {backgroundMode:'theme'} : {})};
    apply(draft, imagePreview);
    if (redraw) fillControls();
    notice('settings.previewing');
  }
  function acceptAppearance(data) {
    const values = Object.fromEntries(appearanceFields.filter(k=>data.preferences[k]!==undefined).map(k=>[k,data.preferences[k]]));
    configuration = {...configuration, themes:data.themes, backgroundDataUrl:data.backgroundDataUrl, preferences:{...configuration.preferences,...values}};
    draft = {...draft,...values}; imagePreview = data.backgroundDataUrl;
    apply(draft, imagePreview); fillControls();
  }
  async function refreshAppearance() {
    // Leave an open preview intact; close/open always retrieves the latest saved values.
    if (!bindings?.desktop || $("settings-dialog").open) return;
    if (!refreshingAppearance) refreshingAppearance = bindings.call('ui.appearance.get').then(data => { if (!$("settings-dialog").open) acceptAppearance(data); }).catch(report).finally(() => { refreshingAppearance = null; });
    return refreshingAppearance;
  }
  async function close() {
    if (busy) return false;
    setBusy(true);
    try {
      if (bindings.desktop) {const saved=await bindings.call("ui.cancelPreview");if(appearanceOnly())acceptAppearance(saved);else accept(saved);}
      else accept(configuration);
      $("settings-dialog").close();
      window.AstroArchiveCustomization?.resetCatalog();
      window.AstroArchiveMonitoring?.diagnosticsVisible(false);
      window.dispatchEvent(new Event("astroarchive-overlay-changed"));
      return true;
    } catch (error) { report(error); return false; }
    finally { setBusy(false); }
  }
  async function open(tab) {
    if (busy) return;
    if ($("settings-dialog").open) {
      if (typeof tab === "string") showTab(tab);
      return;
    }
    if (typeof tab === "string") selectedSettingsTab = tab;
    $("settings-dialog").classList.toggle('settings-appearance-only', appearanceOnly());
    if (appearanceOnly()) selectedSettingsTab = 'appearance';
    window.AstroArchiveActivity.close(false);
    const dialog = $("settings-dialog");
    const source = $("settings-open").getBoundingClientRect();
    showTab(selectedSettingsTab);
    dialog.showModal();
    window.dispatchEvent(new Event("astroarchive-overlay-changed"));
    messageKey = ""; message(""); setBusy(true);
    window.AstroArchiveMotion.expand(dialog, source);
    try { await window.AstroArchiveIslands?.flush(); if (bindings.desktop) {const saved=await bindings.call("ui.load");if(appearanceOnly())acceptAppearance(saved);else accept(saved);} else fillControls(); }
    catch (error) { report(error); }
    finally { setBusy(false); }
  }
  async function save() {
    if (busy || !bindings.desktop) return;
    if (!/^#[0-9a-f]{6}$/i.test($("ui-background-hex").value) || !window.AstroArchiveAppearance.valid()) { notice("settings.hexInvalid", "error"); return; }
    preview(); setBusy(true); notice("settings.saving");
    try {
      const archives=appearanceOnly() ? null : window.AstroArchiveCustomization?.pendingLocations();
      const saved=await bindings.call(appearanceOnly() ? 'ui.appearance.save' : 'ui.save', appearanceOnly() ? Object.fromEntries(appearanceFields.filter(k=>draft[k]!==undefined).map(k=>[k,draft[k]])) : { preferences: draft, ...(archives?{archives}:{}) });
      if(appearanceOnly())acceptAppearance(saved);else accept(saved);
      if(archives)window.AstroArchiveCustomization.locationsSaved(saved.archives);
      window.dispatchEvent(new Event("astroarchive-customization-saved"));
      notice("settings.saved"); bindings.log(t("settings.saved")); $("settings-dialog").close(); window.dispatchEvent(new Event("astroarchive-overlay-changed"));
    } catch (error) { report(error); }
    finally { setBusy(false); }
  }
  $("settings-open").addEventListener("click", open);
  $("settings-close").addEventListener("click", close);
  $("ui-cancel").addEventListener("click", close);
  $("settings-dialog").addEventListener("close", () => window.AstroArchiveMotion.stop($("settings-dialog")));
  $("settings-dialog").addEventListener("cancel", event => { event.preventDefault(); close(); });
  $("ui-save").addEventListener("click", save);
  $("ui-reset").addEventListener("click", () => { const values=defaults(); draft = appearanceOnly() ? {...draft,...Object.fromEntries(appearanceFields.map(k=>[k,values[k]]))} : {...values, islands:window.AstroArchiveIslands?.snapshot() || draft.islands}; imagePreview = null; apply(draft, null); fillControls(); notice("settings.previewing"); });
  $("ui-layout-reset").addEventListener("click", () => { window.AstroArchiveIslands.restore(window.AstroArchiveIslands.resetScope()); preview(); });
  for (const id of ["ui-theme", "ui-background-mode", "ui-animations"]) $(id).addEventListener("change", preview);
  $("ui-language").addEventListener("change", event => {
    if (busy || !event.target.checked) return;
    draft = { ...draft, languageId: event.target.value };
    apply(draft, imagePreview);
    notice("settings.previewing");
  });
  $("ui-background-color").addEventListener("input", () => { $("ui-background-hex").value = $("ui-background-color").value; preview(); });
  $("ui-background-hex").addEventListener("input", () => { if (/^#[0-9a-f]{6}$/i.test($("ui-background-hex").value)) $("ui-background-color").value = $("ui-background-hex").value; preview(); });
  $("ui-plugin-install").addEventListener("click", () => mutatePlugins("ui.plugins.install"));
  $("ui-plugin-catalog").addEventListener("click", () => mutatePlugins("ui.plugins.catalog"));
  $("app-update-check").addEventListener("click", () => checkUpdates());
  $('app-update-download').addEventListener('click',startUpdateDownload);
  $('app-update-stop').addEventListener('click',async()=>{try{await bindings.call('app.updates.cancel');}catch(error){report(error);}});
  $('app-update-folder').addEventListener('click',async()=>{try{await bindings.call('app.updates.folder');}catch(error){report(error);}});
  $("ui-plugin-scan").addEventListener("click", scanPlugins);
  for (const button of document.querySelectorAll("[data-settings-tab]")) button.addEventListener("click", () => showTab(button.dataset.settingsTab, true));
  document.querySelector(".settings-tabs").addEventListener("keydown", event => {
    const tabs = ["appearance", "customization", "plugins", "language", "diagnostics"], index = tabs.indexOf(selectedSettingsTab);
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
    if (next >= 0) { event.preventDefault(); showTab(tabs[next], true); $("settings-tab-" + tabs[next]).focus(); }
  });
  $("ui-plugin-folder").addEventListener("click", async () => {
    if (busy || !bindings.desktop) return;
    try { await bindings.call("ui.plugins.folder"); } catch (error) { report(error); }
  });
  $("ui-choose-background").addEventListener("click", async () => {
    if (busy || !bindings.desktop) return;
    setBusy(true);
    try {
      const selected = await bindings.call("ui.background.choose");
      if (selected) { draft.backgroundImage = selected.fileName; imagePreview = selected.dataUrl; apply(draft, imagePreview); translateOptions(); notice("settings.previewing"); }
    } catch (error) { report(error); }
    finally { setBusy(false); }
  });
  let checkingUpdates=false, updateCheckState=null;
  function renderUpdateStatus() {
    if(!updateCheckState)return;
    const {key,values,problem}=updateCheckState;
    $('app-update-status').textContent=problem||t(key,values);
  }
  let updateDownloadable=false,updateTransfer={phase:'idle'},pollingDownload=false;
  function drawUpdateDownload(value){
    updateTransfer=value;
    const active=value.phase==='downloading',complete=value.phase==='complete';
    $('app-update-download').hidden=!updateDownloadable;
    $('app-update-stop').hidden=!active;
    $('app-update-folder').hidden=!complete;
    $('app-update-progress').hidden=!active;
    $('app-update-progress').value=value.percent||0;
    $('app-update-download-status').textContent=active?t('updates.progress',{percent:value.percent||0,received:Math.round((value.received||0)/1024),total:Math.round((value.total||0)/1024)}):complete?t('updates.complete',{path:value.path}):value.phase==='error'?t('updates.error',{problem:value.problem}):value.phase==='cancelled'?t('updates.cancelled'):'';
    setBusy(busy);
  }
  async function monitorUpdateDownload(){
    if(pollingDownload||!bindings?.desktop)return;
    pollingDownload=true;
    try{
      do{drawUpdateDownload(await bindings.call('app.updates.status'));if(updateTransfer.phase==='downloading')await new Promise(resolve=>setTimeout(resolve,500));}while(updateTransfer.phase==='downloading');
    }catch(error){$('app-update-download-status').textContent=error.message;}
    finally{pollingDownload=false;}
  }
  async function startUpdateDownload(){
    if(!bindings?.desktop||updateTransfer.phase==='downloading')return;
    try{drawUpdateDownload(await bindings.call('app.updates.download'));await monitorUpdateDownload();}
    catch(error){$('app-update-download-status').textContent=error.message;}
  }
  async function checkUpdates(){
    if(checkingUpdates||!bindings?.desktop)return;
    checkingUpdates=true;
    const link=$('app-update-link');
    updateCheckState={key:'updates.checking'};renderUpdateStatus();
    try{
      const result=await bindings.call('app.updates.check');
      updateDownloadable=!!result.downloadable&&(result.available||!result.currentKnown);
      drawUpdateDownload(updateTransfer);
      updateCheckState={key:result.available?'updates.available':!result.currentKnown?'updates.latest':'updates.current',values:{version:(result.available||!result.currentKnown?result.latest:result.current)||'—'},problem:result.problem};renderUpdateStatus();
      link.hidden=!result.latest||!!result.problem;
    }catch(error){updateCheckState={key:'updates.unavailable'};renderUpdateStatus();link.hidden=true;}
    finally{checkingUpdates=false;}
  }
  window.AstroArchivePreferences = {
    connect(value) { bindings = value; window.AstroArchiveCustomization?.connect(value); },
    customizationChanged: preview,
    addIcon(icon) { configuration.icons ||= []; if (!configuration.icons.some(i => i.fileName === icon.fileName)) configuration.icons.push(icon); },
    initialize(data) { accept(data); if(bindings?.desktop&&!window.AstroArchiveRemote)setTimeout(()=>{checkUpdates();monitorUpdateDownload();checkPluginUpdates();},500); },
    close, open, showTab, refreshAppearance,
    get isOpen() { return $("settings-dialog").open; }
  };
})();
