// Beta 1.3 UI
"use strict";
(() => {
  const easing = "cubic-bezier(.22, 1, .36, 1)";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const enabled = () => !reduced.matches && document.documentElement.dataset.animations !== "off";
  const active = new WeakMap();

  function play(dialog, frames, duration) {
    if (!enabled() || !dialog.animate) return;
    active.get(dialog)?.cancel();
    const animation = dialog.animate(frames, { duration, easing });
    active.set(dialog, animation);
    animation.finished.catch(() => {});
  }

  function expand(dialog, source) {
    if (!enabled()) return;
    const box = dialog.getBoundingClientRect();
    if (!source?.width || !source.height) {
      play(dialog, [{ opacity: 0 }, { opacity: 1 }], 160);
      return;
    }
    const left = Math.max(0, Math.min(Math.max(0, box.width - source.width), source.left - box.left));
    const top = Math.max(0, Math.min(Math.max(0, box.height - source.height), source.top - box.top));
    const right = Math.max(0, box.width - left - source.width);
    const bottom = Math.max(0, box.height - top - source.height);
    // Translate the clipped starting area back to its source if it sits outside the dialog.
    const x = source.left - box.left - left;
    const y = source.top - box.top - top;
    play(dialog, [
      { clipPath: `inset(${top}px ${right}px ${bottom}px ${left}px round 8px)`, transform: `translate(${x}px, ${y}px)` },
      { clipPath: "inset(0 round 15px)", transform: "none" }
    ], 320);
  }

  function measure(dialog, update) {
    if (!dialog.open || !enabled()) return update();
    const before = { width: dialog.offsetWidth, height: dialog.offsetHeight };
    active.get(dialog)?.cancel();
    update();
    const after = { width: dialog.offsetWidth, height: dialog.offsetHeight };
    if (before.width === after.width && before.height === after.height) return;
    const pixels = size => ({ width: size.width + "px", height: size.height + "px", maxHeight: size.height + "px" });
    play(dialog, [pixels(before), pixels(after)], 240);
  }

  function stop(dialog) { active.get(dialog)?.cancel(); }
  window.AstroArchiveMotion = { enabled, expand, measure, stop };
})();
