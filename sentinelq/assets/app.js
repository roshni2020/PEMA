// SentinelQ dashboard. Talks to the Python app over socket.io (libs/arduino.js).

const $ = (s) => document.querySelector(s);
const log = $('#log'), trace = $('#trace');
const ui = new WebUI();

function add(cls, text) {
  if (log.firstElementChild && log.firstElementChild.classList.contains('hint')) log.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'msg ' + cls;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function setPill(id, text, cls) {
  const p = $(id);
  p.textContent = text;
  p.className = 'pill' + (cls ? ' ' + cls : '');
}

function isNpu(p) { return p && /htp|npu|hexagon/i.test(String(p.device || '')); }

function showLlm(info) {
  if (!info || info.backend === 'none') return setPill('#llm-pill', 'model: not connected', 'bad');
  const p = info.profile;
  const name = (info.model || '').split('/').pop();
  const tps = p && p.decode_speed ? ` · ${Math.round(p.decode_speed)} tok/s` : '';
  setPill('#llm-pill', `GenieX${isNpu(p) || /htp/i.test(p && p.device ? '' : 'htp') ? ' on Hexagon NPU' : ''}${tps} · ${name}`, 'npu');
  $('#inf-model').textContent = 'model: ' + (info.model || '–') + (info.vlm ? ' (text + vision)' : '');
  if (info.stats) showInference({ last: p, stats: info.stats });
}

function showInference(d) {
  const p = d.last || {};
  $('#inf-device').textContent = isNpu(p) ? 'Hexagon NPU' : (p.device || '–');
  $('#inf-tps').textContent = p.decode_speed ? Math.round(p.decode_speed) : '–';
  $('#inf-ttft').textContent = p.ttft != null ? Number(p.ttft).toFixed(2) : '–';
  $('#inf-prompt').textContent = p.prompt_tokens != null ? p.prompt_tokens : '–';
  $('#inf-gen').textContent = p.generated_tokens != null ? p.generated_tokens : '–';
  if (d.stats) $('#inf-steps').textContent = d.stats.steps;
}

// ---------- connection
ui.on_connect(() => { setPill('#conn-pill', 'board connected', 'ok'); });
ui.on_disconnect(() => { setPill('#conn-pill', 'board disconnected', 'bad'); });
window.addEventListener('load', () => setTimeout(() => { if ($('#conn-pill').textContent !== 'board connected') setPill('#conn-pill', 'not connected: refresh', 'bad'); }, 5000));

// ---------- board -> page
ui.on_message('status', (s) => {
  if (s.state === 'thinking') setPill('#status-pill', 'thinking…', 'thinking');
  else if (s.state === 'busy') setPill('#status-pill', 'busy', 'bad');
  else setPill('#status-pill', 'idle', '');
  if (s.autonomous !== undefined) $('#autonomous').checked = !!s.autonomous;
  if (s.llm) showLlm(s.llm);
  if (s.note) add('info', s.note);
  if (s.state === 'thinking' && s.source && s.source !== 'dashboard') add('user', `[${s.source}] ${s.command}`);
});

ui.on_message('inference', showInference);

ui.on_message('sensors', (s) => {
  const g = $('#sensors');
  g.innerHTML = '';
  const labels = { temp_c: 'temp °C', door: 'door', light: 'light', relay: 'light relay', uptime_s: 'MCU uptime s' };
  const order = ['relay', 'temp_c', 'light', 'door', 'uptime_s'];
  const entries = Object.entries(s || {}).sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  entries.forEach(([k, v]) => {
    const el = document.createElement('div');
    el.className = 'stat';
    let val = typeof v === 'number' ? Math.round(v * 10) / 10 : v;
    if (k === 'door') val = v ? 'open' : 'closed';
    if (k === 'relay') val = v ? 'on' : 'off';
    el.innerHTML = `<b>${val}</b><span>${labels[k] || k}</span>`;
    g.appendChild(el);
  });
  ['temp_c', 'light', 'door'].filter((k) => !(k in (s || {}))).forEach((k) => {
    const el = document.createElement('div');
    el.className = 'stat';
    el.innerHTML = `<b class="hint">—</b><span>${labels[k]} · no sensor wired</span>`;
    g.appendChild(el);
  });
});

function ago(s) { return s < 5 ? 'now' : s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; }

ui.on_message('detections', (d) => {
  const cur = d.current || {};
  const box = $('#detections');
  box.innerHTML = Object.keys(cur).length ? '' : '<span class="hint">no objects in view</span>';
  Object.entries(cur).forEach(([label, conf]) => {
    const c = document.createElement('span');
    c.className = 'chip';
    c.textContent = `${label} ${Math.round(conf * 100)}%`;
    box.appendChild(c);
  });
  const mem = $('#memory');
  const rows = d.memory || [];
  mem.innerHTML = rows.length ? '' : '<div class="hint">nothing remembered yet</div>';
  rows.forEach((r) => {
    const el = document.createElement('div');
    el.className = 'mem' + (r.visible_now ? ' now' : '');
    const usual = r.sightings >= 5 && r.usual_spot !== r.position ? `<div class="hint">usually ${r.usual_spot} · ${r.sightings} sightings</div>` : (r.sightings >= 5 ? `<div class="hint">${r.sightings} sightings</div>` : '');
    el.innerHTML = `<b>${r.object}</b><span class="pos">${r.position}${usual}</span><span class="ago">${ago(r.last_seen_s_ago)}</span>`;
    mem.appendChild(el);
  });
});

function addStep(s) {
  if (trace.firstElementChild && trace.firstElementChild.classList.contains('hint')) trace.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'step ' + (s.error ? 'error' : s.role);
  let meta = '';
  if (s.tool) meta += `tool: ${s.tool}${s.args ? ' ' + JSON.stringify(s.args) : ''}`;
  if (s.profile) meta += `${meta ? ' · ' : ''}${isNpu(s.profile) ? 'NPU' : s.profile.device} ${Math.round(s.profile.decode_speed || 0)} tok/s, ttft ${Number(s.profile.ttft || 0).toFixed(1)}s`;
  if (s.actions && !s.tool) meta += JSON.stringify(s.actions);
  if (s.role === 'hardware' && s.verified !== undefined) meta += `${meta ? ' · ' : ''}${s.verified ? '✓ verified by MCU readback' : '✗ not verified'}`;
  el.innerHTML = `<span class="role">${s.role}</span><div><div class="txt"></div>${meta ? `<div class="meta"></div>` : ''}</div>`;
  el.querySelector('.txt').textContent = s.text || '';
  if (meta) el.querySelector('.meta').textContent = meta;
  trace.appendChild(el);
  trace.scrollTop = trace.scrollHeight;
  if (s.role === 'vision') add('info', 'vision: ' + s.text);
  if (s.role === 'memory') add('info', 'memory: ' + s.text);
}
ui.on_message('agent_step', addStep);
$('#clear-trace').addEventListener('click', () => { trace.innerHTML = '<div class="hint">agent steps appear here</div>'; });

ui.on_message('agent', (a) => { if (a.error) add('err', 'model error: ' + a.error); });

ui.on_message('say', (m) => {
  add('bot', m.text);
  if ($('#speak').checked && 'speechSynthesis' in window) {
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(m.text));
  }
});

