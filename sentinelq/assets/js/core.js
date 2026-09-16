// PEMA frontend core: socket, tabs, chat, trace, session, memory list, inference, actions, voice.
// Other modules: room3d.js (map), camera.js (live + scan), timeline.js.

if (location.search.includes('demo=1')) localStorage.setItem('pema_user', 'Roshni');   // screenshot/demo bypass
if (!localStorage.getItem('pema_user')) location.replace('login.html');
const $ = (s) => document.querySelector(s);
const ui = new WebUI();
window.PEMA = { ui, memory: [], map: null, highlight: [] };
const bus = new EventTarget();          // internal events for the other modules
window.PEMA.bus = bus;

// ---------- tabs
document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === b));
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab));
  bus.dispatchEvent(new CustomEvent('tab', { detail: b.dataset.tab }));
}));

// deep link to a tab: app.html?tab=system
const _tab = new URLSearchParams(location.search).get('tab');
if (_tab) { const b = document.querySelector(`.tab[data-tab="${_tab}"]`); if (b) b.click(); }

// ---------- helpers
const log = $('#log'), trace = $('#trace');
function add(cls, text) {
  if (log.firstElementChild && log.firstElementChild.classList.contains('hint')) log.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'msg ' + cls; el.textContent = text;
  log.appendChild(el); log.scrollTop = log.scrollHeight;
}
function setPill(id, text, cls) { const p = $(id); p.textContent = text; p.className = 'pill' + (cls ? ' ' + cls : ''); }
function ago(s) { return s < 5 ? 'now' : s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; }
function isNpu(p) { return p && /htp|npu|hexagon/i.test(String(p.device || '')); }
window.PEMA.add = add;

// ---------- connection
ui.on_connect(() => setPill('#conn-pill', 'board connected', 'ok'));
ui.on_disconnect(() => setPill('#conn-pill', 'board disconnected', 'bad'));
setTimeout(() => { if ($('#conn-pill').textContent !== 'board connected') setPill('#conn-pill', 'not connected: refresh', 'bad'); }, 6000);

// ---------- status / model / inference
function showLlm(info) {
  if (!info || info.backend === 'none') return setPill('#llm-pill', 'model: not connected', 'bad');
  const p = info.profile, name = (info.model || '').split('/').pop();
  const tps = p && p.decode_speed ? ` · ${Math.round(p.decode_speed)} tok/s` : '';
  setPill('#llm-pill', `GenieX on Hexagon NPU${tps} · ${name}`, 'npu');
  $('#inf-model').textContent = 'model: ' + (info.model || '–') + (info.vlm ? ' (text + vision)' : '');
  if (info.stats) showInference({ last: p, stats: info.stats });
}
function showInference(d) {
  const p = d.last || {};
  $('#inf-device').textContent = isNpu(p) ? 'Hexagon NPU' : (p.device || '–');
  $('#inf-tps').textContent = p.decode_speed ? Math.round(p.decode_speed) : '–';
  $('#inf-ttft').textContent = p.ttft != null ? Number(p.ttft).toFixed(2) : '–';
  $('#inf-prompt').textContent = p.prompt_tokens ?? '–';
  $('#inf-gen').textContent = p.generated_tokens ?? '–';
  if (d.stats) $('#inf-steps').textContent = d.stats.steps;
}
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

// ---------- sensors
ui.on_message('sensors', (s) => {
  const g = $('#sensors'); g.innerHTML = '';
  const labels = { temp_c: 'temp °C', door: 'door', light: 'light', relay: 'light relay', uptime_s: 'MCU uptime s' };
  const order = ['relay', 'temp_c', 'light', 'door', 'uptime_s'];
  Object.entries(s || {}).sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).forEach(([k, v]) => {
    let val = typeof v === 'number' ? Math.round(v * 10) / 10 : v;
    if (k === 'door') val = v ? 'open' : 'closed';
    if (k === 'relay') val = v ? 'on' : 'off';
    g.insertAdjacentHTML('beforeend', `<div class="stat"><b>${val}</b><span>${labels[k] || k}</span></div>`);
  });
  ['temp_c', 'light', 'door'].filter((k) => !(k in (s || {}))).forEach((k) =>
    g.insertAdjacentHTML('beforeend', `<div class="stat"><b class="hint">—</b><span>${labels[k]} · no sensor wired</span></div>`));
});

