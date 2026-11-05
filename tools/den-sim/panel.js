/* Den simulation panel, injected by tools/den-sim/run.mjs into the unchanged Home.
 * A red strip, the hop list and one scripted button. It reads Home's real overlay,
 * presses Home's own view buttons and selects the fixture as a person would, and
 * posts only to /__sim/feed on this harness. There is no send control. */
(function () {
  'use strict';
  const STRIP_PX = 26;
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === 'style') node.style.cssText = value;
      else if (key.startsWith('data-') || key === 'role' || key.startsWith('aria-')) node.setAttribute(key, value);
      else node[key] = value;
    }
    node.append(...children);
    return node;
  };
  const fmt = (v, digits = 1) => Number.isFinite(v) ? v.toFixed(digits) : '–';
  const PILL = { REAL: '#1f7a3d', ADAPTER: '#a86a00', SCRIPTED: '#2a5fa8', MISSING: '#6b2b2b' };

  document.head.append(el('style', { textContent: `
    #root { position: fixed !important; top: ${STRIP_PX}px; left: 0; right: 0; bottom: 0; height: auto !important; width: auto !important; }
    #den-sim-strip { position: fixed; top: 0; left: 0; right: 0; height: ${STRIP_PX}px; z-index: 2147483000; background: #b00020; color: #fff;
      font: 700 13px/${STRIP_PX}px system-ui, sans-serif; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0 8px; box-sizing: border-box; }
    #den-sim-panel { position: fixed; right: 8px; top: ${STRIP_PX + 52}px; width: min(300px, calc(100vw - 16px)); max-height: calc(100vh - ${STRIP_PX + 64}px); overflow: auto;
      z-index: 2147483000; box-sizing: border-box; padding: 8px; background: rgba(12,14,16,.93); color: #e8e8e8; border: 1px solid #b00020; border-radius: 8px;
      font: 12px/1.35 system-ui, sans-serif; user-select: text; -webkit-user-select: text; }
    #den-sim-panel button { font: 600 12px system-ui, sans-serif; padding: 6px 8px; border-radius: 6px; border: 1px solid #555; background: #222; color: #fff; cursor: pointer; }
    #den-sim-panel button[data-den-sim-feed] { width: 100%; background: #b00020; border-color: #e0526a; margin-bottom: 5px; }
    #den-sim-panel button:disabled { opacity: .55; cursor: default; }
    #den-sim-panel .row { display: flex; gap: 5px; }
    #den-sim-panel .row button { flex: 1; font-weight: 500; }
    #den-sim-panel[data-collapsed="1"] [data-den-sim-body] { display: none; }
    #den-sim-panel dl { display: grid; grid-template-columns: auto 1fr; gap: 1px 8px; margin: 6px 0; }
    #den-sim-panel dt { color: #a8a8a8; }
    #den-sim-panel dd { margin: 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    #den-sim-panel summary { cursor: pointer; font-weight: 600; padding: 3px 0; }
    #den-sim-panel ul { list-style: none; margin: 4px 0 0; padding: 0; }
    #den-sim-panel li { margin-bottom: 4px; }
    #den-sim-panel .pill { display: inline-block; min-width: 58px; text-align: center; font: 700 9px/15px system-ui, sans-serif; border-radius: 3px; color: #fff; margin-right: 5px; }
    #den-sim-panel .note { color: #a8a8a8; display: block; }
  ` }));

  const strip = el('div', { id: 'den-sim-strip', role: 'status', 'data-den-sim-strip': '1', textContent: 'SIMULATION · scripted input · nothing is sent to any fixture' });
  const feedButton = el('button', { type: 'button', disabled: true, 'data-den-sim-feed': '1', textContent: 'Feed: person A reading on the couch' });
  const resetButton = el('button', { type: 'button', 'data-den-sim-reset': '1', textContent: 'Reset (reload)', onclick: () => location.reload() });
  const hideButton = el('button', { type: 'button', 'data-den-sim-hide': '1', textContent: 'Hide details',
    onclick: () => { const shut = panel.dataset.collapsed !== '1'; panel.dataset.collapsed = shut ? '1' : '0'; hideButton.textContent = shut ? 'Show details' : 'Hide details'; } });
  const message = el('div', { 'data-den-sim-message': '1', style: 'margin:5px 0', textContent: 'Waiting for Home to load the room…' });
  const values = {};
  const list = rows => el('dl', {}, ...rows.flatMap(([key, label]) => {
    values[key] = el('dd', { 'data-den-sim-value': key, textContent: '–' });
    return [el('dt', { textContent: label }), values[key]];
  }));
  const readback = list([['fixture', 'Fixture'], ['aim', 'Pan / tilt'], ['spot', 'Spot'], ['target', 'Aim target']]);
  const proof = el('details', {}, el('summary', { textContent: 'Proof from this page' }), list([['seen', 'Distinct readbacks'], ['trail', 'Recent pan/tilt'], ['docs', 'Docs sent'],
    ['frames', 'Frames in swing'], ['external', 'External requests'], ['csp', 'Blocked by CSP'], ['refused', 'Refused by harness']]));
  const hopRows = el('ul', { 'data-den-sim-hops': '1' });
  // Open when there is room for it: the list is the point of the page, not a footnote.
  const hops = el('details', { open: innerHeight >= 800 }, el('summary', { textContent: 'What is real here' }), hopRows);
  const body = el('div', { 'data-den-sim-body': '1' }, readback, proof, hops);
  const panel = el('aside', { id: 'den-sim-panel', 'aria-label': 'Den simulation' }, feedButton, el('div', { className: 'row' }, resetButton, hideButton), message, body);
  document.body.append(strip, panel);

  const sim = { status: null, fixtureId: null, seen: new Map(), swinging: false, swingStart: 0, frames: [], longTasks: 0, longestTaskMs: 0, external: new Set() };
  window.__denSim = sim;

  /* ---------- zero-external-requests evidence ---------- */
  const noteResource = entry => {
    let url; try { url = new URL(entry.name, location.href); } catch (_) { return; }
    if (!['data:', 'blob:'].includes(url.protocol) && url.origin !== location.origin) sim.external.add(entry.name);
  };
  performance.getEntriesByType('resource').forEach(noteResource);
  try { new PerformanceObserver(list => list.getEntries().forEach(noteResource)).observe({ type: 'resource', buffered: true }); } catch (_) { /* older engine */ }
  try {
    new PerformanceObserver(list => list.getEntries().forEach(entry => {
      if (!sim.swinging) return;
      sim.longTasks++; sim.longestTaskMs = Math.max(sim.longestTaskMs, entry.duration);
    })).observe({ type: 'longtask' });
  } catch (_) { /* older engine */ }

  /* ---------- reading Home's real overlay ---------- */
  const overlay = () => {
    const engine = window.__APT_ENGINE;
    return engine?.siteId === 'victoria' && engine.overlay?.virtualFixturesReadback ? engine.overlay : null;
  };
  const fixtureDescriptors = () => {
    const live = overlay();
    if (!live) return [];
    return (live.virtualFixturesReadback()?.fixtures || []).map(row => live.virtualFixtureInteractionDescriptor(row.fixture_id)).filter(Boolean);
  };
  const litDescriptor = () => {
    const live = overlay();
    return live && sim.fixtureId ? live.virtualFixtureInteractionDescriptor(sim.fixtureId) : null;
  };
  function sample() {
    const d = litDescriptor();
    if (!d) return;
    const key = `${d.state.pan_deg.toFixed(1)}/${d.state.tilt_deg.toFixed(1)}`;
    if (sim.swinging && !sim.seen.has(key)) sim.seen.set(key, [d.state.pan_deg, d.state.tilt_deg]);
    values.fixture.textContent = d.fixture_id;
    values.aim.textContent = `${fmt(d.state.pan_deg)}° / ${fmt(d.state.tilt_deg)}°`;
    values.spot.textContent = d.state.spotlight.on ? `on, level ${fmt(d.state.spotlight.brightness, 2)}` : 'off';
    values.target.textContent = d.target?.point ? d.target.point.map(v => fmt(v, 2)).join(', ') : '–';
    values.seen.textContent = String(sim.seen.size);
    values.trail.textContent = [...sim.seen.keys()].slice(-6).join('  ') || '–';
  }
  // A timer, not only rAF: a hidden pane stops rAF, and the readbacks still count.
  setInterval(sample, 50);
  let lastFrame = null;
  (function frame(now) {
    if (sim.swinging && lastFrame !== null) sim.frames.push({ dt: now - lastFrame, at: now - sim.swingStart });
    lastFrame = now;
    requestAnimationFrame(frame);
  })(performance.now());
  function frameSummary() {
    if (!sim.frames.length) return '–';
    const sorted = [...sim.frames].sort((a, b) => a.dt - b.dt), worst = sorted.at(-1), p95 = sorted[Math.floor(sorted.length * .95)];
    return `${sorted.length} frames, median ${fmt(sorted[sorted.length >> 1].dt, 0)} ms, p95 ${fmt(p95.dt, 0)} ms, `
      + `worst ${fmt(worst.dt, 0)} ms at +${fmt(worst.at / 1000)} s, over 50 ms: ${sorted.filter(row => row.dt > 50).length}, long tasks ${sim.longTasks}`;
  }
  function blockedSummary() {
    const rows = window.__denSimCsp || [];
    return rows.length ? `${rows.length}: ${[...new Set(rows.map(row => row.uri))].join(', ')}` : '0';
  }

  /* ---------- harness status ---------- */
  async function refresh() {
    let status;
    try { status = await (await fetch('/__sim/status', { cache: 'no-store' })).json(); } catch (_) { return; }
    sim.status = status;
    const room = status.room === 'real' ? 'YOUR SCAN' : 'AUTHORED ROOM';
    strip.textContent = `SIMULATION · scripted input, no inference ran · nothing is sent to any fixture · ${room} · ${status.registration.label} · couch: ${status.couch.label}`;
    if (hopRows.childElementCount !== status.hops.length) {
      hopRows.replaceChildren(...status.hops.map(hop => el('li', { 'data-den-sim-hop': hop.name, 'data-den-sim-status': hop.status },
        el('span', { className: 'pill', style: `background:${PILL[hop.status]}`, textContent: hop.status }), hop.name, el('span', { className: 'note', textContent: hop.note }))));
    }
    values.docs.textContent = String(status.feed.docs_sent);
    values.refused.textContent = `${status.counters.refused} requests, ${status.counters.upgrades_destroyed} sockets (all logged)`;
    values.external.textContent = String(sim.external.size);
    values.csp.textContent = blockedSummary();
    values.frames.textContent = frameSummary();
    const swing = status.feed.swing;
    if (swing) sim.fixtureId = swing.fixture_id;
    if (sim.swinging && status.feed.phase === 'holding') {
      // Keep sampling for one more frame window so the final readback is counted.
      setTimeout(() => {
        sim.swinging = false;
        const seconds = (swing.finished_at_ms - swing.started_at_ms) / 1000;
        setToolbar('Lighting', true);
        message.textContent = `Swing done: ${fmt(swing.angle_deg)}° in ${fmt(seconds)} s to the ${status.couch.label} couch. Scripted. Nothing was sent. Lighting is on: Home's modeled view, not measured. Reset to run it again.`;
      }, 400);
    }
  }
  setInterval(refresh, 500);
  refresh();

  /* ---------- open the view that shows the beam best ---------- */
  // Unlit Mesh with the fixture selected shows the aim line clearly on the textured
  // room, and Lighting comes on at landing for the pool on the couch. Under Lighting
  // the pool also follows the aim live, at more GPU cost: press it by hand before
  // the click and it stays on. Only the Mesh path does that; Photo and Cloud re-bake
  // a moved spot, so their pool appears on landing. These are Home's own controls.
  const toolbarButton = name => [...document.querySelectorAll('[role="toolbar"] button')]
    .find(button => button.getAttribute('aria-label') === name || button.textContent.trim() === name);
  const setToolbar = (name, on) => {
    const button = toolbarButton(name);
    if (button && (button.getAttribute('aria-pressed') === 'true') !== on) button.click();
  };
  const until = async (test, ms) => {
    for (const end = Date.now() + ms; Date.now() < end; await new Promise(done => setTimeout(done, 200))) if (test()) return true;
    return test();
  };
  async function openBestView() {
    message.textContent = 'Opening the Mesh view…';
    setToolbar('Mesh view', true);
    const mesh = await until(() => window.__APT_ENGINE?.modes?.mode === 'mesh', 30000);
    setToolbar('Lighting', false);
    return { mesh };
  }
  function selectInHome(fixtureId) {
    const label = overlay()?.virtualFixturesReadback()?.fixtures.find(row => row.fixture_id === fixtureId)?.label;
    if (label) [...document.querySelectorAll('button')].find(button => button.textContent.trim().startsWith(label))?.click();
  }

  /* ---------- enable once the real overlay has its fixtures ---------- */
  const ready = setInterval(async () => {
    const fixtures = fixtureDescriptors();
    if (!fixtures.length) return;
    clearInterval(ready);
    const view = await openBestView();
    feedButton.disabled = false;
    message.textContent = `Room loaded with ${fixtures.length} virtual fixtures, all off from the scripted empty-room doc.${view.mesh ? '' : ' Mesh is unavailable, so the beam is faint in this view.'}`;
  }, 250);

  feedButton.onclick = async () => {
    const live = overlay(), fixtures = fixtureDescriptors();
    if (!live || !fixtures.length) return;
    feedButton.disabled = true;
    sim.seen.clear(); sim.frames.length = 0; sim.longTasks = 0; sim.longestTaskMs = 0; sim.swingStart = performance.now(); sim.swinging = true;
    message.textContent = 'Swinging at about 30°/s. Scripted input.';
    try {
      const response = await fetch('/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario: 'reading_on_couch', fixtures: fixtures.map(d => ({ fixture_id: d.fixture_id, display_origin_m: d.display_origin_m,
          horizontal_world_reference: d.horizontal_world_reference, pan_deg: d.state.pan_deg, tilt_deg: d.state.tilt_deg })) }) });
      const result = await response.json();
      if (!result.ok) throw Error(result.reason || 'feed_refused');
      sim.fixtureId = result.swing.fixture_id;
      selectInHome(sim.fixtureId);
      const couch = sim.status?.couch?.point;
      if (couch) live.setPerson?.({ pos: [couch[0], couch[1]], sigma_m: .25 }); // Floor disc for scripted person A.
    } catch (error) {
      sim.swinging = false;
      message.textContent = `Feed refused: ${error.message}`;
    }
  };
})();
