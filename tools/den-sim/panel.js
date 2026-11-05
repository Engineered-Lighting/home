/* Den simulation panel, injected by tools/den-sim/run.mjs into the unchanged Home.
 * A red strip, the hop list and two scripted readers for the real brain to judge.
 * It reads Home's real overlay, presses Home's own view buttons and selects the
 * fixture as a person would, and posts only to /__sim/feed on this harness. There
 * is no send control. */
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
    #den-sim-panel button[data-den-sim-feed="not_reading"] { background: #222; border-color: #e0526a; }
    #den-sim-panel [data-den-sim-second-try], #den-sim-panel [data-den-sim-notes] { margin: 5px 0; padding: 5px 6px; border-left: 3px solid #a86a00; background: rgba(168,106,0,.18); }
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

  const strip = el('div', { id: 'den-sim-strip', role: 'status', 'data-den-sim-strip': '1', textContent: 'SIMULATION · scripted reader, no inference ran · nothing is sent to any fixture' });
  const feedButtons = [['reading_on_couch', 'Feed: person A reading on the couch'], ['not_reading', 'Feed: person A on the couch, not reading']]
    .map(([scenario, label]) => el('button', { type: 'button', disabled: true, 'data-den-sim-feed': scenario, textContent: label, onclick: () => runScenario(scenario) }));
  const setFeedEnabled = on => feedButtons.forEach(button => { button.disabled = !on; });
  const resetButton = el('button', { type: 'button', 'data-den-sim-reset': '1', textContent: 'Reset (reload)', onclick: () => location.reload() });
  const hideButton = el('button', { type: 'button', 'data-den-sim-hide': '1', textContent: 'Hide details',
    onclick: () => { const shut = panel.dataset.collapsed !== '1'; panel.dataset.collapsed = shut ? '1' : '0'; hideButton.textContent = shut ? 'Show details' : 'Hide details'; } });
  const message = el('div', { 'data-den-sim-message': '1', style: 'margin:5px 0', textContent: 'Waiting for Home to load the room…' });
  const values = {};
  const list = rows => el('dl', {}, ...rows.flatMap(([key, label]) => {
    values[key] = el('dd', { 'data-den-sim-value': key, textContent: '–' });
    return [el('dt', { textContent: label }), values[key]];
  }));
  const secondTry = el('div', { 'data-den-sim-second-try': '1', hidden: true });
  const inputNotes = el('div', { 'data-den-sim-notes': '1', hidden: true });
  const brainBox = list([['says', 'Brain says'], ['refusal', 'Spot refused'], ['chose', 'Brain chose'], ['hand', 'Hand point'], ['agree', 'Home vs brain']]);
  const readback = list([['fixture', 'Lit fixture'], ['aim', 'Pan / tilt'], ['spot', 'Spot'], ['target', 'Aim target']]);
  const candidateRows = el('ul', { 'data-den-sim-candidates': '1' });
  const candidates = el('details', {}, el('summary', { textContent: 'Fixture by fixture' }), candidateRows);
  const proof = el('details', {}, el('summary', { textContent: 'Proof from this page' }), list([['seen', 'Distinct readbacks'], ['trail', 'Recent pan/tilt'], ['docs', 'Docs sent'],
    ['events', 'Events into brain'], ['frames', 'Frames in swing'], ['external', 'External requests'], ['csp', 'Blocked by CSP'], ['refused', 'Refused by harness']]));
  const hopRows = el('ul', { 'data-den-sim-hops': '1' });
  // Open when there is room for it: the list is the point of the page, not a footnote.
  const hops = el('details', { open: innerHeight >= 800 }, el('summary', { textContent: 'What is real here' }), hopRows);
  const body = el('div', { 'data-den-sim-body': '1' }, brainBox, readback, candidates, proof, hops);
  const panel = el('aside', { id: 'den-sim-panel', 'aria-label': 'Den simulation' }, ...feedButtons, el('div', { className: 'row' }, resetButton, hideButton), message, inputNotes, secondTry, body);
  document.body.append(strip, panel);

  const sim = { status: null, fixtureId: null, selected: null, running: false, seen: new Map(), swinging: false, swingStart: 0, frames: [], longTasks: 0, longestTaskMs: 0, external: new Set() };
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
  function sample() {
    const fixtures = fixtureDescriptors(), lit = fixtures.find(row => row.state.spotlight.on);
    values.fixture.textContent = lit ? lit.fixture_id : '–';
    values.spot.textContent = lit ? `on, level ${fmt(lit.state.spotlight.brightness, 2)}` : fixtures.length ? 'all four off' : '–';
    values.seen.textContent = String(sim.seen.size);
    values.trail.textContent = [...sim.seen.keys()].slice(-6).join('  ') || '–';
    if (!lit) return;
    const key = `${lit.state.pan_deg.toFixed(1)}/${lit.state.tilt_deg.toFixed(1)}`;
    if (sim.swinging && !sim.seen.has(key)) sim.seen.set(key, [lit.state.pan_deg, lit.state.tilt_deg]);
    values.aim.textContent = `${fmt(lit.state.pan_deg)}° / ${fmt(lit.state.tilt_deg)}°`;
    values.target.textContent = lit.target?.point ? lit.target.point.map(v => fmt(v, 2)).join(', ') : '–';
    const decided = sim.status?.feed?.summary;
    if (sim.status?.feed?.phase === 'holding' && decided?.fixture_id === lit.fixture_id) {
      values.agree.textContent = `pan off by ${fmt(Math.abs(lit.state.pan_deg - decided.pan_deg), 2)}°, tilt off by ${fmt(Math.abs(lit.state.tilt_deg - decided.tilt_deg), 2)}°`;
    }
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

  const plain = text => String(text).replace(/_/g, ' ');
  function showCandidates(rows, heading) {
    const key = JSON.stringify([rows, heading]);
    if (key === sim.candidatesShown) return; // This polls: leave a selection in the list alone.
    sim.candidatesShown = key;
    candidateRows.replaceChildren(...rows.map(row => el('li', { 'data-den-sim-candidate': row.fixture_id, textContent: `${row.fixture_id}: ${row.ok ? 'clear' : plain(row.reason)}` })));
    if (heading) candidateRows.prepend(el('li', { className: 'note', textContent: heading }));
  }
  function showBrain(status) {
    const feed = status.feed, decided = feed.summary, first = status.brain.first_try_refused;
    inputNotes.hidden = !status.private_notes.length;
    inputNotes.textContent = status.private_notes.join(' ');
    secondTry.hidden = !status.brain.invented_camera;
    if (status.brain.invented_camera) {
      secondTry.textContent = `First try refused: ${first.refusal}. This is a labelled second try: an INVENTED CAMERA at the nearest fixture makes person A face away from it, which may not be how anyone sits on your couch.`;
    }
    values.says.textContent = feed.live?.why || '–';
    values.refusal.textContent = feed.live?.refusal || '–';
    values.events.textContent = decided ? `${decided.events}, scripted, into the real activity loop` : '–';
    const landed = feed.phase === 'swinging' || feed.phase === 'holding';
    values.chose.textContent = landed ? `${decided.fixture_id}, pan ${fmt(decided.pan_deg)}°, tilt ${fmt(decided.tilt_deg)}°` : '–';
    values.hand.textContent = landed ? `${decided.target_m.map(v => fmt(v, 2)).join(', ')}, ${fmt(decided.hand_from_seat_m, 2)} m from the seat` : '–';
    if (!landed) values.agree.textContent = '–';
    showCandidates(decided ? decided.candidates : [], decided?.candidates.length ? '' : 'The brain only compares fixtures once reading has held for 3 s.');
  }
  function followRun(status) {
    const feed = status.feed;
    if (!sim.running) return;
    if (feed.phase === 'swinging' && !sim.swinging) {
      sim.swinging = true; sim.swingStart = performance.now();
      selectInHome(feed.swing.fixture_id);
      message.textContent = `The brain chose ${feed.swing.fixture_id}. Swinging at about 30°/s: this motion is scripted, because Home's follower has none.`;
    } else if (feed.phase === 'holding' || feed.phase === 'refused') {
      sim.running = false;
      // Keep sampling for one more frame window so the final readback is counted.
      setTimeout(() => {
        sim.swinging = false;
        setToolbar('Lighting', true);
        setFeedEnabled(true);
        const swing = feed.swing, decided = feed.summary, modeled = 'Lighting is on: Home\'s modeled view, not measured.';
        message.textContent = feed.phase === 'holding'
          ? `The beam is on person A's hands: ${fmt(decided.hand_from_seat_m, 2)} m from the ${status.couch.label} seat, after ${fmt(swing.angle_deg)}° in ${fmt((swing.finished_at_ms - swing.started_at_ms) / 1000)} s. The brain's decision is real; the reader and the motion are scripted. Nothing was sent. ${modeled}`
          : `The brain refused: ${decided.refusal}. All four spots stay off. The reader is scripted. Nothing was sent. ${modeled}`;
      }, 400);
    }
  }

  /* ---------- harness status ---------- */
  async function refresh() {
    let status;
    try { status = await (await fetch('/__sim/status', { cache: 'no-store' })).json(); } catch (_) { return; }
    sim.status = status;
    const room = status.room === 'real' ? 'YOUR SCAN' : 'AUTHORED ROOM';
    strip.textContent = `SIMULATION · real brain, scripted reader, no inference ran · nothing is sent to any fixture · ${room} · ${status.registration.label} · couch: ${status.couch.label}${status.brain.invented_camera ? ' · INVENTED CAMERA' : ''}`;
    showBrain(status);
    followRun(status);
    if (hopRows.childElementCount !== status.hops.length) {
      hopRows.replaceChildren(...status.hops.map(hop => el('li', { 'data-den-sim-hop': hop.name, 'data-den-sim-status': hop.status },
        el('span', { className: 'pill', style: `background:${PILL[hop.status]}`, textContent: hop.status }), hop.name, el('span', { className: 'note', textContent: hop.note }))));
    }
    values.docs.textContent = String(status.feed.docs_sent);
    values.refused.textContent = `${status.counters.refused} requests, ${status.counters.upgrades_destroyed} sockets (all logged)`;
    values.external.textContent = String(sim.external.size);
    values.csp.textContent = blockedSummary();
    values.frames.textContent = frameSummary();
  }
  setInterval(refresh, 250);
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
    if (sim.selected === fixtureId) return; // A second press would deselect it.
    const label = overlay()?.virtualFixturesReadback()?.fixtures.find(row => row.fixture_id === fixtureId)?.label;
    if (label) [...document.querySelectorAll('button')].find(button => button.textContent.trim().startsWith(label))?.click();
    sim.selected = fixtureId;
  }

  /* ---------- enable once the real overlay has its fixtures ---------- */
  const ready = setInterval(async () => {
    const fixtures = fixtureDescriptors();
    if (!fixtures.length) return;
    clearInterval(ready);
    const view = await openBestView();
    setFeedEnabled(true);
    message.textContent = `Room loaded with ${fixtures.length} virtual fixtures. The real brain's empty-room state keeps them off.${view.mesh ? '' : ' Mesh is unavailable, so the beam is faint in this view.'}`;
  }, 250);

  async function runScenario(scenario) {
    const live = overlay(), fixtures = fixtureDescriptors();
    if (!live || !fixtures.length) return;
    setFeedEnabled(false);
    sim.seen.clear(); sim.frames.length = 0; sim.longTasks = 0; sim.longestTaskMs = 0; sim.swinging = false; sim.running = true;
    message.textContent = scenario === 'not_reading'
      ? 'The real brain gets the same scripted joints, but the typed label says phone use.'
      : 'The real brain gets scripted joints and a typed "reading" label. It waits for 3 s of reading before it lights a spot.';
    try {
      const response = await fetch('/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, fixtures: fixtures.map(d => ({ fixture_id: d.fixture_id, display_origin_m: d.display_origin_m,
          horizontal_world_reference: d.horizontal_world_reference, pan_deg: d.state.pan_deg, tilt_deg: d.state.tilt_deg })) }) });
      const result = await response.json();
      if (!result.ok) throw Error(result.reason || 'feed_refused');
      live.setPerson?.({ pos: result.person_m, sigma_m: .25 }); // Floor disc for scripted person A.
    } catch (error) {
      sim.running = false;
      setFeedEnabled(true);
      message.textContent = `Feed refused: ${error.message}`;
    }
  }
})();
