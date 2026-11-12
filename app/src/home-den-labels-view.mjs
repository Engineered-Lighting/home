/* Den labels in Home: your labels on the den's scan for every viewer, and, signed in, a Labels tool to add and fit them.
 *
 * It does nothing unless the page shell names it (window.HomeDenLabels = { site, labels }: the gateway's den-live route, or
 * den-live's local page). While the den's apartment view shows (window.__APT_ENGINE.siteId is that site, its canvas on
 * screen) it mounts the labels layer (home-3d/apartment-labels-layer.mjs) and a small overlay, reads the den's one labels
 * document (den-room-labels-1, GET HomeDenLabels.labels) and draws it. A look-only device only sees the labels.
 *
 * Signed in, the Labels button opens the tool (plan.md P4): click anything in the scan and name it; a box of a typical
 * size appears where you clicked (home-den-labels-core.mjs placeBox, flat tops fitted with Home's named-aims fit), and
 * its corner, height, turn and move handles fit it. The first time, a checklist of usual den things leads. Save sends
 * the whole document back (PUT, compare-and-swap on its revision); unsaved changes stay in this browser until then.
 * While the tool is open, a click on the scan labels instead of selecting a light. Nothing here sends a light command. */
import * as THREE from 'three';
import * as L from './home-den-labels-core.mjs';

const config = window.HomeDenLabels;
const TICK_MS = 500, DRAFT_KEY = 'home.denLabels.draft.v1', CLICK_SLOP_PX = 5, ROOM_TOP = 2.6;

if (config && typeof config.labels === 'string' && typeof config.site === 'string') {
    const cb = new URL(import.meta.url).search || '';
    import(`./home-3d/apartment-labels-layer.mjs${cb}`)
        .then(({ attachApartmentLabelsLayer }) => start(attachApartmentLabelsLayer))
        .catch(error => console.warn('[den-labels] layer unavailable', error));
}

