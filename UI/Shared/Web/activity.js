"use strict";
(() => {
  const $ = id => document.getElementById(id), t = (key, values) => window.AstroArchiveI18n.t(key, values);
  const channels = ["archive", "analysis", "preparation"], diaries = { archive: [], analysis: [], preparation: [] };
  const DONE_VISIBLE_MS = 30000, tierOrder = { important: 0, normal: 1, ambient: 2 };
  let actions, tasks = [], ambientTasks = new Map(), taskStates = new Map(), selected = "archive", selectedTaskId = "", notice = null, lastNotice = 0, doneNoticeId = 0, doneUntil = 0, doneTimer, blockedId = "", mode = "log", fadeTimer, hideTimer, closeTimer, restartPending = false;
  let clock = new Intl.DateTimeFormat("bg", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  function text(id, value) { const target = $(id); if (target.textContent !== value) target.textContent = value; }
  function tierRank(task) { return tierOrder[task?.tier] ?? 1; }
  function liveTasks() { return [...tasks, ...ambientTasks.values()]; }
  function activeTasks() { return liveTasks().filter(task => task.state === "running" || task.state === "blocked"); }
  function primaryTask() {
    const active = activeTasks();
    const blocked = active.filter(task => task.state === "blocked").sort((a, b) => (b.started || 0) - (a.started || 0));
    if (blocked.length) return blocked[0];
    return active.filter(task => task.state === "running").sort((a, b) => tierRank(a) - tierRank(b) || (a.started || 0) - (b.started || 0))[0] || null;
  }
  function currentTask() {
    if (selectedTaskId) {
      const chosen = liveTasks().find(task => task.id === selectedTaskId);
      if (chosen && (chosen.state === "running" || chosen.state === "blocked")) return chosen;
    }
    return primaryTask() || liveTasks().find(task => task.section === selected) || liveTasks().at(-1);
  }
  function phaseOf(task) { return task?.phaseKey ? t(task.phaseKey) : task?.phase || t("activity.working"); }
  function scheduleDoneClear() {
    clearTimeout(doneTimer);
    const left = doneUntil - Date.now();
    if (left <= 0) return;
    doneTimer = setTimeout(() => { doneUntil = 0; renderIndicator(); }, left);
  }
  function noteCompletions(nextTasks) {
    for (const task of nextTasks) {
      const before = taskStates.get(task.id);
      if (before === "running" && task.state === "completed" && task.tier !== "ambient") {
        doneUntil = Date.now() + DONE_VISIBLE_MS; scheduleDoneClear();
      }
      taskStates.set(task.id, task.state);
    }
  }
  function stopFade() { clearTimeout(fadeTimer); clearTimeout(hideTimer); $("activity-popover").classList.remove("is-fading"); }
  function startFade() {
    stopFade();
    if (mode !== "notice") return;
    fadeTimer = setTimeout(() => {
      $("activity-popover").classList.add("is-fading");
      hideTimer = setTimeout(() => close(false), 1800);
    }, 8500);
  }
  function choose(section) {
    if (!channels.includes(section)) return;
    selected = section;
    for (const radio of document.querySelectorAll("[data-log-section]")) radio.checked = radio.value === section;
    renderLogs(); renderBody();
  }
  function open(section = selected, nextMode = "log") {
    choose(section); mode = nextMode;
    clearTimeout(closeTimer); $("activity-popover").classList.remove("is-closing");
    $("activity-popover").hidden = false; $("activity-button").setAttribute("aria-expanded", "true");
    stopFade(); renderBody(); renderLogs(); actions?.overlayChanged();
    if (mode === "notice") startFade();
  }
  function close(dismiss = false) {
    const faded = $("activity-popover").classList.contains("is-fading");
    stopFade();
    if (dismiss && mode === "notice" && notice) {
      actions?.call("activity.dismiss", { notificationId: notice.id }).catch(error => add([{ section: selected, time: new Date().toISOString(), category: "УВЕДОМЛЕНИЯ", level: "warning", message: error.message }]));
      notice = null;
    }
    const hide = () => { $("activity-popover").hidden = true; $("activity-popover").classList.remove("is-closing"); $("activity-button").setAttribute("aria-expanded", "false"); actions?.overlayChanged(); };
    clearTimeout(closeTimer);
    if (faded || $("activity-popover").hidden) hide();
    else { $("activity-popover").classList.add("is-closing"); closeTimer = setTimeout(hide, 150); }
  }
  function logsVisible() { return !$("activity-popover").hidden && mode === "log" || $("settings-dialog").open && !$("settings-panel-diagnostics").hidden; }
  function renderLogs() {
    if (!logsVisible()) return;
    for (const id of ["activity-log-entries", "diagnostics-entries"]) {
      const list = $(id), follow = list.scrollHeight - list.scrollTop - list.clientHeight < 50;
      const fragment = document.createDocumentFragment();
      for (const entry of diaries[selected]) {
        const row = document.createElement("li"); row.className = "log-" + (["error", "warning"].includes(entry.level) ? entry.level : "info");
        const stamp = document.createElement("time"), category = document.createElement("strong"), message = document.createElement("span");
        const date = new Date(entry.time); stamp.textContent = Number.isNaN(date.valueOf()) ? "" : clock.format(date);
        category.textContent = entry.category; message.textContent = entry.message; row.append(stamp, category, message); fragment.append(row);
      }
      if (!diaries[selected].length) { const empty = document.createElement("li"); empty.className = "empty-diary"; empty.textContent = t("diagnostics.empty"); fragment.append(empty); }
      list.replaceChildren(fragment); if (follow) list.scrollTop = list.scrollHeight;
    }
  }
  function add(entries) {
    let changed = false;
    for (const entry of entries) {
      const section = channels.includes(entry.section) ? entry.section : "archive";
      diaries[section].push(entry); if (diaries[section].length > 200) diaries[section].shift();
      if (section === selected) changed = true;
    }
    if (changed) renderLogs();
  }
  function ring(id, radius, percent) {
    const length = 2 * Math.PI * radius, target = $(id);
    target.style.strokeDasharray = percent == null ? `${length * .2} ${length * .8}` : String(length);
    target.style.strokeDashoffset = percent == null ? "0" : String(length * (1 - Math.max(0, Math.min(100, percent)) / 100));
  }
  function renderIndicator() {
    const all = liveTasks(), failed = all.find(task => task.state === "blocked");
    const running = all.filter(task => task.state === "running"), task = failed || primaryTask();
    const done = !failed && !running.length && Date.now() < doneUntil;
    const state = failed ? "blocked" : running.length ? "running" : done ? "done" : "idle";
    const point = $("activity-button"); point.dataset.state = state; point.dataset.tier = task?.tier || "";
    point.dataset.multistep = String(!!task && task.steps > 1);
    const indeterminate = state === "running" && (task?.percent == null || task?.tier === "ambient");
    point.dataset.indeterminate = String(indeterminate);
    ring("activity-step-ring", 24, state === "running" || state === "blocked" ? (indeterminate ? null : task.percent) : 100);
    ring("activity-total-ring", 31, indeterminate ? null : task?.overallPercent);
    const caption = state === "idle" ? t("activity.idle") : state === "done" ? t("activity.done") : state === "blocked" ? t("activity.blocked") : phaseOf(task);
    text("activity-caption", caption);
    point.setAttribute("aria-label", t("activity.status", { status: caption }));
    point.title = caption + " · " + t("activity.openDiary");
  }
  function renderTaskList(queue) {
    const list = $("activity-task-list"), current = currentTask()?.id, fragment = document.createDocumentFragment();
    for (const item of queue) {
      const row = document.createElement("li"), button = document.createElement("button");
      button.type = "button"; button.className = "activity-task-row"; button.dataset.taskId = item.id;
      button.dataset.state = item.state; button.dataset.tier = item.tier || "normal";
      if (item.id === current) button.setAttribute("aria-current", "true");
      const dot = document.createElement("span"); dot.className = "activity-task-dot"; dot.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span"); copy.className = "activity-task-copy";
      const name = document.createElement("span"); name.className = "activity-task-name"; name.textContent = item.title || t("activity.working");
      const tier = document.createElement("span"); tier.className = "activity-task-tier"; tier.textContent = t("activity.tier." + (item.tier || "normal"));
      const meta = document.createElement("span"); meta.className = "activity-task-meta";
      meta.textContent = item.state === "blocked" ? t("activity.blocked") : item.percent == null ? t("activity.working") : item.percent + "%";
      copy.append(name, tier); button.append(dot, copy, meta);
      button.addEventListener("click", () => { selectedTaskId = item.id; renderBody(); });
      row.append(button); fragment.append(row);
    }
    list.replaceChildren(fragment);
  }
  function renderBody() {
    const task = currentTask(), completion = mode === "notice" && notice;
    const queue = activeTasks().sort((a, b) => (a.state === "blocked" ? -1 : b.state === "blocked" ? 1 : 0) || tierRank(a) - tierRank(b) || (a.started || 0) - (b.started || 0));
    $("activity-task").hidden = !!completion; $("activity-completion").hidden = !completion;
    $("activity-diary").hidden = !!completion; $("activity-controls").hidden = !!completion;
    $("activity-show-diary").hidden = !completion;
    $("activity-queue").hidden = !!completion || queue.length < 2;
    if (!completion && queue.length > 1) renderTaskList(queue);
    else $("activity-task-list").replaceChildren();
    text("activity-popover-kind", completion ? t(notice.kind === "cancelled" ? "activity.cancelled" : "activity.done") : t("nav." + (task?.section || selected)));
    text("activity-popover-title", completion ? notice.title : task?.title || t("activity.openDiary"));
    if (completion) {
      text("activity-completion-summary", notice.summary);
      const items = notice.details.map(detail => { const item = document.createElement("li"); item.textContent = detail; return item; });
      $("activity-completion-details").replaceChildren(...items); return;
    }
    text("activity-phase", task ? phaseOf(task) : t("activity.noTask"));
    const progress = $("activity-progress"); progress.hidden = !task || task.state === "idle";
    if (task?.percent == null) progress.removeAttribute("value"); else progress.value = task.percent;
    text("activity-percent", task?.percent == null ? task?.state === "running" ? t("activity.working") : "" : task.percent + "%");
    $("activity-overall").hidden = !task || task.steps <= 1;
    if (task?.overallPercent == null) $("activity-overall-progress").removeAttribute("value"); else $("activity-overall-progress").value = task.overallPercent;
    text("activity-overall-percent", task?.overallPercent == null ? "—" : task.overallPercent + "% " + t("activity.overall"));
    const seconds = task?.elapsedSeconds || (task?.started ? Math.max(0, Math.floor(Date.now() / 1000 - task.started)) : 0), elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    text("activity-time", task ? t("activity.elapsed", { elapsed }) + (task.steps > 1 ? " · " + t("activity.step", { step: task.step, total: task.steps }) : "") : "");
    text("activity-detail", task?.detail || ""); text("activity-problem", task?.problem || ""); $("activity-problem").hidden = !task?.problem;
    const blocked = task?.state === "blocked", ambient = task?.tier === "ambient";
    $("activity-stop").hidden = !!blocked || ambient; $("activity-stop").disabled = !task?.canStop;
    $("activity-abandon").hidden = $("activity-restart").hidden = !blocked;
    $("activity-restart").disabled = tasks.some(item => item.state === "running");
  }
  function update(snapshot) {
    const nextTasks = snapshot?.tasks || [];
    noteCompletions(nextTasks); tasks = nextTasks;
    if (snapshot?.notification) {
      notice = snapshot.notification;
      if (notice.id !== doneNoticeId) {
        doneNoticeId = notice.id;
        if (notice.kind === "completed") { doneUntil = Date.now() + DONE_VISIBLE_MS; scheduleDoneClear(); }
      }
    } else { notice = null; doneNoticeId = 0; }
    const failed = liveTasks().find(task => task.state === "blocked");
    renderIndicator(); renderBody();
    if (failed && failed.id !== blockedId) {
      blockedId = failed.id;
      if (window.AstroArchivePreferences?.isOpen) { choose(failed.section); window.AstroArchivePreferences.showTab("diagnostics"); }
      else open(failed.section, "log");
      $("announcements").textContent = t("activity.blocked") + ": " + failed.problem;
    }
    else if (!failed && snapshot?.notification && snapshot.notification.id !== lastNotice) {
      lastNotice = snapshot.notification.id; open(snapshot.notification.section, "notice"); $("announcements").textContent = snapshot.notification.summary;
    }
  }
  async function command(method, parameters) {
    if (!actions?.desktop) return;
    try { await actions.call(method, parameters); }
    catch (error) { add([{ section: selected, time: new Date().toISOString(), category: t("activity.control"), level: "error", message: error.message }]); }
  }
  function restartNotice(message) {
    window.AstroArchiveFunctions.modal(t("activity.restart"), body => body.append(window.AstroArchiveFunctions.node('p', message)));
  }
  async function restart() {
    if (restartPending) return;
    if (!actions?.desktop || window.AstroArchiveRemote) { restartNotice(t('diagnostics.restartLocal')); return; }
    if (tasks.some(task => task.state === 'running')) { restartNotice(t('diagnostics.restartBusy')); return; }
    restartPending = true;
    let accepted = false;
    try {
      if (!await window.AstroArchiveFunctions.confirm(t('activity.restart'), t('diagnostics.restartConfirm'), t('activity.restart'))) return;
      if (tasks.some(task => task.state === 'running')) { restartNotice(t('diagnostics.restartBusy')); return; }
      const result = await actions.call('python.app.restart', {confirmed:true});
      if (result?.accepted !== true) throw new Error(t('diagnostics.restartFailed'));
      accepted = true; restartNotice(t('diagnostics.restarting'));
      setTimeout(() => { try { window.close(); } catch (_) { /* The new process owns reopening the application. */ } }, 1200);
    } catch (error) { restartNotice(error?.message || t('diagnostics.restartFailed')); }
    finally { if (!accepted) restartPending = false; }
  }
  $("activity-button").addEventListener("click", () => {
    const active = liveTasks().find(task => task.state === "blocked") || primaryTask();
    if ($("activity-popover").hidden) open(active?.section || notice?.section || actions?.route() || selected, notice && !active ? "notice" : "log");
    else close(false);
  });
  $("activity-button").addEventListener("mouseenter", () => { if (notice && $("activity-popover").hidden && !$("settings-dialog").open && !activeTasks().length) open(notice.section, "notice"); });
  $("activity-close").addEventListener("click", () => close(true));
  $("activity-popover").addEventListener("mouseenter", stopFade); $("activity-popover").addEventListener("mouseleave", startFade);
  $("activity-stop").addEventListener("click", () => { const task = currentTask(); if (task) command("activity.stop", { taskId: task.id, abandon: false }); });
  $("activity-abandon").addEventListener("click", () => { const task = currentTask(); if (task) command("activity.stop", { taskId: task.id, abandon: true }); });
  $("activity-restart").addEventListener("click", restart);
  $("diagnostics-restart").addEventListener("click", restart);
  $("activity-show-diary").addEventListener("click", () => open(notice?.section || selected, "log"));
  $("activity-open-diagnostics").addEventListener("click", async () => { close(false); await actions?.openDiagnostics(); renderLogs(); });
  for (const radio of document.querySelectorAll("[data-log-section]")) radio.addEventListener("change", () => choose(radio.value));
  $("diagnostics-clear").addEventListener("click", () => { diaries[selected] = []; renderLogs(); });
  $("diagnostics-folder").addEventListener("click", () => command("debug.logs.folder", {}));
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !$("activity-popover").hidden) { event.preventDefault(); close(false); } });
  window.AstroArchiveActivity = {
    connect(value) { actions = value; }, update, add, close,
    ambient(id, task) {
      const key = String(id);
      if (task) ambientTasks.set(key, { id: "ambient:" + key, section: task.section || "archive", title: task.title || t("activity.loadingImage"), phaseKey: task.phaseKey || "activity.loadingImage", state: "running", tier: "ambient", percent: null, overallPercent: null, step: 1, steps: 1, started: Date.now() / 1000, canStop: false });
      else ambientTasks.delete(key);
      renderIndicator(); renderBody();
    },
    languageChanged() { clock = new Intl.DateTimeFormat(window.AstroArchiveI18n.locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" }); renderIndicator(); renderBody(); renderLogs(); },
    showDiagnostics() { renderLogs(); },
    get isOpen() { return !$("activity-popover").hidden; }
  };
})();
