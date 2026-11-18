// Draggable 3D fox for the landing hero. Built to ../src/aliasfox/page_templates/hero3d.js
// (npm run build:hero) and loaded by hero.js when the browser is idle. Cosmetic only: any
// failure leaves the SVG fox in place.
import {
  AmbientLight, Box3, DirectionalLight, ExtrudeGeometry, Group, Mesh,
  MeshStandardMaterial, PerspectiveCamera, Scene, Vector3, WebGLRenderer
} from 'three';
import { SVGLoader } from 'three/examples/jsm/loaders/SVGLoader.js';

export function mount(el) {
  const d = el.querySelector('path')?.getAttribute('d');
  if (!d) return false;
  const renderer = new WebGLRenderer({ alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

  const svg = `<svg xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" fill="#000" d="${d}"/></svg>`;
  const shapes = new SVGLoader().parse(svg).paths.flatMap((p) => SVGLoader.createShapes(p));
  const geo = new ExtrudeGeometry(shapes, {
    depth: 90, bevelEnabled: true, bevelThickness: 14, bevelSize: 10, bevelSegments: 4
  });
  geo.center();
  const fox = new Mesh(geo, new MeshStandardMaterial({ color: 0xf77019, roughness: 0.4, metalness: 0.15 }));
  fox.scale.y = -1; // SVG y points down
  const pivot = new Group();
  pivot.add(fox);

  const scene = new Scene();
  scene.add(pivot, new AmbientLight(0xffffff, 1.1));
  const key = new DirectionalLight(0xffe2c8, 3);
  key.position.set(-300, 400, 600);
  const rim = new DirectionalLight(0xff9a4d, 2);
  rim.position.set(400, -200, -300);
  scene.add(key, rim);

  const size = new Box3().setFromObject(pivot).getSize(new Vector3());
  const camera = new PerspectiveCamera(30, 1, 10, 5000);
  const fit = () => {
    const w = el.clientWidth, h = el.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.position.z = (Math.max(size.x / camera.aspect, size.y) / 2 / Math.tan((camera.fov * Math.PI) / 360)) * 1.25;
    camera.updateProjectionMatrix();
  };
  fit();
  new ResizeObserver(fit).observe(el);

  // drag to spin, release to fling; springs back toward the front when idle
  let yaw = 0, pitch = 0, vYaw = 0, vPitch = 0, drag = null, visible = true, last = performance.now();
  const canvas = renderer.domElement;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY };
    el.classList.add('dragging');
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    vYaw = (e.clientX - drag.x) * 0.012;
    vPitch = (e.clientY - drag.y) * 0.012;
    yaw += vYaw;
    pitch += vPitch;
    drag = { x: e.clientX, y: e.clientY };
  });
  const release = () => {
    drag = null;
    el.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(el);
  const tick = (now) => {
    requestAnimationFrame(tick);
    if (!visible) return;
    const dt = Math.min((now - last) / 16.7, 3);
    last = now;
    if (!drag) {
      yaw += vYaw * dt;
      pitch += vPitch * dt;
      vYaw *= 0.95;
      vPitch *= 0.95;
      // spring home along the shortest way round
      const home = Math.round(yaw / (2 * Math.PI)) * 2 * Math.PI;
      yaw += (home - yaw) * 0.03 * dt;
      pitch += -pitch * 0.05 * dt;
    }
    pivot.rotation.set(Math.max(-1.2, Math.min(1.2, pitch)), yaw, 0);
    pivot.position.y = Math.sin(now / 950) * 14;
    renderer.render(scene, camera);
  };
  requestAnimationFrame(tick);

  el.append(canvas);
  requestAnimationFrame(() => el.classList.add('is3d'));
  return true;
}