function start(attachLayer) {
    const signedIn = window.HomeLookOnly?.active !== true;
    const S = {
        engine: null, layer: null, root: null, host: null, view: null,
        saved: null, labels: [], dirty: 0, undo: [], sel: null, load: 'idle', loadError: '',
        open: false, pending: null, armed: null, checklist: false, skipped: new Set(),
        save: { state: 'idle', text: '' }, conflict: null, toast: '', toastAt: 0, drag: null, down: null, swallowUntil: 0,
    };
    const ui = {};
    const tagEls = new Map(), handleEls = new Map();
    let raf = 0;

    /* ---------------- engine and mount ---------------- */
    function denEngine() {
        const current = window.__APT_ENGINE, canvas = current?.renderer?.domElement;
        if (document.hidden || !current?.apartmentRoot || current.siteId !== config.site || !canvas?.isConnected) return null;
        const host = canvas.parentElement;
        if (host?.dataset?.aptSpatialOwner && host.dataset.aptSpatialOwner !== 'apartment') return null;
        if (!canvas.getClientRects().length || window.getComputedStyle(canvas).visibility === 'hidden') return null;
        const rect = canvas.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 ? current : null;
    }
    function sync() {
        const current = denEngine();
        if (!current) { if (S.engine) unmount(); return; }
        if (current !== S.engine) { unmount(); mount(current); }
        const view = current.renderer.domElement.closest('[data-home-apartment-view="1"]') || current.renderer.domElement.parentElement;
        if (S.root && S.root.parentElement !== view) { view.appendChild(S.root); S.view = view; }
    }
    function mount(engine) {
        S.engine = engine;
        S.layer = attachLayer(engine);
        S.host = engine.renderer.domElement.parentElement;
        S.root = buildDom();
        window.addEventListener('pointerdown', onDown, true);
        window.addEventListener('pointerup', onUp, true);
        window.addEventListener('click', onClick, true);
        if (S.load !== 'ok') load(); else render();
        raf = requestAnimationFrame(frame);
    }
    function unmount() {
        cancelAnimationFrame(raf);
        window.removeEventListener('pointerdown', onDown, true);
        window.removeEventListener('pointerup', onUp, true);
        window.removeEventListener('click', onClick, true);
        S.layer?.dispose(); S.root?.remove();
        tagEls.clear(); handleEls.clear();
        S.engine = S.layer = S.root = S.host = S.view = null;
        S.open = false; S.pending = null; S.drag = null;
    }

    /* ---------------- the document ---------------- */
    async function load() {
        S.load = 'loading'; render();
        try {
            const response = await fetch(config.labels, { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } });
            if (!response.ok) throw Error(response.status === 503 ? 'den-live has no labels store' : `HTTP ${response.status}`);
            const doc = await response.json();
            const problem = L.docProblem(doc);
            if (problem) throw Error(`labels refused (${problem})`);
            S.saved = doc; S.load = 'ok'; S.loadError = '';
            const draft = readDraft();
            if (draft && draft.dirty > 0) { S.labels = draft.labels; S.dirty = draft.dirty; }
            else { S.labels = clone(doc.labels); S.dirty = 0; }
            if (signedIn && !S.labels.length && !S.skipped.size) { S.checklist = true; S.armed = nextChecklistItem(); }
        } catch (error) {
            S.load = 'offline'; S.loadError = String(error.message || error);
            const draft = readDraft();
            if (draft && !S.labels.length) { S.labels = draft.labels; S.dirty = draft.dirty; }
        }
        render();
    }
    const clone = value => JSON.parse(JSON.stringify(value));
    function readDraft() {
        try {
            const value = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
            return value && value.site === config.site && Array.isArray(value.labels) && Number.isInteger(value.dirty) ? value : null;
        } catch (_) { return null; }
    }
    function writeDraft() {
        try {
            if (S.dirty > 0) localStorage.setItem(DRAFT_KEY, JSON.stringify({ site: config.site, base: S.saved?.revision ?? 0, labels: S.labels, dirty: S.dirty }));
            else localStorage.removeItem(DRAFT_KEY);
        } catch (_) { /* a private window keeps no draft; Save still works */ }
    }
    function commit(change, { quiet = false } = {}) {
        S.undo.push(JSON.stringify(S.labels)); if (S.undo.length > 20) S.undo.shift();
        change();
        S.dirty += 1; S.save = { state: 'idle', text: '' };
        writeDraft();
        if (!quiet) render();
    }
    function undo() {
        if (!S.undo.length) return;
        S.labels = JSON.parse(S.undo.pop());
        S.dirty = Math.max(0, S.dirty - 1);
        if (!byId(S.sel)) S.sel = null;
        writeDraft(); render();
    }
    const byId = id => S.labels.find(l => l.id === id) || null;
    const checkThese = () => !!S.saved && S.dirty === 0 && !L.sameScan(S.saved, S.engine?.contentRevision ?? null);

    async function save({ force = false } = {}) {
        if (!S.engine || S.save.state === 'saving') return;
        if (!S.dirty && !checkThese() && !force) return;
        const base = S.saved?.revision ?? 0;
        const doc = { ...L.emptyDoc(S.engine.contentRevision ?? null), revision: base, saved_at: S.saved?.saved_at ?? null, labels: S.labels };
        const problem = L.docProblem(doc);
        if (problem) { S.save = { state: 'error', text: `Not saved: ${problem.replace(/_/g, ' ')}.` }; render(); return; }
        S.save = { state: 'saving', text: 'Saving…' }; render();
        let response = null, body = null;
        try {
            response = await fetch(config.labels, { method: 'PUT', cache: 'no-store', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'x-home-den-labels': '1' }, body: JSON.stringify(doc) });
            body = await response.json().catch(() => null);
        } catch (_) { response = null; }
        if (response?.status === 200 && body && !L.docProblem(body)) {
            S.saved = body; S.labels = clone(body.labels); S.dirty = 0; S.undo = [];
            S.save = { state: 'saved', text: `Saved · revision ${body.revision}` };
            S.load = 'ok'; writeDraft();
        } else if (response?.status === 409 && body && !L.docProblem(body)) {
            S.conflict = body; S.save = { state: 'idle', text: '' };
        } else if (response?.status === 400) {
            S.save = { state: 'error', text: `Not saved: ${String(body?.error || 'refused').replace(/_/g, ' ')}.` };
        } else if (response?.status === 401 || response?.status === 403) {
            S.save = { state: 'error', text: 'Not saved: sign in to Home again, then Save. Your changes are kept in this browser.' };
        } else {
            S.save = { state: 'error', text: "den-live isn't running, so labels can't be saved now. Your changes are kept in this browser." };
        }
        render();
    }

    /* ---------------- picking and placing ---------------- */
    function apartmentRay(clientX, clientY) {
        const e = S.engine;
        try {
            e.picking.pick([], clientX, clientY);
            const ray = e.picking.raycaster.ray;
            e.apartmentRoot.updateMatrixWorld(true);
            const o = e.apartmentRoot.worldToLocal(ray.origin.clone());
            const p = e.apartmentRoot.worldToLocal(ray.origin.clone().add(ray.direction));
            const d = [p.x - o.x, p.y - o.y, p.z - o.z], l = Math.hypot(...d) || 1;
            return { origin: [o.x, o.y, o.z], direction: d.map(v => v / l) };
        } catch (_) { return null; }
    }
    function rayPlane(ray, point, normal) {
        if (!ray) return null;
        const denom = ray.direction[0] * normal[0] + ray.direction[1] * normal[1] + ray.direction[2] * normal[2];
        if (Math.abs(denom) < 1e-6) return null;
        const t = ((point[0] - ray.origin[0]) * normal[0] + (point[1] - ray.origin[1]) * normal[1] + (point[2] - ray.origin[2]) * normal[2]) / denom;
        return t > 0 ? ray.origin.map((v, i) => v + ray.direction[i] * t) : null;
    }
    function viewer() {
        const e = S.engine;
        e.apartmentRoot.updateMatrixWorld(true);
        const v = e.apartmentRoot.worldToLocal(e.camera.position.clone());
        return [v.x, v.y];
    }
    /* Home's named-aims flat-top fit under a click, on the display mesh: { center, size, up } or null. */
    function flatTopFit(clientX, clientY) {
        const N = window.AptNamedAims, e = S.engine, mesh = e.modes?.getMesh?.();
        if (!N?.planarRegion || !N?.fitRectangle || !mesh) return null;
        try {
            const hit = e.picking.pick([mesh], clientX, clientY).find(h => h.point && h.face && Number.isInteger(h.faceIndex));
            const position = hit?.object?.geometry?.attributes?.position;
            if (!position?.array) return null;
            e.apartmentRoot.updateMatrixWorld(true); hit.object.updateMatrixWorld(true);
            const matrix = e.apartmentRoot.matrixWorld.clone().invert().multiply(hit.object.matrixWorld).elements;
            const region = N.planarRegion({ positions: position.array, index: hit.object.geometry.index?.array || null, faceIndex: hit.faceIndex,
                matrix: Array.from(matrix), cacheKey: hit.object.geometry });
            if (!region || Math.abs(region.normal[2]) < 0.9) return null;
            const fit = N.fitRectangle(region.points, region.normal[2] < 0 ? region.normal.map(v => -v) : region.normal);
            return fit && Array.isArray(fit.center) && Array.isArray(fit.size) && Array.isArray(fit.up) ? fit : null;
        } catch (_) { return null; }
    }
    function create(name, kind, hit, clientX, clientY) {
        const k = kind || L.guess(name).kind;
        const fit = k === 'table' && hit.normal[2] > 0.65 ? flatTopFit(clientX, clientY) : null;
        const placed = L.placeBox({ name, kind, hit, viewer: viewer(), fit, roomTop: ROOM_TOP });
        const label = L.makeLabel({ id: L.newId(name), name: L.uniqueName(name, S.labels), kind: k, box: placed.box, on_wall: placed.on_wall,
            updated_at: new Date().toISOString() });
        commit(() => { S.labels.push(label); S.sel = label.id; });
    }
    function scanDistance(point) {
        const e = S.engine, v = new THREE.Vector3(...point);
        e.apartmentRoot.localToWorld(v);
        return v.distanceTo(e.camera.position);
    }
    /* A press and release on the scan that hardly moved is a pick; a drag still turns the room. The release can land on
     * a layer Home puts over the canvas while the pointer is down, so the press decides, and the click that follows a
     * pick is swallowed before Home sees it: a click with the tool open labels, and never selects a light. */
    function onDown(ev) {
        S.down = S.open && signedIn && ev.button === 0 && ev.target === S.engine?.renderer?.domElement
            ? { x: ev.clientX, y: ev.clientY, id: ev.pointerId } : null;
    }
    function onUp(ev) {
        const down = S.down; S.down = null;
        if (!down || ev.pointerId !== down.id || Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > CLICK_SLOP_PX) return;
        S.swallowUntil = performance.now() + 500;
        pickAt(ev.clientX, ev.clientY);
    }
    function onClick(ev) {
        if (!S.swallowUntil || performance.now() > S.swallowUntil) return;
        S.swallowUntil = 0;
        ev.stopPropagation(); ev.preventDefault();
    }
    function pickAt(x, y) {
        const e = S.engine;
        const onLabel = S.armed ? null : (e.picking.pick(S.layer.meshes(), x, y) || [])[0] || null;
        const scan = e.displaySurfaceHit?.(x, y) || null;
        if (onLabel && (!scan || onLabel.distance <= scanDistance(scan.point) + 0.05)) { closePop(); select(onLabel.object.userData.labelId); return; }
        if (!scan) { closePop(); select(null); toast('No surface there. Turn the view and click the object itself.'); return; }
        const hit = { point: scan.point.slice(), normal: scan.normal.slice() };
        if (S.armed) {
            const name = S.armed;
            create(name, null, hit, x, y);
            S.armed = nextChecklistItem(); render();
            return;
        }
        openPop(hit, x, y);
    }
    function select(id) { S.sel = id; render(); }

    /* ---------------- the checklist ---------------- */
    function nextChecklistItem() {
        const done = L.checklistDone(S.labels);
        return L.CHECKLIST.find(item => !done.has(item) && !S.skipped.has(item)) || null;
    }

    /* ---------------- DOM ---------------- */
    const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    function buildDom() {
        const root = document.createElement('div');
        root.className = 'home-den-labels';
        root.dataset.denLabels = '1';
        root.innerHTML = `<div class="home-den-labels-tags"></div><div class="home-den-labels-handles"></div>
            ${signedIn ? `<button type="button" class="home-den-labels-toggle" data-act="open" aria-label="Label objects in the den">Labels</button>
            <div class="home-den-labels-banner" role="status" hidden></div>
            <div class="home-den-labels-hint" role="status" hidden></div>
            <div class="home-den-labels-pop" hidden></div>
            <aside class="home-den-labels-panel" aria-label="Den labels" hidden></aside>
            <div class="home-den-labels-modal" hidden></div>` : ''}`;
        ui.tags = root.querySelector('.home-den-labels-tags');
        ui.handles = root.querySelector('.home-den-labels-handles');
        ui.toggle = root.querySelector('.home-den-labels-toggle');
        ui.banner = root.querySelector('.home-den-labels-banner');
        ui.hint = root.querySelector('.home-den-labels-hint');
        ui.pop = root.querySelector('.home-den-labels-pop');
        ui.panel = root.querySelector('.home-den-labels-panel');
        ui.modal = root.querySelector('.home-den-labels-modal');
        root.addEventListener('click', onRootClick);
        root.addEventListener('change', onRootChange);
        ui.pop?.addEventListener('input', onPopInput);
        ui.pop?.addEventListener('keydown', onPopKey);
        return root;
    }
    function onRootClick(ev) {
        const target = ev.target.closest('[data-act]');
        if (!target || !S.root?.contains(target)) return;
        const act = target.dataset.act, id = target.dataset.id;
        if (act === 'open') { S.open = true; if (S.load !== 'ok') load(); render(); }
        else if (act === 'close') { S.open = false; S.armed = null; closePop(); S.sel = null; render(); }
        else if (act === 'select') { closePop(); select(id); }
        else if (act === 'save') save();
        else if (act === 'fine') save({ force: true });
        else if (act === 'undo') undo();
        else if (act === 'retry') load();
        else if (act === 'checklist') { S.checklist = true; S.armed = nextChecklistItem(); render(); }
        else if (act === 'checklist-done') { S.checklist = false; S.armed = null; render(); }
        else if (act === 'arm') { closePop(); S.sel = null; S.skipped.delete(target.dataset.item); S.armed = target.dataset.item || null; render(); }
        else if (act === 'skip') { ev.stopPropagation(); S.skipped.add(target.dataset.item); S.armed = nextChecklistItem(); render(); }
        else if (act === 'turn') { const l = byId(S.sel); if (l) commit(() => { l.box.yaw_deg = L.normYaw(l.box.yaw_deg + Number(target.dataset.by)); }); }
        else if (act === 'delete') { const l = byId(S.sel); if (l) commit(() => { S.labels = S.labels.filter(o => o.id !== l.id); S.sel = null; }); }
        else if (act === 'done') select(null);
        else if (act === 'suggest') choose(Number(target.dataset.i));
        else if (act === 'kinds') { const k = ui.pop.querySelector('.home-den-labels-kinds'); k.hidden = !k.hidden; }
        else if (act === 'kind') { S.pending.kind = target.dataset.kind; drawPop(); ui.pop.querySelector('input')?.focus(); }
        else if (act === 'keep-mine') { const theirs = S.conflict; S.conflict = null; S.saved = theirs; render(); save({ force: true }); }
        else if (act === 'load-theirs') { const theirs = S.conflict; S.conflict = null; S.saved = theirs; S.labels = clone(theirs.labels); S.dirty = 0; S.undo = []; S.sel = null; writeDraft(); render(); }
    }
    function onRootChange(ev) {
        const field = ev.target.dataset.field, l = byId(S.sel);
        if (!field || !l) return;
        if (field === 'name') {
            const value = L.cleanName(ev.target.value), problem = L.nameProblem(value, S.labels, l.id);
            if (problem) { S.save = { state: 'error', text: problem }; render(); return; }
            commit(() => { l.name = value; l.updated_at = new Date().toISOString(); });
        } else if (field === 'kind') {
            commit(() => { l.kind = ev.target.value; l.updated_at = new Date().toISOString(); });
        } else if (field.startsWith('size')) {
            const i = Number(field.slice(4)), v = Math.min(L.MAX_SIZE, Math.max(L.MIN_SIZE, Number(ev.target.value) || L.MIN_SIZE));
            commit(() => {
                const s = l.box.size_m.slice(); const bottom = l.box.centre_m[2] - s[2] / 2;
                s[i] = Math.round(v * 1000) / 1000; l.box.size_m = s;
                if (i === 2 && !l.on_wall) l.box.centre_m = [l.box.centre_m[0], l.box.centre_m[1], Math.round((bottom + s[2] / 2) * 1000) / 1000];
                l.updated_at = new Date().toISOString();
            });
        }
    }

    /* The name box beside a click: usual names as you type, Enter adds the highlighted one. */
    function openPop(hit, x, y) {
        S.sel = null;
        S.pending = { hit, x, y, name: '', kind: null, hi: 0 };
        ui.pop.hidden = false;
        ui.pop.innerHTML = `<input class="home-den-labels-input" autocomplete="off" spellcheck="false" maxlength="${L.NAME_MAX}"
              placeholder="What is it? For example, 3D printer" aria-label="Label name">
            <div class="home-den-labels-sugg" role="listbox"></div>
            <div class="home-den-labels-kindrow"><span>Kind</span><button type="button" class="home-den-labels-chip" data-act="kinds"></button></div>
            <div class="home-den-labels-kinds" hidden></div>
            <p class="home-den-labels-note">Enter adds it · Esc cancels</p>`;
        drawPop(); render();
        const input = ui.pop.querySelector('input'); input.focus(); requestAnimationFrame(() => input.focus());
    }
    function closePop() { S.pending = null; if (ui.pop) { ui.pop.hidden = true; ui.pop.innerHTML = ''; } }
    function drawPop() {
        if (!S.pending || !ui.pop) return;
        const items = L.suggestions(S.pending.name);
        S.pending.items = items;
        ui.pop.querySelector('.home-den-labels-sugg').innerHTML = items.map((it, i) => `<button type="button" role="option" data-act="suggest" data-i="${i}"
            aria-selected="${i === S.pending.hi}" class="${i === S.pending.hi ? 'is-on' : ''}"><span>${it.typed ? `Add “${esc(it.name)}”` : esc(it.name)}</span>
            <span class="home-den-labels-k">${esc(L.KIND_LABEL[it.kind])}</span></button>`).join('');
        const typed = S.pending.name.trim(), shown = S.pending.kind || (typed ? items[S.pending.hi]?.kind : null);
        ui.pop.querySelector('[data-act="kinds"]').textContent = S.pending.kind ? L.KIND_LABEL[shown] : shown ? `${L.KIND_LABEL[shown]} · guessed` : 'From the name';
        ui.pop.querySelector('.home-den-labels-kinds').innerHTML = L.KINDS.map(k => `<button type="button" class="home-den-labels-chip" data-act="kind"
            data-kind="${k}" aria-pressed="${k === shown}">${esc(L.KIND_LABEL[k])}</button>`).join('');
    }
    function onPopInput(ev) { if (!S.pending || ev.target.tagName !== 'INPUT') return; S.pending.name = ev.target.value; S.pending.hi = 0; drawPop(); }
    function onPopKey(ev) {
        if (!S.pending) return;
        const items = S.pending.items || [];
        if (ev.key === 'ArrowDown') { S.pending.hi = Math.min(items.length - 1, S.pending.hi + 1); drawPop(); }
        else if (ev.key === 'ArrowUp') { S.pending.hi = Math.max(0, S.pending.hi - 1); drawPop(); }
        else if (ev.key === 'Enter') choose(S.pending.hi);
        else if (ev.key === 'Escape') { closePop(); render(); }
        else return;
        ev.preventDefault(); ev.stopPropagation();
    }
    function choose(i) {
        const it = S.pending?.items?.[i];
        if (!it) return;
        const problem = L.nameProblem(it.name, []);
        if (problem) { toast(problem); return; }
        const { hit, x, y, kind } = S.pending;
        closePop();
        create(it.name, kind, hit, x, y);
    }

    function toast(text) { S.toast = text; S.toastAt = performance.now(); render(); }

    function render() {
        if (!S.root) return;
        const check = checkThese();
        S.layer?.set(S.labels, { selectedId: S.open ? S.sel : null, check, faint: !signedIn });
        if (!signedIn) return;
        S.root.classList.toggle('is-open', S.open);
        ui.toggle.hidden = S.open;
        ui.toggle.textContent = S.labels.length ? `Labels · ${S.labels.length}` : 'Labels';
        ui.panel.hidden = !S.open;
        ui.banner.hidden = !(S.open && check);
        if (!ui.banner.hidden) ui.banner.innerHTML = `<span><strong>Check these.</strong> Your labels were placed on an older scan of the den, so they're
            not used until you check them.</span><button type="button" class="home-den-labels-btn" data-act="fine">They're fine</button>`;
        drawHint();
        if (S.open) drawPanel();
        ui.modal.hidden = !S.conflict;
        if (S.conflict) ui.modal.innerHTML = `<div class="home-den-labels-card" role="dialog" aria-label="Labels changed elsewhere">
            <h3>Labels changed on another device</h3><p>The den's labels were saved from another device since you started. Keep yours, or load
            those and lose your ${S.dirty} unsaved change${S.dirty === 1 ? '' : 's'}.</p>
            <div class="home-den-labels-acts"><button type="button" class="home-den-labels-btn is-primary" data-act="keep-mine">Keep mine</button>
            <button type="button" class="home-den-labels-btn" data-act="load-theirs">Load those</button></div></div>`;
    }
    function drawHint() {
        const l = byId(S.sel);
        let text = '';
        if (S.open && S.toast && performance.now() - S.toastAt < 4000) text = esc(S.toast);
        else if (S.open && !S.pending) {
            if (l) text = l.on_wall ? 'Drag the corners to fit it on the wall · <kbd>Enter</kbd> when done'
                : 'Drag corners to fit · <kbd>↕</kbd> height · <kbd>⟳</kbd> or <kbd>R</kbd> turns · <kbd>Enter</kbd> when done';
            else if (S.armed) text = `Now click the <b>${esc(S.armed)}</b> in the scan · <kbd>Esc</kbd> to stop`;
            else text = 'Click anything in the scan to label it · drag to turn the view';
        }
        ui.hint.hidden = !text;
        if (ui.hint.innerHTML !== text) ui.hint.innerHTML = text;
    }
    function drawPanel() {
        const l = byId(S.sel), done = L.checklistDone(S.labels);
        const sorted = [...S.labels].sort((a, b) => a.name.localeCompare(b.name));
        const loadNote = S.load === 'loading' ? '<p class="home-den-labels-note">Reading your labels…</p>'
            : S.load === 'offline' ? `<p class="home-den-labels-note is-warn">Can't reach den-live (${esc(S.loadError)}). Labels can't be saved until it's back.
                <button type="button" class="home-den-labels-link" data-act="retry">Try again</button></p>` : '';
        const checklist = S.checklist ? `<section class="home-den-labels-check"><div class="home-den-labels-sub"><span>Label the usual den things</span>
                <span>${done.size} of ${L.CHECKLIST.length}</span></div>
            <ul>${L.CHECKLIST.map(item => `<li><button type="button" data-act="arm" data-item="${esc(item)}" class="${done.has(item) ? 'is-done' : ''}${S.armed === item ? ' is-armed' : ''}">
                <span class="home-den-labels-box" aria-hidden="true"></span><span>${esc(item[0].toUpperCase() + item.slice(1))}</span>
                ${S.armed === item ? `<span class="home-den-labels-btn is-small" data-act="skip" data-item="${esc(item)}" role="button" tabindex="0">Skip</span>`
                    : `<span class="home-den-labels-k">${S.skipped.has(item) && !done.has(item) ? "don't have" : esc(L.KIND_LABEL[L.guess(item).kind])}</span>`}</button></li>`).join('')}</ul>
            <p class="home-den-labels-note">Pick one, then click it in the scan. Skip anything you don't have.
                <button type="button" class="home-den-labels-link" data-act="checklist-done">Something else</button></p></section>` : '';
        const list = sorted.length ? `<ul class="home-den-labels-list">${sorted.map(o => `<li><button type="button" data-act="select" data-id="${esc(o.id)}"
                class="${o.id === S.sel ? 'is-selected' : ''}"><span class="home-den-labels-k">${esc(L.KIND_LABEL[o.kind])}</span><span class="home-den-labels-nm">${esc(o.name)}</span></button></li>`).join('')}</ul>`
            : S.checklist ? '' : '<p class="home-den-labels-note">Nothing labelled yet. Click the sofa, your desk or a painting in the scan, then type its name.</p>';
        const editor = l ? `<section class="home-den-labels-edit"><label><span>Name</span><input data-field="name" value="${esc(l.name)}" maxlength="${L.NAME_MAX}"></label>
            <label><span>Kind</span><select data-field="kind">${L.KINDS.map(k => `<option value="${k}"${k === l.kind ? ' selected' : ''}>${esc(L.KIND_LABEL[k])}</option>`).join('')}</select></label>
            <div><span>Size · width × depth × height (m)</span><div class="home-den-labels-trio">${l.box.size_m.map((v, i) => `<input type="number" step="0.05"
                min="${L.MIN_SIZE}" max="${L.MAX_SIZE}" data-field="size${i}" value="${v.toFixed(2)}" aria-label="${['Width', 'Depth', 'Height'][i]}">`).join('')}</div></div>
            <div class="home-den-labels-acts"><button type="button" class="home-den-labels-btn is-small" data-act="turn" data-by="90">Turn 90°</button>
                <button type="button" class="home-den-labels-btn is-small" data-act="turn" data-by="15">Turn 15°</button>
                <span class="home-den-labels-grow"></span><button type="button" class="home-den-labels-btn is-small is-danger" data-act="delete">Delete</button></div>
            <button type="button" class="home-den-labels-btn" data-act="done">Done</button></section>` : '';
        const saveText = S.save.text || (S.dirty ? `${S.dirty} unsaved change${S.dirty === 1 ? '' : 's'} · kept in this browser`
            : S.saved?.revision ? `Saved · revision ${S.saved.revision}` : '');
        // The editor's inputs keep their focus and typing across a redraw of everything else.
        const focused = document.activeElement?.dataset?.field && ui.panel.contains(document.activeElement) ? document.activeElement.dataset.field : null;
        ui.panel.innerHTML = `<header><h2>Labels</h2><span class="home-den-labels-count">${S.labels.length || ''}</span>
                ${!S.checklist ? '<button type="button" class="home-den-labels-link" data-act="checklist">Usual things</button>' : ''}
                <button type="button" class="home-den-labels-x" data-act="close" aria-label="Close Labels">×</button></header>
            <div class="home-den-labels-scroll">${loadNote}${checklist}${list}</div>${editor}
            <footer><span class="home-den-labels-state${S.save.state === 'error' ? ' is-warn' : ''}">${esc(saveText)}</span>
                <button type="button" class="home-den-labels-btn" data-act="undo"${S.undo.length ? '' : ' disabled'}>Undo</button>
                <button type="button" class="home-den-labels-btn is-primary" data-act="save"${S.dirty || checkThese() ? '' : ' disabled'}>${S.save.state === 'saving' ? 'Saving…' : 'Save'}</button></footer>`;
        if (focused) ui.panel.querySelector(`[data-field="${focused}"]`)?.focus();
    }

    /* ---------------- handles ---------------- */
    function handleSpecs(l) {
        const [w, d, h] = l.box.size_m, [cx, cy, cz] = l.box.centre_m, specs = [];
        if (l.on_wall) {
            const [fx, fy] = L.front(l.box.yaw_deg), a = l.box.yaw_deg * Math.PI / 180, ux = Math.cos(a), uy = Math.sin(a);
            const fcx = cx + fx * d / 2, fcy = cy + fy * d / 2;
            [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sz], i) => specs.push({ key: `c${i}`, cls: 'is-corner',
                p: [fcx + ux * sx * w / 2, fcy + uy * sx * w / 2, cz + sz * h / 2], data: { sx, sz } }));
            specs.push({ key: 'move', cls: 'is-move', p: [fcx, fcy, cz] });
            return specs;
        }
        const top = cz + h / 2;
        [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sy], i) => {
            const [x, y] = L.toWorld(l.box, sx * w / 2, sy * d / 2);
            specs.push({ key: `c${i}`, cls: 'is-corner', p: [x, y, top], data: { sx, sy } });
        });
        const [tx, ty] = L.toWorld(l.box, 0, d / 2 + 0.35);
        specs.push({ key: 'turn', cls: 'is-turn', text: '⟳', p: [tx, ty, top] });
        specs.push({ key: 'move', cls: 'is-move', p: [cx, cy, top] });
        specs.push({ key: 'lift', cls: 'is-lift', text: '↕', p: [cx, cy, top + 0.3] });
        return specs;
    }
    function handleEl(spec) {
        let el = handleEls.get(spec.key);
        if (!el) {
            el = document.createElement('div');
            el.dataset.key = spec.key;
            el.addEventListener('pointerdown', startDrag);
            ui.handles.appendChild(el); handleEls.set(spec.key, el);
        }
        el.className = `home-den-labels-handle ${spec.cls}`;
        if (el.textContent !== (spec.text || '')) el.textContent = spec.text || '';
        return el;
    }
    function startDrag(ev) {
        const l = byId(S.sel);
        if (!l || !S.engine) return;
        ev.preventDefault(); ev.stopPropagation();
        ev.currentTarget.setPointerCapture(ev.pointerId);
        const key = ev.currentTarget.dataset.key, spec = handleSpecs(l).find(s => s.key === key);
        if (!spec) return;
        S.undo.push(JSON.stringify(S.labels)); if (S.undo.length > 20) S.undo.shift();
        S.dirty += 1; S.save = { state: 'idle', text: '' };
        let normal;
        if (l.on_wall) { const [fx, fy] = L.front(l.box.yaw_deg); normal = [fx, fy, 0]; }
        else if (key === 'lift') {
            const dir = new THREE.Vector3(); S.engine.camera.getWorldDirection(dir);
            const local = S.engine.apartmentRoot.worldToLocal(S.engine.camera.position.clone().add(dir));
            const cam = S.engine.apartmentRoot.worldToLocal(S.engine.camera.position.clone());
            const hx = local.x - cam.x, hy = local.y - cam.y, hl = Math.hypot(hx, hy) || 1;
            normal = [hx / hl, hy / hl, 0];
        } else normal = [0, 0, 1];
        const start = rayPlane(apartmentRay(ev.clientX, ev.clientY), spec.p, normal);
        S.drag = { key, spec, normal, start, box: clone(l.box), id: l.id, el: ev.currentTarget };
        ev.currentTarget.addEventListener('pointermove', moveDrag);
        ev.currentTarget.addEventListener('pointerup', endDrag, { once: true });
        ev.currentTarget.addEventListener('pointercancel', endDrag, { once: true });
    }
    function moveDrag(ev) {
        const d = S.drag, l = d && byId(d.id);
        if (!l || !d.start) return;
        const hit = rayPlane(apartmentRay(ev.clientX, ev.clientY), d.spec.p, d.normal);
        if (!hit) return;
        const b0 = d.box, b = l.box, r3 = v => Math.round(v * 1000) / 1000;
        if (d.key === 'move') {
            b.centre_m = [b0.centre_m[0] + hit[0] - d.start[0], b0.centre_m[1] + hit[1] - d.start[1],
                l.on_wall ? Math.max(b0.size_m[2] / 2, b0.centre_m[2] + hit[2] - d.start[2]) : b0.centre_m[2]].map(r3);
        } else if (d.key === 'lift') {
            const bottom = b0.centre_m[2] - b0.size_m[2] / 2, top = Math.min(L.MAX_SIZE, Math.max(bottom + L.MIN_SIZE, hit[2]));
            b.size_m = [b0.size_m[0], b0.size_m[1], r3(top - bottom)]; b.centre_m = [b0.centre_m[0], b0.centre_m[1], r3((bottom + top) / 2)];
        } else if (d.key === 'turn') {
            let yaw = Math.atan2(hit[1] - b0.centre_m[1], hit[0] - b0.centre_m[0]) * 180 / Math.PI - 90;
            const snap = Math.round(yaw / 90) * 90; if (Math.abs(yaw - snap) < 4) yaw = snap;
            b.yaw_deg = L.normYaw(Math.round(yaw));
        } else if (l.on_wall) {
            const { sx, sz } = d.spec.data, a = b0.yaw_deg * Math.PI / 180, ux = Math.cos(a), uy = Math.sin(a);
            const lx = (hit[0] - b0.centre_m[0]) * ux + (hit[1] - b0.centre_m[1]) * uy, ox = -sx * b0.size_m[0] / 2, oz = b0.centre_m[2] - sz * b0.size_m[2] / 2;
            const w = Math.max(0.05, Math.abs(lx - ox)), h = Math.max(0.05, Math.abs(hit[2] - oz)), mx = (lx + ox) / 2;
            b.size_m = [r3(w), b0.size_m[1], r3(h)];
            b.centre_m = [r3(b0.centre_m[0] + ux * mx), r3(b0.centre_m[1] + uy * mx), r3(Math.max(h / 2, (hit[2] + oz) / 2))];
        } else {
            const { sx, sy } = d.spec.data, [lx, ly] = L.toLocal(b0, hit[0], hit[1]), ox = -sx * b0.size_m[0] / 2, oy = -sy * b0.size_m[1] / 2;
            const w = Math.max(0.05, Math.abs(lx - ox)), dep = Math.max(0.05, Math.abs(ly - oy)), [cx, cy] = L.toWorld(b0, (lx + ox) / 2, (ly + oy) / 2);
            b.size_m = [r3(w), r3(dep), b0.size_m[2]]; b.centre_m = [r3(cx), r3(cy), b0.centre_m[2]];
        }
        S.layer.set(S.labels, { selectedId: S.sel, check: false });
    }
    function endDrag(ev) {
        ev.currentTarget.removeEventListener('pointermove', moveDrag);
        const l = S.drag && byId(S.drag.id);
        if (l) l.updated_at = new Date().toISOString();
        S.drag = null; writeDraft(); render();
    }

    /* ---------------- per frame: tags, handles, the name box ---------------- */
    function toScreen(point) {
        const e = S.engine, v = new THREE.Vector3(point[0], point[1], point[2]);
        e.apartmentRoot.localToWorld(v); v.project(e.camera);
        const canvas = e.renderer.domElement.getBoundingClientRect(), root = S.root.getBoundingClientRect();
        return { x: canvas.left - root.left + (v.x + 1) / 2 * canvas.width, y: canvas.top - root.top + (1 - v.y) / 2 * canvas.height,
            behind: v.z > 1 || v.z < -1 };
    }
    function frame() {
        raf = requestAnimationFrame(frame);
        if (!S.engine || !S.root?.isConnected) return;
        const check = checkThese(), show = signedIn || !check;
        // Tags: the selected one first, then the nearest; one that would overlap another steps up, at most twice.
        const placed = [], live = new Set(), cam = S.engine.camera.position;
        const order = show ? S.labels.map(l => ({ l, dist: (() => { const v = new THREE.Vector3(...l.box.centre_m);
            S.engine.apartmentRoot.localToWorld(v); return v.distanceTo(cam); })() }))
            .sort((a, b) => (b.l.id === S.sel) - (a.l.id === S.sel) || a.dist - b.dist) : [];
        for (const { l } of order) {
            live.add(l.id);
            let el = tagEls.get(l.id);
            if (!el) {
                el = document.createElement(signedIn ? 'button' : 'div');
                if (signedIn) { el.type = 'button'; el.dataset.act = 'select'; el.dataset.id = l.id; }
                ui.tags.appendChild(el); tagEls.set(l.id, el);
            }
            const html = `<span class="home-den-labels-k">${esc(L.KIND_LABEL[l.kind])}</span>${esc(l.name)}`;
            if (el._html !== html) { el.innerHTML = html; el._html = html; el._w = 0; }
            el.className = `home-den-labels-tag${l.id === S.sel && S.open ? ' is-selected' : ''}${check ? ' is-check' : ''}${S.open ? '' : ' is-quiet'}`;
            const s = toScreen([l.box.centre_m[0], l.box.centre_m[1], l.box.centre_m[2] + l.box.size_m[2] / 2 + 0.04]);
            if (!el._w) { el.style.display = ''; el._w = el.offsetWidth; el._h = el.offsetHeight; }
            let y = s.y, shown = !s.behind;
            for (let step = 0; shown && step < 3; step++) {
                const r = { x0: s.x - el._w / 2, x1: s.x + el._w / 2, y0: y - el._h, y1: y };
                if (!placed.some(p => r.x0 < p.x1 && r.x1 > p.x0 && r.y0 < p.y1 && r.y1 > p.y0)) { placed.push(r); break; }
                if (step === 2) shown = false; else y -= el._h + 4;
            }
            el.style.transform = `translate(${Math.round(s.x - el._w / 2)}px, ${Math.round(y - el._h)}px)`;
            el.style.display = shown ? '' : 'none';
        }
        for (const [id, el] of tagEls) if (!live.has(id)) { el.remove(); tagEls.delete(id); }
        const l = S.open && signedIn ? byId(S.sel) : null, want = new Set();
        if (l) for (const spec of handleSpecs(l)) {
            want.add(spec.key);
            const el = handleEl(spec), s = toScreen(spec.p);
            el.style.transform = `translate(${Math.round(s.x)}px, ${Math.round(s.y)}px) translate(-50%, -50%)`;
            el.style.display = s.behind ? 'none' : '';
        }
        for (const [key, el] of handleEls) if (!want.has(key)) { el.remove(); handleEls.delete(key); }
        if (S.pending && ui.pop && !ui.pop.hidden) {
            // Beside the click, never over it, and clear of the panel on the right.
            const s = toScreen(S.pending.hit.point), root = S.root.getBoundingClientRect(), W = ui.pop.offsetWidth || 280;
            const room = root.width - (S.open && ui.panel ? ui.panel.offsetWidth + 24 : 0);
            const left = s.x + 28 + W < room - 8 ? s.x + 28 : s.x - 28 - W;
            ui.pop.style.transform = `translate(${Math.round(Math.min(Math.max(8, left), room - W - 8))}px, ${Math.round(Math.min(Math.max(10, s.y - 40), root.height - (ui.pop.offsetHeight || 300) - 10))}px)`;
        }
        if (S.toast && performance.now() - S.toastAt >= 4000) { S.toast = ''; drawHint(); }
    }

    /* ---------------- keys ---------------- */
    document.addEventListener('keydown', ev => {
        if (!S.open || !S.engine || !signedIn) return;
        const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') { ev.preventDefault(); save(); return; }
        if (typing) return;
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); undo(); return; }
        const l = byId(S.sel);
        if (ev.key === 'Escape') { if (S.pending) closePop(); else if (S.armed) S.armed = null; else if (l) S.sel = null; else S.open = false; render(); }
        else if (ev.key === 'Enter' && l) select(null);
        else if ((ev.key === 'Delete' || ev.key === 'Backspace') && l) commit(() => { S.labels = S.labels.filter(o => o.id !== l.id); S.sel = null; });
        else if ((ev.key === 'r' || ev.key === 'R') && l) commit(() => { l.box.yaw_deg = L.normYaw(l.box.yaw_deg + (ev.shiftKey ? 15 : 90)); });
        else if (l && ev.key.startsWith('Arrow')) {
            const step = ev.shiftKey ? 0.01 : 0.05, d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[ev.key];
            if (!d) return;
            commit(() => { l.box.centre_m = [l.box.centre_m[0] + d[0], l.box.centre_m[1] + d[1], l.box.centre_m[2]].map(v => Math.round(v * 1000) / 1000); });
        } else return;
        ev.preventDefault();
    }, true);

    setInterval(sync, TICK_MS);
    document.addEventListener('visibilitychange', sync);
    sync();

    /* Read-only, for Home's browser tests. */
    window.HomeDenLabelsView = Object.freeze({
        status: () => ({ mounted: !!S.engine, open: S.open, load: S.load, labels: S.labels.length, dirty: S.dirty,
            revision: S.saved?.revision ?? null, selected: S.sel, armed: S.armed, checklist: S.checklist, check: checkThese(),
            save: { ...S.save }, conflict: !!S.conflict, drawn: S.layer ? S.layer.count() : 0 }),
        labels: () => clone(S.labels),
    });
}
