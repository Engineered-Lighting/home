/* Home follows the lighting brain on the four virtual den fixtures.
 * The brain decides; this follower maps each fixture state onto the existing
 * overlay seams (setVirtualFixtureState, aimVirtualFixtureAtTarget), fades in
 * small virtual steps, and never fights a manual change: a hand change pauses
 * that fixture here at once, posts an override, and the fixture stays paused
 * until the brain clears it or the owner chooses Return to Auto. Virtual only. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeLightingBrainFollower = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const STEP_MS = 100, RADIALS = [1, 2, 3, 4, 5, 6];
  // Mirrors the brain's 30 min manual pause when an override never reaches it.
  const LOCAL_PAUSE_MS = 30 * 60 * 1000;
  const LOG_LIMIT = 20, REPEAT_MS = 2000;
  const copy = v => JSON.parse(JSON.stringify(v));
  const clamp = (v, [min, max]) => Math.max(min, Math.min(max, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  // Desired overlay channels for one brain fixture row.
  function desiredChannels(row, ranges) {
    const a = row.ambient, s = row.spot;
    const channel = (value, kelvin, range) => ({ on: value > 0, brightness: value, color_temp_kelvin: Math.round(clamp(kelvin, range)) });
    return {
      radial: channel(a.radial_level, a.cct_k, ranges.radials), downward: channel(a.down_level, a.cct_k, ranges.downward),
      spotlight: channel(s.on ? s.level : 0, s.cct_k, ranges.spotlight),
      ambient_fade_s: a.fade_s, spot_fade_s: s.fade_s, target_m: s.on && s.target_m ? s.target_m.slice() : null,
    };
  }
  const signature = d => JSON.stringify([d.radial, d.downward, d.spotlight, d.target_m]);

  function createFollower({ client, getOverlay, setTimeout: later, clearTimeout: cancel, nowMs = () => Date.now(),
    onChange = () => {}, onApplied = () => {} } = {}) {
    if (!client?.start || typeof getOverlay !== 'function' || typeof later !== 'function' || typeof cancel !== 'function')
      throw TypeError('lighting_brain_follower_dependencies_required');
    // fixture_id -> { applied, generation, tween, local: {reason, at_ms, acknowledged, sent, error} | null }
    const fixtures = new Map();
    let brain = client.readback(), disposed = false, notice = null;
    const entry = id => { if (!fixtures.has(id)) fixtures.set(id, { applied: null, generation: null, tween: null, local: null }); return fixtures.get(id); };
    const rowFor = id => brain.state?.fixtures.find(row => row.fixture_id === id) || null;
    const pausedBy = (id, e = entry(id)) => e.local ? 'manual' : rowFor(id)?.paused_until != null ? 'brain' : null;
    function readback() {
      const state = brain.state;
      return { status: brain.status, reason: brain.reason, online: brain.online, why: state?.why || null, mode: state?.mode || null,
        generated_at: state?.generated_at ?? null, decisions: state ? state.decisions.slice(0, LOG_LIMIT) : [],
        refused_reason: brain.refused_reason, notice,
        fixtures: (state?.fixtures || []).map(row => {
          const e = entry(row.fixture_id), paused = pausedBy(row.fixture_id, e);
          return { fixture_id: row.fixture_id, auto: !paused, paused, pause_reason: e.local ? e.local.reason : row.pause_reason,
            paused_until: row.paused_until, override_sent: e.local ? e.local.sent : null, override_error: e.local?.error || null,
            spot_on: row.spot.on, fading: !!e.tween };
        }), physical_commands: false };
    }
    const notify = () => { try { onChange(readback()); } catch (_) { /* view failure never stops following */ } };
    const stopTween = e => { if (e.tween) { cancel(e.tween.timer); e.tween = null; } };

    function currentState(overlay, id) {
      const result = overlay.virtualFixturesReadback?.();
      const fixture = result?.fixtures?.find(row => row.fixture_id === id);
      return fixture ? { state: fixture.state, ranges: fixture.color_temp_ranges } : null;
    }
    function patchAt(from, to, t, keys) {
      const patch = {};
      const step = (a, b) => ({ on: t >= 1 ? b.on : a.on || b.on, brightness: lerp(a.on ? a.brightness : 0, b.brightness, t),
        color_temp_kelvin: Math.round(lerp(a.color_temp_kelvin, b.color_temp_kelvin, t)) });
      if (keys.radial) patch.radials = RADIALS.map(number => ({ number, ...step(from.radials[number - 1], to.radial) }));
      if (keys.downward) patch.downward = step(from.downward, to.downward);
      if (keys.spotlight) patch.spotlight = step(from.spotlight, to.spotlight);
      return patch;
    }
    // One fixture's fade. Ambient and spot fade together over the longer time;
    // the spot's own fade time only shapes its own channel.
    function applyFixture(overlay, row) {
      const id = row.fixture_id, e = entry(id), current = currentState(overlay, id);
      if (!current) return false;
      const descriptor = overlay.virtualFixtureInteractionDescriptor?.(id);
      const generation = descriptor?.generation ?? null;
      const desired = desiredChannels(row, current.ranges || { radials: [2200, 5000], downward: [2200, 5000], spotlight: [2700, 5000] });
      const sig = signature(desired);
      if (e.applied === sig && e.generation === generation) return false;
      stopTween(e);
      if (desired.target_m) {
        const aimed = overlay.aimVirtualFixtureAtTarget?.(id, { point: desired.target_m, normal: [0, 0, 1], kind: 'lighting_brain_target' },
          generation === null ? undefined : { expectedGeneration: generation });
        if (aimed && !aimed.ok) notice = `Spot aim unavailable · ${aimed.reason}`;
      }
      const from = copy(current.state), started = nowMs();
      const ambientMs = desired.ambient_fade_s * 1000, spotMs = desired.spot_fade_s * 1000;
      const tick = () => {
        if (disposed || e.local || !brain.online) { e.tween = null; return; }
        const elapsed = nowMs() - started;
        const ta = ambientMs > 0 ? Math.min(1, elapsed / ambientMs) : 1, ts = spotMs > 0 ? Math.min(1, elapsed / spotMs) : 1;
        const result = overlay.setVirtualFixtureState?.(id, { ...patchAt(from, desired, ta, { radial: true, downward: true }),
          ...patchAt(from, desired, ts, { spotlight: true }) });
        if (result && result.ok === false) { notice = `Fixture update unavailable · ${result.reason}`; e.tween = null; notify(); return; }
        if (ta < 1 || ts < 1) e.tween = { timer: later(tick, STEP_MS) };
        else { e.tween = null; onApplied(id); notify(); }
      };
      e.applied = sig; e.generation = generation;
      tick();
      return true;
    }
    function settleLocalPauses() {
      const now = nowMs();
      for (const row of brain.state?.fixtures || []) {
        const e = entry(row.fixture_id);
        if (!e.local) continue;
        if (row.paused_until != null) e.local.acknowledged = true;
        // The brain cleared an override it had acknowledged (room empty or 30 min).
        else if (e.local.acknowledged || now - e.local.at_ms >= LOCAL_PAUSE_MS) { e.local = null; e.applied = null; }
      }
    }
    function apply() {
      if (disposed || !brain.online || !brain.state) return;
      const overlay = getOverlay();
      if (!overlay?.setVirtualFixtureState) return;
      notice = null;
      for (const row of brain.state.fixtures) {
        const e = entry(row.fixture_id);
        if (pausedBy(row.fixture_id, e)) { stopTween(e); e.applied = null; continue; }
        applyFixture(overlay, row);
      }
    }
    // Overrides go to an online brain only; a pause made while it is offline
    // is held here and sent when the brain is back.
    function sendOverride(fixtureId, e) {
      const local = e.local;
      if (!local || local.sent || local.sending || !brain.online) return;
      local.sending = true;
      Promise.resolve().then(() => client.postOverride(fixtureId, local.reason, local.made_at_ms / 1000)).then(() => {
        local.sending = false; if (e.local === local) { local.sent = true; local.error = null; notify(); }
      }, error => {
        local.sending = false; if (e.local === local) { local.error = error?.message || 'override_unsent'; notify(); }
      });
    }
    function brainChanged(next) {
      if (disposed) return;
      const wasOnline = brain.online;
      brain = next;
      if (!brain.online && wasOnline) for (const e of fixtures.values()) stopTween(e);
      if (brain.online && !wasOnline) for (const [id, e] of fixtures) if (e.local && !e.local.error) sendOverride(id, e);
      settleLocalPauses(); apply(); notify();
    }
    return {
      readback,
      // Called by the client's onChange when the follower owns the client.
      brainChanged,
      // Re-apply after the overlay document was replaced (new generation).
      refresh() { apply(); notify(); },
      // The W2 hand-change seam lands here: pause first, then tell the brain.
      handChange(fixtureId, { reason = 'change' } = {}) {
        if (disposed || !fixtureId) return readback();
        const e = entry(fixtureId);
        stopTween(e); e.applied = null;
        // A drag reports many aim steps; one override covers the gesture.
        if (e.local && e.local.reason === reason && nowMs() - e.local.at_ms < REPEAT_MS) { e.local.at_ms = nowMs(); return readback(); }
        e.local = { reason, at_ms: nowMs(), made_at_ms: nowMs(), acknowledged: rowFor(fixtureId)?.paused_until != null, sent: false, sending: false, error: null };
        notify();
        sendOverride(fixtureId, e);
        return readback();
      },
      returnToAuto(fixtureId) {
        if (disposed) return Promise.resolve(readback());
        const e = entry(fixtureId);
        // Nothing reached the brain: dropping the local pause is enough.
        if (e.local && !e.local.sent && !e.local.sending && rowFor(fixtureId)?.paused_until == null) {
          e.local = null; e.applied = null; notice = null; apply(); notify(); return Promise.resolve(readback());
        }
        return Promise.resolve().then(() => client.clearOverride(fixtureId)).then(() => {
          e.local = null; e.applied = null; notice = null; apply(); notify(); return readback();
        }, error => {
          notice = `Return to Auto not confirmed · ${error?.message || 'override_clear_failed'}`; notify(); return readback();
        });
      },
      dispose() {
        if (disposed) return;
        disposed = true;
        for (const e of fixtures.values()) stopTween(e);
      },
    };
  }

  // The Victoria view's one wiring point: a client and its follower, started together.
  function createBrainFollowing({ Client, fetch: fetchFn, EventSource: Source, setTimeout: later, clearTimeout: cancel,
    nowMs = () => Date.now(), base, getOverlay, onChange, onApplied } = {}) {
    let follower = null;
    const client = Client.createBrainClient({ base, fetch: fetchFn, EventSource: Source, setTimeout: later, clearTimeout: cancel, nowMs,
      onChange: next => follower?.brainChanged(next) });
    follower = createFollower({ client, getOverlay, setTimeout: later, clearTimeout: cancel, nowMs, onChange, onApplied });
    client.start();
    return { client, follower, dispose() { client.stop(); follower.dispose(); } };
  }

  return Object.freeze({ STEP_MS, LOCAL_PAUSE_MS, desiredChannels, createFollower, createBrainFollowing });
});
