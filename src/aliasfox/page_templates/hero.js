// Drag the hero fox to tilt it; once the browser is idle, swap in a real 3D fox (hero3d.js).
// Cosmetic only.
(() => {
  const el = document.querySelector('.hero-art');
  if (!el || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const MAX = 40; // degrees
  let rx = 0, ry = 0, vx = 0, vy = 0, tx = 0, ty = 0, dragging = false, raf = 0;

  const tick = () => {
    vx = (vx + (tx - rx) * 0.12) * 0.82;
    vy = (vy + (ty - ry) * 0.12) * 0.82;
    rx += vx;
    ry += vy;
    el.style.setProperty('--rx', rx + 'deg');
    el.style.setProperty('--ry', ry + 'deg');
    const still = Math.abs(vx) + Math.abs(vy) + Math.abs(tx - rx) + Math.abs(ty - ry) < 0.02;
    raf = dragging || !still ? requestAnimationFrame(tick) : 0;
  };
  const run = () => raf || (raf = requestAnimationFrame(tick));
  const aim = (e) => {
    const r = el.getBoundingClientRect();
    const clamp = (n) => Math.max(-MAX, Math.min(MAX, n));
    ty = clamp(((e.clientX - r.left) / r.width - 0.5) * 2 * MAX);
    tx = clamp(-((e.clientY - r.top) / r.height - 0.5) * 2 * MAX);
  };

  el.addEventListener('pointerdown', (e) => {
    if (el.classList.contains('is3d')) return;
    dragging = true;
    el.setPointerCapture(e.pointerId);
    el.classList.add('dragging');
    aim(e);
    run();
  });
  el.addEventListener('pointermove', (e) => dragging && aim(e));
  const release = () => {
    dragging = false;
    tx = ty = 0;
    el.classList.remove('dragging');
    run();
  };
  el.addEventListener('pointerup', release);
  el.addEventListener('pointercancel', release);

  // The 3D fox is ~160 KB gzipped: skip phones (art hidden), Save-Data, and anything slow to start.
  if (!matchMedia('(min-width: 960px)').matches || navigator.connection?.saveData) return;
  const load3d = () => import('/hero3d.js').then((m) => m.mount(el)).catch(() => {});
  addEventListener('load', () =>
    'requestIdleCallback' in window ? requestIdleCallback(load3d, { timeout: 4000 }) : setTimeout(load3d, 2000)
  );
})();
