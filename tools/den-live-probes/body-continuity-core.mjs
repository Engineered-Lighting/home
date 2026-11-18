/* The Den body continuity probe's logic: the in-page collector and the offline attribution and join.
 *
 * Household media rule: a record holds timestamps, statuses, counts and 32-bit hashes of ids only. The collector
 * reads no point, position, image or camera field, and the attribution sees only what the collector kept.
 *
 * createBodyContinuityCollector and installBodyContinuityProbe are serialized into the page (body-continuity-probe.mjs),
 * so they must stay self-contained: no imports and no references to this module's other bindings. */

export const PROBE_SCHEMA = 'den-body-continuity-probe-2';
export const TAP_SCHEMA = 'den-body-continuity-tap-2';
export const JOIN_SCHEMA = 'den-body-continuity-join-2';
export const DOMINANT_SHARE = 0.6;
export const ATTRIBUTED_SHARE = 0.95;

export function createBodyContinuityCollector(options = {}) {
    const PEOPLE_FRESH_MS = options.peopleFreshMs ?? 1500;
    // perceive.py's EPOCH_GAP_S: it keeps its tracks this long without a fresh frame.
    const STALL_MS = options.stallMs ?? 3000;
    const MAX_PACKETS = options.maxPackets ?? 20000, MAX_PEOPLE = options.maxPeople ?? 40000;
    const MAX_SEGMENTS = options.maxSegments ?? 60000, MAX_ROWS = options.maxRows ?? 400;
    const REASONS = ['stale', 'scope_changed', 'unavailable', 'insufficient_depth', 'ambiguous', 'empty',
        'pose_unavailable', 'warming_background', 'head_mask_unavailable'];
    // Rejects that call clearBody in home-den-live-view.mjs, so they change what is drawn.
    const CLEARING_REJECTS = ['stale_on_arrival', 'pre_capture', 'scope_mismatch'];
    // The client continuity branch's status().body.rejected keys, as this probe's classes.
    const REJECT_ALIASES = { past_lease: 'stale_on_arrival', before_capture: 'pre_capture', scope: 'scope_mismatch', pre_clock: 'no_clock' };
    const finite = Number.isFinite;
    const h32 = value => {
        if (value === null || value === undefined) return null;
        const text = String(value);
        let h = 0x811c9dc5;
        for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
        return h.toString(16).padStart(8, '0');
    };
    const word = value => typeof value === 'string' && /^[a-z_]{1,32}$/.test(value) ? value : value == null ? null : 'other';
    const reasonOf = value => REASONS.includes(value) ? value : value == null ? null : 'other';
    const bump = (map, key, n = 1) => { map[key] = (map[key] ?? 0) + n; };
    const push = (list, row, cap, overflow) => { if (list.length < cap) list.push(row); else overflow.n++; };

    const conns = [];
    let inWindow = false, windowStart = null, windowEnd = null;
    let packets, people, revisions, segments, falsePersist, acceptToDraw, latencyKeys, latencyRows, wrapperMs;
    let counts, overflow, frameHist;
    let occupancy = { single_confirmed_ms: 0, tracked_ms: 0 };
    let latestPeople = null, lastFreshTracked = null, lastRevision = new Map();
    let lastEffective = null, priorEffective = null;
    let shown = null, clearedAt = null, pendingDraw = [];
    let drawnSinceTick = false, lastVersion = null, lastTick = null, visible = false, mounted = false;
    let counterDetect = { accepted: false, rejected_by_reason: false, reopens: false, accepted_not_drawn: false };
    let lastCounters = null;

    function reset() {
        packets = []; people = []; revisions = []; segments = []; falsePersist = []; acceptToDraw = []; wrapperMs = [];
        latencyKeys = new Set(); latencyRows = new Map();
        overflow = { n: 0 };
        frameHist = {};
        counts = { ticks: 0, rendered_ticks: 0, unknown_render_ticks: 0, draws: 0, presentation_unknown_draws: 0,
            false_persistence: { after_clear: 0, after_producer_expiry: 0 }, drawn_after_local_deadline: 0,
            never_drawn: { superseded: 0, cleared: 0, expired: 0 }, counter_source: {}, accepted_ready: 0,
            rejected_ready: {}, processed_clears: {}, silent_clears: 0 };
        occupancy = { single_confirmed_ms: 0, tracked_ms: 0 };
    }
    reset();

    const lastConn = route => { for (let i = conns.length - 1; i >= 0; i--) if (conns[i].route === route) return conns[i]; return null; };
    const bodyConn = () => lastConn('body');
    const upper = (conn, t) => {
        const e = (t - conn.started) / 1000;
        return conn.clock.serverTs + e + 0.005 + e * 0.0001;
    };
    // Local times at which the page's upper bound (the app's own expiry) and the lower bound (certainly past the
    // producer's expiry) reach a producer time.
    const deadlineUpper = (conn, ts) => conn.started + (ts - conn.clock.serverTs - 0.005) / 1.0001 * 1000;
    const deadlineLower = (conn, ts) => (ts - conn.clock.serverTs + 0.005 + conn.clock.at / 1000 - conn.started * 1e-7)
        / (1e-3 - 1e-7);
    const tracking = t => {
        const p = latestPeople;
        if (p && p.tracked && t - p.t <= PEOPLE_FRESH_MS) return { tracked: true, eligible: p.eligible, stall: false };
        // A perception stall: no fresh frame since someone was tracked, shown by a non-fresh people event (stale,
        // degraded, starting) or by silence on an open people stream. perceive.py keeps the tracks for STALL_MS, so the
        // person counts as still there that long; a fresh event with no tracks ends it at once.
        const f = lastFreshTracked, stream = lastConn('people');
        if (f && t - f.t < STALL_MS && (p !== f || !stream || stream.state === 'open')) return { tracked: true, eligible: f.eligible, stall: true };
        return { tracked: false, eligible: false, stall: false };
    };

    function connect(route, started) {
        const conn = { id: conns.length, route, started, state: 'connecting', clock: null, opens: [], errors: [], closes: [] };
        conns.push(conn);
        return conn;
    }
    function sse(conn, kind, t, readyState) {
        if (!conn) return;
        if (kind === 'open') { conn.state = 'open'; if (conn.opens.length < MAX_ROWS) conn.opens.push(t); }
        else if (kind === 'error') { conn.state = readyState === 2 ? 'closed' : 'error'; if (conn.errors.length < MAX_ROWS) conn.errors.push(t); }
        else if (kind === 'close') { conn.state = 'closed'; if (conn.closes.length < MAX_ROWS) conn.closes.push(t); }
    }
    function bodyClock(conn, serverTs, t) {
        if (conn && !conn.clock && finite(serverTs)) conn.clock = { serverTs, at: t };
    }
    function bodyPacket(conn, event, t) {
        const status = event?.status === 'ready' ? 'ready' : event?.status === 'clear' ? 'clear' : 'other';
        const rec = { t, conn: conn?.id ?? null, run_h: h32(event?.run_id), seq: Number.isSafeInteger(event?.seq) ? event.seq : null,
            status, reason: status === 'clear' ? reasonOf(event?.reason) : null, track_h: h32(event?.track_id),
            scope_h: h32([event?.run_id, event?.track_epoch, event?.content_revision, event?.transform_id, event?.mode].join('|')),
            upper_age_ms: null, remains_ms: null, deadline_upper: null, deadline_lower: null,
            accepted: null, processed: null, reject: null, source: null };
        if (status === 'ready' && conn?.clock && finite(event.capture_ts) && finite(event.expires_ts)) {
            const up = upper(conn, t);
            rec.upper_age_ms = (up - event.capture_ts) * 1000;
            rec.remains_ms = (event.expires_ts - up) * 1000;
            rec.pre_capture = up < event.capture_ts;
            rec.deadline_upper = deadlineUpper(conn, event.expires_ts);
            rec.deadline_lower = deadlineLower(conn, event.expires_ts);
        }
        rec.connection_age_ms = conn ? t - conn.started : null;
        if (inWindow) push(packets, rec, MAX_PACKETS, overflow);
        return rec;
    }
    function detect(after) {
        if (!after) return;
        if (typeof after.accepted === 'number') counterDetect.accepted = true;
        if (after.rejected && typeof after.rejected === 'object') counterDetect.rejected_by_reason = true;
        if (finite(after.reopens)) counterDetect.reopens = true;
        if (typeof after.accepted_not_drawn === 'number') counterDetect.accepted_not_drawn = true;
        lastCounters = { frames: after.frames ?? null, accepted: after.accepted ?? null, reopens: after.reopens ?? null,
            accepted_not_drawn: after.accepted_not_drawn ?? null,
            rejected_by_reason: after.rejected && typeof after.rejected === 'object' ? { ...after.rejected } : null };
    }
    /* After the app's own body listener ran: whether it accepted the packet, read from HomeDenLiveView.status().body.
     * The newer counters (accepted, rejected by key) are used when the page has them; otherwise frames, which the
     * app bumps only for an accepted ready, and a reject reason inferred from this probe's identical clock. */
    function bodyHandled(rec, before, after) {
        detect(after);
        if (rec.status === 'ready') {
            if (before && after && typeof before.accepted === 'number' && typeof after.accepted === 'number') {
                rec.source = 'status_counters';
                rec.accepted = after.accepted > before.accepted;
                if (!rec.accepted && before.rejected && after.rejected) {
                    const key = Object.keys(after.rejected).find(k => (after.rejected[k] ?? 0) > (before.rejected[k] ?? 0));
                    if (key) rec.reject = REJECT_ALIASES[key] ?? word(key);
                }
                // No counter moved: the app ignored the packet without counting it (a superseded connection).
                if (!rec.accepted && !rec.reject) rec.reject = 'uncounted';
            } else if (before && after && finite(before.frames) && finite(after.frames)) {
                rec.source = 'frames_fallback';
                rec.accepted = after.frames > before.frames;
            } else rec.source = 'unknown';
            if (rec.accepted === false && !rec.reject) {
                const conn = conns[rec.conn];
                rec.reject = !conn?.clock ? 'no_clock'
                    : rec.pre_capture === true ? 'pre_capture'
                    : finite(rec.remains_ms) && rec.remains_ms <= 0 ? 'stale_on_arrival'
                    : after?.reason === 'scope_changed' && before?.reason !== 'scope_changed' ? 'scope_mismatch'
                    : 'silent_drop';
            }
            if (inWindow) {
                bump(counts.counter_source, rec.source);
                if (rec.accepted) counts.accepted_ready++;
                else if (rec.accepted === false) bump(counts.rejected_ready, rec.reject);
            }
            if (rec.accepted) {
                if (inWindow) counts.never_drawn.superseded += pendingDraw.length;
                pendingDraw = [rec]; shown = rec; clearedAt = null; lastEffective = rec;
            } else if (rec.accepted === false && CLEARING_REJECTS.includes(rec.reject)) {
                if (inWindow) counts.never_drawn.cleared += pendingDraw.length;
                pendingDraw = []; clearedAt = rec.t; lastEffective = rec;
            }
        } else if (rec.status === 'clear') {
            rec.processed = after ? after.reason === rec.reason || (rec.reason === 'other' && after.reason != null) : null;
            if (rec.processed !== false) {
                if (inWindow) { bump(counts.processed_clears, rec.reason); counts.never_drawn.cleared += pendingDraw.length; }
                pendingDraw = []; clearedAt = rec.t; lastEffective = rec;
            } else if (inWindow) counts.silent_clears++;
        }
    }
    function peopleEvent(event, t) {
        const tracks = Array.isArray(event?.tracks) ? event.tracks : [];
        let confirmed = 0, coasting = 0, ambiguous = 0, noObs = 0, obsKnown = true;
        const hashes = [];
        for (const track of tracks) {
            if (track?.state === 'confirmed') confirmed++; else if (track?.state === 'coasting') coasting++;
            if (track?.association_status === 'ambiguous') ambiguous++;
            if (!track || !('current_obs_ids' in track)) obsKnown = false;
            else if (!Array.isArray(track.current_obs_ids) || track.current_obs_ids.length === 0) noObs++;
            hashes.push(h32(track?.track_id));
        }
        const fresh = event?.perception_status === 'fresh';
        const next = new Map();
        tracks.forEach((track, i) => {
            const rev = track?.association_revision ?? null;
            if (lastRevision.has(hashes[i]) && lastRevision.get(hashes[i]) !== rev && inWindow)
                push(revisions, { t, track_h: hashes[i] }, MAX_PEOPLE, overflow);
            next.set(hashes[i], rev);
        });
        lastRevision = next;
        if (latestPeople && inWindow) {
            const dt = Math.max(0, Math.min(t - Math.max(latestPeople.t, windowStart), PEOPLE_FRESH_MS));
            if (latestPeople.eligible) occupancy.single_confirmed_ms += dt;
            if (latestPeople.tracked) occupancy.tracked_ms += dt;
        }
        latestPeople = { t, tracked: fresh && tracks.length > 0, eligible: fresh && tracks.length === 1 && tracks[0]?.state === 'confirmed',
            status: word(event?.perception_status), presence: word(event?.presence), n: tracks.length };
        if (fresh) lastFreshTracked = tracks.length ? latestPeople : null;
        if (inWindow) push(people, { t, n: tracks.length, confirmed, coasting, ambiguous, no_obs: obsKnown ? noObs : null,
            fresh, presence: word(event?.presence), qwen: word(event?.health?.qwen?.state), tracks: hashes }, MAX_PEOPLE, overflow);
    }
    function draw(t, version, presentation) {
        if (presentation === false) return;
        if (presentation == null && inWindow) counts.presentation_unknown_draws++;
        drawnSinceTick = true;
        if (!inWindow) {
            if (version !== lastVersion) { lastVersion = version; if (pendingDraw.length) pendingDraw[pendingDraw.length - 1].drawn_at = t; pendingDraw = []; }
            return;
        }
        counts.draws++;
        if (version !== lastVersion) {
            lastVersion = version;
            if (pendingDraw.length) {
                const applied = pendingDraw[pendingDraw.length - 1];
                counts.never_drawn.superseded += pendingDraw.length - 1;
                applied.drawn_at = t;
                push(acceptToDraw, t - applied.t, 20000, overflow);
                pendingDraw = [];
            }
        }
        if (clearedAt !== null && t > clearedAt) {
            counts.false_persistence.after_clear++;
            push(falsePersist, { t, kind: 'after_clear', over_ms: t - clearedAt }, MAX_ROWS, overflow);
        } else if (shown && finite(shown.deadline_lower) && t > shown.deadline_lower) {
            counts.false_persistence.after_producer_expiry++;
            push(falsePersist, { t, kind: 'after_producer_expiry', over_ms: t - shown.deadline_lower }, MAX_ROWS, overflow);
        }
        if (clearedAt === null && shown && finite(shown.deadline_upper) && t > shown.deadline_upper) counts.drawn_after_local_deadline++;
    }
    function frame(t, rendered, isMounted) {
        mounted = isMounted !== false;
        if (pendingDraw.length && finite(pendingDraw[pendingDraw.length - 1].deadline_upper)
            && t > pendingDraw[pendingDraw.length - 1].deadline_upper) {
            if (inWindow) counts.never_drawn.expired += pendingDraw.length;
            pendingDraw = [];
        }
        if (rendered !== false) { visible = drawnSinceTick; drawnSinceTick = false; }
        if (!inWindow) { lastTick = t; return; }
        counts.ticks++;
        if (rendered === true) counts.rendered_ticks++; else if (rendered == null) counts.unknown_render_ticks++;
        if (lastTick !== null && t > lastTick) bump(frameHist, Math.min(1000, Math.round(t - lastTick)));
        lastTick = t;
        const { tracked, eligible, stall } = tracking(t);
        const last = segments[segments.length - 1];
        if (last && last.visible === visible && last.tracked === tracked && last.eligible === eligible && last.stall === stall) {
            last.last = t; last.frames++; return;
        }
        const body = bodyConn();
        push(segments, { start: t, last: t, visible, tracked, eligible, stall, frames: 1, body_stream: body ? body.state : 'none', mounted },
            MAX_SEGMENTS, overflow);
    }
    /* The probe's own synchronous time per body packet, outside the app's listener. */
    function cost(ms) { if (inWindow && finite(ms)) push(wrapperMs, Math.round(ms * 1000) / 1000, 20000, overflow); }
    /* Optional GPU-completion witness: rows of window.HomeDenLatency.snapshot().entries (kind body), deduplicated. */
    function latency(entries) {
        if (!inWindow || !Array.isArray(entries)) return;
        for (const row of entries) {
            if (row?.kind !== 'body' || typeof row.phase !== 'string') continue;
            const key = `${h32(row.run_id)}:${row.seq}`, full = `${key}:${row.phase}:${row.reason ?? ''}`;
            if (latencyKeys.has(full) || latencyKeys.size > 50000) continue;
            latencyKeys.add(full);
            if (!latencyRows.has(key) && latencyRows.size < 20000) latencyRows.set(key, {});
            const slot = latencyRows.get(key);
            if (!slot) continue;
            const at = row.phase === 'submitted' && finite(row.frame_submitted_at_ms) ? row.frame_submitted_at_ms
                : row.phase === 'gpu_completed' && finite(row.gpu_completed_at_ms) ? row.gpu_completed_at_ms : row.at_ms;
            if (row.phase === 'unavailable') slot.unavailable = word(row.reason) ?? 'none';
            else if (finite(at)) slot[word(row.phase)] = at;
        }
    }
    function beginWindow(t) {
        reset();
        inWindow = true; windowStart = t; windowEnd = null; lastTick = t;
        priorEffective = lastEffective;   // by reference, so a draw after the window opens still sets its drawn_at
        const { tracked, eligible, stall } = tracking(t), body = bodyConn();
        segments.push({ start: t, last: t, visible, tracked, eligible, stall, frames: 0, body_stream: body ? body.state : 'none', mounted });
    }
    function occupancyAt(t) {
        const out = { ...occupancy };
        if (latestPeople && inWindow) {
            const dt = Math.max(0, Math.min(t - Math.max(latestPeople.t, windowStart), PEOPLE_FRESH_MS));
            if (latestPeople.eligible) out.single_confirmed_ms += dt;
            if (latestPeople.tracked) out.tracked_ms += dt;
        }
        return out;
    }
    function state(t) {
        const body = bodyConn();
        return { tracking: tracking(t), latest_people_age_ms: latestPeople ? t - latestPeople.t : null,
            people: latestPeople ? { status: latestPeople.status, presence: latestPeople.presence, tracks: latestPeople.n } : null,
            body_stream: body ? body.state : 'none', body_connection_age_ms: body ? t - body.started : null,
            body_clock: !!body?.clock, mounted, in_window: inWindow };
    }
    function exportWindow(t) {
        const occupied = occupancyAt(t);
        inWindow = false; windowEnd = t;
        const latencyList = [...latencyRows.values()];
        return { window: { start: windowStart, end: windowEnd }, prior_effective: priorEffective,
            connections: conns.map(c => ({ id: c.id, route: c.route, started: c.started, state: c.state,
                clock_setup_ms: c.clock ? c.clock.at - c.started : null, opens: c.opens, errors: c.errors, closes: c.closes })),
            packets, people, revisions, segments, false_persistence: falsePersist, accept_to_draw_ms: acceptToDraw,
            frame_interval_hist: frameHist, counts, occupancy: occupied, overflow: overflow.n,
            counters_detected: { ...counterDetect }, counters_last: lastCounters, wrapper_ms: wrapperMs,
            latency: latencyList.length ? latencyList : null };
    }
    return { connect, sse, bodyClock, bodyPacket, bodyHandled, people: peopleEvent, draw, frame, latency, cost,
        beginWindow, occupancy: occupancyAt, state, export: exportWindow, hash: h32 };
}