// ---------- detections + memory
ui.on_message('detections', (d) => {
  const cur = d.current || {};
  const box = $('#detections');
  box.innerHTML = Object.keys(cur).length ? '' : '<span class="hint">no objects in view</span>';
  Object.entries(cur).forEach(([label, conf]) => box.insertAdjacentHTML('beforeend', `<span class="chip">${label} ${Math.round(conf * 100)}%</span>`));
  window.PEMA.memory = d.memory || [];
  if (d.view_angle !== undefined) { $('#view-angle').value = d.view_angle; $('#view-angle-val').textContent = Math.round(d.view_angle) + '°'; }
  const mem = $('#memory');
  mem.innerHTML = window.PEMA.memory.length ? '' : '<div class="hint">nothing remembered yet</div>';
  window.PEMA.memory.forEach((r) => {
    const el = document.createElement('div');
    el.className = 'mem' + (r.visible_now ? ' now' : '') + (window.PEMA.highlight.includes(r.object) ? ' hl' : '');
    const usual = r.sightings >= 5 && r.usual_spot !== r.position ? `<div class="hint">usually ${r.usual_spot}</div>` : '';
    el.innerHTML = `<b>${r.object}</b><span>${r.position}${usual}</span><span class="ago">${ago(r.last_seen_s_ago)}</span>`;
    el.addEventListener('click', () => { bus.dispatchEvent(new CustomEvent('highlight', { detail: [r.object] })); sendCommand(`Where is the ${r.object}?`); });
    mem.appendChild(el);
  });
  bus.dispatchEvent(new CustomEvent('memory', { detail: window.PEMA.memory }));
});
ui.on_message('roommap', (m) => { window.PEMA.map = m; if (m.room) $('#room-name').textContent = m.room; bus.dispatchEvent(new CustomEvent('map', { detail: m })); });
ui.on_message('highlight', (h) => {
  const names = h.objects || (h.object ? [h.object] : []);
  window.PEMA.highlight = names;
  bus.dispatchEvent(new CustomEvent('highlight', { detail: names }));
  document.querySelectorAll('.mem').forEach((el) => el.classList.toggle('hl', names.includes(el.querySelector('b').textContent)));
  setTimeout(() => { window.PEMA.highlight = []; bus.dispatchEvent(new CustomEvent('highlight', { detail: [] })); document.querySelectorAll('.mem.hl').forEach((el) => el.classList.remove('hl')); }, 8000);
});

// ---------- agent trace + speech
function addStep(s) {
  if (trace.firstElementChild && trace.firstElementChild.classList.contains('hint')) trace.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'step ' + (s.error ? 'error' : s.role);
  let meta = '';
  if (s.tool) meta += `${s.tool}${s.args ? ' ' + JSON.stringify(s.args) : ''}`;
  if (s.profile) meta += `${meta ? ' · ' : ''}${isNpu(s.profile) ? 'NPU' : s.profile.device} ${Math.round(s.profile.decode_speed || 0)} tok/s, ttft ${Number(s.profile.ttft || 0).toFixed(1)}s`;
  if (s.role === 'hardware' && s.verified !== undefined) meta += `${meta ? ' · ' : ''}${s.verified ? '✓ verified by MCU' : '✗ not verified'}`;
  el.innerHTML = `<span class="role">${s.role}</span><div><div class="txt"></div>${meta ? '<div class="meta"></div>' : ''}</div>`;
  el.querySelector('.txt').textContent = s.text || '';
  if (meta) el.querySelector('.meta').textContent = meta;
  trace.appendChild(el); trace.scrollTop = trace.scrollHeight;
  if (s.role === 'vision') add('info', 'vision: ' + s.text);
  if (s.role === 'memory') add('info', 'memory: ' + s.text);
}
ui.on_message('agent_step', addStep);
$('#clear-trace').addEventListener('click', () => { trace.innerHTML = '<div class="hint">steps appear here</div>'; });
ui.on_message('agent', (a) => { if (a.error) add('err', 'model error: ' + a.error); });
ui.on_message('say', (m) => {
  add('bot', m.text);
  if ($('#speak').checked && 'speechSynthesis' in window) { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(m.text)); }
});