// Beep on this device too, so the buzzer action is audible even with no buzzer wired to D8.
let audioCtx = null;
function beep(pattern) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const reps = pattern === 'double' ? 2 : 1, len = pattern === 'long' ? 0.7 : 0.15;
    for (let i = 0; i < reps; i++) {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = 2000; o.connect(g); g.connect(audioCtx.destination);
      const t = audioCtx.currentTime + i * (len + 0.1);
      g.gain.setValueAtTime(0.2, t); g.gain.setValueAtTime(0, t + len);
      o.start(t); o.stop(t + len + 0.01);
    }
  } catch (e) { /* audio blocked until the user interacts with the page */ }
}

function describeAction(act) {
  return act.type === 'matrix' ? `matrix → ${act.icon}` : act.type === 'buzzer' ? `buzzer → ${act.pattern}` :
    act.type === 'relay' ? `light → ${act.on ? 'on' : 'off'}` : act.type === 'servo' ? `servo → ${act.angle}°` :
    act.type === 'led' ? `led → rgb(${act.r},${act.g},${act.b})` : JSON.stringify(act);
}

ui.on_message('action', (a) => {
  const box = $('#actions');
  if (box.firstElementChild && box.firstElementChild.classList.contains('hint')) box.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'act';
  if (!a.ok) {
    el.innerHTML = `<span>${describeAction(a.raw || a.action)}</span><span class="x">✗ ${a.error}</span>`;
    box.prepend(el);
    return addStep({ role: 'safety', text: `vetoed ${JSON.stringify(a.raw || a.action)}: ${a.error}`, error: true });
  }
  const act = a.action;
  if (act.type === 'buzzer' && act.pattern !== 'off') beep(act.pattern);
  el.innerHTML = `<span>${describeAction(act)} <span class="hint">· ${a.source}</span></span><span class="${a.verified ? 'v' : 'x'}">${a.verified ? '✓ verified' : '✗ unverified'} <span class="hint">${a.detail || ''}</span></span>`;
  box.prepend(el);
  while (box.children.length > 12) box.removeChild(box.lastChild);
  if (a.source === 'dashboard') addStep({ role: 'hardware', text: `manual: ${describeAction(act)} ${a.verified ? '✓ verified by MCU' : '✗ not verified'}` });
});

