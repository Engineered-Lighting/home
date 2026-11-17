/* den-shell.js: the prototypes' page: the device frame, the transport, the toggles, and the loop that feeds the client.
 * A direction calls DenShell.mount({ ..., setup(ctx) }) and returns { frame(view, now, dt), layout?(name) }. */
(function (root) {
'use strict';
const LINKS = root.DEN_LINKS || {};
const DIRECTIONS = [['A', 'Caption'], ['B', 'Signal path'], ['C', 'Why the light'], ['D', 'Last two minutes'], ['check', 'Design check']];
const store = {
    get(key, fallback) { try { const v = localStorage.getItem('den-proto.' + key); return v == null ? fallback : v; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem('den-proto.' + key, value); } catch (_) {} },
};
const esc = text => String(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function mount({ letter, title, subtitle, notes = '', side = 0, phoneStage = '1fr', startAt = 18.5, setup }) {
    const shellRoot = document.createElement('div');
    document.body.appendChild(shellRoot);
    shellRoot.innerHTML = `
    <div class="page">
      <header class="top">
        <div class="top-title"><span class="eyebrow">Den · live inference · direction ${esc(letter)}</span><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>
        <nav class="nav" aria-label="Directions">${DIRECTIONS.map(([l, name]) => `<a href="${esc(LINKS[l] || '#')}" ${l === letter ? 'aria-current="page"' : ''} target="_top">${l.length === 1 ? l + ' · ' : ''}${esc(name)}</a>`).join('')}</nav>
      </header>
      <div class="toggles">
        <span class="seg-label">Device</span><div class="seg" id="seg-layout" role="group" aria-label="Device"><button id="lay-laptop" data-v="laptop">Laptop</button><button id="lay-phone" data-v="phone">Phone</button></div>
        <span class="seg-label">Theme</span><div class="seg" id="seg-theme" role="group" aria-label="Theme"><button id="th-auto" data-v="auto">Auto</button><button id="th-light" data-v="light">Light</button><button id="th-dark" data-v="dark">Dark</button></div>
        <span class="seg-label">Fields</span><div class="seg" id="seg-fields" role="group" aria-label="Fields"><button id="f-today" data-v="today" title="Only what den-live sends Home today">Today's stream</button><button id="f-proposed" data-v="proposed" title="Adds the fields this design asks den-live for">+ proposed fields</button></div>
      </div>
      <div class="device-wrap"><div class="device" id="device"><div class="app" id="app">
        <div class="stage" id="stage"><div class="overlay" id="overlay"></div>
          <div class="view-detents" role="group" aria-label="Zoom"><button id="v-overview" data-v="overview">Overview</button><button id="v-room" data-v="room">Room</button><button id="v-close" data-v="close">Close</button></div>
        </div>
        <aside class="side" id="side"></aside>
        <div class="bottom" id="bottom"></div>
      </div></div></div>
      <section class="transport" aria-label="Playback">
        <div class="transport-row">
          <button class="btn btn-play" id="play" aria-label="Pause">❚❚</button>
          <span class="clock" id="clock">0:00</span>
          <span class="chapter" id="chapter"></span>
          <div class="seg" id="seg-speed" role="group" aria-label="Speed"><button class="speed" id="sp-1" data-v="1">1×</button><button class="speed" id="sp-2" data-v="2">2×</button><button class="speed" id="sp-4" data-v="4">4×</button></div>
        </div>
        <div class="scrub" id="scrub" role="slider" tabindex="0" aria-label="Story time" aria-valuemin="0" aria-valuemax="96"><div class="scrub-track"></div><div class="scrub-fill" id="scrub-fill"></div><div class="scrub-head" id="scrub-head"></div></div>
        <div class="inject"><span class="seg-label">Make it happen now</span>
          <button class="btn" id="x-dark">Too dark · 8 s</button><button class="btn" id="x-stall">Stream stops · 6 s</button>
          <button class="btn" id="x-qwen">Qwen fails · 20 s</button><button class="btn" id="x-stop">Stop</button><button class="btn" id="x-clear">Clear these</button></div>
      </section>
      <section class="notes">${notes}</section>
    </div>`;

    const $ = id => document.getElementById(id);
    const sim = root.DenSim.create();
    const client = root.DenClient.create(sim);
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stageHost = $('stage');
    const stage = root.DenStage.create(stageHost, sim, { reducedMotion: () => reduce.matches });
    const device = $('device');
    const state = { t: startAt, playing: true, speed: 1, proposed: store.get('fields', 'proposed') !== 'today', layout: store.get('layout', window.innerWidth < 700 ? 'phone' : 'laptop') };
    const listeners = { layout: [] };

    function pressed(group, value) { for (const b of $(group).querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.v === String(value))); }
    function setLayout(v) {
        state.layout = v; device.dataset.layout = v; store.set('layout', v); pressed('seg-layout', v);
        document.getElementById('app').style.setProperty('--side', v === 'phone' ? '0px' : side + 'px');
        document.getElementById('app').style.setProperty('--stage-share', phoneStage);
        for (const f of listeners.layout) f(v);
    }
    function setTheme(v) { if (v === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.dataset.theme = v; store.set('theme', v); pressed('seg-theme', v); }
    function setFields(v) { state.proposed = v === 'proposed'; store.set('fields', v); pressed('seg-fields', v); }
    $('seg-layout').addEventListener('click', e => e.target.dataset.v && setLayout(e.target.dataset.v));
    $('seg-theme').addEventListener('click', e => e.target.dataset.v && setTheme(e.target.dataset.v));
    $('seg-fields').addEventListener('click', e => e.target.dataset.v && setFields(e.target.dataset.v));
    $('seg-speed').addEventListener('click', e => { if (e.target.dataset.v) { state.speed = Number(e.target.dataset.v); pressed('seg-speed', state.speed); } });
    for (const v of ['overview', 'room', 'close']) $('v-' + v).addEventListener('click', () => { stage.setView(v); syncView(); });
    function syncView() { for (const v of ['overview', 'room', 'close']) $('v-' + v).setAttribute('aria-pressed', String(stage.view === v)); }
    $('play').addEventListener('click', () => { state.playing = !state.playing; $('play').textContent = state.playing ? '❚❚' : '▶'; $('play').setAttribute('aria-label', state.playing ? 'Pause' : 'Play'); });

    function seek(t) {
        state.t = Math.max(0, t);
        client.reset();
        client.feed(sim.events(Math.max(0, state.t - 150), state.t), state.t);
    }
    const scrub = $('scrub');
    const loopStart = () => Math.floor(state.t / sim.LOOP_S) * sim.LOOP_S;
    function scrubTo(clientX) { const r = scrub.getBoundingClientRect(); seek(loopStart() + Math.max(0, Math.min(0.999, (clientX - r.left) / r.width)) * sim.LOOP_S); }
    let scrubbing = false;
    scrub.addEventListener('pointerdown', e => { scrubbing = true; scrub.setPointerCapture(e.pointerId); scrubTo(e.clientX); });
    scrub.addEventListener('pointermove', e => { if (scrubbing) scrubTo(e.clientX); });
    scrub.addEventListener('pointerup', () => { scrubbing = false; });
    scrub.addEventListener('keydown', e => { if (e.key === 'ArrowRight') seek(state.t + 2); if (e.key === 'ArrowLeft') seek(state.t - 2); });
    for (const c of sim.CHAPTERS) {
        if (c.t === 0) continue;
        const tick = document.createElement('button');
        tick.className = 'scrub-tick'; tick.style.left = (c.t / sim.LOOP_S * 100) + '%'; tick.title = c.label; tick.type = 'button';
        tick.style.border = '0'; tick.style.background = 'transparent'; tick.style.padding = '0 0 18px'; tick.style.cursor = 'pointer';
        tick.textContent = ''; tick.setAttribute('aria-label', 'Jump to ' + c.label);
        tick.addEventListener('pointerdown', e => { e.stopPropagation(); seek(loopStart() + c.t - 0.5); });
        scrub.appendChild(tick);
    }
    const inj = (kind, dur) => () => { sim.inject(kind, state.t, state.t + dur); seek(state.t); drawFaults(); };
    $('x-dark').addEventListener('click', inj('dark', 8));
    $('x-stall').addEventListener('click', inj('stall', 6));
    $('x-qwen').addEventListener('click', inj('qwen', 20));
    $('x-stop').addEventListener('click', () => { sim.inject('stop', state.t); seek(state.t); drawFaults(); });
    $('x-clear').addEventListener('click', () => { sim.clearFaults(); seek(state.t); drawFaults(); });
    function drawFaults() {
        for (const el of scrub.querySelectorAll('.scrub-fault')) el.remove();
        const k0 = loopStart();
        for (const f of sim.faults) {
            const a = Math.max(0, f.t0 - k0), b = Math.min(sim.LOOP_S, (f.t1 === Infinity ? state.t + 1 : f.t1) - k0);
            if (b <= 0 || a >= sim.LOOP_S) continue;
            const el = document.createElement('div'); el.className = 'scrub-fault';
            el.style.left = (a / sim.LOOP_S * 100) + '%'; el.style.width = Math.max(0.4, (b - a) / sim.LOOP_S * 100) + '%'; el.title = f.kind;
            scrub.appendChild(el);
        }
        $('x-stop').textContent = sim.stopped(state.t) ? 'Resume' : 'Stop';
    }

    const ctx = { sim, client, stage, overlay: $('overlay'), side: $('side'), bottom: $('bottom'), stageEl: stageHost, device,
        get proposed() { return state.proposed; }, get layout() { return state.layout; }, get t() { return state.t; }, seek,
        onLayout(f) { listeners.layout.push(f); }, reduced: () => reduce.matches, esc };
    const direction = setup(ctx) || {};

    setTheme(store.get('theme', 'auto')); setFields(state.proposed ? 'proposed' : 'today'); pressed('seg-speed', 1); setLayout(state.layout);
    syncView();
    seek(state.t);

    let last = performance.now(), lastClock = '', lastChapter = '';
    function loop(nowMs) {
        const real = Math.min(0.1, (nowMs - last) / 1000); last = nowMs;
        const prev = state.t;
        if (state.playing && !document.hidden) state.t += real * state.speed;
        if (state.t > prev) client.feed(sim.events(prev, state.t), state.t);
        const view = client.view(state.t, { proposed: state.proposed });
        const dt = state.playing ? real * state.speed : 0;
        stage.frame(view, state.t, dt);
        direction.frame?.(view, state.t, dt);
        const u = state.t - loopStart();
        const m = Math.floor(u / 60), sec = Math.floor(u % 60);
        const clock = `${m}:${String(sec).padStart(2, '0')} / 1:36`;
        if (clock !== lastClock) { $('clock').textContent = clock; lastClock = clock; scrub.setAttribute('aria-valuenow', String(Math.floor(u))); }
        const ch = sim.chapterAt(state.t).label;
        if (ch !== lastChapter) { $('chapter').textContent = ch; lastChapter = ch; drawFaults(); }
        $('scrub-fill').style.width = (u / sim.LOOP_S * 100) + '%'; $('scrub-head').style.left = (u / sim.LOOP_S * 100) + '%';
        requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
    window.DenProto = ctx;   // for checks in the console
    return ctx;
}

root.DenShell = Object.freeze({ mount, esc });
})(window);
