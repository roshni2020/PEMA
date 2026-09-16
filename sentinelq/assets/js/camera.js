// Camera module: live frames to the board, plus the room scan (pan the live camera, or play an uploaded video).
(() => {
  const { ui, bus, add } = window.PEMA;
  const $ = (s) => document.querySelector(s);
  const video = $('#cam-video'), canvas = $('#cam-canvas'), result = $('#cam-result'), camInfo = $('#cam-info');
  let camTimer = null, frameInFlight = false, scanning = false;

  function grab(src, w = 480) {
    canvas.width = w; canvas.height = Math.round(w * src.videoHeight / src.videoWidth) || Math.round(w * 0.75);
    canvas.getContext('2d').drawImage(src, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.6).split(',')[1];
  }

  // ---------- live loop
  function sendFrame() {
    if (scanning || frameInFlight || video.readyState < 2) return;
    frameInFlight = true;
    ui.send_message('frame', { image: grab(video) });
    setTimeout(() => { frameInFlight = false; }, 8000);
  }
  ui.on_message('frame_result', (r) => {
    frameInFlight = false;
    if (r.error) return camInfo.textContent = 'detection error: ' + r.error;
    result.src = 'data:image/jpeg;base64,' + r.image; result.hidden = false; video.hidden = true;
    camInfo.textContent = `${r.count} object${r.count === 1 ? '' : 's'} · YOLOX ${r.ms} ms on the board`;
  });
  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { camInfo.textContent = 'camera needs https or http://127.0.0.1'; return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
      video.srcObject = stream; video.hidden = false; $('#cam-start').hidden = true; $('#cam-toggle').textContent = 'Camera off';
      camInfo.textContent = 'camera on · frames go to the board as fast as it can detect';
      if (!camTimer) camTimer = setInterval(sendFrame, 700);
    } catch (e) { $('#cam-start').hidden = false; camInfo.textContent = 'camera blocked: ' + e.message; }
  }
  function stopCamera() {
    if (camTimer) { clearInterval(camTimer); camTimer = null; }
    if (video.srcObject) { video.srcObject.getTracks().forEach((t) => t.stop()); video.srcObject = null; }
    video.hidden = true; result.hidden = true; frameInFlight = false;
    camInfo.textContent = 'camera off · nothing is being captured'; $('#cam-toggle').textContent = 'Camera on';
    $('#detections').innerHTML = '<span class="hint">camera off</span>';
  }
  $('#cam-toggle').addEventListener('click', () => (camTimer || video.srcObject) ? stopCamera() : startCamera());
  $('#cam-start').addEventListener('click', startCamera);
  startCamera();

  // ---------- room scan
  const overlay = $('#scan-overlay'), scanText = $('#scan-text'), scanBar = $('#scan-bar');
  let scanResolve = null;
  ui.on_message('scan', (s) => {
    if (s.state === 'frame') { scanBar.style.width = Math.round(s.progress * 100) + '%'; scanText.textContent = `frame ${s.frame} · ${Math.round(s.angle)}° · ${s.objects.join(', ') || 'no objects'}${s.fixtures.length ? ' · fixtures: ' + s.fixtures.join(', ') : ''}${s.vision ? ' · NPU' : ''}`; bus.dispatchEvent(new CustomEvent('scanframe', { detail: s })); if (scanResolve) scanResolve(); }
    if (s.state === 'done') { overlay.hidden = true; scanning = false; add('info', `room scanned · fixtures: ${Object.keys(s.fixtures || {}).join(', ') || 'none'}`); }
    if (s.state === 'error') { scanText.textContent = 'scan error: ' + s.error; }
  });
  function waitFrame() { return new Promise((res) => { scanResolve = res; setTimeout(res, 25000); }); }

  async function runScan(getFrame, durationS, label) {
    if (scanning) return;
    scanning = true; overlay.hidden = false; scanBar.style.width = '0%'; scanText.textContent = label;
    document.querySelector('.tab[data-tab="room"]').click();
    ui.send_message('scan_start', { sweep_deg: 360, fixture_every: 3 });
    const n = Math.max(8, Math.round(durationS));          // ~1 frame per second of sweep
    for (let i = 0; i < n; i++) {
      const img = await getFrame(i / (n - 1));
      if (!img) break;
      ui.send_message('scan_frame', { image: img, progress: i / (n - 1) });
      await waitFrame();
    }
    ui.send_message('scan_finish', {});
  }

  // Scan by panning the live camera: 30 seconds, the user turns slowly around once.
  $('#scan-btn').addEventListener('click', async () => {
    if (!video.srcObject) await startCamera();
    if (!video.srcObject) return;
    let count = 3;
    overlay.hidden = false; scanText.textContent = 'get ready: turn slowly around once, starting in 3';
    await new Promise((r) => { const iv = setInterval(() => { count--; scanText.textContent = count > 0 ? `turn slowly around once, starting in ${count}` : 'go'; if (count <= 0) { clearInterval(iv); r(); } }, 1000); });
    const T = 30, t0 = performance.now();
    await runScan(async (p) => { const target = t0 + p * T * 1000; while (performance.now() < target) await new Promise((r) => setTimeout(r, 50)); return grab(video); }, T, 'scanning · keep turning slowly');
  });

  // Scan from an uploaded video: sampled at ~1 frame per second of video, decoded in the browser.
  $('#scan-video').addEventListener('change', async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const src = $('#scan-source'); src.src = URL.createObjectURL(file);
    await new Promise((r) => { src.onloadedmetadata = r; });
    const dur = Math.min(90, src.duration || 30);
    await runScan(async (p) => {
      src.currentTime = p * Math.max(0.1, dur - 0.1);
      await new Promise((r) => { src.onseeked = r; });
      return grab(src);
    }, dur, `scanning uploaded video (${Math.round(dur)} s)`);
    e.target.value = '';
  });
})();