// ---------- session: enrollment, presence, changes
ui.on_message('presence', (p) => {
  if (p.present) setPill('#presence-pill', (p.owner && p.owner_verified ? p.owner.name + ' present' : 'someone present'), 'present');
  else setPill('#presence-pill', p.away_for_s ? `away ${ago(p.away_for_s)}` : 'no one present', 'away');
  if (p.owner) {
    $('#enroll-box').hidden = true; $('#owner-box').hidden = false;
    $('#owner-name').textContent = p.owner.name;
    $('#owner-verified').textContent = p.owner_verified ? '✓ verified this session' : 'say your passphrase to verify';
  }
  if (p.event === 'left') add('info', 'you left · watching your things');
  if (p.event === 'returned') add('info', `you're back · ${(p.last_changes || []).length} change(s) noticed`);
  const box = $('#changes');
  const ch = p.last_changes || [];
  if (p.event === 'returned' || ch.length) {
    box.innerHTML = ch.length ? '' : '<span class="hint">nothing moved while you were away</span>';
    ch.forEach((c) => {
      const el = document.createElement('div');
      el.className = 'change';
      el.innerHTML = `<b>${c.object}</b> ${c.change}${c.was ? ` · was ${c.was}` : ''}${c.now ? ` · now ${c.now}` : ''}`;
      box.appendChild(el);
    });
  }
});

$('#enroll-form').addEventListener('submit', (e) => {
  e.preventDefault();
  ui.send_message('enroll', { name: $('#enroll-name').value.trim(), phrase: $('#enroll-phrase').value.trim() });
});

ui.on_message('wake', () => add('info', 'wake word heard'));

// ---------- page -> board
function sendCommand(text) {
  if (!text) return;
  add('user', text);
  ui.send_message('command', { text });
}

$('#cmd-form').addEventListener('submit', (e) => {
  e.preventDefault();
  sendCommand($('#cmd').value.trim());
  $('#cmd').value = '';
});

$('#autonomous').addEventListener('change', (e) => ui.send_message('set_mode', { autonomous: e.target.checked }));

document.querySelectorAll('button[data-action]').forEach((b) =>
  b.addEventListener('click', () => ui.send_message('manual_action', JSON.parse(b.dataset.action))));

// ---------- browser camera -> board (YOLOX on the QRB2210), ~1 frame every 1.5 s
const video = $('#cam-video'), canvas = $('#cam-canvas'), result = $('#cam-result'), camInfo = $('#cam-info');
let camTimer = null, frameInFlight = false;

function sendFrame() {
  if (frameInFlight || video.readyState < 2) return;
  // 480 px wide is plenty for YOLOX-nano and cuts transfer + decode time on the board
  canvas.width = 480; canvas.height = Math.round(480 * video.videoHeight / video.videoWidth) || 360;
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  const b64 = canvas.toDataURL('image/jpeg', 0.6).split(',')[1];
  frameInFlight = true;
  ui.send_message('frame', { image: b64 });
  setTimeout(() => { frameInFlight = false; }, 8000);   // never wedge if a result is lost
}

ui.on_message('frame_result', (r) => {
  frameInFlight = false;
  if (r.error) return camInfo.textContent = 'detection error: ' + r.error;
  result.src = 'data:image/jpeg;base64,' + r.image;
  result.hidden = false; video.hidden = true;
  camInfo.textContent = `${r.count} object${r.count === 1 ? '' : 's'} · YOLOX ${r.ms} ms on the board`;
});

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    camInfo.textContent = 'camera needs https or http://127.0.0.1 (browser rule)';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
    video.srcObject = stream; video.hidden = false; $('#cam-start').hidden = true;
    camInfo.textContent = 'camera on · frames go to the board as fast as it can detect';
    if (!camTimer) camTimer = setInterval(sendFrame, 700);   // sendFrame skips while one is in flight
  } catch (e) {
    $('#cam-start').hidden = false;
    camInfo.textContent = 'camera blocked: ' + e.message + ' · click Enable camera and allow it';
  }
}
function stopCamera() {
  if (camTimer) { clearInterval(camTimer); camTimer = null; }
  if (video.srcObject) { video.srcObject.getTracks().forEach((t) => t.stop()); video.srcObject = null; }
  video.hidden = true; result.hidden = true; frameInFlight = false;
  camInfo.textContent = 'camera off · nothing is being captured';
  $('#cam-toggle').textContent = 'Camera on';
  $('#detections').innerHTML = '<span class="hint">camera off</span>';
}
$('#cam-toggle').addEventListener('click', () => {
  if (camTimer || video.srcObject) stopCamera();
  else { $('#cam-toggle').textContent = 'Camera off'; startCamera(); }
});
$('#cam-start').addEventListener('click', startCamera);
startCamera();

// ---------- voice input (browser speech recognition, uses this device's mic)
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const mic = $('#mic');
if (!SR) {
  mic.disabled = true;
  mic.title = 'Speech recognition needs Chrome or Edge';
} else {
  const rec = new SR();
  rec.lang = 'en-US';
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  let listening = false;
  rec.onstart = () => { listening = true; mic.classList.add('on'); setPill('#status-pill', 'listening…', 'thinking'); };
  rec.onresult = (ev) => sendCommand(ev.results[0][0].transcript);
  rec.onerror = (ev) => add('err', 'microphone: ' + ev.error + (ev.error === 'not-allowed' ? ' (allow the mic in the address bar)' : ''));
  rec.onend = () => { listening = false; mic.classList.remove('on'); setPill('#status-pill', 'idle', ''); };
  mic.addEventListener('click', () => { if (listening) rec.stop(); else rec.start(); });
}
