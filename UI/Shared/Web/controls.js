"use strict";
// Visual feedback only. No RPC, action handlers, stored state or plugin coupling.
(() => {
  if (window.AstroArchiveControls) return;
  const groups = new Map(), dirty = new Map(), ripples = new Set();
  const selectors = '.navigation, .settings-tabs, .function-segmented, .flow-view-buttons';
  const buttonSelector = 'button, input[type="button"], input[type="submit"], [role="button"]';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let frame = 0;
  const motion = () => !reduced.matches && document.documentElement.dataset.animations !== 'off';
  // The main route indicator is a small positional cue; honour the app's own
  // switch even when the operating system asks for reduced motion.
  const sliderMotion = group => document.documentElement.dataset.animations !== 'off' &&
    (!reduced.matches || group.classList.contains('navigation'));
  const disabled = button => button.matches(':disabled,[aria-disabled="true"]') || button.closest('[inert]');
  const selected = button => button.getAttribute('aria-current') === 'page' ||
    button.getAttribute('aria-selected') === 'true' || button.getAttribute('aria-pressed') === 'true';

  function schedule(group, animate = false) {
    if (!groups.has(group)) return;
    dirty.set(group, (dirty.get(group) || false) || animate);
    if (!frame) frame = requestAnimationFrame(flush);
  }
  function flush() {
    frame = 0;
    for (const [group, animate] of dirty) place(group, animate);
    dirty.clear();
    for (const [group, state] of groups) if (!group.isConnected) {
      state.resize.disconnect(); state.animation?.cancel(); groups.delete(group);
    }
  }
  function place(group, animate) {
    const state = groups.get(group);
    if (!state || !group.isConnected) return;
    const button = [...group.children].find(node => node.matches(buttonSelector) && selected(node) && node.getClientRects().length);
    if (!button) {
      if (!state.indicator.hidden) state.indicator.hidden = true;
      state.animation?.cancel(); state.animation = null; state.ready = false; return;
    }
    // A route change can resize the page and trigger a layout refresh. Keep the
    // running transition; the final position is already stored on the indicator.
    if (state.animation?.playState === 'running' && state.selected === button) return;
    const box = group.getBoundingClientRect();
    // Layout bounds deliberately ignore the button's temporary press scale.
    const target = {left:box.left + group.clientLeft + button.offsetLeft - group.scrollLeft,
      top:box.top + group.clientTop + button.offsetTop - group.scrollTop,
      width:button.offsetWidth,height:button.offsetHeight};
    if (!target.width || !target.height) { state.ready = false; return; }
    const previous = state.indicator.getBoundingClientRect();
    const changed = state.selected !== button;
    state.animation?.cancel(); state.animation = null;
    Object.assign(state.indicator.style, {
      left: (target.left - box.left + group.scrollLeft - group.clientLeft) + 'px',
      top: (target.top - box.top + group.scrollTop - group.clientTop) + 'px',
      width: target.width + 'px', height: target.height + 'px'
    });
    if (state.indicator.hidden) state.indicator.hidden = false;
    if (animate && changed && state.ready && sliderMotion(group) && previous.width && previous.height) {
      const dx = previous.left - target.left, dy = previous.top - target.top;
      const stretch = Math.max(previous.width, target.width) + Math.min(Math.abs(dx) * .24, target.width * .22);
      const transform = (x, y, width) => `translate3d(${x}px,${y}px,0) scaleX(${width / target.width})`;
      const animation = state.indicator.animate([
        { transform: transform(dx, dy, previous.width), offset: 0 },
        { transform: transform(dx * .47, dy * .47, stretch), offset: .4 },
        { transform: transform(-Math.sign(dx) * 1.2, 0, target.width * .99), offset: .78 },
        { transform: 'translate3d(0,0,0) scaleX(1)', offset: 1 }
      ], { duration: 440 + Math.min(Math.abs(dx) * .15, 75), easing: 'cubic-bezier(.2,.7,.25,1)' });
      state.animation = animation;
      // Re-place once after the transition so late layout shifts settle exactly.
      animation.finished.catch(() => {}).finally(() => {
        if (state.animation !== animation) return;
        state.animation = null; schedule(group);
      });
    }
    state.ready = true; state.selected = button;
  }
  function addGroup(group) {
    if (groups.has(group)) return;
    const indicator = document.createElement('span');
    indicator.className = 'aa-route-indicator'; indicator.setAttribute('aria-hidden', 'true');
    indicator.hidden = true; group.prepend(indicator); group.classList.add('aa-slider-group');
    const resize = new ResizeObserver(() => schedule(group));
    const state = {indicator, resize, ready: false, selected: null, animation: null};
    groups.set(group, state); resize.observe(group);
    for (const button of group.querySelectorAll(buttonSelector)) resize.observe(button);
    schedule(group);
  }
  function discover(root) {
    if (!(root instanceof Element)) return;
    if (root.matches(buttonSelector)) root.classList.add('aa-button');
    for (const button of root.querySelectorAll(buttonSelector)) button.classList.add('aa-button');
    if (root.matches(selectors)) addGroup(root);
    for (const group of root.querySelectorAll(selectors)) addGroup(group);
    const group = root.closest(selectors);
    if (group && groups.has(group)) {
      for (const button of group.querySelectorAll(buttonSelector)) groups.get(group).resize.observe(button);
      schedule(group);
    }
  }
  function ripple(button, x, y) {
    if (!motion() || disabled(button) || button.matches('input,.activity-button,.island-resize,.island-move')) return;
    const box = button.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const size = Math.max(box.width, box.height) * 2;
    const dot = document.createElement('span'); dot.className = 'aa-button-ripple'; dot.setAttribute('aria-hidden', 'true');
    dot.style.setProperty('--ripple-size', size + 'px');
    dot.style.setProperty('--ripple-left', ((x ?? box.width / 2) - size / 2) + 'px');
    dot.style.setProperty('--ripple-top', ((y ?? box.height / 2) - size / 2) + 'px');
    button.append(dot);
    const animation = dot.animate([{transform:'scale(.08)',opacity:.85},{transform:'scale(1)',opacity:0}],
      {duration:520,easing:'cubic-bezier(.15,.7,.3,1)'});
    const item = {dot, animation}; ripples.add(item);
    animation.finished.catch(() => {}).finally(() => { dot.remove(); ripples.delete(item); });
  }
  document.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !event.isPrimary) return;
    const button = event.target.closest(buttonSelector);
    if (button) {const box = button.getBoundingClientRect(); ripple(button, event.clientX - box.left, event.clientY - box.top);}
  }, {capture:true,passive:true});
  document.addEventListener('click', event => {
    if (event.detail !== 0) return;
    const button = event.target.closest(buttonSelector);
    if (button) ripple(button);
  }, true);
  const changes = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList' && [...record.addedNodes,...record.removedNodes].every(node =>
        node instanceof Element && node.matches('.aa-button-ripple,.aa-route-indicator'))) continue;
      if (record.type === 'childList') for (const node of record.addedNodes) {
        if (node instanceof Element && !node.classList.contains('aa-button-ripple') && !node.classList.contains('aa-route-indicator')) discover(node);
      }
      const target = record.target instanceof Element ? record.target : record.target.parentElement;
      const group = target?.closest(selectors);
      if (group) schedule(group, record.type === 'attributes' && record.attributeName !== 'hidden');
    }
    if ([...groups.keys()].some(group => !group.isConnected) && !frame) frame=requestAnimationFrame(flush);
  });
  const motionChanged = () => {
    if (!motion()) for (const item of ripples) item.animation.cancel();
    for (const [group,state] of groups) {state.animation?.cancel();schedule(group);}
  };
  changes.observe(document.body, {subtree:true, childList:true, characterData:true,
    attributes:true, attributeFilter:['aria-current','aria-selected','aria-pressed','hidden']});
  new MutationObserver(motionChanged).observe(document.documentElement, {attributes:true,attributeFilter:['data-animations']});
  reduced.addEventListener('change', motionChanged);
  document.fonts?.ready.then(() => {for (const group of groups.keys()) schedule(group);});
  discover(document.body);
  window.AstroArchiveControls = {refresh: () => {discover(document.body);for (const group of groups.keys()) schedule(group);}};
})();