/* The page side, serialized into the page with the collector (so self-contained too). It observes and never acts:
 * - an EventSource subclass that times opens, errors and closes, reads body_clock and people events, and wraps the
 *   app's own body listener to read HomeDenLiveView.status().body before and after each packet;
 * - an onAfterRender witness on the body's 'body-recording' points (the WebGL draw), restored on stop();
 * - a rAF loop for frame boundaries, using the renderer's frame counter to tell a rendered frame;
 * - guards: getUserMedia and getDisplayMedia refuse, and pointer, touch, wheel and key input never reaches the app. */
export function installBodyContinuityProbe(win, createCollector, opts = {}) {
    const c = createCollector(opts.collector || {});
    const now = () => win.performance.now();
    const blocked = { input_events: 0, user_media: 0 };
    const probe = { collector: c, blocked, hooked: null, stopped: false };
    win.__denBodyProbe = probe;
    try { for (const [key, value] of Object.entries(opts.storage || {})) win.localStorage.setItem(key, value); } catch (_) { /* optional */ }
    const media = win.navigator?.mediaDevices;
    if (media) for (const name of ['getUserMedia', 'getDisplayMedia']) if (typeof media[name] === 'function') {
        try {
            media[name] = () => { blocked.user_media++; return Promise.reject(new (win.DOMException || Error)('read-only probe', 'NotAllowedError')); };
        } catch (_) { /* read-only property */ }
    }
    if (opts.blockInput !== false && typeof win.addEventListener === 'function') {
        for (const type of ['click', 'dblclick', 'auxclick', 'contextmenu', 'pointerdown', 'pointerup', 'mousedown', 'mouseup',
            'touchstart', 'touchend', 'wheel', 'keydown', 'keyup']) {
            win.addEventListener(type, event => { blocked.input_events++; event.stopImmediatePropagation(); event.preventDefault(); },
                { capture: true, passive: false });
        }
    }
    const routeOf = url => {
        try {
            const href = win.location?.href ?? 'http://localhost/', path = new URL(url, href).pathname, config = win.HomeDenLive || {};
            if (typeof config.body === 'string' && new URL(config.body, href).pathname === path) return 'body';
            if (typeof config.stream === 'string' && new URL(config.stream, href).pathname === path) return 'people';
            if (/\/api\/den-live\/v1\/homes\/[^/]+\/body(?:-v2)?$/.test(path)) return 'body';
            if (/\/api\/den-live\/v1\/homes\/[^/]+\/stream$/.test(path)) return 'people';
        } catch (_) { /* not ours */ }
        return null;
    };
    const statusBody = () => { try { return win.HomeDenLiveView?.status?.().body ?? null; } catch (_) { return null; } };
    const total = value => typeof value === 'number' ? value : value && typeof value === 'object'
        ? Object.values(value).reduce((sum, n) => sum + (Number.isFinite(n) ? n : 0), 0) : undefined;
    // Either client branch's names: rejected_by_reason or rejected (by key), reopens as a number or by reason, and
    // accepted_not_drawn or unapplied.
    const pick = body => {
        if (!body) return null;
        const rejected = [body.rejected_by_reason, body.rejected].find(value => value && typeof value === 'object');
        return { frames: body.frames, accepted: body.accepted, reopens: total(body.reopens),
            accepted_not_drawn: body.accepted_not_drawn ?? body.unapplied, rejected: rejected ? { ...rejected } : undefined,
            reason: body.reason ?? null, stream: body.stream };
    };
    const Native = win.EventSource;
    if (typeof Native === 'function') {
        const nativeAdd = Native.prototype.addEventListener;
        class ProbeEventSource extends Native {
            constructor(url, init) {
                const started = now();
                super(url, init);
                const route = routeOf(url), conn = route ? c.connect(route, started) : null;
                Object.defineProperty(this, '__probeConn', { value: conn });
                if (!conn) return;
                nativeAdd.call(this, 'open', () => c.sse(conn, 'open', now()));
                nativeAdd.call(this, 'error', () => c.sse(conn, 'error', now(), this.readyState));
                if (route === 'body') nativeAdd.call(this, 'body_clock', message => {
                    try { c.bodyClock(conn, JSON.parse(message.data).server_ts, now()); } catch (_) { /* malformed */ }
                });
                else nativeAdd.call(this, 'people', message => {
                    try { c.people(JSON.parse(message.data), now()); } catch (_) { /* malformed */ }
                });
            }
            addEventListener(type, listener, options) {
                const conn = this.__probeConn;
                if (conn?.route !== 'body' || type !== 'body' || typeof listener !== 'function') return super.addEventListener(type, listener, options);
                const wrapped = function (message) {
                    let rec = null, before = null;
                    const t0 = now();
                    try { rec = c.bodyPacket(conn, JSON.parse(message.data), t0); before = pick(statusBody()); } catch (_) { rec = null; }
                    const t1 = now();
                    try { return listener.call(this, message); }
                    finally {
                        const t2 = now();
                        if (rec) try { c.bodyHandled(rec, before, pick(statusBody())); } catch (_) { /* observe only */ }
                        c.cost(t1 - t0 + now() - t2);
                    }
                };
                return super.addEventListener(type, wrapped, options);
            }
            close() {
                if (this.__probeConn) c.sse(this.__probeConn, 'close', now());
                return super.close();
            }
        }
        win.EventSource = ProbeEventSource;
    }
    function unhook() {
        const h = probe.hooked;
        if (h && h.point.onAfterRender === h.fn) h.point.onAfterRender = h.original;
        probe.hooked = null;
    }
    function hook(engine) {
        let point = null;
        try { point = engine?.apartmentRoot?.getObjectByName?.('home-apartment-body-owned')?.getObjectByName?.('body-recording') ?? null; }
        catch (_) { point = null; }
        if (point === (probe.hooked?.point ?? null)) return;
        unhook();
        if (!point) return;
        const original = point.onAfterRender;
        const fn = function (...args) {
            try { return original?.apply(this, args); }
            finally {
                try {
                    const opacity = point.material?.uniforms?.uOpacity?.value ?? 0;
                    if (point.visible && (point.geometry?.drawRange?.count ?? 0) > 0 && opacity > 0) {
                        c.draw(now(), point.geometry?.attributes?.position?.version ?? null,
                            typeof engine.isPresentationFrame === 'function' ? engine.isPresentationFrame() === true : null);
                    }
                } catch (_) { /* observe only */ }
            }
        };
        point.onAfterRender = fn;
        probe.hooked = { point, original, fn };
    }
    let lastCounter = null, lastLookup = -Infinity;
    const tick = () => {
        if (probe.stopped) return;
        win.requestAnimationFrame(tick);
        const engine = win.__APT_ENGINE, t = now();
        if (t - lastLookup >= 250 || (probe.hooked && !probe.hooked.point.parent)) { lastLookup = t; hook(engine); }
        const counter = engine?.renderer?.info?.render?.frame;
        const rendered = Number.isFinite(counter) ? counter !== lastCounter : null;
        lastCounter = counter;
        c.frame(t, rendered, !!probe.hooked);
    };
    if (typeof win.requestAnimationFrame === 'function') win.requestAnimationFrame(tick);
    const poll = typeof win.setInterval === 'function' ? win.setInterval(() => {
        try { const snapshot = win.HomeDenLatency?.snapshot?.(); if (snapshot) c.latency(snapshot.entries); } catch (_) { /* optional */ }
    }, opts.latencyPollMs ?? 1000) : null;
    probe.stop = () => {
        probe.stopped = true;
        if (poll !== null) win.clearInterval(poll);
        unhook();
        if (Native) win.EventSource = Native;
    };
    return probe;
}

