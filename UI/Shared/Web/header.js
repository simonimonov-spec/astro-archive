"use strict";
// Presentation only. The original activity control keeps its listeners and state.
(() => {
  const $ = id => document.getElementById(id), root = document.documentElement;
  const header = document.querySelector('.topbar'), wheel = $('activity-button');
  if (!header || !wheel) return;
  const storageKey = 'astroarchive.header.collapsed';
  const tr = (bg, en) => window.AstroArchiveI18n.locale.startsWith('en') ? en : bg;
  const node = (tag, className) => { const e = document.createElement(tag); if (className) e.className = className; return e; };
  const svg = paths => {
    const e = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); e.setAttribute('viewBox', '0 0 24 24'); e.setAttribute('aria-hidden', 'true');
    for (const d of paths) { const p = document.createElementNS(e.namespaceURI, 'path'); p.setAttribute('d', d); e.append(p); } return e;
  };
  const shell = node('div', 'header-shell'); shell.id = 'top-menu'; header.before(shell); shell.append(header);
  const home = node('span', 'header-activity-home'); wheel.before(home); home.append(wheel);
  const actions = node('div', 'header-actions'), settings = $('settings-open');
  const settingsText = node('span', 'header-settings-label');
  // Keep the translated settings label as a text node; i18n can replace it later.
  settingsText.textContent = settings.firstChild.textContent.trim(); settings.firstChild.replaceWith(settingsText);
  settings.prepend(svg(['M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z', 'M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z']));
  settings.setAttribute('aria-label', settingsText.textContent);
  const toggle = node('button', 'icon-button header-toggle'); toggle.id = 'header-collapse'; toggle.type = 'button';
  toggle.setAttribute('aria-controls', shell.id); toggle.setAttribute('aria-expanded', 'true'); toggle.append(svg(['M5 14l7-7 7 7', 'M5 20h14']));
  header.append(actions); actions.append(settings, toggle);

  const resources = node('details', 'header-resources'); resources.id = 'header-resources';
  const summary = node('summary'), resourceCopy = node('span', 'header-resource-copy'), resourceLabel = node('strong'), free = node('span');
  resourceLabel.id = 'header-resource-label'; free.id = 'header-resource-free'; resourceCopy.append(resourceLabel, free);
  const arrow = svg(['m7 10 5 5 5-5']); arrow.classList.add('header-resource-chevron');
  summary.append(svg(['M4 4h16v16H4Z', 'M8 8h8M8 12h5M8 16h2']), resourceCopy, arrow);
  const panel = node('div', 'header-resource-panel'), monitor = $('resource-monitor'); monitor.before(resources); resources.append(summary, panel); panel.append(monitor);
  const docks = new Map();
  for (const page of document.querySelectorAll('main > .page')) {
    const title = page.querySelector('.page-heading h1'); if (!title) continue;
    const line = node('div', 'workspace-title-line'), slot = node('span', 'workspace-activity-slot'); slot.dataset.headerDock = page.id;
    title.before(line); line.append(slot, title); docks.set(page, slot);
  }
  const visibleDock = () => [...docks].find(([page]) => !page.hidden)?.[1] || home;
  const motion = () => root.dataset.animations !== 'off' && !matchMedia('(prefers-reduced-motion: reduce)').matches;
  let collapsed = false, flight = null, animations = [], positioning = 0;
  function text(e, value) { if (e.textContent !== value) e.textContent = value; }
  function updateLabels() {
    // The existing translator replaces this button's contents; restore only its presentation.
    if (!settings.contains(settingsText)) {
      settings.replaceChildren(svg(['M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z', 'M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z']), settingsText);
    }
    text(settingsText, tr('Настройки', 'Settings'));
    const label = tr('Прибери горното меню', 'Collapse the top menu'); toggle.title = label; toggle.setAttribute('aria-label', label);
    const caption = $('activity-caption').textContent;
    if (collapsed) {
      const restore = tr('Покажи горното меню', 'Show the top menu');
      wheel.title = restore + ' · ' + caption;
      wheel.setAttribute('aria-label', restore + '. ' + caption);
      wheel.setAttribute('aria-controls', shell.id); wheel.setAttribute('aria-expanded', 'false'); wheel.removeAttribute('aria-haspopup');
    } else {
      wheel.setAttribute('aria-controls', 'activity-popover'); wheel.setAttribute('aria-haspopup', 'dialog');
      wheel.setAttribute('aria-label', window.AstroArchiveI18n.t('activity.status', {status: caption}));
      wheel.title = caption + ' · ' + window.AstroArchiveI18n.t('activity.openDiary');
    }
    settings.setAttribute('aria-label', tr('Настройки', 'Settings')); settings.title = tr('Настройки', 'Settings');
    text(resourceLabel, tr('Диск и връзка', 'Disk and connection'));
    text(free, $('header-disk-free')?.textContent || tr('Състояние на системата', 'System status'));
    summary.title = tr('Диск, връзка и добавки', 'Disk, connection and plugins'); summary.setAttribute('aria-label', summary.title);
  }
  function position() {
    positioning = 0;
    const b = wheel.getBoundingClientRect(), left = Math.max(12, Math.min(b.left, innerWidth - Math.min(630, innerWidth - 40) - 12));
    root.style.setProperty('--activity-popover-left', left + 'px');
    root.style.setProperty('--activity-popover-top', Math.max(12, b.bottom + 12) + 'px');
    root.style.setProperty('--activity-popover-arrow', Math.max(12, b.left + b.width / 2 - left - 7) + 'px');
    root.style.setProperty('--header-resource-top', Math.max(12, summary.getBoundingClientRect().bottom + 10) + 'px');
  }
  const schedulePosition = () => { if (!positioning) positioning = requestAnimationFrame(position); };
  function settle() {
    const finished = flight; flight = null;
    for (const animation of animations) animation.cancel(); animations = [];
    (collapsed ? visibleDock() : home).append(wheel); wheel.classList.remove('header-activity-flying'); wheel.style.removeProperty('transform');
    shell.classList.remove('is-moving'); shell.hidden = collapsed; header.inert = collapsed;
    updateLabels(); schedulePosition(); window.AstroArchiveControls?.refresh();
    if (finished) window.dispatchEvent(new CustomEvent('astroarchive-header-settled', {detail: {collapsed}}));
  }
  function setCollapsed(value, animate = true, remember = true) {
    value = !!value;
    if (value === collapsed && !flight) return;
    const before = wheel.getBoundingClientRect(), height = shell.getBoundingClientRect().height;
    settle();
    const focused = document.activeElement === toggle || document.activeElement === wheel;
    window.AstroArchiveActivity.close(false); resources.open = false;
    collapsed = value;
    if (remember) { try { localStorage.setItem(storageKey, collapsed ? '1' : '0'); } catch { /* Layout remains usable when storage is blocked. */ } }
    shell.hidden = false; root.dataset.headerCollapsed = String(collapsed);
    // First measure the destination in its final layout, then animate the real wheel above it.
    shell.hidden = collapsed; header.inert = collapsed;
    const target = collapsed ? visibleDock() : home;
    const after = target.getBoundingClientRect(), endHeight = collapsed ? 0 : header.getBoundingClientRect().height;
    if (!animate || !motion()) { settle(); if (focused) wheel.focus({preventScroll: true}); return; }
    shell.hidden = false; header.inert = collapsed; shell.classList.add('is-moving');
    document.body.append(wheel); wheel.classList.add('header-activity-flying'); updateLabels();
    const endX = after.left + (after.width - 44) / 2, endY = after.top + (after.height - 44) / 2;
    const from = `translate(${before.left}px, ${before.top}px)`, to = `translate(${endX}px, ${endY}px)`;
    wheel.style.transform = to;
    const timing = {duration: 380, easing: 'cubic-bezier(.22,.8,.22,1)'};
    const layoutAnimation = shell.animate([{height: height + 'px'}, {height: endHeight + 'px'}], timing);
    const wheelAnimation = wheel.animate([{transform: from}, {transform: to}], timing);
    const heading = [...docks].find(([page]) => !page.hidden)?.[0].querySelector('.page-heading h1');
    animations = [layoutAnimation, wheelAnimation];
    if (heading) animations.push(heading.animate([{transform: `translateX(${collapsed ? -54 : 54}px)`}, {transform: 'translateX(0)'}], timing));
    const current = {}; flight = current;
    Promise.all(animations.map(a => a.finished.catch(() => {}))).then(() => { if (flight === current) settle(); });
    if (focused) wheel.focus({preventScroll: true});
  }
  toggle.addEventListener('click', () => setCollapsed(true));
  wheel.addEventListener('click', event => { if (collapsed) { event.preventDefault(); event.stopImmediatePropagation(); setCollapsed(false); } }, true);
  wheel.addEventListener('mouseenter', event => { if (collapsed) event.stopImmediatePropagation(); }, true);
  resources.addEventListener('toggle', schedulePosition);
  document.addEventListener('pointerdown', event => { if (resources.open && !resources.contains(event.target)) resources.open = false; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && resources.open) { resources.open = false; summary.focus(); event.preventDefault(); } });
  const routeChanged = () => { if (flight) settle(); if (collapsed) { visibleDock().append(wheel); updateLabels(); } schedulePosition(); };
  for (const page of docks.keys()) new MutationObserver(routeChanged).observe(page, {attributes: true, attributeFilter: ['hidden']});
  new MutationObserver(() => { updateLabels(); schedulePosition(); }).observe($('activity-caption'), {childList: true, characterData: true, subtree: true});
  new MutationObserver(updateLabels).observe(wheel, {attributes: true, attributeFilter: ['data-state']});
  new MutationObserver(updateLabels).observe($('header-disk-free'), {childList: true, characterData: true, subtree: true});
  new MutationObserver(updateLabels).observe(settings, {childList: true});
  new MutationObserver(() => { if (!motion() && flight) settle(); updateLabels(); }).observe(root, {attributes: true, attributeFilter: ['lang', 'data-animations']});
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => { if (!motion() && flight) settle(); });
  window.addEventListener('resize', () => { if (flight) settle(); schedulePosition(); });
  window.addEventListener('scroll', schedulePosition, {passive: true});
  window.addEventListener('astroarchive-remote-connection', event => { resources.dataset.tone = event.detail.state === 'offline' ? 'error' : ['slow', 'fair', 'retrying'].includes(event.detail.state) ? 'warning' : 'normal'; });
  window.addEventListener('storage', event => { if (event.key === storageKey) setCollapsed(event.newValue === '1', true, false); });
  updateLabels(); schedulePosition();
  try { if (localStorage.getItem(storageKey) === '1') setCollapsed(true, false, false); } catch { /* No storage is required to collapse or restore. */ }
  window.AstroArchiveHeader = {setCollapsed, get collapsed() { return collapsed; }};
})();
