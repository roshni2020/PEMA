// Timeline: scrub through the last 30 minutes of sightings (SQLite on the board) and move the 3D objects accordingly.
(() => {
  const { bus } = window.PEMA;
  const $ = (s) => document.querySelector(s);
  const slider = $('#tl-slider'), tlTime = $('#tl-time'), tlInfo = $('#tl-info'), evBox = $('#tl-events');
  let data = null;

  async function load() {
    try {
      data = await (await fetch('/timeline?seconds=1800')).json();
      tlInfo.textContent = `${data.frames.length} time buckets · ${data.events.length} events · ${data.actions.length} actions`;
      evBox.innerHTML = '';
      const items = [...data.events.map((e) => ({ t: e.t, text: `${e.kind} ${e.detail && e.detail !== '[]' ? e.detail : ''}` })),
                     ...data.actions.map((a) => ({ t: a.t, text: `${a.source}: ${a.action} ${a.verified ? '✓ verified' : '✗'}` }))].sort((a, b) => b.t - a.t).slice(0, 40);
      items.forEach((i) => evBox.insertAdjacentHTML('beforeend', `<div class="ev"><span class="t">${new Date(i.t * 1000).toLocaleTimeString()}</span><span>${i.text}</span></div>`));
      slider.value = 100; tlTime.textContent = 'now';
    } catch (e) { tlInfo.textContent = 'timeline unavailable'; }
  }
  slider.addEventListener('input', () => {
    if (!data || !data.frames.length) return;
    const p = slider.value / 100;
    if (p >= 0.999) { tlTime.textContent = 'now'; bus.dispatchEvent(new CustomEvent('timeline', { detail: null })); return; }
    const t0 = data.frames[0].t, t1 = data.now, t = t0 + (t1 - t0) * p;
    let best = data.frames[0];
    for (const f of data.frames) if (f.t <= t) best = f;
    tlTime.textContent = new Date(t * 1000).toLocaleTimeString() + ' · ' + Object.keys(best.objects).join(', ');
    bus.dispatchEvent(new CustomEvent('timeline', { detail: best.objects }));
    document.querySelector('.tab[data-tab="room"]').classList.contains('active') || null;
  });
  $('#tl-refresh').addEventListener('click', load);
  bus.addEventListener('tab', (e) => { if (e.detail === 'timeline') load(); });
})();
