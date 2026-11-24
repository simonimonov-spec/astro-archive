/* Beta 1.3 UI (в разработка, не е издание) · виж docs/BETA-1.3-UI.md */
"use strict";
// Keyboard shortcuts: Ctrl+1/2/3 switch workspaces, Ctrl+, opens Settings.
(() => {
  const routes = { '1': 'archive', '2': 'analysis', '3': 'preparation' };
  const en = () => window.AstroArchiveI18n?.locale?.startsWith('en');
  function hints() {
    for (const [digit, route] of Object.entries(routes)) {
      const button = document.querySelector(`.navigation [data-route="${route}"]`);
      if (button) { button.title = `${button.textContent.trim()} (Ctrl+${digit})`; button.setAttribute('aria-keyshortcuts', `Control+${digit}`); }
    }
    const settings = document.getElementById('settings-open');
    if (settings) { settings.title = (en() ? 'Settings' : 'Настройки') + ' (Ctrl+,)'; settings.setAttribute('aria-keyshortcuts', 'Control+Comma'); }
  }
  document.addEventListener('keydown', event => {
    if (!event.ctrlKey || event.altKey || event.shiftKey || event.metaKey || event.repeat) return;
    if (document.querySelector('dialog[open]')) return;
    const route = routes[event.key];
    const target = route ? document.querySelector(`.navigation [data-route="${route}"]`) : event.key === ',' ? document.getElementById('settings-open') : null;
    if (!target || target.disabled || target.hidden) return;
    event.preventDefault();
    target.click();
  });
  hints();
  window.addEventListener('astroarchive-language', hints);
  setTimeout(hints, 1500);
})();