/* ---------- Offline attribution (node only) ---------- */

const TRUTHFUL = new Set(['producer_empty', 'producer_unavailable', 'producer_warming_background', 'producer_head_mask_unavailable']);
const HOLD = new Set(['producer_pose_unavailable', 'producer_insufficient_depth']);
const CLEARING = new Set(['stale_on_arrival', 'pre_capture', 'scope_mismatch']);
// Not flicker: truthful producer hides, the wait for a first figure after someone appears, and time before any body
// packet on this connection.
const EXCLUDED = new Set(['truthful_hide', 'entry_wait', 'no_body_yet']);
// Hidden time no evidence explains; G12-C passes when the rest is at least ATTRIBUTED_SHARE of hidden-while-tracked time.
const UNEXPLAINED = new Set(['unattributed', 'producer_other', 'no_body_yet']);
const CONNECTION = new Set(['sse_reconnect', 'client_reopen', 'body_stream_not_open', 'not_mounted']);
// What a perception stall explains when it follows in the same hidden interval: perceive.py's idle clears ('stale'
// with the camera up, 'unavailable' with it down) and the lease of the last frame before frames stopped.
const STALL_ABSORBS = new Set(['lease_elapsed', 'producer_unavailable', 'no_body_yet']);
export const FIXES = Object.freeze({
    perception_stall: 'G12-D: missing camera frames and processed-frame cadence (perception stalls); the host tap names the people status',
    producer_scope_changed: 'G12-A (S1): same-track rebind without a scope clear; Spatial reviews the revision semantics',
    producer_ambiguous: 'G12-B: ambiguity gating in body_rgb, after at least 5 host-tapped episodes show extra-track state',
    lease_elapsed: 'G12-D: ready cadence and lease margin; the host join splits frame rate from body skips',
    producer_hold_reason: 'G12-D: hold-reason runs (pose_unavailable, insufficient_depth); BodyLink.counts diagnostic',
    arrived_past_lease: 'G12-E (V3): client lease clock re-anchor and reopen',
    client_reopen: 'G12-E (V3): destructive client reopen (clock refresh) without make-before-break',
    sse_reconnect: 'G12-F: body SSE reconnect cost',
    body_stream_not_open: 'G12-F: body SSE not open',
    accepted_not_drawn: 'G12-G (V3): accepted-but-not-drawn in the client',
    client_reject: 'G12-G (V3): silent client rejects',
    not_mounted: 'G12-G: body layer not mounted (presentation dispose or remount)',
    no_body_yet: 'none: no body packet before this interval; lengthen the window',
    producer_other: 'unknown producer clear reason: inspect before choosing a fix',
    unattributed: 'none: unattributed hidden time; extend the probe before choosing a fix',
});
export const familyOf = cause => cause.startsWith('client_reject') ? 'client_reject'
    : cause.startsWith('lease_elapsed') ? 'lease_elapsed'
    : TRUTHFUL.has(cause) ? 'truthful_hide' : HOLD.has(cause) ? 'producer_hold_reason' : cause;

