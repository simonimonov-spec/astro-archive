/* Beta 1.3 UI (в разработка, не е издание) · виж docs/BETA-1.3-UI.md */
"use strict";
// Data tag (astrophotography / photometry with type): labels, badges and the
// dialogs used at import, in the Archive and before photometry.
(() => {
  const U = window.AstroArchiveFunctions, { node } = U;
  const OPTIONS = [
    ['astro', 'Астрофотография', { kind: 'astro' }],
    ['variable', 'Променливи звезди', { kind: 'photometry', type: 'variable' }],
    ['exoplanet', 'Екзопланети', { kind: 'photometry', type: 'exoplanet' }],
    ['other', 'Друго', { kind: 'photometry', type: 'other' }]];
  const STACK_OPTIONS = [['astro', 'Астрофотография', { kind: 'astro' }], ['photometry', 'Фотометрия', { kind: 'photometry' }]];
  const key = tag => !tag ? '' : tag.kind === 'astro' ? 'astro' : (tag.type || 'photometry');
  const text = tag => !tag ? '' : tag.kind === 'astro' ? 'Астрофотография' : tag.type === 'other' && tag.label ? tag.label : ({ variable: 'Променливи звезди', exoplanet: 'Екзопланети', other: 'Друго' }[tag.type] || 'Фотометрия');
  const make = (option, label) => option ? { ...option[2], ...(option[0] === 'other' && label ? { label } : {}) } : null;

  function badge(tag) {
    if (!tag) return null;
    const b = node('span', text(tag), 'data-tag'); b.dataset.tag = key(tag); b.title = tag.kind === 'astro' ? 'Етикет: астрофотография' : 'Етикет: фотометрия · ' + text(tag);
    return b;
  }
  function mixedBadge() { const b = node('span', 'смесени', 'data-tag'); b.dataset.tag = 'mixed'; b.title = 'Различни етикети в папката'; return b; }

  // Underlined segmented control (same look as the app's tabs).
  function segmented(options, value, onChange, compact = false) {
    const group = node('div', null, 'tag-seg' + (compact ? ' tag-seg-compact' : '')); group.setAttribute('role', 'radiogroup');
    const render = current => { for (const b of group.children) b.setAttribute('aria-checked', String(b.dataset.value === current)); };
    for (const [value_, label] of options) {
      const b = node('button', compact ? label.split(' ')[0] : label); b.type = 'button'; b.dataset.value = value_; b.setAttribute('role', 'radio');
      b.onclick = event => { event.stopPropagation(); render(value_); onChange(value_); }; group.append(b);
    }
    render(value); group.set = render; return group;
  }
  function labelField(value, onInput) {
    const input = node('input'); input.type = 'text'; input.maxLength = 40; input.placeholder = 'Име за „Друго“ (по желание)'; input.value = value || ''; input.className = 'tag-label';
    input.oninput = () => onInput(input.value.trim()); return input;
  }

  // "What are you importing?" — one choice per night/object group, per-file overrides.
  function chooseImport(preview) {
    return new Promise(resolve => {
      let result = null;
      const choice = new Map(), labels = new Map(); // file index -> option key; group key -> label
      for (const g of preview.groups) for (const f of g.files) choice.set(f.index, 'astro');
      const optionsFor = g => g.stack ? STACK_OPTIONS : OPTIONS;
      const dialog = U.modal('Какво внасяш?', (body, box) => {
        box.classList.add('tag-import-dialog');
        const nights = new Set(preview.groups.map(g => g.night)).size, objects = new Set(preview.groups.map(g => g.object)).size;
        body.append(node('p', `${preview.source ? '„' + preview.source + '“ · ' : ''}${preview.count} кадъра · ${nights} ${nights === 1 ? 'вечер' : 'вечери'} · ${objects} ${objects === 1 ? 'обект' : 'обекта'}`, 'tag-import-sub'));
        const all = node('div', null, 'tag-import-all'); all.append(node('span', 'За всички наведнъж:'));
        const groupsHost = node('div', null, 'tag-import-groups'), controls = [];
        all.append(segmented(OPTIONS, '', value => { for (const c of controls) c.apply(value); }));
        body.append(all, groupsHost);
        for (const g of preview.groups) {
          const options = optionsFor(g), box_ = node('div', null, 'tag-group'), head = node('div', null, 'tag-group-head');
          const chevron = node('button', '▸', 'tag-chevron'); chevron.type = 'button'; chevron.setAttribute('aria-label', 'Покажи кадрите'); chevron.setAttribute('aria-expanded', 'false');
          const title = node('div', null, 'tag-group-title'); title.append(node('strong', g.night), node('span', g.object + (g.telescope ? ' · ' + g.telescope : '')));
          const mixed = node('span', 'смесени', 'tag-mixed'); mixed.hidden = true;
          const count = node('span', g.files.length + (g.files.length === 1 ? ' кадър' : ' кадъра'), 'tag-count');
          const files = node('div', null, 'tag-files'); files.hidden = true;
          const label = labelField('', value => labels.set(g.key, value)); label.hidden = true;
          const refresh = () => {
            const values = new Set(g.files.map(f => choice.get(f.index)));
            seg.set(values.size === 1 ? [...values][0] : ''); mixed.hidden = values.size < 2; label.hidden = !values.has('other');
            for (const row of files.querySelectorAll('[data-index]')) row.seg.set(choice.get(Number(row.dataset.index)));
          };
          const apply = value => { const v = options.some(o => o[0] === value) ? value : 'photometry'; for (const f of g.files) choice.set(f.index, v); refresh(); };
          const seg = segmented(options, 'astro', apply);
          controls.push({ apply });
          chevron.onclick = () => {
            const open = files.hidden; files.hidden = !open; chevron.textContent = open ? '▾' : '▸'; chevron.setAttribute('aria-expanded', String(open));
            if (open && !files.childElementCount) {
              for (const f of g.files) {
                const row = node('label', null, 'tag-file'); row.dataset.index = f.index;
                const pick = node('input'); pick.type = 'checkbox'; pick.className = 'tag-file-pick';
                const name = node('span', f.name, 'tag-file-name'), exposure = node('span', Number.isFinite(f.exposure) ? U.number(f.exposure, 1) + ' s' : '', 'tag-file-exposure');
                row.seg = segmented(options, choice.get(f.index), value => {
                  const targets = pick.checked ? [...files.querySelectorAll('[data-index]')].filter(r => r.querySelector('.tag-file-pick').checked).map(r => Number(r.dataset.index)) : [f.index];
                  for (const index of targets) choice.set(index, value); refresh();
                }, true);
                row.append(pick, name, exposure, row.seg); files.append(row);
              }
              files.append(node('p', 'Отметни няколко кадъра и смени типа на един от тях — сменя се на всички отметнати.', 'tag-files-hint'));
            }
          };
          head.append(chevron, title, mixed, count, seg); box_.append(head, label, files); groupsHost.append(box_);
        }
        const actions = node('div', null, 'function-actions tag-actions');
        const cancel = node('button', 'Отказ', 'quiet-button'), accept = node('button', 'Внеси ' + preview.count + (preview.count === 1 ? ' кадър' : ' кадъра'), 'primary-button');
        cancel.type = accept.type = 'button'; cancel.onclick = () => box.close();
        accept.onclick = () => {
          const tags = {};
          for (const g of preview.groups) for (const f of g.files) { const options = optionsFor(g), option = options.find(o => o[0] === choice.get(f.index)); tags[f.index] = make(option, labels.get(g.key)); }
          result = { tags }; box.close();
        };
        actions.append(node('span', 'Стакове: само „Астрофотография“ или „Фотометрия“.', 'tag-actions-note'), cancel, accept); body.append(actions);
      });
      dialog.addEventListener('close', () => resolve(result));
    });
  }

  // Choose a tag for already archived data. Resolves to a tag, or undefined when cancelled.
  function chooseTag({ stack = false, current = null, title = 'Етикет за данните' } = {}) {
    return new Promise(resolve => {
      let result; const options = stack ? STACK_OPTIONS : OPTIONS;
      let value = key(current) && options.some(o => o[0] === key(current)) ? key(current) : 'astro', label = current?.label || '';
      const dialog = U.modal(title, (body, box) => {
        box.classList.add('tag-small-dialog');
        const field = labelField(label, v => { label = v; }); field.hidden = value !== 'other';
        body.append(segmented(options, value, v => { value = v; field.hidden = v !== 'other'; }), field);
        const actions = node('div', null, 'function-actions tag-actions'), cancel = node('button', 'Отказ', 'quiet-button'), accept = node('button', 'Запази етикета', 'primary-button');
        cancel.type = accept.type = 'button'; cancel.onclick = () => box.close(); accept.onclick = () => { result = make(options.find(o => o[0] === value), label); box.close(); };
        actions.append(cancel, accept); body.append(actions);
      });
      dialog.addEventListener('close', () => resolve(result));
    });
  }

  // Before photometry on untagged data. Resolves to a photometry tag, or null for "Не сега".
  function ask(description) {
    return new Promise(resolve => {
      let result = null, value = 'variable', label = '';
      const options = OPTIONS.filter(o => o[0] !== 'astro');
      const dialog = U.modal('Да добавя ли етикет за фотометрия?', (body, box) => {
        box.classList.add('tag-small-dialog');
        body.append(node('p', description + ' нямат етикет. С етикет ще ги намираш лесно с филтъра в Архив и ще ги сравняваш с други наблюдения на същия обект.'));
        const field = labelField('', v => { label = v; }); field.hidden = true;
        body.append(segmented(options, value, v => { value = v; field.hidden = v !== 'other'; }), field);
        const actions = node('div', null, 'function-actions tag-actions'), later = node('button', 'Не сега', 'quiet-button'), accept = node('button', 'Добави етикета', 'primary-button');
        later.type = accept.type = 'button'; later.onclick = () => box.close(); accept.onclick = () => { result = make(options.find(o => o[0] === value), label); box.close(); };
        actions.append(later, accept); body.append(actions);
      });
      dialog.addEventListener('close', () => resolve(result));
    });
  }

  // Import through preview -> dialog -> import with a tag per file.
  async function importWithTags(params) {
    const P = window.AstroArchivePython, preview = await P.task('archive.import.preview', params);
    if (!preview || preview.cancelled) return null;
    const choice = await chooseImport(preview);
    if (!choice) return null;
    return P.task('archive.import', { token: preview.token, tags: choice.tags });
  }

  window.AstroArchiveTags = { OPTIONS, STACK_OPTIONS, key, text, badge, mixedBadge, chooseImport, chooseTag, ask, importWithTags };
})();
