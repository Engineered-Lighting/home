/* Read-side client for the central den lighting brain (activity-lighting-state-1).
 * It reads state and the SSE stream through the web gateway with Home's existing
 * session, validates every document, reconnects with backoff and reports
 * "brain offline" after a missed heartbeat window. It never applies anything
 * itself; the follower decides what reaches the virtual fixtures. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeLightingBrainClient = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'activity-lighting-state-1', OVERRIDE_SCHEMA = 'activity-lighting-override-1';
  const BASE = '/api/lighting-brain/v1/homes/victoria';
  const FIXTURE_IDS = Object.freeze(['victoria-den-proposed-fixture-01', 'victoria-den-proposed-fixture-02',
    'victoria-den-mount-03-virtual-fixture', 'victoria-den-mount-04-virtual-fixture']);
  const MODES = Object.freeze(['off', 'ambient', 'night_ambient', 'reading', 'degraded_ambient']);
  const OVERRIDE_REASONS = Object.freeze(['change', 'aim', 'undo', 'redo']);
  // The stream pushes every 5 s; three missed pushes means the brain is offline.
  const HEARTBEAT_MS = 15000, BACKOFF_MIN_MS = 1000, BACKOFF_MAX_MS = 30000, MAX_BYTES = 262144;
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const level = v => finite(v) && v >= 0 && v <= 1;
  const text = (v, max = 240) => typeof v === 'string' && v.length <= max;
  const nullable = (v, check) => v === null || check(v);
  const vector = v => Array.isArray(v) && v.length === 3 && v.every(n => finite(n) && Math.abs(n) <= 100);
  const cct = v => finite(v) && v >= 1000 && v <= 10000;
  const fade = v => finite(v) && v >= 0 && v <= 600;
  const copy = v => JSON.parse(JSON.stringify(v));

  function refuse(reason) { return { ok: false, reason, state: null }; }

  function validateFixture(row) {
    if (!object(row) || !FIXTURE_IDS.includes(row.fixture_id)) return 'fixture_id_unknown';
    const a = row.ambient, s = row.spot;
    if (!object(a) || !level(a.radial_level) || !level(a.down_level) || !cct(a.cct_k) || !fade(a.fade_s)) return 'fixture_ambient_invalid';
    if (!object(s) || typeof s.on !== 'boolean' || !level(s.level) || !cct(s.cct_k) || !fade(s.fade_s)
        || !nullable(s.target_m, vector)) return 'fixture_spot_invalid';
    if (!nullable(row.paused_until, finite) || !nullable(row.pause_reason, v => text(v, 120))) return 'fixture_pause_invalid';
    return null;
  }

  // Closed where Home acts on a value; open where the brain may add detail.
  function validateState(doc) {
    if (!object(doc)) return refuse('state_not_object');
    if (doc.schema !== SCHEMA) return refuse('state_schema_unsupported');
    if (doc.home !== 'victoria' || doc.room !== 'den') return refuse('state_scope_mismatch');
    if (!finite(doc.generated_at) || doc.generated_at <= 0) return refuse('state_generated_at_invalid');
    if (!text(doc.brain, 40) || !MODES.includes(doc.mode) || !text(doc.why)) return refuse('state_header_invalid');
    if (!Array.isArray(doc.fixtures) || doc.fixtures.length !== FIXTURE_IDS.length) return refuse('state_fixtures_incomplete');
    for (const row of doc.fixtures) { const reason = validateFixture(row); if (reason) return refuse(reason); }
    if (new Set(doc.fixtures.map(row => row.fixture_id)).size !== FIXTURE_IDS.length) return refuse('state_fixtures_duplicate');
    if (!Array.isArray(doc.decisions) || doc.decisions.length > 20 || !doc.decisions.every(row => object(row)
        && finite(row.at) && text(row.rule, 80) && text(row.why) && nullable(row.change ?? null, v => text(v, 240))))
      return refuse('state_decisions_invalid');
    if (doc.occupancy != null && !object(doc.occupancy) || doc.activity != null && !object(doc.activity)
        || doc.cost != null && !object(doc.cost) || doc.inputs != null && !object(doc.inputs)) return refuse('state_detail_invalid');
    return { ok: true, reason: null, state: copy(doc) };
  }

  function parse(raw) {
    if (typeof raw !== 'string' || raw.length > MAX_BYTES) return refuse('state_too_large');
    try { return validateState(JSON.parse(raw)); } catch (_) { return refuse('state_json_invalid'); }
  }

  function createBrainClient({ base = BASE, fetch: fetchFn, EventSource: Source, setTimeout: later, clearTimeout: cancel,
    nowMs = () => Date.now(), heartbeatMs = HEARTBEAT_MS, onChange = () => {} } = {}) {
    if (typeof fetchFn !== 'function' || typeof later !== 'function' || typeof cancel !== 'function')
      throw TypeError('lighting_brain_client_dependencies_required');
    const root = String(base).replace(/\/+$/, '');
    let status = 'stopped', reason = null, state = null, receivedAtMs = null, refusedReason = null, refusedCount = 0;
    let source = null, watchdog = null, retry = null, backoff = BACKOFF_MIN_MS, epoch = 0, started = false;
    const readback = () => ({ status, reason, online: status === 'online', state: state && copy(state),
      received_at_ms: receivedAtMs, refused_reason: refusedReason, refused_count: refusedCount, physical_commands: false });
    const notify = () => { try { onChange(readback()); } catch (_) { /* a view failure never stops the stream */ } };
    const clearTimer = id => { if (id !== null) cancel(id); return null; };
    const closeSource = () => { if (source) { try { source.close(); } catch (_) { /* already closed */ } } source = null; };

    function goOffline(why) {
      if (!started) return;
      // The last state is kept for display only; the follower applies nothing new.
      status = 'offline'; reason = why; notify();
    }
    function arm() {
      watchdog = clearTimer(watchdog);
      const token = epoch;
      watchdog = later(() => {
        if (token !== epoch) return;
        watchdog = null; goOffline('heartbeat_missed'); closeSource(); schedule();
      }, heartbeatMs);
    }
    function accept(result) {
      if (!result.ok) {
        refusedReason = result.reason; refusedCount++;
        notify(); return false;
      }
      // Out-of-order GET/stream documents never move the brain backwards.
      if (state && result.state.generated_at < state.generated_at) return false;
      state = result.state; receivedAtMs = nowMs(); refusedReason = null;
      backoff = BACKOFF_MIN_MS;
      status = state.brain === 'online' ? 'online' : 'offline';
      reason = status === 'online' ? null : `brain_${state.brain}`;
      arm(); notify(); return true;
    }
    function schedule() {
      if (!started || retry !== null) return;
      const token = epoch, delay = backoff;
      backoff = Math.min(BACKOFF_MAX_MS, backoff * 2);
      retry = later(() => { retry = null; if (token === epoch) connect(); }, delay);
    }
    async function readState(token) {
      try {
        const response = await fetchFn(`${root}/state`, { cache: 'no-store', credentials: 'same-origin', redirect: 'error',
          headers: { Accept: 'application/json' } });
        if (token !== epoch) return;
        if (!response.ok) { goOffline(`state_http_${response.status}`); return; }
        accept(parse(await response.text()));
      } catch (_) { if (token === epoch) goOffline('state_unreachable'); }
    }
    function connect() {
      if (!started) return;
      closeSource();
      const token = epoch;
      if (status !== 'online') { status = state ? status : 'connecting'; notify(); }
      arm();
      readState(token);
      if (typeof Source !== 'function') return;
      try { source = new Source(`${root}/stream`, { withCredentials: true }); }
      catch (_) { source = null; goOffline('stream_unavailable'); schedule(); return; }
      const message = event => { if (token === epoch) accept(parse(event?.data)); };
      source.onmessage = message;
      source.addEventListener?.('state', message);
      source.onerror = () => {
        if (token !== epoch) return;
        closeSource(); schedule();
      };
    }
    async function send(method, path, body) {
      const response = await fetchFn(`${root}${path}`, { method, cache: 'no-store', credentials: 'same-origin', redirect: 'error',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Home-Lighting-Brain-CSRF': '1' },
        ...(body ? { body: JSON.stringify(body) } : {}) });
      if (!response.ok) throw Error(`override_http_${response.status}`);
      return { ok: true };
    }
    return {
      readback,
      start() { if (started) return readback(); started = true; epoch++; backoff = BACKOFF_MIN_MS; status = 'connecting'; reason = null; connect(); return readback(); },
      stop() {
        started = false; epoch++; closeSource();
        watchdog = clearTimer(watchdog); retry = clearTimer(retry);
        status = 'stopped'; reason = null; notify(); return readback();
      },
      postOverride(fixtureId, why, atS = nowMs() / 1000) {
        if (!FIXTURE_IDS.includes(fixtureId) || !OVERRIDE_REASONS.includes(why)) return Promise.reject(Error('override_invalid'));
        return send('POST', '/overrides', { schema: OVERRIDE_SCHEMA, fixture_id: fixtureId, reason: why, at: atS });
      },
      clearOverride(fixtureId) {
        if (!FIXTURE_IDS.includes(fixtureId)) return Promise.reject(Error('override_invalid'));
        return send('DELETE', `/overrides/${encodeURIComponent(fixtureId)}`);
      },
    };
  }

  // AL-5's always-on monitor (den-ops-banner-1). Only a warn or alert banner
  // is shown (a silent monitor reports as warn); anything unreadable shows nothing.
  const OPS_URL = '/api/den-ops/v1/status', OPS_POLL_MS = 30000;
  function opsBanner(doc) {
    const b = object(doc) && doc.schema === 'den-ops-banner-1' ? doc.banner : null;
    if (!object(b) || !['warn', 'alert'].includes(b.severity) || !text(b.text, 240) || !b.text
        || !(b.host == null || text(b.host, 64))) return null;
    return { severity: b.severity, host: b.host || null, text: b.text,
      level: Number.isInteger(doc.level) ? doc.level : null, step: text(doc.step, 40) ? doc.step : null };
  }
  function createOpsStatusPoller({ url = OPS_URL, fetch: fetchFn, setTimeout: later, clearTimeout: cancel,
    isHidden = () => false, intervalMs = OPS_POLL_MS, onChange = () => {} } = {}) {
    if (typeof fetchFn !== 'function' || typeof later !== 'function' || typeof cancel !== 'function')
      throw TypeError('ops_status_poller_dependencies_required');
    let timer = null, epoch = 0, running = false, banner = null;
    const publish = next => {
      if (JSON.stringify(next) === JSON.stringify(banner)) return;
      banner = next; try { onChange(banner); } catch (_) { /* view failure never stops polling */ }
    };
    const schedule = () => { if (running) timer = later(tick, intervalMs); };
    async function tick() {
      timer = null;
      if (!running) return;
      // A hidden tab does not poll; wake() resumes at once when it is shown.
      if (isHidden()) { schedule(); return; }
      const token = epoch;
      let next = null;
      try {
        const response = await fetchFn(url, { cache: 'no-store', credentials: 'same-origin', redirect: 'error',
          headers: { Accept: 'application/json' } });
        if (response.ok) { const raw = await response.text(); next = raw.length <= MAX_BYTES ? opsBanner(JSON.parse(raw)) : null; }
      } catch (_) { next = null; }
      if (token !== epoch || !running) return;
      publish(next); schedule();
    }
    return {
      readback: () => banner,
      start() { if (!running) { running = true; epoch++; tick(); } },
      wake() { if (running && !isHidden()) { if (timer !== null) cancel(timer); epoch++; tick(); } },
      stop() { running = false; epoch++; if (timer !== null) cancel(timer); timer = null; publish(null); },
    };
  }

  return Object.freeze({ SCHEMA, OVERRIDE_SCHEMA, BASE, FIXTURE_IDS, MODES, OVERRIDE_REASONS, HEARTBEAT_MS,
    OPS_URL, OPS_POLL_MS, validateState, parse, createBrainClient, opsBanner, createOpsStatusPoller });
});