export function percentiles(values) {
    const a = values.filter(Number.isFinite).sort((x, y) => x - y);
    if (!a.length) return null;
    const q = p => Math.round(a[Math.max(0, Math.ceil(a.length * p) - 1)] * 10) / 10;
    return { n: a.length, p50: q(0.5), p95: q(0.95), max: Math.round(a[a.length - 1] * 10) / 10 };
}
function histogramPercentiles(hist) {
    const rows = Object.entries(hist || {}).map(([ms, n]) => [Number(ms), n]).sort((a, b) => a[0] - b[0]);
    const total = rows.reduce((sum, row) => sum + row[1], 0);
    if (!total) return null;
    const at = p => {
        const goal = Math.max(1, Math.ceil(total * p));
        let seen = 0;
        for (const [ms, n] of rows) { seen += n; if (seen >= goal) return ms; }
        return rows.at(-1)[0];
    };
    const sum = rows.reduce((acc, [ms, n]) => acc + ms * n, 0);
    return { n: total, p50: at(0.5), p95: at(0.95), max: rows.at(-1)[0], mean_fps: sum ? Math.round(1000 / (sum / total) * 10) / 10 : null,
        long_frames_over_50ms: rows.filter(([ms]) => ms > 50).reduce((acc, row) => acc + row[1], 0) };
}
const lastAtOrBefore = (rows, t, key = 't') => {
    let lo = 0, hi = rows.length - 1, found = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (rows[mid][key] <= t) { found = mid; lo = mid + 1; } else hi = mid - 1; }
    return found;
};
export const isEffective = p => p.status === 'clear' ? p.processed !== false
    : p.status === 'ready' && (p.accepted === true || (p.accepted === false && CLEARING.has(p.reject)));

