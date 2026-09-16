// Room map: Three.js scene. Camera (you) at the centre, objects and fixtures placed by angle + distance.
// Objects are green spheres with labels and trails; fixtures are blue blocks around the room edge.
// Highlighted items pulse when the assistant talks about them. Drag to orbit, wheel to zoom.
(() => {
  const { bus } = window.PEMA;
  const holder = document.getElementById('room3d'), canvas = document.getElementById('room-canvas');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  let orbit = { theta: 0.4, phi: 1.0, r: 9 };
  function placeCam() {
    cam.position.set(orbit.r * Math.sin(orbit.phi) * Math.sin(orbit.theta), orbit.r * Math.cos(orbit.phi), orbit.r * Math.sin(orbit.phi) * Math.cos(orbit.theta));
    cam.lookAt(0, 0.3, 0);
  }
  placeCam();
  scene.add(new THREE.HemisphereLight(0xdde6ff, 0x111622, 1.1));
  const dl = new THREE.DirectionalLight(0xffffff, 0.5); dl.position.set(3, 6, 2); scene.add(dl);

  // floor + walls (a circular room of radius 4.5 m, purely schematic)
  const R = 4.5;
  const floor = new THREE.Mesh(new THREE.CircleGeometry(R, 64), new THREE.MeshStandardMaterial({ color: 0x151b27, roughness: 0.95 }));
  floor.rotation.x = -Math.PI / 2; scene.add(floor);
  const grid = new THREE.PolarGridHelper(R, 12, 4, 64, 0x2a3446, 0x1f2836); grid.position.y = 0.005; scene.add(grid);
  const wall = new THREE.Mesh(new THREE.CylinderGeometry(R, R, 2.4, 64, 1, true), new THREE.MeshStandardMaterial({ color: 0x1b2331, side: THREE.BackSide, transparent: true, opacity: 0.6 }));
  wall.position.y = 1.2; scene.add(wall);

  // you / camera marker + field-of-view wedge
  const you = new THREE.Group();
  you.add(new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.5, 4), new THREE.MeshStandardMaterial({ color: 0xffb020 })));
  you.children[0].rotation.x = Math.PI / 2; you.children[0].position.y = 0.3;
  const fov = new THREE.Mesh(new THREE.CircleGeometry(2.2, 24, -Math.PI / 6 + Math.PI / 2, Math.PI / 3), new THREE.MeshBasicMaterial({ color: 0xffb020, transparent: true, opacity: 0.08, side: THREE.DoubleSide }));
  fov.rotation.x = -Math.PI / 2; fov.position.y = 0.01; you.add(fov);
  scene.add(you);
  addLabel(you, 'you', 0.9, '#ffb020');

  const objects = new Map(), fixtures = new Map(), trails = new Map();
  let highlight = new Set();

  function toXZ(angDeg, dist) { const a = THREE.MathUtils.degToRad(angDeg); return [Math.sin(a) * Math.min(dist, R - 0.3), -Math.cos(a) * Math.min(dist, R - 0.3)]; }

  function addLabel(parent, text, y, color) {
    const c = document.createElement('canvas'); c.width = 256; c.height = 64;
    const ctx = c.getContext('2d'); ctx.font = '600 28px system-ui'; ctx.fillStyle = color; ctx.textAlign = 'center';
    ctx.fillText(text, 128, 42);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthTest: false }));
    sp.scale.set(1.6, 0.4, 1); sp.position.y = y; parent.add(sp); return sp;
  }

  function upsertObject(r) {
    let o = objects.get(r.object);
    if (!o) {
      o = new THREE.Group();
      o.add(new THREE.Mesh(new THREE.SphereGeometry(0.16, 20, 20), new THREE.MeshStandardMaterial({ color: 0x3ddc97, emissive: 0x0b3d2a })));
      o.children[0].position.y = 0.16;
      addLabel(o, r.object, 0.6, '#3ddc97');
      scene.add(o); objects.set(r.object, o);
    }
    const [x, z] = toXZ(r.ang || 0, r.dist || 2);
    o.userData.target = new THREE.Vector3(x, 0, z);
    o.userData.visible = r.visible_now; o.userData.age = r.last_seen_s_ago;
    // trail
    if (trails.has(r.object)) scene.remove(trails.get(r.object));
    if (r.trail && r.trail.length > 1) {
      const pts = r.trail.map(([a, d]) => { const [tx, tz] = toXZ(a, d); return new THREE.Vector3(tx, 0.05, tz); });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x3ddc97, transparent: true, opacity: 0.35 }));
      scene.add(line); trails.set(r.object, line);
    }
  }

  function setFixtures(fx) {
    fixtures.forEach((m) => scene.remove(m)); fixtures.clear();
    Object.entries(fx || {}).forEach(([name, f]) => {
      const g = new THREE.Group();
      const wide = ['sofa', 'bed', 'table', 'desk', 'shelf', 'wardrobe'].includes(name);
      g.add(new THREE.Mesh(new THREE.BoxGeometry(wide ? 1.6 : 0.9, name === 'door' ? 2.0 : 0.8, 0.35), new THREE.MeshStandardMaterial({ color: 0x6ab3ff, emissive: 0x11284d, transparent: true, opacity: 0.85 })));
      g.children[0].position.y = name === 'door' ? 1.0 : 0.4;
      addLabel(g, name, name === 'door' ? 2.3 : 1.1, '#9cc3ff');
      const [x, z] = toXZ(f.ang, Math.min(f.dist || 3.2, R - 0.25));
      g.position.set(x, 0, z); g.lookAt(0, 0, 0);
      scene.add(g); fixtures.set(name, g);
    });
  }

  bus.addEventListener('memory', (e) => {
    const names = new Set(e.detail.map((r) => r.object));
    objects.forEach((o, name) => { if (!names.has(name)) { scene.remove(o); objects.delete(name); if (trails.has(name)) { scene.remove(trails.get(name)); trails.delete(name); } } });
    e.detail.forEach(upsertObject);
  });
  bus.addEventListener('map', (e) => { const m = e.detail; setFixtures(m.fixtures); you.rotation.y = -THREE.MathUtils.degToRad(m.view_angle || 0);
    const info = document.getElementById('room-info');
    info.textContent = m.scanned_at ? `scanned ${new Date(m.scanned_at * 1000).toLocaleTimeString()} · ${Object.keys(m.fixtures || {}).length} fixtures: ${Object.keys(m.fixtures || {}).join(', ') || 'none'} · ${(m.objects || []).length} remembered objects` : 'no scan yet · press Scan room and slowly turn the camera around once (about 30 s)';
  });
  bus.addEventListener('highlight', (e) => { highlight = new Set(e.detail); });
  bus.addEventListener('scanframe', (e) => { you.rotation.y = -THREE.MathUtils.degToRad(e.detail.angle || 0); });
  bus.addEventListener('timeline', (e) => {   // scrubbing: move objects to historical positions
    const objs = e.detail; if (!objs) return;
    objects.forEach((o, name) => { if (objs[name]) { const [x, z] = toXZ(objs[name][0], objs[name][1]); o.userData.target = new THREE.Vector3(x, 0, z); o.userData.ghost = false; } else o.userData.ghost = true; });
  });

  // interaction
  let drag = null;
  canvas.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, t: orbit.theta, p: orbit.phi }; });
  window.addEventListener('pointermove', (e) => { if (!drag) return; orbit.theta = drag.t - (e.clientX - drag.x) * 0.008; orbit.phi = Math.min(1.45, Math.max(0.25, drag.p - (e.clientY - drag.y) * 0.006)); placeCam(); });
  window.addEventListener('pointerup', () => { drag = null; });
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); orbit.r = Math.min(16, Math.max(4, orbit.r + e.deltaY * 0.01)); placeCam(); }, { passive: false });

  function resize() { const w = holder.clientWidth, h = holder.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); }
  window.addEventListener('resize', resize); resize();
  bus.addEventListener('tab', (e) => { if (e.detail === 'room') setTimeout(resize, 30); });

  let t = 0, autoSpin = true;
  canvas.addEventListener('pointerdown', () => { autoSpin = false; });
  function animate() {
    requestAnimationFrame(animate); t += 0.016;
    if (autoSpin) { orbit.theta += 0.0015; placeCam(); }
    objects.forEach((o, name) => {
      if (o.userData.target) o.position.lerp(o.userData.target, 0.08);
      const m = o.children[0].material;
      const hl = highlight.has(name);
      m.emissiveIntensity = hl ? 1.5 + Math.sin(t * 8) * 0.8 : 0.4;
      o.children[0].scale.setScalar(hl ? 1.5 + Math.sin(t * 8) * 0.25 : 1);
      m.opacity = o.userData.ghost ? 0.25 : o.userData.visible ? 1 : 0.6; m.transparent = true;
    });
    fixtures.forEach((g, name) => { const m = g.children[0].material; m.emissiveIntensity = highlight.has(name) ? 1.5 + Math.sin(t * 8) * 0.8 : 0.5; });
    renderer.render(scene, cam);
  }
  animate();
})();