// ---------- actions (verified)
let audioCtx = null;
function beep(pattern) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const reps = pattern === 'double' ? 2 : 1, len = pattern === 'long' ? 0.7 : 0.15;
    for (let i = 0; i < reps; i++) {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = 2000; o.connect(g); g.connect(audioCtx.destination);
      const t = audioCtx.currentTime + i * (len + 0.1);
      g.gain.setValueAtTime(0.2, t); g.gain.setValueAtTime(0, t + len); o.start(t); o.stop(t + len + 0.01);
    }
  } catch (e) { /* audio needs a user gesture first */ }
}
function describeAction(act) {
  return act.type === 'matrix' ? `matrix → ${act.icon}` : act.type === 'buzzer' ? `buzzer → ${act.pattern}` :
    act.type === 'relay' ? `light → ${act.on ? 'on' : 'off'}` : act.type === 'servo' ? `servo → ${act.angle}°` : JSON.stringify(act);
}
ui.on_message('action', (a) => {
  const box = $('#actions');
  if (box.firstElementChild && box.firstElementChild.classList.contains('hint')) box.innerHTML = '';
  const el = document.createElement('div'); el.className = 'act';
  if (!a.ok) { el.innerHTML = `<span>${describeAction(a.raw || a.action)}</span><span class="x">✗ ${a.error}</span>`; box.prepend(el); return addStep({ role: 'safety', text: `vetoed: ${a.error}`, error: true }); }
  if (a.action.type === 'buzzer' && a.action.pattern !== 'off') beep(a.action.pattern);
  el.innerHTML = `<span>${describeAction(a.action)} <span class="hint">· ${a.source}</span></span><span class="${a.verified ? 'v' : 'x'}">${a.verified ? '✓ verified' : '✗'} <span class="hint">${a.detail || ''}</span></span>`;
  box.prepend(el); while (box.children.length > 12) box.removeChild(box.lastChild);
  if (a.source === 'dashboard') addStep({ role: 'hardware', text: `manual: ${describeAction(a.action)}`, verified: a.verified });
});

// ---------- session
ui.on_message('presence', (p) => {
  if (p.present) setPill('#presence-pill', p.owner && p.owner_verified ? p.owner.name + ' present' : 'someone present', 'present');
  else setPill('#presence-pill', p.away_for_s ? `away ${ago(p.away_for_s)}` : 'no one present', 'away');
  if (p.owner) { $('#enroll-box').hidden = true; $('#owner-box').hidden = false; $('#owner-name').textContent = p.owner.name; $('#owner-verified').textContent = p.owner_verified ? '✓ verified this session' : 'say your passphrase to verify'; }
  if (p.event === 'left') add('info', 'you left · watching your things');
  if (p.event === 'returned') add('info', `you're back · ${(p.last_changes || []).length} change(s) noticed`);
  const ch = p.last_changes || [], box = $('#changes');
  if (p.event === 'returned' || ch.length) {
    box.innerHTML = ch.length ? '' : '<span class="hint">nothing moved while you were away</span>';
    ch.forEach((c) => box.insertAdjacentHTML('beforeend', `<div class="change"><b>${c.object}</b> ${c.change}${c.was ? ` · was ${c.was}` : ''}${c.now ? ` · now ${c.now}` : ''}</div>`));
    if (ch.length) bus.dispatchEvent(new CustomEvent('highlight', { detail: ch.map((c) => c.object) }));
  }
});
$('#enroll-form').addEventListener('submit', (e) => { e.preventDefault(); ui.send_message('enroll', { name: $('#enroll-name').value.trim(), phrase: $('#enroll-phrase').value.trim() }); });

// ---------- commands
function sendCommand(text) { if (!text) return; add('user', text); ui.send_message('command', { text }); }
window.PEMA.sendCommand = sendCommand;
$('#cmd-form').addEventListener('submit', (e) => { e.preventDefault(); sendCommand($('#cmd').value.trim()); $('#cmd').value = ''; });
$('#autonomous').addEventListener('change', (e) => ui.send_message('set_mode', { autonomous: e.target.checked }));
document.querySelectorAll('button[data-action]').forEach((b) => b.addEventListener('click', () => ui.send_message('manual_action', JSON.parse(b.dataset.action))));
$('#forget').addEventListener('click', () => { if (confirm('Forget all remembered objects?')) ui.send_message('forget', {}); });
$('#view-angle').addEventListener('input', (e) => { $('#view-angle-val').textContent = e.target.value + '°'; });
$('#view-angle').addEventListener('change', (e) => ui.send_message('set_view', { angle: Number(e.target.value) }));
fetch('/history').then((r) => r.json()).then((h) => { $('#history-info').textContent = `${h.sightings} sightings · ${h.events} events · ${h.actions} actions · ${h.file}`; }).catch(() => {});

// ---------- voice input
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const mic = $('#mic');
if (!SR) { mic.disabled = true; mic.title = 'needs Chrome or Edge'; }
else {
  const rec = new SR(); rec.lang = 'en-US'; rec.interimResults = false; let listening = false;
  rec.onstart = () => { listening = true; mic.classList.add('on'); setPill('#status-pill', 'listening…', 'thinking'); };
  rec.onresult = (ev) => sendCommand(ev.results[0][0].transcript);
  rec.onerror = (ev) => add('err', 'microphone: ' + ev.error);
  rec.onend = () => { listening = false; mic.classList.remove('on'); setPill('#status-pill', 'idle', ''); };
  mic.addEventListener('click', () => { if (listening) rec.stop(); else rec.start(); });
}