/* The cause of one hidden-while-tracked interval [s, e), from the packet and connection timeline. Its first hidden
 * frame is at s, so whatever hid the figure arrived after the previous drawn frame (prevLast) and at or before s. */
export function causeOf(interval, timeline, { frameToleranceMs = 20 } = {}) {
    const { s, e, prevLast } = interval;
    const near = t => t > prevLast - frameToleranceMs && t <= e;
    const body = (timeline.connections || []).filter(c => c.route === 'body');
    if (body.some(c => c.errors.some(near))) return 'sse_reconnect';
    if (body.some(c => c.closes.some(near))) return 'client_reopen';
    if (interval.body_stream && interval.body_stream !== 'open') {
        const last = body.at(-1);
        return interval.body_stream === 'connecting' && !(last && last.errors.length) ? 'body_stream_not_open' : 'sse_reconnect';
    }
    if (interval.mounted === false) return 'not_mounted';
    const packets = timeline.packets || [];
    let p = null;
    for (let i = lastAtOrBefore(packets, s); i >= 0; i--) if (isEffective(packets[i])) { p = packets[i]; break; }
    if (!p && timeline.prior_effective && timeline.prior_effective.t <= s) p = timeline.prior_effective;
    // Someone appeared while nothing was drawn: the wait for a first figure, unless a newer packet hid it.
    if (interval.entry && (!p || p.t <= interval.entryAt)) return 'entry_wait';
    if (!p) return 'no_body_yet';
    if (p.status === 'clear') return p.reason === 'stale' ? 'lease_elapsed' : p.reason ? `producer_${p.reason}` : 'producer_other';
    if (p.accepted === false) return p.reject === 'stale_on_arrival' ? 'arrived_past_lease' : `client_reject_${p.reject}`;
    if (Number.isFinite(p.deadline_upper) && s >= p.deadline_upper - frameToleranceMs) {
        // The shown frame's lease ran out. A newer ready the client dropped silently would have covered it.
        const dropped = packets.find(q => q.t > p.t && q.t <= e && q.status === 'ready' && q.accepted === false
            && !CLEARING.has(q.reject) && Number.isFinite(q.deadline_upper) && q.deadline_upper > s);
        return dropped ? `client_reject_${dropped.reject}` : 'lease_elapsed';
    }
    // An accepted frame not yet drawn is the client's; one that was drawn and then hidden for no seen reason is not.
    return Number.isFinite(p.drawn_at) && p.drawn_at <= s ? 'unattributed' : 'accepted_not_drawn';
}

/* One hidden interval, split where a perception stall began inside it: the part before keeps its own cause unless
 * the stall explains it too, and the stall with the hidden tail after it is perception_stall. A connection cause
 * holds for the whole interval, since a dropped people stream looks like a stall. */
function stallParts(iv, timeline, options) {
    const whole = causeOf(iv, timeline, options);
    if (iv.stallAt == null || CONNECTION.has(whole)) return [[iv, whole]];
    const pre = iv.stallAt > iv.s ? causeOf({ ...iv, e: iv.stallAt }, timeline, options) : null;
    if (pre === null || STALL_ABSORBS.has(pre)) return [[iv, 'perception_stall']];
    return [[{ ...iv, e: iv.stallAt, frames: iv.preStallFrames }, pre],
        [{ ...iv, s: iv.stallAt, prevLast: iv.stallAt, frames: iv.frames - iv.preStallFrames, open_at_start: false, entry: false }, 'perception_stall']];
}

