/* Den simulation panel, injected by tools/den-sim/run.mjs into the unchanged Home.
 * A red strip, one button per prepared take, the top line, the picture read back out of the
 * take with the detector's person box, score and joints from that frame's recorded row, the torso
 * height against the on-the-couch limit, and one hop list read from the click's run-receipt.json. It reads Home's real overlay,
 * presses Home's own view buttons, selects the fixture as a person would, and posts only to
 * /__sim/feed on this harness. There is no send control. It prints no coordinate: the aim target
 * and the hand point show only their distance from the seat, and the camera is a marker. */
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
  const GROUPS = [['recorded', 'Recorded once'], ['page', 'Built when this page started'], ['click', 'Ran on this click'], ['not run', 'Not built yet, or not reached on this click']];

  document.head.append(el('style', { textContent: `
    #root { position: fixed !important; top: ${STRIP_PX}px; left: 0; right: 0; bottom: 0; height: auto !important; width: auto !important; }
    #den-sim-strip { position: fixed; top: 0; left: 0; right: 0; height: ${STRIP_PX}px; z-index: 2147483000; background: #b00020; color: #fff;
      font: 700 13px/${STRIP_PX}px system-ui, sans-serif; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0 8px; box-sizing: border-box; }
    #den-sim-panel { position: fixed; right: 8px; top: ${STRIP_PX + 52}px; width: min(340px, calc(100vw - 16px)); max-height: calc(100vh - ${STRIP_PX + 64}px); overflow: auto;
      z-index: 2147483000; box-sizing: border-box; padding: 8px; background: #0c0e10; color: #e8e8e8; border: 1px solid #b00020; border-radius: 8px;
      font: 12px/1.35 system-ui, sans-serif; user-select: text; -webkit-user-select: text; }
    #den-sim-panel button { font: 600 12px system-ui, sans-serif; padding: 6px 8px; border-radius: 6px; border: 1px solid #555; background: #222; color: #fff; cursor: pointer; }
    #den-sim-panel button[data-den-sim-feed] { width: 100%; margin-bottom: 4px; border-color: #e0526a; text-align: left; }
    #den-sim-panel [data-den-sim-notes], #den-sim-panel [data-den-sim-startup] { margin: 5px 0; padding: 5px 6px; border-left: 3px solid #a86a00; background: rgba(168,106,0,.18); }
    #den-sim-panel [data-den-sim-top-line] { margin: 6px 0; padding: 6px; border: 1px solid #555; border-radius: 6px; background: #1a1d21; }
    #den-sim-panel [data-den-sim-top-line] strong { display: block; font-size: 13px; }
    #den-sim-panel [data-den-sim-top-line][data-verdict="yes"] { border-color: #1f7a3d; }
    #den-sim-panel [data-den-sim-picture-box] { position: relative; margin: 4px 0; }
    #den-sim-panel [data-den-sim-picture] { display: block; width: 100%; box-sizing: border-box; background: #000; border: 1px solid #333; border-radius: 4px; }
    #den-sim-panel [data-den-sim-boxes] { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
    #den-sim-panel [data-den-sim-torso] { margin: 4px 0; padding: 4px 6px; border-left: 3px solid #2a5fa8; background: rgba(42,95,168,.18); }
    #den-sim-panel [data-den-sim-command] { margin: 6px 0; padding-top: 6px; border-top: 1px solid #555; }
    #den-sim-panel [data-den-sim-not-sent] { background: #b00020; color: #fff; font: 700 12px/20px system-ui, sans-serif; text-align: center; border-radius: 4px; margin-bottom: 5px; }
    #den-sim-panel pre { margin: 4px 0; padding: 5px; max-height: 220px; overflow: auto; background: #000; border: 1px solid #333; border-radius: 4px;
      font: 10.5px/1.35 ui-monospace, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
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
    #den-sim-panel li[data-reached="0"] { opacity: .45; }
    #den-sim-panel h4 { margin: 8px 0 3px; font: 700 11px system-ui, sans-serif; color: #c8c8c8; text-transform: uppercase; letter-spacing: .04em; }
    #den-sim-panel .pill { display: inline-block; min-width: 58px; text-align: center; font: 700 9px/15px system-ui, sans-serif; border-radius: 3px; color: #fff; margin-right: 5px; }
    #den-sim-panel .note { color: #a8a8a8; display: block; }
    #den-sim-panel [data-den-sim-qwen], #den-sim-panel [data-den-sim-owed], #den-sim-panel [data-den-sim-last-ask] { margin: 5px 0; padding: 5px 6px; border-left: 3px solid #2a5fa8; background: rgba(42,95,168,.18); }
    #den-sim-panel [data-den-sim-owed] { border-left-color: #b00020; background: rgba(176,0,32,.18); }
    #den-sim-panel code { display: block; margin: 4px 0; padding: 4px; background: #000; border: 1px solid #333; border-radius: 4px; font: 10.5px/1.35 ui-monospace, Consolas, monospace; overflow-wrap: anywhere; user-select: all; }
    #den-sim-panel table { width: 100%; border-collapse: collapse; margin: 4px 0; font-size: 10.5px; table-layout: fixed; }
    #den-sim-panel th:nth-child(1) { width: 27%; } #den-sim-panel th:nth-child(2) { width: 15%; } #den-sim-panel th:nth-child(3) { width: 25%; } #den-sim-panel th:nth-child(4) { width: 14%; }
    #den-sim-panel th, #den-sim-panel td { border: 1px solid #333; padding: 2px 4px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
  ` }));

  const strip = el('div', { id: 'den-sim-strip', role: 'status', 'data-den-sim-strip': '1',
    textContent: 'SIMULATION · rendered takes, a real detector\'s recorded rows, Qwen\'s words recorded once and looked up by exact bytes · INVENTED CAMERA: it does not exist · nothing is sent to any fixture' });
  const sceneButtons = el('div', { 'data-den-sim-scenes': '1' });
  const everyButton = el('button', { type: 'button', 'data-den-sim-every-run': '1', textContent: 'Run every scene', disabled: true, style: 'width:100%;margin-bottom:4px', onclick: () => runEvery() });
  const setFeedEnabled = on => [...sceneButtons.querySelectorAll('button'), everyButton].forEach(button => { button.disabled = !on; });
  const resetButton = el('button', { type: 'button', 'data-den-sim-reset': '1', textContent: 'Reset (reload)', onclick: () => location.reload() });
  const hideButton = el('button', { type: 'button', 'data-den-sim-hide': '1', textContent: 'Hide details',
    onclick: () => { const shut = panel.dataset.collapsed !== '1'; panel.dataset.collapsed = shut ? '1' : '0'; hideButton.textContent = shut ? 'Show details' : 'Hide details'; } });
  const message = el('div', { 'data-den-sim-message': '1', style: 'margin:5px 0', textContent: 'Waiting for Home to load the room…' });
  const values = {};
  const list = rows => el('dl', {}, ...rows.flatMap(([key, label]) => {
    values[key] = el('dd', { 'data-den-sim-value': key, textContent: '–' });
    return [el('dt', { textContent: label }), values[key]];
  }));
  const startupBox = el('div', { 'data-den-sim-startup': '1', hidden: true });
  const inputNotes = el('div', { 'data-den-sim-notes': '1', hidden: true });
  const topLine = el('div', { 'data-den-sim-top-line': '1', hidden: true });
  const picture = el('img', { 'data-den-sim-picture': '1', alt: 'The take\'s own frame, read back out of its NV12 plane', hidden: true });
  const pictureBox = el('div', { 'data-den-sim-picture-box': '1', hidden: true }, picture);
  const pictureNote = el('span', { className: 'note', 'data-den-sim-picture-note': '1', hidden: true });
  const torsoLine = el('div', { 'data-den-sim-torso': '1', hidden: true });
  // The model's box, score and joints, drawn in the frame's own pixels from that frame's recorded row.
  const SVG = 'http://www.w3.org/2000/svg', JOINT_MIN = .4;   // the pose manifest's score_threshold: a joint at or over it is supported
  const EDGES = [[0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
  const draw = (tag, attrs, text) => { const node = document.createElementNS(SVG, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); if (text) node.textContent = text; return node; };
  // boxes: the shown frame's person boxes, or null when hop 6's rows didn't hold. note: receipt.mjs's words for either.
  function drawBoxes(boxes, note) {
    pictureBox.querySelector('svg')?.remove();
    pictureNote.textContent = note || '';
    if (!boxes?.length) return;
    const svg = draw('svg', { viewBox: '0 0 640 360', 'data-den-sim-boxes': String(boxes.length), 'aria-hidden': 'true' });
    for (const box of boxes) {
      const [x0, y0, x1, y1] = box.box_xyxy, points = box.keypoints || [];
      // The score at 26 units of a 640-wide frame, on a dark backing: legible with the picture shown about 320 px wide.
      const lx = Math.min(Math.max(0, x0), 640 - 150), ly = y0 >= 32 ? y0 - 32 : Math.max(0, y0);
      svg.append(draw('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: 'none', stroke: '#3cdc3c', 'stroke-width': 2, 'data-den-sim-box': '1' }),
        draw('rect', { x: lx, y: ly, width: 150, height: 31, rx: 3, fill: 'rgba(0,0,0,.72)' }),
        draw('text', { x: lx + 5, y: ly + 24, fill: '#3cdc3c', 'font-size': 26, 'font-weight': 700, 'font-family': 'system-ui, sans-serif' }, `person ${box.score.toFixed(2)}`));
      for (const [a, b] of EDGES) if (points[a]?.[2] >= JOINT_MIN && points[b]?.[2] >= JOINT_MIN) svg.append(draw('line', { x1: points[a][0], y1: points[a][1], x2: points[b][0], y2: points[b][1], stroke: '#ffc800', 'stroke-width': 1.5 }));
      for (const point of points) if (point?.[2] >= JOINT_MIN) svg.append(draw('circle', { cx: point[0], cy: point[1], r: 3, fill: '#00ff66', 'data-den-sim-joint': '1' }));
    }
    pictureBox.append(svg);
  }
  const brainBox = list([['says', 'Brain says'], ['refusal', 'Spot refused'], ['chose', 'Brain chose'], ['hand', 'Hand point'], ['agree', 'Home vs brain']]);
  const readback = list([['fixture', 'Lit fixture'], ['aim', 'Pan / tilt'], ['spot', 'Spot'], ['target', 'Aim target'], ['camera', 'Camera marker']]);
  const candidateRows = el('ul', { 'data-den-sim-candidates': '1' });
  const candidates = el('details', {}, el('summary', { textContent: 'Fixture by fixture' }), candidateRows);
  const proof = el('details', {}, el('summary', { textContent: 'Proof from this page' }), list([['seen', 'Distinct readbacks'], ['trail', 'Recent pan/tilt'], ['docs', 'Docs sent'],
    ['events', 'Events into brain'], ['frames', 'Frames in swing'], ['external', 'External requests'], ['csp', 'Blocked by CSP'], ['refused', 'Refused by harness']]));
  const hopRows = el('div', { 'data-den-sim-hops': '1' }, el('span', { className: 'note', textContent: 'No click yet: each row\'s word comes from what a click recorded.' }));
  // Open when there is room for it: the list is the point of the page, not a footnote.
  const hops = el('details', { open: innerHeight >= 800 }, el('summary', { textContent: 'Each hop, from this click\'s receipt' }), hopRows);
  const commandBox = el('div', { 'data-den-sim-command': '1', hidden: true });
  const choice = el('div', { className: 'note', 'data-den-sim-choice': '1' });
  // Qwen: what it said on the last click, what it still owes with the owner's one paste, and what the last paste said.
  const qwenBox = el('div', { 'data-den-sim-qwen': '1', hidden: true });
  const pasteCode = el('code', { 'data-den-sim-paste': '1' });
  const copyButton = el('button', { type: 'button', 'data-den-sim-copy': '1', textContent: 'Copy', onclick: () => copyPaste() });
  const owedText = el('div', { 'data-den-sim-owed-text': '1' });
  const owedBox = el('div', { 'data-den-sim-owed': '1', hidden: true }, owedText, pasteCode, copyButton);
  const lastAskBox = el('div', { 'data-den-sim-last-ask': '1', hidden: true });
  const everyBox = el('div', { 'data-den-sim-every': '1', hidden: true });
  const body = el('div', { 'data-den-sim-body': '1' }, topLine, pictureBox, pictureNote, torsoLine, qwenBox, brainBox, readback, commandBox, candidates, hops, proof);
  const panel = el('aside', { id: 'den-sim-panel', 'aria-label': 'Den simulation' }, sceneButtons, everyButton, el('div', { className: 'row' }, resetButton, hideButton),
    message, choice, owedBox, lastAskBox, everyBox, startupBox, inputNotes, body);
  document.body.append(strip, panel);

  const sim = { status: null, selected: null, running: false, seen: new Map(), swinging: false, swingStart: 0, frames: [], longTasks: 0, longestTaskMs: 0, external: new Set() };
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
  const distance = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));
  function sample() {
    const fixtures = fixtureDescriptors(), lit = fixtures.find(row => row.state.spotlight.on);
    values.fixture.textContent = lit ? lit.fixture_id : '–';
    values.spot.textContent = lit ? `on, level ${fmt(lit.state.spotlight.brightness, 2)}` : fixtures.length ? 'all four off' : '–';
    values.seen.textContent = String(sim.seen.size);
    values.trail.textContent = [...sim.seen.keys()].slice(-6).join('  ') || '–';
    if (!lit) { values.target.textContent = '–'; return; }
    const key = `${lit.state.pan_deg.toFixed(1)}/${lit.state.tilt_deg.toFixed(1)}`;
    if (sim.swinging && !sim.seen.has(key)) sim.seen.set(key, [lit.state.pan_deg, lit.state.tilt_deg]);
    values.aim.textContent = `${fmt(lit.state.pan_deg)}° / ${fmt(lit.state.tilt_deg)}°`;
    const decided = sim.status?.feed?.summary, point = lit.target?.point;
    // A distance, never the point: from the brain's hand point, which the harness measured from the seat.
    values.target.textContent = point && decided?.target_m
      ? `${fmt(distance(point, decided.target_m), 3)} m from the brain's hand point, which is ${fmt(decided.hand_from_seat_m, 2)} m from the seat` : point ? 'set' : '–';
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
    const feed = status.feed, decided = feed.summary;
    inputNotes.hidden = !status.private_notes.length;
    inputNotes.textContent = status.private_notes.join(' ');
    startupBox.hidden = !status.startup;
    startupBox.textContent = status.startup ? `Fixtures for the stated facing, at the spot: ${status.startup}.` : '';
    // A broken or stopped click never reaches the brain: Home's off document's reason isn't the brain's word.
    values.says.textContent = feed.phase === 'pipeline_failed' ? 'not run: the pipeline broke'
      : feed.phase === 'stopped' ? 'not run: the click stopped before it' : feed.live?.why || '–';
    values.refusal.textContent = feed.live?.refusal || '–';
    values.events.textContent = decided ? `${decided.events}, from the adapter, into the real activity loop` : '–';
    values.camera.textContent = status.camera_m ? 'shown in the room: a green dot where the invented camera sits' : '–';
    const landed = feed.phase === 'swinging' || feed.phase === 'holding';
    values.chose.textContent = landed ? `${decided.fixture_id}, pan ${fmt(decided.pan_deg)}°, tilt ${fmt(decided.tilt_deg)}°` : '–';
    values.hand.textContent = landed ? `${fmt(decided.hand_from_seat_m, 2)} m from the seat` : '–';
    if (!landed) values.agree.textContent = '–';
    showCandidates(decided ? decided.candidates : [], decided?.candidates.length ? '' : 'The brain only compares fixtures once reading has held for 3 s.');
  }

  /* ---------- the top line and the one hop list, from this click's receipt ---------- */
  function showClick(click) {
    const key = click ? `${click.click}:${click.top_line.line}:${click.rows.length}` : 'none';
    if (key === sim.clickShown) return;
    sim.clickShown = key;
    topLine.hidden = pictureBox.hidden = pictureNote.hidden = !click;
    torsoLine.hidden = !click?.torso;
    if (!click) return;
    torsoLine.textContent = click.torso ? `Against hop 15's on-the-couch limit, which step 5 builds: ${click.torso}.` : '';
    const top = click.top_line;
    topLine.dataset.verdict = top.verdict;
    topLine.replaceChildren(el('strong', { 'data-den-sim-top-line-text': '1', textContent: top.line }), el('span', { className: 'note', 'data-den-sim-count': '1', textContent: top.count_line }));
    if (top.reasons.length) {
      topLine.append(el('details', { 'data-den-sim-reasons': '1' }, el('summary', { textContent: `Why: ${top.reasons.length} reasons` }),
        el('ul', {}, ...top.reasons.map(reason => el('li', { 'data-den-sim-reason': String(reason.condition ?? ''), textContent: reason.condition ? `(${reason.condition}) ${reason.text}` : reason.text })))));
    }
    if (click.picture) { picture.src = `/__sim/picture?click=${click.click}`; drawBoxes(click.boxes, click.picture_note); } else pictureBox.hidden = pictureNote.hidden = true;
    // Qwen on this click: rebuilt from what hop 12 recorded, never from the button pressed.
    qwenBox.hidden = !click.qwen;
    if (click.qwen) {
      const asked = click.rows.find(row => row.n === 12)?.ran || '';
      const head = click.qwen.word === 'MISSING' ? `Qwen: ${click.rows.find(row => row.n === 12).summary}`
        : `Qwen${click.qwen.word === 'SCRIPTED' ? ' (SCRIPTED: a fake vLLM, not Qwen)' : ''}: model root ${click.qwen.root}, ${asked}.`;
      qwenBox.replaceChildren(el('strong', { textContent: head }), el('ul', {}, ...click.qwen.asks.map(ask => el('li', { 'data-den-sim-ask': ask.request_id,
        textContent: ask.not_asked ? `${ask.request_id}: not asked, ${ask.not_asked}` : `${ask.request_id}: frames ${ask.stills.join(', ')} (${ask.seconds.join(', ')} s before the newest)${ask.association_safe ? '' : ', a bundle not marked safe to tie to one person'}: ${ask.said}` }))));
    }
    hopRows.replaceChildren(...GROUPS.flatMap(([group, heading]) => {
      const rows = click.rows.filter(row => row.group === group);
      return rows.length ? [el('h4', { textContent: heading }), el('ul', {}, ...rows.map(row => el('li', { 'data-den-sim-hop': String(row.n), 'data-den-sim-name': row.name,
        'data-den-sim-status': row.word, 'data-reached': row.reached ? '1' : '0' },
        el('span', { className: 'pill', style: `background:${PILL[row.word]}`, textContent: row.word }), `${row.n} ${row.name}`,
        el('span', { className: 'note', textContent: [row.reached ? row.ran : 'not reached on this click', row.summary, ...row.stand_ins.map(text => `Stand-in: ${text}.`)].filter(Boolean).join(' · ') }))))] : [];
    }));
  }

  /* ---------- the exact command: text only, with nothing that could send it ---------- */
  const block = (summary, name, text, open = false) => el('details', { open }, el('summary', { textContent: summary }), el('pre', { 'data-den-sim-pre': name, textContent: text }));
  const rows = pairs => el('dl', {}, ...pairs.flatMap(([label, text]) => [el('dt', { textContent: label }), el('dd', { textContent: text })]));
  const post = row => `POST ${row.route}\n${JSON.stringify(row.body)}`;
  const shortSha = text => text ? text.slice(0, 16) : 'unknown';
  function renderCommand(command) {
    const badge = el('div', { 'data-den-sim-not-sent': '1', textContent: 'NOT SENT · nothing here can send it' });
    if (command.failed) return commandBox.replaceChildren(badge, el('div', { textContent: command.failed }));
    if (!command.brain) return commandBox.replaceChildren(badge, el('div', { textContent: 'No fixture command: the brain refused, so nothing was compiled.' }));
    const ik = command.ik;
    if (!ik.ok) return commandBox.replaceChildren(badge, el('div', { textContent: `No fixture command: compile_target refused (${ik.reason}).` }));
    const [pan, tilt] = ik.fixture_angles_deg, [homePan, homeTilt] = ik.home_angles_deg, spot = command.spot;
    const listed = [['Profile', `${ik.profile_id}, FICTIONAL: zero, direction, ratio and limits are invented`],
      ['Fixture angles', `pan ${fmt(pan)}°, tilt ${fmt(tilt)}° from straight down`],
      ['Home\'s angles', `pan ${fmt(homePan)}°, tilt ${fmt(homeTilt)}° from level: ${ik.agrees_with_home ? 'the same aim' : 'THESE DISAGREE'}`],
      ['Motor target', `${ik.target_centideg.join(', ')} centideg at ${ik.speed_deg_s.join(', ')} deg/s`],
      ['Spot', `raw duty ${spot.raw_duty} of ${spot.full_duty}, a test value with no colour claim. The brain asked for ${fmt(spot.brain_level, 2)} at ${spot.brain_cct_k} K, and semantic CCT refuses: ${spot.semantic_refusal}.`]];
    const parts = [badge];
    if (!command.wire) {
      parts.push(rows(listed), el('div', { textContent: `The codec was not found at ${command.codec.tools}, so no packets exist.` }));
    } else {
      const { counts, packets } = command.wire, aim = command.plan.aim, last = packets.at(-1);
      listed.push(['Packets', `${counts.total}: ${counts.session_open} session open, ${counts.plan_prepare} plan prepare, ${counts.plan_commit} commit. Sealed with the contract's public test key for a fictional device.`],
        ['Aim in the bytes', `plan offset ${aim.offset}, carried by packets ${aim.packets.join(' and ')}`]);
      parts.push(rows(listed), block(`Literal POST, packet ${last.n} of ${counts.total}: commit`, 'post', post(last), true),
        block(`The aim's POSTs, packets ${aim.packets.join(' and ')}`, 'aim', aim.packets.map(number => post(packets[number - 1])).join('\n')),
        block(`All ${counts.total} POSTs, in order`, 'all', packets.map(row => `${row.n} ${row.stage}\n${post(row)}`).join('\n')));
    }
    const can = command.can;
    parts.push(block('The two CAN A4 frames', 'can', `${can.frames.map(frame => `${frame.id_hex} ${frame.axis}  ${frame.data_hex}`).join('\n')}\n\nEncoded here from the contract's byte fields: the firmware's encoder did not run. `
      + `Contract oracle: ${can.oracle_ok === true ? 'matches' : can.oracle_ok === false ? 'DOES NOT MATCH' : 'not available'}. Motor allowlist entries: ${can.allowlist_entries ?? 'unknown'}.`));
    if (command.wire) {
      parts.push(block('Where these bytes came from', 'sources', `Codec, by path: ${command.codec.tools}\nc12_runtime_plan.py ${shortSha(command.codec.c12_runtime_plan_sha256)}\nc12_runtime_wire.py ${shortSha(command.codec.c12_runtime_wire_sha256)}\n`
        + `Older than the release origin/main pins. Device ${command.wire.device_id} and ieee ${command.wire.ieee} are placeholders.\n${command.wire.time_exchange}\nPlan (${command.plan.bytes} bytes, ${command.plan.status}):\n${command.plan.hex}`));
    }
    return commandBox.replaceChildren(...parts);
  }
  async function showCommand(click) {
    commandBox.hidden = false;
    try { renderCommand(await (await fetch(`/__sim/command?click=${click}`, { cache: 'no-store' })).json()); }
    catch (error) { commandBox.replaceChildren(el('div', { textContent: `The command could not be read: ${error.message}` })); }
  }

  function followRun(status) {
    const feed = status.feed;
    if (!sim.running) return;
    if (feed.phase === 'pipeline') {
      const last = feed.pipeline.rows.at(-1);
      message.textContent = `Running the pinned tools on a prepared take: ${last ? `hop ${last.n} done` : 'checking its frames by hash'}. No model runs on a click.`;
      return;
    }
    if (feed.phase === 'pipeline_failed' || feed.phase === 'stopped') {
      if (!status.click) return; // The receipt lands with the stop.
      sim.running = false;
      setFeedEnabled(true);
      const unseen = /^Qwen has not seen these exact frames/.test(feed.pipeline.failed || '');
      message.textContent = feed.phase === 'stopped' ? `The click stopped: ${feed.pipeline.failed}. All four spots stay off.`
        : unseen ? `${feed.pipeline.failed}, so need gets no input, the brain does not decide, and all four spots stay off. The paste below asks Qwen once.`
          : `The pipeline broke, so the brain did not decide and all four spots stay off. ${feed.pipeline.failed}`;
      return;
    }
    if (feed.summary && !sim.personShown) {
      sim.personShown = true;
      if (feed.summary.person_m) overlay()?.setPerson?.({ pos: feed.summary.person_m, sigma_m: .25 }); // Floor disc at the adapter's person.
      message.textContent = 'The pipeline is done. The older brain waits for 3 s of reading before it lights a spot.';
    }
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
        if (status.click) showCommand(status.click.click);
        const swing = feed.swing, decided = feed.summary, modeled = 'Lighting is on: Home\'s modeled view, not measured.';
        // Said on every lit result, never chosen by the button: nothing tests for being on the couch yet.
        const qwen = status.click?.qwen, asked = (qwen?.asks || []).filter(ask => ask.said);
        const counted = [...new Set(asked.map(ask => ask.said))].map(said => `${said} on ${asked.filter(ask => ask.said === said).length} of ${asked.length}`).join('; ');
        const words = qwen?.word === 'SCRIPTED' ? `The words were a fake vLLM's, scripted for a test: ${counted}.` : qwen ? `Qwen's words, recorded once from ${qwen.root}: ${counted}.` : '';
        const scripted = `The older brain's own 1.5 m rule decides whether the reader is on the couch, so a figure standing within 1.5 m of the seat lights a spot too, until step 5 builds the test for being on the couch. ${words}`;
        message.textContent = feed.phase === 'holding'
          ? `The beam is on the reader's hands: ${fmt(decided.hand_from_seat_m, 2)} m from the ${status.couch.label} seat, after ${fmt(swing.angle_deg)}° in ${fmt((swing.finished_at_ms - swing.started_at_ms) / 1000)} s. ${scripted} Nothing was sent. ${modeled}`
          : `The brain refused: ${decided.refusal}. ${words} All four spots stay off. Nothing was sent. ${modeled}`;
      }, 400);
    }
  }

  /* ---------- harness status ---------- */
  function showScenes(status) {
    if (sim.scenesShown) return;
    sim.scenesShown = true;
    sceneButtons.replaceChildren(...status.scenes.map(scene => el('button', { type: 'button', disabled: true, 'data-den-sim-feed': scene.take, textContent: scene.label, onclick: () => runTake(scene.take) })));
    const tries = status.tries ? `, chosen from ${status.tries.count} ${status.tries.count === 1 ? 'try' : 'tries'}: Qwen said reading on ${status.tries.reading}` : '';
    const stopped = (status.set.notes || []).map(note => ` Note: ${note}.`).join('');
    choice.textContent = `Prepared set ${status.set.id}, recorded ${status.set.recorded}: ${status.set.figure}, book ${plain(status.set.way)}${tries}. ${status.placement.summary}.${stopped}`;
  }
  // What Qwen still owes, with the owner's paste line, whenever a reply is still needed; and the last paste's record.
  function showQwen(status) {
    const qwen = status.qwen, twelve = status.click?.rows?.[11], missed = twelve?.word === 'MISSING' && /has not seen these exact frames/.test(twelve.summary);
    owedBox.hidden = !(qwen.owed > 0 || missed);
    pasteCode.textContent = qwen.paste;
    owedText.textContent = qwen.owed > 0 ? `Qwen still owes ${qwen.owed} ${qwen.owed === 1 ? 'reply' : 'replies'}. Paste this line into a new PowerShell window (run.mjs holds this one), then press a scene again:`
      : qwen.owed === 0 ? 'Qwen has no reply for this click\'s exact frames, and none is filed as owed: an agent runs prepare.py --owed, then you paste this line in a new PowerShell window:'
        : 'Qwen has no reply for this click\'s exact frames, and no owed list is filed yet: an agent runs prepare.py --owed, then you paste this line in a new PowerShell window:';
    const last = qwen.last_ask;
    lastAskBox.hidden = !last;
    if (last) {
      const when = new Date(last.finished_unix * 1000).toLocaleString();
      lastAskBox.textContent = `Your last paste, ${when}: ${last.stopped_at ? `stopped at ${last.stopped_at}: ${last.reason}. ` : ''}${last.needed === null ? '' : `${last.answered} of ${last.needed} replies came back, `}`
        + `${last.still_needed === null ? 'nothing is filed as owed' : `${last.still_needed} still needed`}${last.root ? `; model root ${last.root}` : ''}.`;
    }
  }
  async function copyPaste() {
    try { await navigator.clipboard.writeText(pasteCode.textContent); copyButton.textContent = 'Copied'; return; } catch (_) { /* no clipboard API: select it */ }
    const range = document.createRange(), selection = getSelection();
    range.selectNodeContents(pasteCode); selection.removeAllRanges(); selection.addRange(range);
    copyButton.textContent = document.execCommand('copy') ? 'Copied' : 'Selected: press Ctrl+C';
  }
  // "Run every scene": one row per scene, from what each click recorded; the top line stays as the last single click set it.
  function showEvery(status) {
    const table = status.every;
    const key = JSON.stringify(table);
    if (key === sim.everyShown) return;
    sim.everyShown = key;
    everyBox.hidden = !table.rows.length && !sim.every;
    if (everyBox.hidden) return;
    const label = take => status.scenes.find(scene => scene.take === take)?.label || take;
    const cells = (tag, values) => el('tr', {}, ...values.map(value => el(tag, { textContent: value })));
    everyBox.replaceChildren(el('strong', { textContent: `Run every scene: ${table.rows.length} of ${status.scenes.length}` }),
      el('table', { 'data-den-sim-every-table': '1' }, cells('th', ['Scene', 'Box', 'Qwen', 'Beam', 'Why it stopped']),
        ...table.rows.map(row => cells('td', [label(row.take), row.box, row.qwen, row.beam, row.why || '–']))),
      el('span', { className: 'note', 'data-den-sim-every-note': '1', textContent: `No meter on this run. The top line stays as the last single click set it. ${table.verdict ? `MISSING: ${table.verdict}.`
        : table.roots.length ? `Every reply on this table came from one model root, ${table.roots[0]}.` : 'No reply was used on this table.'}` }));
  }
  const settled = phase => ['holding', 'refused', 'stopped', 'pipeline_failed'].includes(phase);
  async function runEvery() {
    const scenes = sim.status?.scenes || [];
    if (!overlay() || !scenes.length) return;
    setFeedEnabled(false);
    sim.every = true; sim.everyShown = null;
    try {
      for (const [i, scene] of scenes.entries()) {
        message.textContent = `Run every scene: ${scene.label}, ${i + 1} of ${scenes.length}. No meter on this run.`;
        const fixtures = fixtureDescriptors().map(d => ({ fixture_id: d.fixture_id, display_origin_m: d.display_origin_m, horizontal_world_reference: d.horizontal_world_reference, pan_deg: d.state.pan_deg, tilt_deg: d.state.tilt_deg }));
        const result = await (await fetch('/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ take: scene.take, fixtures, every: true, first: i === 0 }) })).json();
        if (!result.ok) throw Error(result.reason || 'feed_refused');
        await until(() => sim.status?.every.rows.length === i + 1 && !sim.status.every.running && settled(sim.status.feed.phase), 180000);
        if (sim.status.feed.phase === 'holding') await new Promise(done => setTimeout(done, 1500));   // let the beam be seen
      }
      message.textContent = `Run every scene: done, ${scenes.length} scenes. No meter on this run; the top line is the last single click's.`;
    } catch (error) {
      message.textContent = `Run every scene stopped: ${error.message}`;
    } finally {
      sim.every = false;
      setFeedEnabled(true);
    }
  }
  let markerShown = false;
  async function refresh() {
    let status;
    try { status = await (await fetch('/__sim/status', { cache: 'no-store' })).json(); } catch (_) { return; }
    sim.status = status;
    const room = status.room === 'real' ? 'YOUR SCAN' : 'AUTHORED ROOM';
    strip.textContent = `SIMULATION · rendered takes, a real detector's recorded rows, Qwen's words recorded once and looked up by exact bytes · nothing is sent to any fixture · ${room} · ${status.registration.label} · couch: ${status.couch.label} · INVENTED CAMERA: it does not exist · Home's code from this PC's harness, not your deployed Home`;
    showScenes(status);
    showBrain(status);
    showClick(status.click);
    showQwen(status);
    showEvery(status);
    followRun(status);
    // The invented camera, as a marker in Home's own overlay. Its place is never printed.
    if (!markerShown && status.camera_m && overlay()?.setCalibMarkers) { overlay().setCalibMarkers([status.camera_m]); markerShown = true; }
    values.docs.textContent = String(status.feed.docs_sent);
    values.refused.textContent = `${status.counters.refused} requests, ${status.counters.upgrades_destroyed} sockets (all logged)`;
    values.external.textContent = String(sim.external.size);
    values.csp.textContent = blockedSummary();
    values.frames.textContent = frameSummary();
  }
  setInterval(refresh, 250);
  refresh();

  /* ---------- open the view that shows the beam best ---------- */
  // Unlit Mesh with the fixture selected shows the aim line clearly on the textured room, and
  // Lighting comes on at landing for the pool on the couch. These are Home's own controls.
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
    if (!fixtures.length || !sim.scenesShown) return;
    clearInterval(ready);
    const view = await openBestView();
    setFeedEnabled(true);
    message.textContent = `Room loaded with ${fixtures.length} virtual fixtures. The older brain's empty-room state keeps them off.${view.mesh ? '' : ' Mesh is unavailable, so the beam is faint in this view.'}`;
  }, 250);

  async function runTake(take) {
    const live = overlay(), fixtures = fixtureDescriptors();
    if (!live || !fixtures.length) return;
    setFeedEnabled(false);
    commandBox.hidden = true;
    sim.seen.clear(); sim.frames.length = 0; sim.longTasks = 0; sim.longestTaskMs = 0; sim.swinging = false; sim.personShown = false; sim.running = true;
    message.textContent = 'Checking the take\'s frames by hash and its recorded detector rows, then running the pinned tools on them. No model runs on a click.';
    try {
      const response = await fetch('/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ take, fixtures: fixtures.map(d => ({ fixture_id: d.fixture_id, display_origin_m: d.display_origin_m,
          horizontal_world_reference: d.horizontal_world_reference, pan_deg: d.state.pan_deg, tilt_deg: d.state.tilt_deg })) }) });
      const result = await response.json();
      if (!result.ok) throw Error(result.reason || 'feed_refused');
    } catch (error) {
      sim.running = false;
      setFeedEnabled(true);
      message.textContent = `Feed refused: ${error.message}`;
    }
  }
})();
