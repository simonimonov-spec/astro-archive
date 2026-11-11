"use strict";
(() => {
  let api;
  const $ = id => document.getElementById(id);
  const tr = (bg, en) => window.AstroArchiveI18n.locale.startsWith('en') ? (en || bg) : bg;
  const number = (value, digits = 3) => typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat(window.AstroArchiveI18n.locale, { maximumFractionDigits: digits }).format(value) : '—';
  const time = seconds => seconds == null ? '—' : `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds % 3600 / 60)).padStart(2, '0')}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  function node(tag, text, className) { const element = document.createElement(tag); if (text != null) element.textContent = text; if (className) element.className = className; return element; }
  function options(select, values, selected) {
    select.replaceChildren(...values.map(item => { const option = node('option', select.id.endsWith('-telescope') ? window.AstroArchiveCustomization?.telescope(item.name) || item.name : item.name); option.dataset.originalLabel=item.name; option.value = String(item.id); return option; }));
    if (selected != null && values.some(item => String(item.id) === String(selected))) select.value = String(selected);
  }
  function table(container, columns, rows, onSelect, selected, pageSize = 100) {
    columns = window.AstroArchiveCustomization?.tableColumns(container, columns) || columns;
    const start = Math.min(container._offset || 0, Math.max(0, Math.floor((rows.length - 1) / pageSize) * pageSize)); container._offset = start;
    const wrap = node('div', null, 'function-table-scroll'), htmlTable = node('table'), head = node('tr'), body = node('tbody');
    columns.forEach(column => {const cell=node('th', column.title);if(column.key)cell.dataset.column=column.key;head.append(cell);});
    const thead = node('thead'); thead.append(head); htmlTable.append(thead, body);
    for (const row of rows.slice(start, start + pageSize)) {
      const line = node('tr'); line.tabIndex = 0; line.dataset.id = row.id; line.dataset.state = row.state || '';
      if (String(row.id) === String(selected)) line.setAttribute('aria-selected', 'true');
      columns.forEach(column => {
        const cell = node('td'); const value = column.value(row);
        if (value instanceof Node) cell.append(value); else cell.textContent = value == null ? '—' : String(value);
        if (column.className) cell.className = column.className; line.append(cell);
      });
      if (onSelect) {
        const choose = () => { for (const other of body.children) other.removeAttribute('aria-selected'); line.setAttribute('aria-selected', 'true'); onSelect(row); };
        line.addEventListener('click', choose); line.addEventListener('keydown', e => { if (e.key === 'Enter') choose(); });
      }
      body.append(line);
    }
    wrap.append(htmlTable); container.replaceChildren(wrap);
    if (!rows.length) container.append(node('p', tr('Няма данни за този избор.', 'No data for this selection.'), 'function-empty'));
    const foot = node('div', null, 'function-table-footer'); foot.append(node('span', `${number(rows.length, 0)} ${tr('записа', 'entries')}`));
    if (rows.length > pageSize) {
      for (const [caption, shift] of [['←', -pageSize], ['→', pageSize]]) {
        const button = node('button', caption, 'icon-button'); button.type = 'button'; button.disabled = shift < 0 ? start === 0 : start + pageSize >= rows.length;
        window.AstroArchiveIcons.mount(button,shift<0?'previous':'next',shift<0?tr('Предишна страница','Previous page'):tr('Следваща страница','Next page'));
        button.onclick = () => { container._offset = start + shift; tableRender(); }; foot.append(button);
      }
    }
    function tableRender() { table(container, columns, rows, onSelect, selected, pageSize); }
    container.append(foot);
  }
  const loading = new Map();
  async function read(section, action) {
    const count = (loading.get(section) || 0) + 1; loading.set(section, count);
    const indicator = $(`${section}-loader`); const timer = setTimeout(() => { if (loading.get(section)) { indicator.hidden = false; indicator.setAttribute('aria-busy', 'true'); } }, 120);
    try { return await action(); }
    finally { clearTimeout(timer); loading.set(section, Math.max(0, (loading.get(section) || 1) - 1)); if (!loading.get(section)) { indicator.hidden = true; indicator.removeAttribute('aria-busy'); } }
  }
  function error(section, exception) {
    if (exception?.cancelled) return;
    const banner = $(section==='archive'?'error-banner':`${section}-problem`); banner.textContent = exception.message || String(exception); banner.hidden = false;
    api?.log(banner.textContent, 'error', section);
  }
  async function run(section, action, controls = []) {
    $(`${section}-problem`).hidden = true;
    controls.forEach(control => control.disabled = true);
    try { return await action(); } catch (exception) { error(section, exception); return null; }
    finally { controls.forEach(control => control.disabled = false); }
  }
  function modal(title, build) {
    const dialog = node('dialog', null, 'function-dialog'), heading = node('div', null, 'panel-heading'); heading.append(node('h2', title));
    const close = node('button', '×', 'icon-button'); close.type = 'button'; close.setAttribute('aria-label', tr('Затвори', 'Close'));
    window.AstroArchiveIcons.mount(close,'close',tr('Затвори','Close'));
    heading.append(close); const body = node('div', null, 'function-dialog-body'); dialog.append(heading, body); document.body.append(dialog);
    close.onclick = () => dialog.close(); dialog.addEventListener('close', () => dialog.remove()); build(body, dialog); dialog.showModal(); return dialog;
  }
  function confirm(title, description, buttonText) {
    return new Promise(resolve => {
      let answer = false;
      const dialog = modal(title, (body, box) => {
        body.append(node('p', description)); const actions = node('div', null, 'function-actions');
        const cancel = node('button', tr('Отказ', 'Cancel'), 'quiet-button'), accept = node('button', buttonText || tr('Потвърди', 'Confirm'), 'primary-button');
        cancel.onclick = () => box.close(); accept.onclick = () => { answer = true; box.close(); }; actions.append(cancel, accept); body.append(actions);
      });
      dialog.addEventListener('close', () => resolve(answer));
    });
  }
  window.AstroArchiveFunctions = {
    $, tr, number, time, node, options, table, read, run, error, modal, confirm,
    call: (method, parameters) => api.call(method, parameters),
    connect(value) { api = value; },
    async show(section) { if(window.AstroArchiveExtensions?.isActive(section))return; if (section === 'analysis') await window.AstroArchiveAnalysis.show(); if (section === 'preparation') await window.AstroArchivePreparation.show(); },
    navigate: route => api.navigate(route),
    preparationChanged() { window.AstroArchivePreparation.changed(); },
    automatic(all) { window.AstroArchivePreparation.automatic(all); },
    languageChanged() { window.AstroArchiveAnalysis.translate(); window.AstroArchivePreparation.translate(); }
  };
})();