function peopleContext(people, s, e, freshMs) {
    const rows = [];
    for (let i = Math.max(0, lastAtOrBefore(people, s - freshMs) + 1); i < people.length && people[i].t <= e; i++) rows.push(people[i]);
    if (!rows.length) return null;
    return { events: rows.length, max_tracks: Math.max(...rows.map(r => r.n)),
        coasting_events: rows.filter(r => r.coasting > 0).length, ambiguous_events: rows.filter(r => r.ambiguous > 0).length,
        no_obs_events: rows.filter(r => r.no_obs > 0).length, distinct_tracks: new Set(rows.flatMap(r => r.tracks)).size,
        qwen_asking_events: rows.filter(r => r.qwen === 'asking').length };
}

export function decide(blankByFamily, { share = DOMINANT_SHARE } = {}) {
    const ranked = Object.entries(blankByFamily).filter(([family]) => !EXCLUDED.has(family))
        .map(([family, ms]) => ({ family, ms: Math.round(ms) })).filter(row => row.ms > 0).sort((a, b) => b.ms - a.ms);
    const total = ranked.reduce((sum, row) => sum + row.ms, 0);
    for (const row of ranked) row.share = Math.round(row.ms / total * 1000) / 1000;
    const rule = `the cause family with at least ${share} of hidden-while-tracked ms picks the fix (truthful hides, entry waits and time before the first body packet excluded)`;
    if (!total) return { rule, verdict: 'no_attributable_blank', ranked };
    const top = ranked[0];
    return top.ms / total >= share
        ? { rule, verdict: 'dominant', family: top.family, share: top.share, fix: FIXES[top.family] ?? FIXES.unattributed, ranked }
        : { rule, verdict: 'no_dominant_cause', ranked, note: 'no family reaches the share: fix in ranked order or lengthen the window' };
}

/* A window that did not meet its gate picks no fix: the ranking stays, as a description only. */
export function withheld(decision, why) {
    return { rule: decision.rule, verdict: 'not_decisive', ranked: decision.ranked, note: `${why}: the ranking describes this window and picks no fix` };
}

const round1 = v => Math.round(v * 10) / 10;
const sumInto = (map, key, v) => { map[key] = (map[key] ?? 0) + v; };
const rounded = map => Object.fromEntries(Object.entries(map).map(([k, v]) => [k, Math.round(v)]));

