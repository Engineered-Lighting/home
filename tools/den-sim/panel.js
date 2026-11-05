/* Den simulation panel, injected by tools/den-sim/run.mjs into the unchanged Home.
 * A red strip, the hop list and two scripted scenes for the pipeline and brain.
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
    #den-sim-panel [data-den-sim-figure] { display: block; width: 100%; margin: 4px 0; background: #000; border: 1px solid #333; border-radius: 4px; }
    #den-sim-panel [data-den-sim-pipeline-failed] { color: #ff9aa8; font-weight: 600; }
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
    #den-sim-panel .pill { display: inline-block; min-width: 58px; text-align: center; font: 700 9px/15px system-ui, sans-serif; border-radius: 3px; color: #fff; margin-right: 5px; }
    #den-sim-panel .note { color: #a8a8a8; display: block; }
  ` }));

  const strip = el('div', { id: 'den-sim-strip', role: 'status', 'data-den-sim-strip': '1', textContent: 'SIMULATION · scripted scene, no inference ran · nothing is sent to any fixture' });
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
  const commandBox = el('div', { 'data-den-sim-command': '1', hidden: true });
  const pipelineRows = el('ul', { 'data-den-sim-pipeline': '1' });
  const pipelineBox = el('details', { open: true, hidden: true }, el('summary', { textContent: 'The pipeline, this click' }), pipelineRows);
  const body = el('div', { 'data-den-sim-body': '1' }, pipelineBox, brainBox, readback, commandBox, candidates, proof, hops);
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
    secondTry.hidden = false;
    secondTry.textContent = (status.brain.away_from_fixture
      ? `With joints facing the room, the brain found no clear fixture: ${first.refusal}. So person A is turned away from the nearest fixture, which may not be how anyone sits on your couch. `
      : '') + 'The camera is INVENTED, 3 m behind person A, because den-observe cannot read den-register\'s output.';
    values.says.textContent = feed.live?.why || '–';
    values.refusal.textContent = feed.live?.refusal || '–';
    values.events.textContent = decided ? `${decided.events}, from the adapter, into the real activity loop` : '–';
    const landed = feed.phase === 'swinging' || feed.phase === 'holding';
    values.chose.textContent = landed ? `${decided.fixture_id}, pan ${fmt(decided.pan_deg)}°, tilt ${fmt(decided.tilt_deg)}°` : '–';
    values.hand.textContent = landed ? `${decided.target_m.map(v => fmt(v, 2)).join(', ')}, ${fmt(decided.hand_from_seat_m, 2)} m from the seat` : '–';
    if (!landed) values.agree.textContent = '–';
    showCandidates(decided ? decided.candidates : [], decided?.candidates.length ? '' : 'The brain only compares fixtures once reading has held for 3 s.');
  }
  /* ---------- the exact command: text only, with nothing that could send it ---------- */
  const block = (summary, name, text, open = false) => el('details', { open }, el('summary', { textContent: summary }), el('pre', { 'data-den-sim-pre': name, textContent: text }));
  const rows = pairs => el('dl', {}, ...pairs.flatMap(([label, text]) => [el('dt', { textContent: label }), el('dd', { textContent: text })]));
  const post = row => `POST ${row.route}\n${JSON.stringify(row.body)}`;
  const shortSha = text => text ? text.slice(0, 16) : 'unknown';
  function hueBlock(decide) {
    if (!decide?.ok) return el('div', { className: 'note', textContent: decide?.reason || 'decide v0 did not run.' });
    const text = decide.calls.length ? decide.calls.map(post).join('\n') : decide.why;
    const label = decide.calls.length ? `Hue form from decide v0 (${decide.calls.length} calls, logged only)` : 'Hue form from decide v0: none';
    const note = decide.calls.length ? `${decide.why} ${decide.look_status}. decide outputs on, brightness and kelvin; the light.turn_on shape is this page's. Source ${decide.source}.` : decide.why;
    return block(label, 'hue', `${text}\n\n${note}`);
  }
  function renderCommand(command) {
    const badge = el('div', { 'data-den-sim-not-sent': '1', textContent: 'NOT SENT · nothing here can send it' });
    if (command.failed) return commandBox.replaceChildren(badge, el('div', { textContent: command.failed }));
    if (!command.brain) return commandBox.replaceChildren(badge, el('div', { textContent: 'No fixture command: the brain refused, so nothing was compiled.' }), hueBlock(command.decide));
    const ik = command.ik;
    if (!ik.ok) return commandBox.replaceChildren(badge, el('div', { textContent: `No fixture command: compile_target refused (${ik.reason}).` }), hueBlock(command.decide));
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
      + `Contract oracle: ${can.oracle_ok === true ? 'matches' : can.oracle_ok === false ? 'DOES NOT MATCH' : 'not available'}. Motor allowlist entries: ${can.allowlist_entries ?? 'unknown'}.`), hueBlock(command.decide));
    if (command.wire) {
      parts.push(block('Where these bytes came from', 'sources', `Codec, by path: ${command.codec.tools}\nc12_runtime_plan.py ${shortSha(command.codec.c12_runtime_plan_sha256)}\nc12_runtime_wire.py ${shortSha(command.codec.c12_runtime_wire_sha256)}\n`
        + `Older than the release origin/main pins. Device ${command.wire.device_id} and ieee ${command.wire.ieee} are placeholders.\n${command.wire.time_exchange}\nPlan (${command.plan.bytes} bytes, ${command.plan.status}):\n${command.plan.hex}`));
    }
    return commandBox.replaceChildren(...parts);
  }
  async function showCommand(scenario) {
    commandBox.hidden = false;
    try { renderCommand(await (await fetch(`/__sim/command?scenario=${encodeURIComponent(scenario)}`, { cache: 'no-store' })).json()); }
    catch (error) { commandBox.replaceChildren(el('div', { textContent: `The command could not be read: ${error.message}` })); }
  }

  /* ---------- the pipeline, hop by hop, and its stick figure ---------- */
  const SVG = 'http://www.w3.org/2000/svg';
  const shape = (tag, attributes, ...children) => {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    node.append(...children);
    return node;
  };
  const BONES = [[0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
  // The detector row's own numbers. There is no picture behind them: nothing looked at one.
  function stickFigure(row) {
    const [x0, y0, x1, y1] = row.box_xyxy, joints = row.keypoints, pad = 12;
    return el('div', {}, shape('svg', { viewBox: `${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`, height: 170, role: 'img', 'aria-label': 'Scripted joints and their box. There is no picture.', 'data-den-sim-figure': '1' },
      shape('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: 'none', stroke: '#e0526a', 'stroke-width': 1.5 }),
      ...BONES.filter(([a, b]) => joints[a] && joints[b]).map(([a, b]) => shape('line', { x1: joints[a][0], y1: joints[a][1], x2: joints[b][0], y2: joints[b][1], stroke: '#e8e8e8', 'stroke-width': 2, 'stroke-linecap': 'round' })),
      ...joints.filter(Boolean).map(joint => shape('circle', { cx: joint[0], cy: joint[1], r: 2.5, fill: '#7fb2ff' }))),
      el('span', { className: 'note', textContent: 'The detector row\'s box and joints, from fake-scripted-1. There is no picture behind them.' }));
  }
  function showPipeline(pipeline) {
    const key = `${pipeline.rows.length}:${pipeline.failed}`;
    if (key === sim.pipelineShown) return;
    sim.pipelineShown = key;
    pipelineBox.hidden = !pipeline.rows.length && !pipeline.failed;
    pipelineRows.replaceChildren(...pipeline.rows.flatMap(row => {
      const line = el('li', { 'data-den-sim-pipeline-hop': row.hop, 'data-den-sim-status': row.status },
        el('span', { className: 'pill', style: `background:${PILL[row.status]}`, textContent: row.status }), row.hop,
        el('span', { className: 'note', textContent: row.ms === undefined ? row.summary : `${row.summary} ${fmt(row.ms / 1000)} s.` }));
      return row.keypoints ? [line, el('li', {}, stickFigure(row))] : [line];
    }));
    if (pipeline.failed) pipelineRows.append(el('li', { 'data-den-sim-pipeline-failed': '1', textContent: `Stopped: ${pipeline.failed}` }));
  }

  function followRun(status) {
    const feed = status.feed;
    if (!sim.running) return;
    if (feed.phase === 'pipeline') {
      message.textContent = `Running the pinned pipeline tools on a scripted scene: ${feed.pipeline.rows.length} hops so far. No inference runs.`;
      return;
    }
    if (feed.phase === 'pipeline_failed') {
      sim.running = false;
      setFeedEnabled(true);
      message.textContent = `The pipeline stopped, so the brain did not decide and all four spots stay off. ${feed.pipeline.failed}`;
      return;
    }
    if (feed.summary && !sim.personShown) {
      sim.personShown = true;
      overlay()?.setPerson?.({ pos: feed.summary.person_m, sigma_m: .25 }); // Floor disc at the adapter's person A.
      message.textContent = feed.scenario === 'not_reading'
        ? 'The pipeline is done, and need did not call this reading. The real brain now judges it.'
        : 'The pipeline is done, and need called this reading. The real brain waits for 3 s of reading before it lights a spot.';
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
        showCommand(feed.scenario);
        const swing = feed.swing, decided = feed.summary, modeled = 'Lighting is on: Home\'s modeled view, not measured.';
        message.textContent = feed.phase === 'holding'
          ? `The beam is on person A's hands: ${fmt(decided.hand_from_seat_m, 2)} m from the ${status.couch.label} seat, after ${fmt(swing.angle_deg)}° in ${fmt((swing.finished_at_ms - swing.started_at_ms) / 1000)} s. The tools and the brain ran for real; the scene and the motion are scripted. Nothing was sent. ${modeled}`
          : `The brain refused: ${decided.refusal}. All four spots stay off. The scene is scripted. Nothing was sent. ${modeled}`;
      }, 400);
    }
  }

  /* ---------- harness status ---------- */
  async function refresh() {
    let status;
    try { status = await (await fetch('/__sim/status', { cache: 'no-store' })).json(); } catch (_) { return; }
    sim.status = status;
    const room = status.room === 'real' ? 'YOUR SCAN' : 'AUTHORED ROOM';
    strip.textContent = `SIMULATION · real tools, scripted scene, no inference ran · nothing is sent to any fixture · ${room} · ${status.registration.label} · couch: ${status.couch.label} · INVENTED CAMERA`;
    showBrain(status);
    showPipeline(status.feed.pipeline);
    followRun(status);
    const hopsKey = JSON.stringify(status.hops);
    if (hopsKey !== sim.hopsShown) {
      sim.hopsShown = hopsKey;
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
    commandBox.hidden = true;
    sim.seen.clear(); sim.frames.length = 0; sim.longTasks = 0; sim.longestTaskMs = 0; sim.swinging = false; sim.personShown = false; sim.running = true;
    message.textContent = 'Starting the pinned pipeline tools on a scripted scene. No inference runs.';
    try {
      const response = await fetch('/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, fixtures: fixtures.map(d => ({ fixture_id: d.fixture_id, display_origin_m: d.display_origin_m,
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
