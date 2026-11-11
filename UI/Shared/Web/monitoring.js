"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const tr = (bg, en) => window.AstroArchiveI18n.locale.startsWith('en') ? en : bg;
  let api, usage = null, disk = null, selection = null, summary = null, recovery = null;
  let summaryKey, summaryVersion = 0, resourceBusy = false, diskBusy = false, pendingBusy = false;
  let diagnostics = false, timer, diskTime = 0, pendingTime = 0;
  let summaryReading = false, archiveWorking = false;
  let audit = null, auditBusy = false, auditTask = null, auditStatusLoaded = false, auditProblem = null;
  const n = (value, digits = 1) => Number.isFinite(value) ? new Intl.NumberFormat(window.AstroArchiveI18n.locale,
    {maximumFractionDigits: digits, minimumFractionDigits: digits}).format(value) : '—';
  const set = (id, value) => { const node = $(id); if (node.textContent !== value) node.textContent = value; };
  const pct = value => Number.isFinite(value) ? n(value) + '%' : '—';
  const percent = (value, total) => Number.isFinite(value) && Number.isFinite(total) && total > 0 ? value / total * 100 : null;
  const difference = (total, part) => Number.isFinite(total) && Number.isFinite(part) ? Math.max(0, total - part) : null;
  function bytes(value) {
    if (!Number.isFinite(value)) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB']; let size = value, index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
    return n(size) + ' ' + units[index];
  }

  $('resource-monitor').innerHTML = `<div class="disk-meter vertical-meter" id="header-disk-meter" role="img"><span class="meter-free"></span><span class="meter-other"></span><span class="meter-app"></span></div>
    <div class="header-disk-copy"><strong id="header-disk-title"></strong><span id="header-disk-free"></span><small id="header-disk-detail"></small></div>
    <button type="button" id="disk-refresh" class="disk-refresh">↻</button>`;
  $('diagnostic-resources').innerHTML = `<h3 id="resource-heading"></h3><p class="resource-key"><span class="resource-app-text" id="resource-app-key"></span><span class="resource-other-text" id="resource-other-key"></span><span class="resource-free-text" id="resource-free-key"></span></p>
    ${['cpu', 'ram', 'disk'].map(id => `<section class="diagnostic-meter" id="resource-${id}-row"><div class="meter-information"><strong id="resource-${id}-name"></strong><span class="resource-app-text" id="resource-${id}-app">—</span><span class="resource-other-text" id="resource-${id}-other">—</span><span class="resource-free-text" id="resource-${id}-free">—</span></div><div class="horizontal-meter" id="diagnostic-${id}-meter" role="img"><span class="meter-app"></span><span class="meter-other"></span><span class="meter-free"></span></div></section>`).join('')}`;

  function meter(id, app, other) {
    const node = $(id), valid = Number.isFinite(app) && Number.isFinite(other);
    const a = valid ? Math.max(0, Math.min(100, app)) : 0;
    const b = valid ? Math.max(0, Math.min(100 - a, other)) : 0;
    for (const [name, value] of [['app', a], ['other', b], ['free', 100 - a - b]]) {
      const segment = node.querySelector('.meter-' + name), share = value + '%';
      if (segment.style.flexBasis !== share) segment.style.flexBasis = share;
    }
    node.dataset.unknown = String(!valid);
  }
  function renderUsage() {
    const appCpu = usage?.cpuPercent, otherCpu = difference(usage?.totalCpuPercent, appCpu);
    const appRam = usage?.privateWorkingSetBytes, totalRam = usage?.totalRamBytes;
    const otherRam = difference(usage?.usedRamBytes, appRam), freeRam = difference(totalRam, usage?.usedRamBytes);
    meter('diagnostic-cpu-meter', appCpu, otherCpu);
    meter('diagnostic-ram-meter', percent(appRam, totalRam), percent(otherRam, totalRam));
    set('resource-cpu-app', tr('Приложение: ', 'Application: ') + pct(appCpu));
    set('resource-cpu-other', tr('Други: ', 'Others: ') + pct(otherCpu));
    set('resource-cpu-free', tr('Неизползван CPU: ', 'Idle CPU: ') + pct(difference(100, usage?.totalCpuPercent)));
    set('resource-ram-app', tr('Приложение: ', 'Application: ') + bytes(appRam) + ' · ' + pct(percent(appRam, totalRam)));
    set('resource-ram-other', tr('Други: ', 'Others: ') + bytes(otherRam) + ' · ' + pct(percent(otherRam, totalRam)));
    set('resource-ram-free', tr('Свободни: ', 'Free: ') + bytes(freeRam) + ' / ' + bytes(totalRam));
    $('resource-cpu-row').title = tr('CPU на Python хоста и числените работни процеси, спрямо всички логически ядра.', 'Python host and numerical workers across all logical processors.') + (usage?.problem ? '\n' + usage.problem : '');
    $('resource-ram-row').title = tr('Физическа RAM на Python хоста и работните му процеси, включително споделени страници. Браузърът е в „Други“.', 'Resident RAM of the Python host and its workers, including shared pages. Browser memory is in Others.');
    $('diagnostic-cpu-meter').setAttribute('aria-label', `CPU: ${pct(appCpu)} / ${pct(otherCpu)}`);
    $('diagnostic-ram-meter').setAttribute('aria-label', `RAM: ${bytes(appRam)} / ${bytes(otherRam)} / ${bytes(freeRam)}`);
  }
  function renderDisk() {
    const other = difference(disk ? disk.totalBytes - disk.freeBytes : null, disk?.archiveBytes);
    const appShare = percent(disk?.archiveBytes, disk?.totalBytes), otherShare = percent(other, disk?.totalBytes);
    const barApp = disk ? appShare ?? 0 : null;
    const barOther = disk && appShare == null ? percent(disk.totalBytes - disk.freeBytes, disk.totalBytes) : otherShare;
    meter('header-disk-meter', barApp, barOther); meter('diagnostic-disk-meter', barApp, barOther);
    const label = disk ? `${disk.name} (${disk.volume.replace(/[\\/]/g, '')})` : tr('Архивен диск', 'Archive drive');
    set('header-disk-title', label); set('resource-disk-name', label);
    set('header-disk-free', disk ? `${bytes(disk.freeBytes)} ${tr('свободни от', 'free of')} ${bytes(disk.totalBytes)}` : tr('Избери архив', 'Select an archive'));
    set('header-disk-detail', disk ? `${tr('Архив', 'Archive')} ${disk.reading || disk.skipped ? '≈ ' : ''}${bytes(disk.archiveBytes)} · ${tr('Други', 'Others')} ${bytes(other)}` : '');
    set('resource-disk-app', tr('Архив: ', 'Archive: ') + bytes(disk?.archiveBytes) + ' · ' + pct(appShare));
    set('resource-disk-other', tr('Други: ', 'Others: ') + bytes(other) + ' · ' + pct(otherShare));
    set('resource-disk-free', tr('Свободни: ', 'Free: ') + bytes(disk?.freeBytes) + ' / ' + bytes(disk?.totalBytes));
    const description = tr('Зелено: избраният архив. Виолетово: други файлове. Сиво: свободно място. Размерът на архива е ориентировъчен сбор на файловете.', 'Green: selected archive. Violet: other files. Gray: free space. Archive size is an approximate sum of file sizes.')
      + (disk && appShare == null ? tr('\nРазмерът на архива се изчислява; засега цялото заето място е виолетово.', '\nArchive size is being calculated; all occupied space is temporarily violet.') : '') + (disk?.problem ? '\n' + disk.problem : '');
    $('resource-monitor').title = description; $('resource-disk-row').title = description;
    $('header-disk-meter').setAttribute('aria-label', `${label}: ${bytes(disk?.freeBytes)} ${tr('свободни', 'free')}`);
    $('diagnostic-disk-meter').setAttribute('aria-label', description);
    $('resource-monitor').dataset.low = String(!!disk && disk.freeBytes < Math.max(512 * 1024 ** 2, disk.totalBytes * .01));
    $('disk-refresh').disabled = !selection?.enabled || !!disk?.reading || diskBusy;
  }
  async function resources() {
    if (!api?.desktop || !diagnostics || resourceBusy || document.hidden || !$('settings-dialog').open) return;
    resourceBusy = true;
    try { usage = await api.call('resources.read'); renderUsage(); }
    catch (error) { usage = null; renderUsage(); $('diagnostic-resources').title = error.message; }
    finally { resourceBusy = false; }
  }
  async function readDisk(refresh = false) {
    if (!selection?.enabled || diskBusy || !api?.desktop) return;
    diskBusy = true; const archiveId = selection.archiveId, root = selection.root;
    try {
      const value = await api.call('archive.disk', {archiveId, refresh});
      if (selection?.archiveId === archiveId && selection.root === root) { disk = value; diskTime = Date.now(); renderDisk(); }
    } catch (error) {
      if (selection?.archiveId === archiveId) { disk = null; renderDisk(); $('resource-monitor').title = error.message; set('header-disk-free', tr('Дискът не е достъпен', 'Drive unavailable')); diskTime = Date.now(); }
    } finally { diskBusy = false; $('disk-refresh').disabled = !selection?.enabled || !!disk?.reading; }
  }
  function renderPending() {
    const show = !!recovery?.message;
    $('pending-archive-notice').hidden = !show;
    if (!show) return;
    set('pending-archive-title', tr('Записът изчаква свободно място', 'Saving is waiting for free space'));
    set('pending-archive-description', recovery.message);
    set('pending-archive-path', recovery.count > 0 ? `${recovery.count} ${recovery.count === 1 ? tr('временен файл', 'recovery file') : tr('временни файла', 'recovery files')} · ${bytes(recovery.bytes)} · ${recovery.folder}` : recovery.originalFolder || recovery.folder || '');
    set('pending-archive-open', tr('Отвори запазените файлове ↗', 'Open preserved files ↗'));
  }
  async function checkPending(force = false) {
    if (!api?.desktop || pendingBusy || (!force && Date.now() - pendingTime < 5000)) return;
    pendingBusy = true; pendingTime = Date.now();
    try { recovery = await api.call('archive.pending'); renderPending(); }
    catch (error) { $('pending-archive-notice').title = error.message; }
    finally { pendingBusy = false; }
  }
  async function loadSummary(reset = false) {
    const current = selection, version = ++summaryVersion;
    if (!current?.enabled || current.summaryEnabled === false) { if (api?.desktop) api.call('archive.summary.stop').catch(() => {}); summary = null; renderSummary(); return; }
    summaryReading = true;
    if (reset) summary = null;
    renderSummary(!summary);
    try {
      const value = await api.call('archive.summary', {archiveId: current.archiveId, relativePath: current.relativePath, refresh: false});
      if (version !== summaryVersion) return; summary = value; renderSummary();
    } catch (error) {
      if (version !== summaryVersion || error.cancelled) return;
      renderSummary(); set('summary-state', tr('Няма достъпна статистика', 'Statistics unavailable')); $('summary-state').title = error.message;
    } finally { if (version === summaryVersion) summaryReading = false; }
  }
  function duration(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
    return tr(`${n(h, 0)} ч ${n(m, 0)} м ${n(s, Number.isInteger(s) ? 0 : 2)} с`, `${n(h, 0)} h ${n(m, 0)} m ${n(s, Number.isInteger(s) ? 0 : 2)} s`);
  }
  function renderSummary(loading = false) {
    set('summary-heading', tr('ИЗБРАНА ПАПКА', 'SELECTED FOLDER')); set('summary-folder', selection?.name || tr('Избери папка', 'Select a folder')); $('summary-folder').title = selection?.name || '';
    set('summary-frames-label', summary?.kind === 'stacks' ? tr('Кадри в стаковете', 'Frames in stacks') : summary?.kind === 'photos' ? tr('Готови снимки', 'Finished photos') : tr('Кадри', 'Frames'));
    set('summary-exposure-label', summary?.kind==='photos'?tr('Последно създаване','Latest creation'):tr('Обща експозиция', 'Total exposure'));
    set('summary-frames', summary?.frames != null ? (summary.minimumFrames || summary.partial ? '≥ ' : '') + n(summary.frames, 0) : '—');
    set('summary-exposure', summary?.exposureSeconds != null ? (summary.unknownExposure > 0 || summary.partial ? '≥ ' : '') + duration(summary.exposureSeconds) : '—');
    if(summary?.kind==='photos'){set('summary-frames',n(summary.files,0));set('summary-exposure',summary.created?new Date(summary.created).toLocaleDateString(window.AstroArchiveI18n.locale):'—');}
    const updating = summary?.building;
    const note = updating ? summary?.totalFiles > 0 ? tr(`Проверка на информацията: ${n(summary.scannedFiles||0,0)} / ${n(summary.totalFiles,0)} файла`, `Checking metadata: ${n(summary.scannedFiles||0,0)} / ${n(summary.totalFiles,0)} files`) : summary?.frames != null ? tr('Запазен сбор · обновява се…', 'Saved totals · updating…')
      : tr('Преброяване на архива · подготвя се…', 'Counting archive files · preparing…')
      : loading ? tr('Зареждане на справката…', 'Loading saved totals…') : summary?.problem ? tr('Проверката не завърши · виж дневника', 'Check incomplete · see diagnostics')
      : summary?.stale ? tr('Индексът не е проверен изцяло · натисни Обнови', 'Index not fully checked · select Refresh')
      : summary ? summary.kind!=='photos'&&summary.unknownExposure > 0 ? tr(`Без записана експозиция: ${n(summary.unknownExposure, 0)} файла`, `No recorded exposure: ${n(summary.unknownExposure, 0)} files`) : summary.partial ? tr('Непълни данни · виж дневника', 'Partial data · see diagnostics') : summary.fromIndex ? tr('Запазен сбор', 'Cached totals') : tr('Проверено', 'Checked') : '';
    set('summary-state', note); $('folder-summary').setAttribute('aria-busy', String(loading || !!updating));
    $('summary-state').title = summary ? tr('Сбор за избраната папка и всички вложени файлове. „Обнови“ проверява броя и експозицията отново.', 'Totals include every nested file. Refresh rechecks count and exposure.') + (summary.savedUtc ? '\n' + new Date(summary.savedUtc).toLocaleString(window.AstroArchiveI18n.locale) : '') + (summary.cacheFile ? '\n' + summary.cacheFile : '') + (summary.problem ? '\n' + summary.problem : '') : '';
  }

  function renderAudit() {
    set('archive-audit-heading', tr('Проверка на архивите', 'Archive check'));
    set('archive-audit-run', tr('Провери и обнови архива', 'Check and refresh archives'));
    set('archive-audit-stop', tr('Спри безопасно', 'Stop safely'));
    set('archive-audit-open', tr('Покажи подробния отчет ↗', 'Show detailed report ↗'));
    set('archive-audit-scope', tr('Последователна проверка на FITS хедъри, размери, PNG структура и разположение. Обновява справочните файлове; оригиналите не се променят. Не удостоверява всеки пиксел или контролна сума.', 'Sequential check of FITS headers, dimensions, PNG structure and archive placement. Updates saved totals; originals remain untouched. Does not certify every pixel or checksum.'));
    const running = auditBusy || !!auditTask;
    $('archive-audit-run').disabled = !api?.desktop || running || archiveWorking;
    $('archive-audit-run').dataset.unavailable = String(!api?.desktop || running || archiveWorking);
    $('archive-audit-stop').hidden = !running;
    $('archive-audit-stop').disabled = !auditTask?.canStop;
    $('archive-audit-stop').dataset.unavailable = String(!auditTask?.canStop);
    $('archive-audit-progress').hidden = !running;
    if (auditTask?.percent != null) $('archive-audit-progress').value = auditTask.percent; else $('archive-audit-progress').removeAttribute('value');
    if (running) set('archive-audit-status', auditTask ? `${auditTask.phase}\n${auditTask.detail || ''}\n${tr('Изминали', 'Elapsed')}: ${auditTask.elapsedSeconds || 0} s` : tr('Подготовка на проверката…', 'Preparing the check…'));
    else set('archive-audit-status', auditProblem || (audit ? `${tr('FITS', 'FITS')}: ${n(audit.fits, 0)} · PNG: ${n(audit.photos, 0)} · ${tr('Забележки', 'Issues')}: ${n(audit.issues, 0)}\n${new Date(audit.savedUtc).toLocaleString(window.AstroArchiveI18n.locale)}` : tr('Няма завършена проверка.', 'No completed check.')));
    $('archive-audit-open').hidden = !audit?.reportPath;
    $('archive-audit-issues').hidden = !audit?.issues;
    set('archive-audit-issues-title', tr('Забележки и файлове за преглед', 'Issues and files to review'));
  }
  function acceptAudit(value) {
    audit = value; const fragment = document.createDocumentFragment();
    for (const example of value?.examples || []) { const item = document.createElement('li'); item.textContent = example.path + '\n' + example.reason; fragment.append(item); }
    for (const problem of value?.problems || []) { const item = document.createElement('li'); item.textContent = problem; fragment.append(item); }
    $('archive-audit-issue-list').replaceChildren(fragment); renderAudit();
  }
  async function loadAudit() {
    if (!api?.desktop) return;
    try { acceptAudit(await api.call('archive.audit.status')); auditStatusLoaded = true; }
    catch (error) { $('archive-audit-status').title = error.message; }
  }
  async function runAudit() {
    if (!api?.desktop || archiveWorking || auditBusy) return;
    auditBusy = true; auditProblem = null; renderAudit();
    try { acceptAudit(await api.call('archive.audit')); await loadSummary(); }
    catch (error) { auditProblem = error.cancelled ? tr('Проверката е спряна. Предишният отчет е запазен.', 'Check stopped. The previous report is preserved.') : error.message; }
    finally { auditBusy = false; renderAudit(); }
  }
  function theme() {
    const light = getComputedStyle(document.documentElement).colorScheme === 'light';
    for (const id of ['resource-monitor', 'diagnostic-resources']) $(id).style.setProperty('--resource-other', light ? '#7351b5' : '#ad95ef');
  }
  function translate() {
    theme(); set('resource-heading', tr('Използване на ресурси', 'Resource usage'));
    set('resource-app-key', tr('● AstroArchive / архив', '● AstroArchive / archive'));
    set('resource-other-key', tr('● Други приложения и Windows / файлове', '● Other apps and Windows / files'));
    set('resource-free-key', tr('● Свободно', '● Free')); set('resource-cpu-name', 'CPU'); set('resource-ram-name', 'RAM');
    $('disk-refresh').title = tr('Обнови размера на архива', 'Refresh archive size'); $('disk-refresh').setAttribute('aria-label', $('disk-refresh').title);
    renderUsage(); renderDisk(); renderPending(); renderSummary(summaryReading && !summary); renderAudit();
  }
  window.AstroArchiveMonitoring = {
    connect(value) { api = value; renderAudit(); if (api.desktop) { checkPending(true); timer = setInterval(() => { resources(); if (document.hidden) return; checkPending(); if (summary?.building && !summaryReading) loadSummary(); if (selection?.enabled && Date.now() - diskTime > (disk?.reading ? 2000 : 15000)) readDisk(); }, 1500); } },
    select(value, refresh = false) {
      const key = JSON.stringify([value.archiveId, value.root, value.relativePath, value.enabled, value.summaryEnabled]), changed = key !== summaryKey;
      const archiveChanged = value.archiveId !== selection?.archiveId || value.root !== selection?.root || value.enabled !== selection?.enabled;
      selection = value;
      if (changed || refresh) { summaryKey = key; loadSummary(changed); }
      if (archiveChanged || refresh) { disk = null; diskTime = 0; renderDisk(); readDisk(refresh); }
    },
    diagnosticsVisible(value) { diagnostics = value; if (value) { resources(); if (!auditStatusLoaded) loadAudit(); } },
    updateActivity(snapshot) {
      const previous = auditTask;
      archiveWorking = !!snapshot?.tasks?.some(task => task.section === 'archive' && task.state === 'running');
      auditTask = snapshot?.tasks?.find(task => task.section === 'archive' && task.state === 'running' && task.title === 'Внимателна проверка на целия архив') || null;
      renderAudit(); renderSummary(summaryReading && !summary);
      if (previous && !auditTask && !auditBusy) loadAudit();
    },
    reloadSummary: () => loadSummary(),
    checkPending, translate, theme
  };
  $('disk-refresh').onclick = () => readDisk(true);
  $('archive-audit-run').onclick = runAudit;
  $('archive-audit-stop').onclick = async () => { if (auditTask) await api.call('activity.stop', {taskId: auditTask.id, abandon: false}).catch(error => { $('archive-audit-status').title = error.message; }); };
  $('archive-audit-open').onclick = async () => { await api.call('archive.audit.open').catch(error => { $('archive-audit-status').title = error.message; }); };
  $('pending-archive-open').onclick = async () => { try { await api.call('archive.pending.open'); } catch (error) { $('pending-archive-path').textContent = error.message; } };
  window.addEventListener('pagehide', () => clearInterval(timer)); translate();
})();