/* Hidden-while-tracked intervals with cause, the drawn fractions, exit latency, false persistence and acceptance. */
export function attributeWindow(timeline, { peopleFreshMs = 1500, frameToleranceMs = 20, timeOrigin = null } = {}) {
    const t0 = timeline.window.start, t1 = timeline.window.end;
    const segs = timeline.segments.map((seg, i, all) => ({ ...seg, end: i + 1 < all.length ? all[i + 1].start : t1 }));
    const rel = t => round1(t - t0);
    const wall = t => timeOrigin == null ? null : Math.round(timeOrigin + t);
    const raw = [];
    let tracked = 0, eligible = 0, stalled = 0, visibleTracked = 0, visibleEligible = 0;
    for (let i = 0; i < segs.length; i++) {
        const seg = segs[i], ms = Math.max(0, seg.end - seg.start);
        if (seg.tracked) { tracked += ms; if (seg.visible) visibleTracked += ms; if (seg.stall) stalled += ms; }
        if (seg.eligible) { eligible += ms; if (seg.visible) visibleEligible += ms; }
        if (!(seg.tracked && !seg.visible)) continue;
        const prev = raw.at(-1);
        if (prev && prev.lastSeg === i - 1) {
            if (seg.stall && prev.stallAt == null) { prev.stallAt = seg.start; prev.preStallFrames = prev.frames; }
            prev.e = seg.end; prev.lastSeg = i; prev.frames += seg.frames; prev.eligible &&= seg.eligible;
            continue;
        }
        const before = segs[i - 1];
        // Untracked only follows a fresh event with no tracks or a stall past perceive.py's epoch gap, so this is entry.
        raw.push({ s: seg.start, e: seg.end, prevLast: before ? before.last : seg.start, lastSeg: i, frames: seg.frames,
            eligible: seg.eligible, body_stream: seg.body_stream, mounted: seg.mounted, open_at_start: i === 0,
            entry: !!before && !before.tracked && !before.visible, entryAt: seg.start,
            stallAt: seg.stall ? seg.start : null, preStallFrames: 0 });
    }
    const packets = timeline.packets || [], people = timeline.people || [];
    const intervals = raw.flatMap(whole => stallParts(whole, timeline, { frameToleranceMs })).map(([iv, cause]) => {
        const inside = packets.filter(p => p.t > iv.prevLast - frameToleranceMs && p.t <= iv.e);
        const rejects = {};
        for (const p of inside) if (p.status === 'ready' && p.accepted === false) sumInto(rejects, p.reject, 1);
        return { start_ms: rel(iv.s), end_ms: rel(iv.e), duration_ms: round1(iv.e - iv.s),
            start_wall_ms: wall(iv.s), end_wall_ms: wall(iv.e), cause, family: familyOf(cause), eligible: iv.eligible,
            hidden_frames: iv.frames, open_at_start: iv.open_at_start, open_at_end: iv.e >= t1,
            clears: inside.filter(p => p.status === 'clear').map(p => ({ at_ms: rel(p.t), reason: p.reason, processed: p.processed })),
            readies: inside.filter(p => p.status === 'ready').length, rejects, people: peopleContext(people, iv.s, iv.e, peopleFreshMs) };
    });
    const byCause = {}, byFamily = {}, eligibleByFamily = {}, durations = {};
    for (const iv of intervals) {
        sumInto(byCause, iv.cause, iv.duration_ms);
        sumInto(byFamily, iv.family, iv.duration_ms);
        if (iv.eligible) sumInto(eligibleByFamily, iv.family, iv.duration_ms);
        (durations[iv.cause] ||= []).push(iv.duration_ms);
    }
    const minutes = Math.max(1e-9, (t1 - t0) / 60000);
    const causes = Object.fromEntries(Object.entries(durations).map(([cause, list]) => [cause, {
        count: list.length, per_minute: Math.round(list.length / minutes * 100) / 100, blank_ms: Math.round(byCause[cause]),
        duration_ms: percentiles(list) }]));
    const excludedEligible = [...EXCLUDED].reduce((sum, family) => sum + (eligibleByFamily[family] ?? 0), 0);
    // Hide latency: from a processed clear, or the shown frame's own local deadline, to the first hidden frame.
    const segAt = t => { const i = lastAtOrBefore(segs, t, 'start'); return i >= 0 && t < segs[i].end ? i : -1; };
    const hiddenAfter = t => { for (let i = Math.max(0, segAt(t)); i < segs.length; i++) if (segs[i].start > t && !segs[i].visible) return segs[i].start; return null; };
    const visibleAt = t => { const i = segAt(t); return i >= 0 && segs[i].visible; };
    const hideAfterClear = {}, hideAfterLease = [];
    const effectiveRows = packets.filter(isEffective);
    effectiveRows.forEach((p, i) => {
        if (p.status !== 'ready' || p.accepted === false) {
            if (!visibleAt(p.t)) return;
            const h = hiddenAfter(p.t);
            if (h !== null) (hideAfterClear[p.status === 'clear' ? p.reason : p.reject] ||= []).push(h - p.t);
            return;
        }
        const next = effectiveRows[i + 1];
        if (!Number.isFinite(p.deadline_upper) || (next && next.t <= p.deadline_upper) || p.deadline_upper >= t1) return;
        if (!visibleAt(p.deadline_upper - frameToleranceMs)) return;
        const h = hiddenAfter(p.deadline_upper - frameToleranceMs);
        if (h !== null) hideAfterLease.push(h - p.deadline_upper);
    });
    const scopeClears = packets.filter(p => p.status === 'clear' && p.reason === 'scope_changed' && p.processed !== false);
    const blinked = scopeClears.filter(p => intervals.some(iv => iv.cause === 'producer_scope_changed'
        && iv.start_ms >= rel(p.t) - frameToleranceMs && iv.start_ms <= rel(p.t) + 100)).length;
    const counts = timeline.counts, readies = packets.filter(p => p.status === 'ready');
    const fraction = (a, b) => b > 0 ? Math.round(a / b * 1000) / 1000 : null;
    const hiddenMs = intervals.reduce((sum, iv) => sum + iv.duration_ms, 0);
    const unexplainedMs = intervals.filter(iv => UNEXPLAINED.has(iv.family)).reduce((sum, iv) => sum + iv.duration_ms, 0);
    const attributed = hiddenMs > 0 ? fraction(hiddenMs - unexplainedMs, hiddenMs) : null;
    return {
        window_ms: Math.round(t1 - t0),
        occupancy: { single_confirmed_ms: Math.round(timeline.occupancy.single_confirmed_ms), tracked_ms: Math.round(timeline.occupancy.tracked_ms),
            frames_tracked_ms: Math.round(tracked), frames_single_confirmed_ms: Math.round(eligible), frames_stall_ms: Math.round(stalled) },
        drawn_fraction: { while_tracked: fraction(visibleTracked, tracked), while_single_confirmed: fraction(visibleEligible, eligible),
            while_single_confirmed_excluding_truthful_hides: fraction(visibleEligible, eligible - excludedEligible) },
        blank_ms_by_cause: rounded(byCause), blank_ms_by_family: rounded(byFamily),
        blank_ms_by_family_single_confirmed: rounded(eligibleByFamily),
        causes, hidden_intervals_under_250ms: intervals.filter(iv => iv.duration_ms < 250).length,
        unexplained_intervals: intervals.filter(iv => UNEXPLAINED.has(iv.family)).length,
        attribution_coverage: { hidden_while_tracked_ms: Math.round(hiddenMs), unexplained_ms: Math.round(unexplainedMs),
            unexplained_families: [...UNEXPLAINED], attributed_fraction: attributed, criterion: ATTRIBUTED_SHARE,
            meets_criterion: attributed === null ? null : attributed >= ATTRIBUTED_SHARE },
        decision: decide(byFamily),
        intervals: intervals.slice(0, 2000), intervals_truncated: Math.max(0, intervals.length - 2000),
        exit: { hide_after_clear_ms: Object.fromEntries(Object.entries(hideAfterClear).map(([k, v]) => [k, percentiles(v)])),
            hide_after_local_deadline_ms: percentiles(hideAfterLease) },
        false_persistence: { frames_drawn_after_clear: counts.false_persistence.after_clear,
            frames_drawn_after_producer_expiry: counts.false_persistence.after_producer_expiry,
            frames_drawn_after_local_deadline: counts.drawn_after_local_deadline,
            first: (timeline.false_persistence || []).slice(0, 20).map(row => ({ at_ms: rel(row.t), kind: row.kind, over_ms: round1(row.over_ms) })) },
        acceptance: { counter_source: counts.counter_source, counters_detected: timeline.counters_detected,
            counters_last: timeline.counters_last, ready_received: readies.length, accepted: counts.accepted_ready,
            rejected_by_reason: counts.rejected_ready, processed_clears: counts.processed_clears, silent_clears: counts.silent_clears,
            accepted_never_drawn: counts.never_drawn, accept_to_draw_ms: percentiles(timeline.accept_to_draw_ms || []),
            arrived_past_lease: readies.filter(p => Number.isFinite(p.remains_ms) && p.remains_ms <= 0).length,
            ready_lease_remaining_ms: percentiles(readies.map(p => p.remains_ms)),
            ready_upper_age_ms: percentiles(readies.map(p => p.upper_age_ms)),
            ready_gap_ms: percentiles(readies.slice(1).map((p, i) => p.t - readies[i].t)) },
        scope_changed: { clears: scopeClears.length, with_hidden_frame: blinked, without_hidden_frame: scopeClears.length - blinked,
            same_track_revision_changes: (timeline.revisions || []).length },
        frames: { ticks: counts.ticks, rendered_ticks: counts.rendered_ticks, unknown_render_ticks: counts.unknown_render_ticks,
            body_draws: counts.draws, presentation_unknown_draws: counts.presentation_unknown_draws,
            raf_interval_ms: histogramPercentiles(timeline.frame_interval_hist) },
        gpu_completion_witness: latencySummary(timeline.latency),
        probe_cost: { body_wrapper_ms: percentiles(timeline.wrapper_ms || []),
            note: 'the probe\'s own synchronous time per body packet (a second JSON parse and two status() reads), outside the app listener' },
        overflow_rows: timeline.overflow,
    };
}

function latencySummary(rows) {
    if (!rows) return { available: false };
    const unavailable = {};
    for (const row of rows) if (row.unavailable) sumInto(unavailable, row.unavailable, 1);
    const span = (a, b) => percentiles(rows.filter(r => Number.isFinite(r[a]) && Number.isFinite(r[b])).map(r => r[b] - r[a]));
    return { available: true, body_packets_traced: rows.length, gpu_completed: rows.filter(r => Number.isFinite(r.gpu_completed)).length,
        received_to_applied_ms: span('received', 'applied'), applied_to_submitted_ms: span('applied', 'submitted'),
        submitted_to_gpu_completed_ms: span('submitted', 'gpu_completed'), received_to_gpu_completed_ms: span('received', 'gpu_completed'),
        unavailable_reasons: unavailable };
}

/* Compact packet rows for the join: wall time, run hash, seq, status, reason or reject, accepted. */
export function packetRows(timeline, timeOrigin) {
    return (timeline.packets || []).map(p => [Math.round(timeOrigin + p.t), p.run_h, p.seq, p.status,
        p.status === 'clear' ? p.reason : p.reject, p.accepted === true ? 1 : p.accepted === false ? 0 : null]);
}

/* ---------- Join of a browser receipt with a host tap ---------- */

const median = values => { const a = values.filter(Number.isFinite).sort((x, y) => x - y); return a.length ? a[Math.floor((a.length - 1) / 2)] : null; };

