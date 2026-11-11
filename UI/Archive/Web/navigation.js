"use strict";
(() => {
  // One semantic icon map for the menu and file table. Future icon sets can be
  // applied here without changing navigation, paths, or archive operations.
  const shapes = {
    archive: '<path d="M3 7h18v13H3zM2 3h20v4H2zM9 11h6"/>',
    'archive-frames': '<rect x="3" y="4" width="15" height="15" rx="2"/><path d="M7 21h14V7M6 15l4-5 5 6M14 8h.01"/>',
    'archive-stacks': '<path d="m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
    'archive-photos': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m4 17 5-6 4 4 3-3 5 6M16 8h.01"/>',
    telescope: '<path d="m4 10 12-7 4 7-12 7zm10 4 4 7m-6-6-3 6m3-6v7M3 11l-1-2 4-2"/>',
    object: '<circle cx="12" cy="12" r="5"/><ellipse cx="12" cy="12" rx="11" ry="3" transform="rotate(-28 12 12)"/><path d="M19 3v3m-1.5-1.5h3"/>',
    day: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 2v6m10-6v6M3 10h18M7 14h3m4 0h3m-10 4h3"/>',
    folder: '<path d="M2 7V4h7l3 3h10v13H2z"/>',
    fits: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="m12 6 1.5 4.5L18 12l-4.5 1.5L12 18l-1.5-4.5L6 12l4.5-1.5z"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m4 17 5-6 4 4 3-3 5 6M16 8h.01"/>'
  };
  let layouts={};
  const anchorParts=(path,id)=>parts(path).slice(0,Math.max(0,(layouts[id]?.levels?.length||3)-1));
  const parts = path => (path || '').split(/[\\/]/).filter(Boolean);
  const pathKey = path => parts(path).join('\\').toLocaleLowerCase('en-US');
  const tr = (bg, en) => window.AstroArchiveI18n.locale.startsWith('en') ? en : bg;
  const number = value => new Intl.NumberFormat(window.AstroArchiveI18n.locale).format(value);
  const make = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  function icon(role) {
    const node = make('span', 'archive-icon'); node.dataset.iconRole = role; node.dataset.iconSet = 'default'; node.setAttribute('aria-hidden', 'true');
    const choice=window.AstroArchiveCustomization?.icon(role)||{role};
    if(choice.data){const image=make('img');image.src=choice.data;image.alt='';node.append(image);}
    else node.innerHTML = '<svg viewBox="0 0 24 24" fill="'+(choice.style==='solid'?'currentColor':'none')+'" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + (shapes[choice.role] || shapes.folder) + '</svg>';
    return node;
  }
  function role(archiveId, path, kind = 'folder') {
    if (kind === 'archive') return shapes['archive-' + archiveId] ? 'archive-' + archiveId : 'archive';
    if (kind !== 'folder') return kind === 'image' ? 'image' : 'fits';
    return layouts[archiveId]?.levels?.[parts(path).length-1]?.icon || ['folder', 'telescope', 'object', 'day'][Math.min(parts(path).length, 4)] || 'folder';
  }
  window.AstroArchiveArchiveIcons = {create: icon, role, layouts(archives){layouts=Object.fromEntries(archives.map(a=>[a.id,a.structure]));}, refresh(){for(const old of document.querySelectorAll('.archive-icon[data-icon-role]'))old.replaceWith(icon(old.dataset.iconRole));}};

  window.AstroArchiveArchiveNavigation = {
    create(actions) {
      const list = document.getElementById('archive-roots'), title = document.getElementById('archive-nav-title');
      const heading = document.querySelector('.archive-navigation-title'), count = document.getElementById('archive-root-count');
      const level = document.getElementById('archive-nav-level'), back = document.getElementById('archive-nav-back'), home = document.getElementById('archive-nav-home');
      let context, key, entries = [], total = 0, version = 0, moreBusy = false;
      let animations = [], flying = null;
      const motion = () => document.documentElement.dataset.animations !== 'off' && !matchMedia('(prefers-reduced-motion: reduce)').matches;
      function stopAnimation() {
        for (const animation of animations) animation.cancel(); animations = [];
        flying?.remove(); flying = null; heading.style.opacity = ''; list.style.opacity = '';
      }
      function selected() {
        if (!context?.entered) return;
        const path = pathKey(context.path), anchor = pathKey(anchorParts(context.path,context.archiveId).join('\\'));
        for (const button of list.querySelectorAll('[data-nav-path]')) {
          const candidate = pathKey(button.dataset.navPath), active = path !== anchor && (path === candidate || path.startsWith(candidate + '\\'));
          if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
        }
      }
      function describe() {
        const path = parts(context.path), anchor = anchorParts(context.path,context.archiveId), archive = context.archives.find(a => a.id === context.archiveId);
        return {anchor: anchor.join('\\'), depth: anchor.length, title: !context.entered ? window.AstroArchiveI18n.t('archive.roots')
          : (anchor.length===1?window.AstroArchiveCustomization.telescope(anchor[0]):anchor.at(-1)) || actions.name(archive), icon: anchor.length ? role(context.archiveId, anchor.join('\\')) : role(context.archiveId, '', 'archive'),
          hint: !context.entered ? tr('Избери архив', 'Choose an archive') : ({telescope:tr('Телескопи','Telescopes'),object:tr('Обекти','Objects'),night:tr('Наблюдателни нощи','Observation nights')}[layouts[context.archiveId]?.levels?.[anchor.length]?.field]||tr('Папки','Folders'))};
      }
      function renderHeading() {
        const description = describe(); title.textContent = description.title; title.title = description.title;
        count.textContent = context.entered ? (context.unavailable ? '—' : number(total)) : String(total).padStart(2, '0');
        count.title = context.entered ? tr('Папки на това ниво', 'Folders at this level') : tr('Архиви', 'Archives');
        level.textContent = description.hint;
        const holder = document.getElementById('archive-nav-icon'); holder.hidden = !context.entered; holder.replaceChildren(icon(description.icon));
        back.hidden = home.hidden = !context.entered;
        back.textContent = tr('← Назад', '← Back'); home.textContent = tr('Всички архиви', 'All archives');
        const parent = parts(context.path).slice(0, -1).at(-1) || (context.path ? actions.name(context.archives.find(a => a.id === context.archiveId)) : window.AstroArchiveI18n.t('archive.roots'));
        back.title = tr('Назад към ', 'Back to ') + parent;
        list.setAttribute('aria-label', description.title + ' · ' + description.hint);
      }
      function renderEntries() {
        const fragment = document.createDocumentFragment();
        for (const entry of entries) {
          const button = make('button', 'archive-nav-item'); button.type = 'button'; button.dataset.navPath = entry.relativePath || '';
          button.dataset.navKey = entry.kind==='archive' ? 'archive:' + entry.archiveId : pathKey(entry.relativePath);
          const entryIcon = entry.kind==='archive' ? role(entry.archiveId, '', 'archive') : entry.icon||role(context.archiveId, entry.relativePath);
          const caption = make('span', 'archive-nav-caption'); caption.append(make('strong', '', entry.displayName||(!entry.archiveId&&parts(entry.relativePath).length===1?window.AstroArchiveCustomization.telescope(entry.name):entry.name)));
          if (entry.kind==='archive') caption.append(make('small', '', window.AstroArchiveI18n.t(entry.configured ? 'archive.savedPath' : 'archive.chooseSettings')));
          button.append(icon(entryIcon), caption, make('span', 'archive-nav-chevron', '›')); button.title = entry.name;
          if (entry.isLink) { button.disabled = true; button.title = window.AstroArchiveI18n.t('file.linkBlocked'); }
          const archiveId = context.archiveId;
          button.onclick = () => actions.navigate(entry.archiveId || archiveId, entry.relativePath || ''); fragment.append(button);
        }
        if (!entries.length) fragment.append(make('p', 'archive-nav-empty', context.unavailable
          ? tr('Избери папка за този архив от настройките.', 'Choose an archive folder in Settings.')
          : tr('Няма папки на това ниво. Файловете са вдясно.', 'No folders at this level. Files are on the right.')));
        if (context.entered && entries.length < total) {
          const more = make('button', 'text-button archive-nav-more', tr('Покажи още…', 'Show more…')); more.type = 'button'; more.disabled = moreBusy; more.onclick = loadMore; fragment.append(more);
        }
        list.replaceChildren(fragment); selected();
      }
      async function loadMore() {
        if (moreBusy) return; moreBusy = true; const current = version, anchor = describe().anchor;
        renderEntries();
        try {
          const page = await actions.call('archive.navigation', {archiveId: context.archiveId, relativePath: anchor, offset: entries.length});
          if (current !== version) return;
          const known = new Set(entries.map(entry => pathKey(entry.relativePath)));
          entries.push(...page.entries.filter(entry => !known.has(pathKey(entry.relativePath)))); total = page.totalFolders;
        } catch (error) { if (current === version) actions.problem(error); }
        finally { if (current === version) { moreBusy = false; renderHeading(); renderEntries(); } }
      }
      async function replace(next, nextEntries, nextTotal, newKey, animate = true) {
        const current = ++version; moreBusy = false; stopAnimation();
        const old = context, description = anchorParts(next.path,next.archiveId).join('\\');
        const selectedKey = next.entered && !old?.entered ? 'archive:' + next.archiveId : pathKey(description);
        const source = [...list.querySelectorAll('[data-nav-key]')].find(node => node.dataset.navKey === selectedKey);
        const sourceRect = source?.getBoundingClientRect();
        if (animate && motion() && old && newKey !== key) {
          if (source && sourceRect) {
            flying = source.cloneNode(true); flying.removeAttribute('id'); flying.tabIndex = -1; flying.setAttribute('aria-hidden', 'true'); flying.classList.add('archive-nav-flying'); flying.querySelector('small')?.remove(); flying.querySelector('.archive-nav-chevron')?.remove();
            Object.assign(flying.style, {left: sourceRect.x + 'px', top: sourceRect.y + 'px', width: sourceRect.width + 'px', height: sourceRect.height + 'px'}); document.body.append(flying);
          }
          animations = [...list.children].map(node => node.animate([{opacity: 1, transform: 'translateX(0)'}, {opacity: 0, transform: 'translateX(-10px)'}], {duration: 130, fill: 'forwards'}));
          await Promise.all(animations.map(animation => animation.finished.catch(() => {})));
          if (current !== version) return;
        }
        context = next; entries = nextEntries; total = nextTotal; key = newKey;
        renderHeading(); renderEntries(); list.scrollTop = 0;
        if (!animate || !motion() || !old || old.entered === next.entered && pathKey(anchorParts(old.path,old.archiveId).join('\\')) === pathKey(description) && old.archiveId === next.archiveId) { stopAnimation(); return; }
        list.style.opacity = '0';
        const flight = flying;
        if (flight && sourceRect) {
          const target = heading.getBoundingClientRect(); heading.style.opacity = '0';
          const animation = flight.animate([{transform: 'translate(0,0)', opacity: 1}, {transform: `translate(${target.x - sourceRect.x - 10}px,${target.y + target.height / 2 - sourceRect.y - sourceRect.height / 2}px)`, opacity: .9}], {duration: 240, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards'});
          animations.push(animation); await animation.finished.catch(() => {}); if (current !== version) return;
          flight.remove(); flying = null; heading.style.opacity = '';
        }
        const reveal = list.animate([{opacity: 0, transform: 'translateY(8px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 180, easing: 'ease-out', fill: 'forwards'});
        animations.push(reveal); await reveal.finished.catch(() => {}); if (current === version) stopAnimation();
      }
      function update(next, force = false) {
        const anchor = anchorParts(next.path,next.archiveId).join('\\'), newKey = JSON.stringify([next.entered, next.archiveId, next.root, pathKey(anchor)]);
        const navigation = next.folder?.navigation;
        let incoming = !next.entered ? next.archives.map(a => ({name: actions.name(a),kind:'archive', archiveId: a.id, configured: !!a.path}))
          : navigation?.entries || (pathKey(anchor) === pathKey(next.path) ? (next.folder?.entries || []).filter(e => e.kind === 'folder') : []);
        const incomingTotal = !next.entered ? incoming.length : navigation?.totalFolders ?? next.folder?.directFolders ?? 0;
        if (newKey === key && !force && entries.length >= incoming.length && incoming.every((entry, i) => entry.name === entries[i]?.name && pathKey(entry.relativePath) === pathKey(entries[i]?.relativePath)) && total === incomingTotal) {
          context = next; renderHeading(); selected(); return;
        }
        // Serialize rapid clicks: a later selection cancels both the fade and flight.
        replace(next, incoming, incomingTotal, newKey, !force).catch(actions.problem);
      }
      back.onclick = () => { if (!context?.entered) return; const parent = parts(context.path).slice(0, -1).join('\\'); if (!context.path) actions.home(); else actions.navigate(context.archiveId, parent); };
      home.onclick = actions.home;
      return {update, busy(value) { document.querySelector('.archive-navigation-heading').classList.toggle('is-loading', value); list.setAttribute('aria-busy', String(value)); },
        translate() { if (context) { renderHeading(); renderEntries(); } }, dispose() { ++version; stopAnimation(); }};
    }
  };
})();
