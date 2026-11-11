// The scan page's panel, beside Home's own 3D view (scan_pick.mjs serves both). Pick mode: one row per feature, the
// camera's picture with your clicks numbered, and a click on the scan (without dragging) picks the highlighted row's
// feature through Home's display-mesh picker. Show mode: the camera where the fit put it and your six features as the
// camera sees them, as Home's own calibration markers. It talks to nothing but this harness.
(() => {
  'use strict';
  const TOKEN = window.__scanPick?.token;
  const engine = () => window.__APT_ENGINE;
  const fmtCm = metres => `${(metres * 100).toFixed(1)} cm`;
  const until = async (test, ms) => {
    for (const end = Date.now() + ms; Date.now() < end; await new Promise(done => setTimeout(done, 200))) if (test()) return true;
    return test();
  };

  /* ---------- the panel ---------- */
  const style = document.createElement('style');
  style.textContent = `
    #scan-pick { position: fixed; top: 12px; right: 12px; width: 340px; max-height: calc(100vh - 24px); overflow: auto; z-index: 2147483000;
      background: rgba(18, 20, 24, .94); color: #e8e8e8; font: 13px/1.4 system-ui, sans-serif; border-radius: 8px; padding: 12px;
      box-shadow: 0 6px 24px rgba(0,0,0,.4); }
    #scan-pick h2 { font-size: 14px; margin: 0 0 6px; }
    #scan-pick p { margin: 6px 0; color: #c8c8c8; }
    #scan-pick canvas { width: 100%; border-radius: 4px; background: #000; display: block; margin: 6px 0; }
    #scan-pick ol { list-style: none; padding: 0; margin: 6px 0; }
    #scan-pick li { display: flex; gap: 8px; align-items: baseline; padding: 5px 6px; border-radius: 4px; cursor: pointer; }
    #scan-pick li.active { background: #2b5d8a; }
    #scan-pick li .id { font-weight: 600; width: 22px; }
    #scan-pick li .label { flex: 1; }
    #scan-pick li .state { color: #8fd18f; white-space: nowrap; }
    #scan-pick li .state.no { color: #999; }
    #scan-pick button { font: inherit; padding: 6px 12px; border-radius: 4px; border: 0; background: #3b82c4; color: #fff; cursor: pointer; }
    #scan-pick button:disabled { background: #555; cursor: default; }
    #scan-pick .message { min-height: 2.8em; color: #ffd27a; }
    @media (max-width: 720px) { #scan-pick { width: auto; left: 12px; max-height: 45vh; } }`;
  document.head.appendChild(style);
  const panel = document.createElement('div');
  panel.id = 'scan-pick';
  panel.innerHTML = '<h2></h2><p class="intro"></p><canvas width="640" height="400" hidden></canvas><ol></ol>'
    + '<p class="message"></p><button type="button" disabled></button>';
  document.body.appendChild(panel);
  const [title, intro, canvas, list, message, button] = ['h2', '.intro', 'canvas', 'ol', '.message', 'button'].map(s => panel.querySelector(s));
  const say = text => { message.textContent = text; };

  let state = null, active = null, ready = false;
  const picks = {};   // id -> { point_m, surface_ref }, new on this page

  /* ---------- the camera's picture, with the clicks numbered ---------- */
  const left = new Image();
  function drawLeft() {
    if (!state?.left || !left.complete || !left.naturalWidth) return;
    canvas.hidden = false;
    const g = canvas.getContext('2d');
    g.drawImage(left, 0, 0, 640, 400);
    for (const [id, c] of Object.entries(state.clicks)) {
      const on = id === active;
      g.beginPath(); g.arc(c.u, c.v, on ? 14 : 10, 0, Math.PI * 2);
      g.lineWidth = on ? 4 : 3; g.strokeStyle = on ? '#ffcc33' : '#33ff99'; g.stroke();
      g.font = 'bold 22px system-ui'; g.fillStyle = on ? '#ffcc33' : '#33ff99';
      g.fillText(id, c.u + 14, c.v - 10);
    }
  }
  left.onload = drawLeft;

  /* ---------- the rows ---------- */
  const isPicked = point => !!picks[point.id] || point.picked;
  function render() {
    list.replaceChildren(...state.points.map(point => {
      const row = document.createElement('li');
      row.className = point.id === active ? 'active' : '';
      const clickedOnCamera = state.clicks[point.id] ? '' : ' (not clicked on the camera)';
      if (state.mode === 'show') {
        const feature = state.show.features.find(item => item.id === point.id);
        row.innerHTML = '<span class="id"></span><span class="label"></span><span class="state"></span>';
        row.children[2].textContent = feature ? `${fmtCm(feature.apart_m)} off` : 'no depth';
      } else {
        row.innerHTML = '<span class="id"></span><span class="label"></span><span class="state"></span>';
        row.children[2].textContent = picks[point.id] ? 'picked' : point.picked ? 'kept' : 'not yet';
        row.children[2].className = 'state' + (isPicked(point) ? '' : ' no');
        row.onclick = () => { active = point.id; render(); say(`Click ${point.id} in the scan: ${point.label}.`); };
      }
      row.children[0].textContent = point.id;
      row.children[1].textContent = point.label + clickedOnCamera;
      return row;
    }));
    drawLeft();
    if (state.mode === 'pick') button.disabled = !ready || !state.points.every(isPicked);
    markers();
  }
  function markers() {
    const overlay = engine()?.overlay;
    if (!overlay?.setCalibMarkers) return;
    if (state.mode === 'show') overlay.setCalibMarkers([state.show.camera_m, ...state.show.features.map(item => item.point_m)]);
    else overlay.setCalibMarkers(state.points.map(point => picks[point.id]?.point_m || point.point_m).filter(Boolean));
  }
  function nextUnpicked(after) {
    const ids = state.points.map(point => point.id), start = Math.max(0, ids.indexOf(after) + 1);
    return [...ids.slice(start), ...ids.slice(0, start)].find(id => !isPicked(state.points.find(point => point.id === id))) || null;
  }

  /* ---------- a click on the scan picks the active row's feature ---------- */
  let down = null;
  const onScan = event => {
    const canvasEl = engine()?.renderer?.domElement;
    return !!canvasEl && !panel.contains(event.target) && (event.target === canvasEl || canvasEl.parentElement?.contains(event.target));
  };
  window.addEventListener('pointerdown', event => { down = onScan(event) ? { x: event.clientX, y: event.clientY } : null; }, true);
  window.addEventListener('click', event => {
    // The press must start on the scan; Home may release it over one of its own overlay elements, so the click's
    // target isn't checked again, only that it isn't this panel.
    if (state?.mode !== 'pick' || !ready || !active || !down || panel.contains(event.target)) return;
    const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
    down = null;
    if (moved > 5) return;
    event.stopPropagation(); event.preventDefault();
    let hit = null;
    try { hit = engine().deskTargetSurfaceHit(event.clientX, event.clientY, state.binding); } catch (error) { say(`No pick: ${error.message}`); return; }
    if (!hit) { say('Nothing of the scan is under that spot. Orbit closer and click again.'); return; }
    picks[active] = { point_m: hit.point.map(v => Math.round(v * 1e6) / 1e6),
      surface_ref: { asset_name: hit.surface_ref.asset_name, asset_sha256: hit.surface_ref.asset_sha256 } };
    const done = active;
    active = nextUnpicked(active);
    say(active ? `${done} picked. Now ${active}: ${state.points.find(point => point.id === active).label}.`
      : `${done} picked. All six are in: check the green dots, re-pick any row, then Save.`);
    render();
  }, true);

  /* ---------- save, or done ---------- */
  async function post(where, body) {
    const response = await fetch(where, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Scan-Pick': TOKEN }, body: JSON.stringify(body) });
    return response.json();
  }
  button.onclick = async () => {
    button.disabled = true;
    try {
      const result = await post(state.mode === 'pick' ? '/__pick/save' : '/__pick/done', state.mode === 'pick' ? { picks } : {});
      if (!result.ok) throw Error(result.error || 'refused');
      say(state.mode === 'pick' ? 'Saved. Back in PowerShell, the fit runs now. You can close this tab.' : 'Done. You can close this tab.');
    } catch (error) {
      say(`Not saved: ${error.message}`);
      button.disabled = false;
    }
  };

  /* ---------- start: the state, then Home's mesh and its picker ---------- */
  const toolbarButton = name => [...document.querySelectorAll('[role="toolbar"] button')]
    .find(item => item.getAttribute('aria-label') === name || item.textContent.trim() === name);
  (async () => {
    try { state = await (await fetch('/__pick/state')).json(); } catch (error) { say(`The harness didn't answer: ${error.message}`); return; }
    if (state.mode === 'show') {
      title.textContent = 'Step 2: where the fit puts the camera';
      intro.textContent = `Green dots: the camera (${state.show.transform_id}), and your six features as the camera sees them, carried into your scan by the fit. Each should sit on its feature. The distance beside each row is how far it lands from your scan pick.`;
      button.textContent = 'Done';
      button.disabled = false;
    } else {
      title.textContent = 'Step 2: the same six features, in your scan';
      intro.textContent = 'Pick a row, orbit to that feature, and click it once without dragging. The camera\'s picture shows what you clicked there.';
      button.textContent = 'Save';
      active = nextUnpicked(null);
    }
    if (state.left) left.src = '/__pick/left.png';
    render();
    say('Opening your scan...');
    const view = await until(() => engine()?.modes && toolbarButton('Mesh view'), 60000);
    if (!view) { say('Home\'s 3D view didn\'t open. Reload the page.'); return; }
    if (toolbarButton('Mesh view').getAttribute('aria-pressed') !== 'true') toolbarButton('Mesh view').click();
    await until(() => engine()?.modes?.mode === 'mesh', 30000);
    if (state.mode === 'pick') {
      try { await engine().prepareDeskTargetSurface(state.binding); } catch (error) { say(`The scan's mesh isn't pickable: ${error.message}`); return; }
      ready = true;
      say(active ? `Click ${active} in the scan: ${state.points.find(point => point.id === active).label}.` : 'All six are kept from before. Re-pick any row, or Save.');
    } else {
      say('Look for the dots, then press Done.');
    }
    render();
  })();
})();