/* Joins by wall time. The browser-minus-host offset is the median over body packets matched on run and seq (it holds
 * transport delay and any clock offset); without matches it is 0 and the clocks are assumed synchronized. Host
 * evidence counts for an interval when it lies within toleranceMs of it after that offset. The decision is withheld
 * (not_decisive) unless the browser receipt met its occupancy gate and the host tap overlaps its window. */
export function joinReceipts(browser, tap, { toleranceMs = 250 } = {}) {
    const notes = [];
    const hostPackets = (tap.timeline?.body || []).map(([wall, run_h, seq, status, reason, track_h]) => ({ wall, run_h, seq, status, reason, track_h }));
    const browserPackets = (browser.timeline?.packets || []).map(([wall, run_h, seq, status, reason, accepted]) => ({ wall, run_h, seq, status, reason, accepted }));
    const hostByKey = new Map(hostPackets.map(p => [`${p.run_h}:${p.seq}`, p]));
    const deltas = [], matched = new Set();
    for (const p of browserPackets) {
        const h = hostByKey.get(`${p.run_h}:${p.seq}`);
        if (h) { deltas.push(p.wall - h.wall); matched.add(`${p.run_h}:${p.seq}`); }
    }
    let offset = median(deltas);
    if (offset === null) { offset = 0; notes.push('no body packet matched on run and seq: host and workstation wall clocks are assumed synchronized'); }
    const toBrowser = w => w + offset;
    const gaps = (tap.coverage?.gaps || []).map(g => ({ ...g, s: toBrowser(g.start_wall_ms), e: toBrowser(g.end_wall_ms) }));
    const episodes = (tap.ambiguous?.episodes || []).map(ep => ({ ...ep, s: toBrowser(ep.start_wall_ms), e: toBrowser(ep.end_wall_ms) }));
    const overlaps = (list, s, e) => list.filter(x => x.s <= e + toleranceMs && x.e >= s - toleranceMs);
    const browserStart = browser.window?.start_wall_ms, browserEnd = browser.window?.end_wall_ms;
    const hostStart = tap.window?.start_wall_ms, hostEnd = tap.window?.end_wall_ms;
    const known = [browserStart, browserEnd, hostStart, hostEnd].every(Number.isFinite);
    const overlapMs = known ? Math.max(0, Math.min(browserEnd, toBrowser(hostEnd)) - Math.max(browserStart, toBrowser(hostStart))) : 0;
    if (!known) notes.push('a window has no wall times, so the host tap cannot be placed against the browser window');
    else if (overlapMs <= 0) notes.push('the host tap window does not overlap the browser window');
    // Only a browser window that met its occupancy gate, with host evidence over it, may pick a fix.
    const decisive = browser.decisive === true && overlapMs > 0;
    const intervals = (browser.attribution?.intervals || []).map(iv => {
        const s = iv.start_wall_ms, e = iv.end_wall_ms, row = { ...iv, subcause: iv.cause, host: null };
        if (!Number.isFinite(s) || !Number.isFinite(e)) return row;
        if (Number.isFinite(hostStart) && Number.isFinite(hostEnd) && (e < toBrowser(hostStart) - toleranceMs || s > toBrowser(hostEnd) + toleranceMs)) {
            row.subcause = `${iv.cause}:outside_host_window`;
            return row;
        }
        const hostGaps = overlaps(gaps, s, e), hostEpisodes = overlaps(episodes, s, e);
        const hostClears = hostPackets.filter(p => p.status === 'clear' && toBrowser(p.wall) >= s - toleranceMs && toBrowser(p.wall) <= e + toleranceMs);
        const undelivered = hostPackets.filter(p => p.status === 'ready' && toBrowser(p.wall) >= s - toleranceMs && toBrowser(p.wall) <= e
            && !matched.has(`${p.run_h}:${p.seq}`));
        row.host = { gaps: hostGaps.map(g => ({ cause: g.cause, subcause: g.subcause, duration_ms: g.duration_ms })),
            clears: hostClears.map(p => p.reason), undelivered_readies: undelivered.length,
            ambiguous: hostEpisodes.map(ep => ({ extra_track: ep.extra_track, duration_ms: ep.duration_ms })) };
        if (iv.family === 'lease_elapsed') {
            const gap = hostGaps.find(g => g.cause === 'lease_elapsed');
            row.subcause = gap ? `lease_elapsed:${gap.subcause}` : undelivered.length ? 'lease_elapsed:not_delivered'
                : 'lease_elapsed:host_still_covered';
        } else if (iv.cause === 'producer_ambiguous' && hostEpisodes.length) {
            row.subcause = `producer_ambiguous:${hostEpisodes[0].extra_track?.class ?? 'unknown'}`;
        } else if (iv.family === 'perception_stall') {
            const gap = hostGaps.find(g => g.cause === 'perception_stall');
            if (gap) row.subcause = `perception_stall:${gap.subcause ?? 'unknown'}`;
        }
        return row;
    });
    const bySub = {}, byFamily = {};
    for (const iv of intervals) { sumInto(bySub, iv.subcause, iv.duration_ms); sumInto(byFamily, iv.family, iv.duration_ms); }
    const lease = Object.entries(bySub).filter(([k]) => k.startsWith('lease_elapsed:'));
    const leaseTotal = lease.reduce((sum, [, ms]) => sum + ms, 0);
    const leaseShares = leaseTotal ? Object.fromEntries(lease.map(([k, ms]) => [k.slice('lease_elapsed:'.length), Math.round(ms / leaseTotal * 1000) / 1000])) : null;
    const leaseTop = leaseShares ? Object.entries(leaseShares).sort((a, b) => b[1] - a[1])[0] : null;
    let decision = decide(byFamily);
    if (!decisive) {
        const why = [`browser status ${browser.status ?? 'unknown'}${browser.decisive === true ? '' : ', receipt not decisive'}`];
        if (overlapMs <= 0) why.push('no host tap over the browser window');
        decision = withheld(decision, `not decisive (${why.join('; ')})`);
    }
    return {
        schema: JOIN_SCHEMA,
        decisive, browser_status: browser.status ?? null,
        host_overlap: { ms: Math.round(overlapMs), fraction_of_browser_window: known && browserEnd > browserStart
            ? Math.round(overlapMs / (browserEnd - browserStart) * 1000) / 1000 : null },
        tolerance_ms: toleranceMs,
        clock: { matched_packets: deltas.length, browser_packets: browserPackets.length, host_packets: hostPackets.length,
            browser_minus_host_ms: Math.round(offset), residual_ms: deltas.length ? percentiles(deltas.map(d => Math.abs(d - offset))) : null,
            basis: deltas.length ? 'median of browser minus host receive wall time over packets matched on run and seq' : 'none' },
        blank_ms_by_cause: rounded(bySub), blank_ms_by_family: rounded(byFamily),
        decision,
        lease_elapsed_split: leaseShares ? { shares: leaseShares, target: leaseTop[1] > 0.5 ? leaseTop[0] : 'no_majority',
            rule: 'within lease_elapsed, the host sub-cause above 0.5 of its blank ms sets the G12-D target' } : null,
        host: { frame_vs_ready: tap.join?.frame_vs_ready ?? null, ambiguous_rule: tap.ambiguous?.rule ?? null, qwen: tap.join?.qwen ?? null },
        intervals, notes,
    };
}
