"use strict";

// No build tools or remote resources: this frontend is shipped with the C# host.
// Messages expose specific operations, never filesystem APIs or arbitrary commands.
(() => {
  const $ = id => document.getElementById(id);
  const webview = window.chrome?.webview;
  const i18n = window.AstroArchiveI18n;
  const t = (key, parameters) => i18n.t(key, parameters);
  const activity = window.AstroArchiveActivity;
  const pending = new Map();
  const state = { route: "archive", archives: [], archiveId: "frames", entered: false, path: "", offset: 0, search: "", folder: null, version: 0 };
  let archiveNavigation;
  let archiveFilter=null,manualArchive=null;
  let number = new Intl.NumberFormat("bg");
  let sizeNumber = new Intl.NumberFormat("bg", { maximumFractionDigits: 1 });
  let date = new Intl.DateTimeFormat("bg", { dateStyle: "short", timeStyle: "short" });
  let requestNumber = 0;
  let searchTimer;

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function call(method, params = {}) {
    if(['function.image','image.open','viewer.neighbor','viewer.close'].includes(method)&&!params.viewState){const view=window.AstroArchiveViewer?.state?.();if(view)params={...params,viewState:view};}
    if (!webview) return Promise.reject(new Error(t("offline.description")));
    const id = String(++requestNumber);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error(t("error.timeout")));
      }, ['plugin.call','archive.summary','archive.summary.refresh','archive.audit','image.open','function.image','viewer.render','viewer.neighbor'].includes(method) ? 86_400_000 : /^(analysis\.(run|stack\.run|noise|photometry|coverage)|preparation\.(start|finish|auto|add|cancel)|analysis\.(trash\.|toqueue|redo))/.test(method) ? 86_400_000 : 60_000);
      pending.set(id, { resolve, reject, timeout });
      try { webview.postMessage({ version: 1, id, method, params }); }
      catch (error) { clearTimeout(timeout); pending.delete(id); reject(error); }
    });
  }

  webview?.addEventListener("message", event => {
    const message = event.data;
    if (!message || typeof message !== "object") return;
    if (message.type === 'event' && message.name === 'ui.appearance.changed') { window.AstroArchivePreferences.refreshAppearance(); return; }
    if (message.type === "event" && message.name === "remote.changed") { window.AstroArchiveExtensions.broadcast('remote.changed',{}); return; }
    if (message.type === "event" && message.name?.startsWith("viewer.")) {
      if (message.name === "viewer.loading") activity.ambient("viewer", { title: message.fileName, section: state.route });
      else if (["viewer.loaded", "viewer.problem", "viewer.closed"].includes(message.name)) activity.ambient("viewer", null);
      window.AstroArchiveViewer.event(message); return;
    }
    if (message.type === "event" && message.name === "app.log" && Array.isArray(message.entries)) {
      activity.add(message.entries);
      window.AstroArchiveExtensions.broadcast("log",message.entries);
      return;
    }
    if (message.type === "event" && message.name === "activity.changed") { activity.update(message.snapshot); window.AstroArchiveMonitoring.updateActivity(message.snapshot); if(message.snapshot?.tasks?.some(task=>task.state==='blocked'))window.AstroArchiveMonitoring.checkPending(); window.AstroArchiveExtensions.broadcast("activity",message.snapshot); return; }
    if (message.type === "event" && message.name === "archive.summary.changed") { window.AstroArchiveMonitoring.reloadSummary(); refreshRows(); return; }
    if (message.type === "event" && message.name === "archive.changed") { call('plugin.archives').then(result=>{state.archives=result.archives;return loadFolder(true);}).catch(problem); return; }
    if (message.type === "event" && message.name === "function.navigate") { showRoute(message.section); return; }
    if (message.type === "event" && message.name === "preparation.changed") { window.AstroArchiveFunctions.preparationChanged(); window.AstroArchiveExtensions.broadcast("preparation.changed",{}); return; }
    if (message.type === "event" && message.name === "preparation.automatic") { window.AstroArchiveFunctions.automatic(message.all); showRoute("preparation"); return; }
    if (message.type === "event" && message.name === "action.problem") {
      window.AstroArchiveMonitoring.checkPending(true);
      activity.add([{ section: message.section, time: new Date().toISOString(), category: t("activity.control"), level: "error", message: message.message }]);
      if (message.section === "archive") { $("error-banner").textContent = message.message; $("error-banner").hidden = false; }
      return;
    }
    if (message.type === "event" && message.name === "analysis.result") { window.AstroArchiveAnalysis.acceptStack(message.result); return; }
    if (message.type !== "response") return;
    const request = pending.get(message.id);
    if (!request) return;
    clearTimeout(request.timeout);
    pending.delete(message.id);
    if (message.ok) request.resolve(message.result);
    else request.reject(Object.assign(new Error(message.error || t("error.request")), { cancelled: !!message.cancelled }));
  });

  function log(message, level = "info", section = state.route) {
    activity.add([{ section, time: new Date().toISOString(), category: t("journal.interface"), message, level }]);
  }

  function problem(error) {
    $("error-banner").textContent = error.message;
    $("error-banner").hidden = false;
    $("announcements").textContent = error.message;
    if (state.route !== "archive") window.AstroArchiveFunctions.error(state.route, error);
    log(error.message, "error");
  }

  function showRoute(route) {
    if (!["archive", "analysis", "preparation"].includes(route) || route === state.route) return;
    state.route = route;
    for (const page of document.querySelectorAll(".page")) page.hidden = page.id !== "page-" + route;
    for (const button of document.querySelectorAll("[data-route]")) {
      if (button.dataset.route === route) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    const name = t(route + ".title");
    $("announcements").textContent = name;
    log(t("route.selected", { name }));
    window.AstroArchiveSession?.changed('route');
    return window.AstroArchiveFunctions.show(route).then(()=>window.AstroArchiveSession?.activate(route)).catch(problem);
  }

  function archiveName(archive) {const defaults={frames:'Архив на кадри',stacks:'Архив на стакове',photos:'Архив на снимки'};return archive ? defaults[archive.id]===archive.name ? t('archive.'+archive.id) : archive.name : t('nav.archive');}

  function languageChanged() {
    number = new Intl.NumberFormat(i18n.locale);
    sizeNumber = new Intl.NumberFormat(i18n.locale, { maximumFractionDigits: 1 });
    date = new Intl.DateTimeFormat(i18n.locale, { dateStyle: "short", timeStyle: "short" });
    activity.languageChanged();
    window.AstroArchiveFunctions.languageChanged();
    window.AstroArchiveMonitoring.translate();
    window.AstroArchiveViewer.languageChanged();
    window.AstroArchiveExtensions.theme();
    archiveNavigation?.translate(); drawBreadcrumbs();
    if (!state.entered && state.folder) { loadFolder(); return; }
    if (state.folder) drawFiles(state.folder);
    else {
      const selected = state.archives.find(item => item.id === state.archiveId);
      $("empty-message").textContent = t(webview ? (selected?.path ? "folder.loadingPaths" : "folder.notConfigured") : "offline.description");
      $("folder-counts").textContent = t("folder.direct");
      if (!selected?.path) $("folder-path").textContent = t("folder.noArchive");
    }
  }

  function pluginsChanged(plugins) {
    window.AstroArchiveExtensions.update(plugins);
    call('plugin.archives').then(result=>{state.archives=result.archives;if(!state.archives.some(a=>a.id===state.archiveId)){showArchiveHome();return;}if(!state.entered)loadFolder();}).catch(problem);
  }

  async function openStorage() {
    if (!webview) return;
    try {
      if (window.AstroArchivePreferences.isOpen && !(await window.AstroArchivePreferences.close())) return;
      const result = await call("ui.storage"); state.archives = result.archives; if (result.problem) problem(new Error(result.problem));
      await loadFolder(true); window.AstroArchiveAnalysis.reset(); window.AstroArchivePreparation.reset();
      if (state.route !== "archive") await window.AstroArchiveFunctions.show(state.route);
    } catch (error) { problem(error); }
  }

  function drawRoots(force = false) {
    window.AstroArchiveArchiveIcons.layouts(state.archives);
    const selected = state.archives.find(item => item.id === state.archiveId);
    if(archiveFilter)return;
    archiveNavigation?.update({entered: state.entered, archiveId: state.archiveId, archives: state.archives,
      root: selected?.path || "", path: state.path, folder: state.folder, unavailable: !selected?.path || !webview}, force);
  }

  function drawBreadcrumbs() {
    const archive = state.archives.find(item => item.id === state.archiveId);
    const fragment = document.createDocumentFragment();
    const parts = state.path.split(/[\\/]/).filter(Boolean);
    const filterSummary=$('archive-filter-summary');filterSummary.hidden=!archiveFilter;filterSummary.replaceChildren();
    document.querySelector('#page-archive .content-panel').classList.toggle('archive-filter-results',!!archiveFilter);
    $('search').placeholder=archiveFilter?(i18n.locale.startsWith('en')?'Search filtered results…':'Търси сред резултатите…'):t('folder.search');
    if(archiveFilter){
      fragment.append(element('strong','filter-results-title',i18n.locale.startsWith('en')?'Filtered results':'Резултати от филтриране'),element('span','filter-results-badge',i18n.locale.startsWith('en')?'FILTER ON':'АКТИВЕН ФИЛТЪР'));
      const clear=element('button','filter-clear',i18n.locale.startsWith('en')?'Turn off':'Изключи');clear.type='button';clear.onclick=()=>$('archive-filter-toggle').click();fragment.append(clear);$('breadcrumbs').replaceChildren(fragment);
      const labels={archive:'Архив',telescope:'Телескоп',object:'Обект',night:'Нощ',since:'Добавени'},roles={archive:'archive',telescope:'telescope',object:'object',night:'day',since:'day'};
      for(const[key,value]of Object.entries(archiveFilter)){if(!value)continue;const chip=element('span','archive-filter-chip'),copy=element('span','');chip.append(window.AstroArchiveArchiveIcons.create(roles[key]||'folder'));copy.append(element('small','',labels[key]||key),element('strong','',key==='archive'?archiveName(archive):key==='since'?`Последните ${Math.max(1,Math.round((Date.now()/1000-value)/86400))} дни`:key==='telescope'?window.AstroArchiveCustomization.telescope(value):String(value)));chip.append(copy);filterSummary.append(chip);}
      return;
    }
    function crumb(label, path) {
      const button = element("button", "", label);
      button.type = "button";
      const archiveId = state.archiveId;
      button.addEventListener("click", () => navigateFolder(archiveId, path));
      fragment.append(button);
    }
    const home = element("button", "", t("archive.roots")); home.type = "button"; home.onclick = showArchiveHome; fragment.append(home);
    if (!state.entered) { $("breadcrumbs").replaceChildren(fragment); return; }
    fragment.append(element("span", "muted", "›"));
    crumb(archiveName(archive), "");
    parts.forEach((part, index) => {
      const divider = element("span", "muted", "›");
      divider.setAttribute("aria-hidden", "true");
      fragment.append(divider);
      crumb(index === 0 ? window.AstroArchiveCustomization.telescope(part) : part, parts.slice(0, index + 1).join("\\"));
    });
    $("breadcrumbs").replaceChildren(fragment);
  }

  function formatSize(bytes) {
    if (bytes === null) return "—";
    if (bytes < 1024) return number.format(bytes) + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024, index = 0;
    while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
    return sizeNumber.format(value) + " " + units[index];
  }

  async function activateEntry(archiveId, entry) {
    if (entry.kind === "archive") { navigateFolder(entry.archiveId, ""); return; }
    if (entry.isLink) { problem(new Error(t("file.linkBlocked"))); return; }
    if (entry.kind === "folder") { navigateFolder(archiveId, entry.relativePath); return; }
    if (!["fits", "image"].includes(entry.kind)) return;
    try {
      await call("image.open", { archiveId, relativePath: entry.relativePath });
      log(t("file.opened", { name: entry.name }));
    } catch (error) { problem(error); }
  }

  function drawFiles(folder) {
    const fragment = document.createDocumentFragment();
    const rowContext=window.AstroArchiveArchiveRows.context(folder);
    const columns = !window.AstroArchiveCustomization.archiveVariant(rowContext)&&folder.columns ? folder.columns.map(key=>({key,title:window.AstroArchiveArchiveSchema.titles[key]||key})) : window.AstroArchiveCustomization.archiveColumns(rowContext);
    const headings = document.querySelector('#page-archive .content-panel thead tr');
    headings.replaceChildren(...columns.map(c=>element('th','',window.AstroArchiveArchiveRows.columnTitle(c,folder.entries,folder))),element('th','',''));
    for (const entry of folder.entries) {
      const row = element("tr");
      row.tabIndex = 0;
      const name = element("td");
      const label = element("span", "file-name");
      const icon = window.AstroArchiveArchiveIcons.create(entry.icon||window.AstroArchiveArchiveIcons.role(entry.archiveId || folder.archiveId, entry.relativePath, entry.kind));
      const display = !entry.displayName && entry.kind==='folder' && entry.relativePath.split(/[\\/]/).filter(Boolean).length===1 ? window.AstroArchiveCustomization.telescope(entry.name) : entry.displayName ?? entry.name;
      label.append(icon, element("span", "", display)); label.title = entry.name;
      name.append(label);
      if(folder.filtered){row.classList.add('archive-filter-result');name.append(element('small','filter-result-origin',entry.relativePath.split(/[\\/]/).slice(0,-1).join(' › ')||archiveName(state.archives.find(a=>a.id===folder.archiveId))));}
      for (const column of columns) {
        if (column.key === 'name') { row.append(name); continue; }
        const value = column.key === 'kind' ? entry.kind === "archive" ? t("nav.archive") : !entry.isLink && entry.details?.kind==='stack' ? (i18n.locale.startsWith('en')?'Stack':'Стак') : !entry.isLink && entry.details?.kind==='frame' ? (i18n.locale.startsWith('en')?'Frame':'Кадър') : entry.kind==="image" ? entry.name.split(".").at(-1).toUpperCase() : t("type." + (entry.isLink ? "link" : entry.kind))
          : column.key === 'size' ? formatSize(entry.bytes) : window.AstroArchiveArchiveRows.cell(entry,column.key,columns,folder);
        const cell=element('td','muted',value);
        if(column.key==='date'||column.key==='modified')cell.title=window.AstroArchiveArchiveRows.label(window.AstroArchiveArchiveRows.dateKind(entry,folder));
        if(entry.details?.building||entry.details?.stale)cell.title+=(cell.title?' · ':'')+'Справката се обновява';
        if(entry.details?.problem)cell.title+=(cell.title?' · ':'')+entry.details.problem;
        row.append(cell);
      }
      const actionCell = element("td");
      if (!entry.isLink && entry.kind !== "file") {
        const openFolder = entry.kind === "folder" || entry.kind === "archive";
        const action = element("button", "row-action", t(openFolder ? "file.open" : "file.preview"));
        action.type = "button";
        action.setAttribute("aria-label", t(openFolder ? "file.openName" : "file.previewName", { name: entry.name }));
        action.addEventListener("click", event => { event.stopPropagation(); activateEntry(folder.archiveId, entry); });
        actionCell.append(action);
        if(folder.filtered){const location=element('button','row-action filter-folder-action','Към папката');location.type='button';location.onclick=event=>{event.stopPropagation();navigateFolder(entry.archiveId||folder.archiveId,entry.relativePath.split(/[\\/]/).slice(0,-1).join('/'));};actionCell.append(location);}
        if(entry.details?.kind==='stack'&&entry.id){const source=element('button','row-action','Изходни нощи');source.type='button';source.onclick=event=>{event.stopPropagation();window.AstroArchiveArchiveSchema.sourceNights(entry.id).catch(problem);};actionCell.append(source);}
      }
      row.append(actionCell);
      row.addEventListener('contextmenu',event=>{if(window.AstroArchivePython?.entry){event.preventDefault();window.AstroArchivePython.entry(entry.archiveId||folder.archiveId,entry);}});
      window.AstroArchiveRemote?.decorateEntry(actionCell,folder,entry);
      row.addEventListener("dblclick", () => activateEntry(folder.archiveId, entry));
      row.addEventListener("keydown", event => {
        if (event.target === row && event.key === "Enter") { event.preventDefault(); activateEntry(folder.archiveId, entry); }
      });
      fragment.append(row);
    }
    $("files").replaceChildren(fragment);
    $("empty").hidden = folder.entries.length !== 0;
    document.querySelector('#empty h2').textContent=folder.filtered?'Няма намерени файлове':t('folder.emptyTitle');
    $("empty-message").textContent = folder.filtered?'Няма файлове, които съвпадат с избраните филтри.':t(state.search ? "folder.noMatches" : "folder.empty");
    $("folder-counts").textContent = folder.filtered?`Намерени ${number.format(folder.total)} файла · всички папки в избрания архив`:t("folder.counts", { files: number.format(folder.directFiles), folders: number.format(folder.directFolders) });
    $("page-label").textContent = folder.total ? t("folder.page", { first: number.format(folder.offset + 1), last: number.format(folder.offset + folder.entries.length), total: number.format(folder.total) }) : "";
    $("previous").disabled = folder.offset === 0;
    $("next").disabled = folder.offset + folder.pageSize >= folder.total;
    $("folder-path").textContent = folder.filtered?'Резултати от всички папки на „'+archiveName(state.archives.find(a=>a.id===folder.archiveId))+'“':folder.fullPath;
    $("folder-path").title = folder.fullPath;
    if (!state.entered) $("folder-counts").textContent = new Intl.NumberFormat(i18n.locale).format(state.archives.length) + ' ' + (i18n.locale.startsWith('en') ? 'archives' : 'архива');
  }

  function refreshRows(){const version=state.version;if(webview&&state.folder)window.AstroArchiveArchiveRows.watch(state.folder,call,()=>version===state.version,()=>drawFiles(state.folder));}
  async function loadFolder(refresh = false) {
    const version = ++state.version;
    window.AstroArchiveArchiveRows.stop();
    const selected = state.archives.find(item => item.id === state.archiveId);
    window.AstroArchiveMonitoring.select({ archiveId: state.archiveId, relativePath: state.path,
      name: state.entered ? state.path.split(/[\\/]/).filter(Boolean).at(-1) || archiveName(selected) : t("folder.choose"),
      rootName: archiveName(selected), root: selected?.path || "", enabled: !!webview && !!selected?.path, summaryEnabled: state.entered }, refresh);
    $("error-banner").hidden = true;
    drawBreadcrumbs();
    $("previous").disabled = $("next").disabled = true;
    if (!state.entered) {
      if (webview) call('archive.read.stop').catch(() => {});
      const entries = state.archives.filter(archive => archiveName(archive).toLocaleLowerCase(i18n.locale).includes(state.search.toLocaleLowerCase(i18n.locale)))
        .map(archive => ({archiveId: archive.id, name: archiveName(archive), kind: "archive", relativePath: "", bytes: null, modified: null, isLink: false}));
      state.folder = {archiveId: state.archiveId, entries, relativePath: "", fullPath: t("folder.choose"), total: entries.length,
        offset: 0, pageSize: 100, directFiles: 0, directFolders: entries.length};
      $("loading").hidden = true; $("files").setAttribute("aria-busy", "false"); archiveNavigation?.busy(false);
      drawRoots(refresh); drawFiles(state.folder); refreshRows(); return;
    }
    if (!selected?.path || !webview) {
      state.folder = null;
      $("loading").hidden = true;
      $("files").setAttribute("aria-busy", "false");
      $("files").replaceChildren();
      $("empty").hidden = false;
      $("empty-message").textContent = t(webview ? "folder.notConfigured" : "offline.description");
      $("folder-path").textContent = selected?.path || t("folder.noArchive");
      $("folder-counts").textContent = t("folder.direct");
      $("page-label").textContent = "";
      drawRoots(refresh); archiveNavigation?.busy(false);
      return;
    }
    $("loading").hidden = false;
    $("files").setAttribute("aria-busy", "true");
    archiveNavigation?.busy(true);
    try {
      const folder = archiveFilter ? await call('archive.filtered',{filters:archiveFilter,offset:state.offset,search:state.search}) : await call("archive.list", { archiveId: state.archiveId, relativePath: state.path, offset: state.offset, search: state.search, refresh });
      if(archiveFilter)window.dispatchEvent(new CustomEvent('astroarchive-archive-filter-statistics',{detail:folder.statistics}));
      if (version !== state.version) return;
      state.folder = folder;
      state.offset = folder.offset;
      drawFiles(folder);
      refreshRows();
      drawRoots(refresh);
      if (folder.problem) problem(new Error(folder.problem));
      log(t("folder.loaded", { path: folder.fullPath, total: number.format(folder.total) }));
    } catch (error) {
      if (version !== state.version || error.cancelled) return;
      state.folder = null;
      $("files").replaceChildren();
      $("empty").hidden = false;
      $("empty-message").textContent = t("folder.failed");
      $("folder-counts").textContent = t("folder.noContent");
      $("page-label").textContent = "";
      $("folder-path").textContent = selected.path;
      drawRoots(refresh);
      problem(error);
    } finally {
      if (version === state.version) { $("loading").hidden = true; $("files").setAttribute("aria-busy", "false"); archiveNavigation?.busy(false); }
    }
  }

  function navigateFolder(archiveId, path) {
    if(archiveFilter){archiveFilter=null;manualArchive=null;window.dispatchEvent(new Event('astroarchive-archive-filter-reset'));}
    clearTimeout(searchTimer);
    state.archiveId = archiveId;
    state.entered = true;
    state.path = path;
    state.offset = 0;
    state.search = "";
    state.folder = null;
    $("search").value = "";
    $("files").replaceChildren();
    $("empty").hidden = true;
    window.AstroArchiveSession?.changed('archive');
    return loadFolder();
  }

  function showArchiveHome() {
    if(archiveFilter){archiveFilter=null;manualArchive=null;window.dispatchEvent(new Event('astroarchive-archive-filter-reset'));}
    clearTimeout(searchTimer); state.entered = false; state.path = ""; state.offset = 0; state.search = ""; state.folder = null;
    $("search").value = ""; window.AstroArchiveSession?.changed('archive'); return loadFolder();
  }

  async function bootstrap() {
    try {
      if (webview) {
        const data = await call("app.bootstrap");
        if (data.protocol !== 1) throw new Error(t("error.protocol"));
        state.archives = data.archives;
        window.AstroArchivePreferences.initialize(data.ui);
        activity.update(data.activity);
        if (data.problem) log(data.problem, "warning");
      } else {
        state.archives = [ { id: "frames", name: "Архив на кадри", path: "" }, { id: "stacks", name: "Архив на стакове", path: "" }, { id: "photos", name: "Архив на снимки", path: "" } ];
        window.AstroArchivePreferences.initialize(null);
        log(t("offline.log"));
      }
      await loadFolder();
      if(webview)await window.AstroArchiveSession.start();
    } catch (error) { problem(error); }
  }

  for (const button of document.querySelectorAll("[data-route]")) button.addEventListener("click", () => showRoute(button.dataset.route));
  for (const button of document.querySelectorAll("[data-debug-section]")) {
    if (!webview) { button.disabled = true; button.title = t("offline.action"); }
    button.addEventListener("click", async () => {
      try {
        if ($("settings-dialog").open && !(await window.AstroArchivePreferences.close())) return;
        await call("debug.open", { section: button.dataset.debugSection });
        log(t("workspace.opened"));
      } catch (error) { problem(error); }
    });
  }
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.search = $("search").value.trim(); state.offset = 0; loadFolder(); }, 220);
  });
  $("refresh").addEventListener("click", async () => {
    clearTimeout(searchTimer);
    $('refresh').disabled=true;
    try{if(webview){await window.AstroArchivePython.refreshArchives();const data=await call('app.bootstrap');state.archives=data.archives;}await loadFolder(true);}catch(error){problem(error);}finally{$('refresh').disabled=false;}
  });
  $("previous").addEventListener("click", () => { if (state.folder) { state.offset = Math.max(0, state.offset - state.folder.pageSize); loadFolder(); } });
  $("next").addEventListener("click", () => { if (state.folder) { state.offset += state.folder.pageSize; loadFolder(); } });
  for (const button of document.querySelectorAll("[data-storage-settings]")) { button.disabled = !webview; button.addEventListener("click", openStorage); }
  $("archive-import").disabled = !webview;
  window.AstroArchiveFunctions.connect({ call, log, desktop: !!webview, navigate: showRoute });
  window.AstroArchiveViewer.connect({call});
  window.AstroArchiveExtensions.connect({call});
  archiveNavigation = window.AstroArchiveArchiveNavigation.create({call, name: archiveName, navigate: navigateFolder, home: showArchiveHome, problem});
  window.AstroArchiveMonitoring.connect({call, desktop: !!webview});
  activity.connect({ call, desktop: !!webview, route: () => state.route, overlayChanged: () => {}, openDiagnostics: () => window.AstroArchivePreferences.open("diagnostics") });
  window.AstroArchivePreferences.connect({ call, log, languageChanged, pluginsChanged, desktop: !!webview });
  window.addEventListener('astroarchive-customization',()=>{if(state.folder)drawFiles(state.folder);drawBreadcrumbs();drawRoots(true);});
  window.addEventListener('astroarchive-customization-saved',()=>loadFolder(true));
  window.addEventListener('astroarchive-locations-saved',event=>{state.archives=event.detail.archives;window.AstroArchiveAnalysis.reset();window.AstroArchivePreparation.reset();if(!state.archives.some(a=>a.id===state.archiveId))showArchiveHome();else loadFolder(true);});
  window.AstroArchiveArchiveView={
    navigate:navigateFolder,
    current:()=>({archiveId:state.archiveId,path:state.path,entered:state.entered}),
    filter(filters){if(filters&&!archiveFilter){manualArchive={archiveId:state.archiveId,entered:state.entered,path:state.path,offset:state.offset,search:state.search};state.search='';}archiveFilter=filters;if(filters){state.archiveId=filters.archive;state.entered=true;state.path='';state.offset=0;}else if(manualArchive){Object.assign(state,manualArchive);manualArchive=null;}$("search").value=state.search;window.AstroArchiveSession?.changed('archive');return loadFolder(true);}
  };
  window.AstroArchiveSession.register('route',{read:()=>({section:state.route}),write:value=>showRoute(value.section)});
  window.AstroArchiveSession.register('archive',{
    visible:()=>state.route==='archive',
    read:()=>({entered:state.entered,archiveId:state.archiveId,path:state.path,offset:state.offset,search:state.search,filters:archiveFilter}),
    async write(value){clearTimeout(searchTimer);Object.assign(state,{entered:value.entered,archiveId:value.archiveId,path:value.path||'',offset:value.offset||0,search:value.search||''});archiveFilter=value.filters||null;manualArchive=null;$('search').value=state.search;window.dispatchEvent(new CustomEvent('astroarchive-shared-archive',{detail:value}));await loadFolder();}
  });
  bootstrap();
})();
