"use strict";
// Click a column heading to sort the visible archive rows; click again to reverse.
// Sorting is applied to the rows of the current page and kept while browsing folders.
(() => {
  const state = { key: null, direction: 1 };
  const collator = () => new Intl.Collator(window.AstroArchiveI18n?.locale || 'bg', { numeric: true, sensitivity: 'base' });
  const time = text => { const value = text ? Date.parse(text) : NaN; return Number.isFinite(value) ? value : null; };

  function value(entry, key, columns, folder) {
    const d = entry.details || {};
    switch (key) {
      case 'name': return entry.displayName ?? entry.name ?? '';
      case 'size': return entry.bytes ?? null;
      case 'frames': case 'files': case 'stacks': return d[key] ?? null;
      case 'exposure': case 'totalExposure': return d.totalExposure ?? d.exposure ?? null;
      case 'date': case 'modified':
        return window.AstroArchiveArchiveRows.dateKind(entry, folder) === 'observation' ? time(d.firstObservation) : time(entry.modified);
      case 'observed': return time(entry.info?.observed);
      case 'created': return time(d.created);
      default: {
        const text = window.AstroArchiveArchiveRows.cell(entry, key, columns, folder);
        return text === '—' ? null : String(text);
      }
    }
  }

  function apply(entries, columns, folder) {
    if (!state.key || !columns.some(c => c.key === state.key)) return entries;
    const compare = collator().compare, keyed = entries.map((entry, index) => ({ entry, index, v: value(entry, state.key, columns, folder) }));
    keyed.sort((a, b) => {
      if (a.v == null || b.v == null) return a.v == null && b.v == null ? a.index - b.index : a.v == null ? 1 : -1; // empty values stay last
      const order = typeof a.v === 'number' && typeof b.v === 'number' ? a.v - b.v : compare(String(a.v), String(b.v));
      return order * state.direction || a.index - b.index;
    });
    return keyed.map(item => item.entry);
  }

  function heading(column, title, redraw) {
    const th = document.createElement('th'), button = document.createElement('button'), arrow = document.createElement('span');
    th.scope = 'col'; th.className = 'sortable'; button.type = 'button';
    const active = state.key === column.key;
    arrow.className = 'sort-arrow'; arrow.setAttribute('aria-hidden', 'true'); arrow.textContent = active && state.direction < 0 ? '▼' : '▲';
    if (active) th.setAttribute('aria-sort', state.direction > 0 ? 'ascending' : 'descending');
    const en = window.AstroArchiveI18n?.locale?.startsWith('en');
    button.title = en ? 'Sort by this column; click again to reverse' : 'Подреди по тази колона; натисни пак за обратен ред';
    button.append(document.createTextNode(title), arrow);
    button.onclick = () => { if (state.key === column.key) state.direction *= -1; else { state.key = column.key; state.direction = 1; } redraw(); };
    th.append(button);
    return th;
  }

  window.AstroArchiveArchiveSort = { apply, heading, state };
})();
