"use strict";
(() => {
  const colors = { clear: '#6de0bb', warning: '#ffcf89', poor: '#f37981', unknown: '#91a3bd', measured: '#74c1ff' };
  class ScatterChart {
    constructor(canvas, handlers = {}) {
      this.canvas = canvas; this.handlers = handlers; this.data = []; this.viewport = null; this.selected = null;
      this.resize = new ResizeObserver(() => this.draw()); this.resize.observe(canvas);
      canvas.addEventListener('wheel', e => {
        if (!this.bounds || !this.data.length) return; e.preventDefault();
        const at = this.toValue(e.offsetX, e.offsetY), factor = Math.exp(Math.max(-1, Math.min(1, e.deltaY / 700))), b = this.bounds;
        this.setAxes({ xmin: at.x + (b.xmin - at.x) * factor, xmax: at.x + (b.xmax - at.x) * factor,
          ymin: at.y + (b.ymin - at.y) * factor, ymax: at.y + (b.ymax - at.y) * factor });
      }, { passive: false });
      canvas.addEventListener('pointerdown', e => {
        if (e.button === 1 || e.button === 0 && e.altKey) { e.preventDefault(); this.pan = { x: e.offsetX, y: e.offsetY, bounds: { ...this.bounds } }; canvas.setPointerCapture(e.pointerId); }
      });
      canvas.addEventListener('pointermove', e => {
        if (this.pan && this.area) {
          const b = this.pan.bounds, dx = (e.offsetX - this.pan.x) / this.area.w * (b.xmax - b.xmin), dy = (e.offsetY - this.pan.y) / this.area.h * (b.ymax - b.ymin);
          this.viewport = { xmin: b.xmin - dx, xmax: b.xmax - dx, ymin: b.ymin + dy, ymax: b.ymax + dy }; this.draw();
        }
        const hit = this.hit(e.offsetX, e.offsetY); canvas.title = hit ? `${hit.row.name}\n${this.xlabel}: ${hit.x}\n${this.ylabel}: ${hit.y}\n${hit.row.reason || ''}` : '';
      });
      canvas.addEventListener('pointerup', () => this.pan = null);
      canvas.addEventListener('click', e => { if (e.altKey) return; const hit = this.hit(e.offsetX, e.offsetY); if (hit) { this.selected = hit.row.id; this.draw(); handlers.select?.(hit.row); } });
      canvas.addEventListener('dblclick', e => { const hit = this.hit(e.offsetX, e.offsetY); if (hit) handlers.open?.(hit.row); else this.reset(); });
      canvas.addEventListener('contextmenu', e => { e.preventDefault(); const hit = this.hit(e.offsetX, e.offsetY); if (hit) handlers.context?.(hit.row, e.clientX, e.clientY); });
      canvas.tabIndex = 0;
    }
    set(rows, x, y, xlabel, ylabel, sorted = false) {
      this.data = rows.map((row, i) => ({ row, x: x(row, i), y: y(row, i) })).filter(p => Number.isFinite(p.x) && Number.isFinite(p.y));
      if (sorted) { this.data.sort((a, b) => a.y - b.y); this.data.forEach((p, i) => p.x = i + 1); }
      this.xlabel = xlabel; this.ylabel = ylabel; this.viewport = null; this.draw();
    }
    reset() { this.viewport = null; this.draw(); }
    setAxes(b) { if (Object.values(b).every(Number.isFinite) && b.xmax > b.xmin && b.ymax > b.ymin) { this.viewport = b; this.draw(); return true; } return false; }
    toValue(x, y) { const b = this.bounds, a = this.area; return { x: b.xmin + (x - a.x) / a.w * (b.xmax - b.xmin), y: b.ymax - (y - a.y) / a.h * (b.ymax - b.ymin) }; }
    hit(x, y) { return this.points?.reduce((best, p) => Math.hypot(p.px - x, p.py - y) < (best?.distance ?? 13) ? { ...p, distance: Math.hypot(p.px - x, p.py - y) } : best, null); }
    draw() {
      const canvas = this.canvas, w = canvas.clientWidth, h = canvas.clientHeight; if (!w || !h) return;
      const dpr = window.devicePixelRatio || 1; if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
      const style = getComputedStyle(document.documentElement), muted = style.getPropertyValue('--muted'), border = style.getPropertyValue('--border');
      ctx.fillStyle = style.getPropertyValue('--background'); ctx.fillRect(0, 0, w, h); ctx.font = '11px "Segoe UI", sans-serif';
      const a = this.area = { x: 70, y: 28, w: Math.max(1, w - 98), h: Math.max(1, h - 88) };
      if (!this.data.length) { ctx.fillStyle = muted; ctx.textAlign = 'center'; ctx.fillText('Няма измерени стойности за избрания показател.', w / 2, h / 2); this.points = []; return; }
      let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
      for (const p of this.data) { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); ymin = Math.min(ymin, p.y); ymax = Math.max(ymax, p.y); }
      const xp = Math.max((xmax - xmin) * .04, Math.abs(xmin) * .001, .01), yp = Math.max((ymax - ymin) * .08, Math.abs(ymin) * .001, .0001);
      const b = this.bounds = this.viewport || { xmin: xmin - xp, xmax: xmax + xp, ymin: ymin - yp, ymax: ymax + yp };
      ctx.strokeStyle = border; ctx.lineWidth = 1; ctx.fillStyle = muted;
      for (let i = 0; i <= 5; i++) {
        const x = a.x + a.w * i / 5, y = a.y + a.h * i / 5;
        ctx.beginPath(); ctx.moveTo(x, a.y); ctx.lineTo(x, a.y + a.h); ctx.moveTo(a.x, y); ctx.lineTo(a.x + a.w, y); ctx.stroke();
        ctx.textAlign = 'center'; ctx.fillText(this.tick(b.xmin + (b.xmax - b.xmin) * i / 5), x, a.y + a.h + 22);
        ctx.textAlign = 'right'; ctx.fillText(this.tick(b.ymax - (b.ymax - b.ymin) * i / 5), a.x - 10, y + 4);
      }
      ctx.textAlign = 'center'; ctx.fillText(this.xlabel || '', a.x + a.w / 2, h - 10); ctx.save(); ctx.translate(15, a.y + a.h / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(this.ylabel || '', 0, 0); ctx.restore();
      this.points = []; ctx.save(); ctx.beginPath(); ctx.rect(a.x, a.y, a.w, a.h); ctx.clip();
      for (const p of this.data) {
        const px = a.x + (p.x - b.xmin) / (b.xmax - b.xmin) * a.w, py = a.y + (b.ymax - p.y) / (b.ymax - b.ymin) * a.h;
        if (px < a.x || px > a.x + a.w || py < a.y || py > a.y + a.h) continue;
        ctx.fillStyle = colors[p.row.state] || colors.measured; ctx.globalAlpha = .82; ctx.beginPath(); ctx.arc(px, py, p.row.id === this.selected ? 6 : 3.5, 0, Math.PI * 2); ctx.fill();
        if (p.row.id === this.selected) { ctx.strokeStyle = '#fff'; ctx.stroke(); } this.points.push({ ...p, px, py });
      }
      ctx.restore(); ctx.globalAlpha = 1;
    }
    tick(value) { return Math.abs(value) > 1e6 || Math.abs(value) < .0001 && value !== 0 ? value.toExponential(2) : new Intl.NumberFormat('bg', { maximumFractionDigits: 4 }).format(value); }
  }
  function heatmap(canvas, grid, options = {}) {
    const ctx = canvas.getContext('2d'); canvas.width = grid.width; canvas.height = grid.height;
    const image = ctx.createImageData(grid.width, grid.height), valid = grid.data.filter(Number.isFinite), low = Math.min(...valid), high = Math.max(...valid);
    for (let y = 0; y < grid.height; y++) for (let x = 0; x < grid.width; x++) {
      const v = grid.data[y * grid.width + x], t = Number.isFinite(v) ? high > low ? Math.max(0, Math.min(1, (v - low) / (high - low))) : .5 : 0;
      const i = ((grid.height - y - 1) * grid.width + x) * 4;
      if (options.gray) image.data.set([t * 255, t * 255, t * 255, 255], i);
      else image.data.set([40 + t * 210, 90 + (1 - Math.abs(t - .5) * 2) * 125, 200 - t * 145, 255], i);
    }
    ctx.putImageData(image, 0, 0); return { low, high };
  }
  window.AstroArchiveCharts = { ScatterChart, heatmap };
})();
