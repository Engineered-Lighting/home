/* Echo's engineered light on the shared aim path (APT-A01, A05, C04): the
 * reticle, tap, keys, a recalled named aim and Chat all propose into the one
 * HomeApartmentAimProposals store, and the room bar, the Aim tab and a Chat
 * card all Set through this host's one `set`. Set is simulated: it moves the
 * Simulation Mode runtime's aim and nothing else. Outside Simulation Mode the
 * light is physical, so Set refuses and no command path exists here.
 * Undo and redo step per-light {current aim, proposal} snapshots, 30 deep
 * (SPEC › Aim lifecycle). */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeApartmentEchoAim = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const HISTORY_STEPS = 30;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const vector = value => Array.isArray(value) && value.length === 3 && value.every(finite);
  const near = (a, b) => vector(a) && vector(b) && Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) < .0015;
  const COPY = Object.freeze({
    proposed: 'Proposed aim. Set aim moves the simulated light; Discard keeps the current aim.',
    invalid: 'This spot can’t be aimed at. Discard it or drag again.',
    set: 'Aim set in the simulation. No hardware command sent.',
    toast: 'Aim set · simulated, no hardware command sent',
    discarded: shortcut => `Proposal discarded. ${shortcut} brings it back.`,
    stale: 'That proposal belongs to an earlier scene. Drag the aim handle again.',
    physical: 'Set aim is simulated only. This light is physical, and Home has no movement authority for it.',
    undone: 'Aim undone.', redone: 'Aim restored.', earlier: 'This aim belongs to an earlier scene. Select a new room surface.',
  });

  /* `getContext(fixtureId)` returns HomeApartmentNamedAims.echoAimContext for
   * the light now (or null); `runtime` is the Simulation Mode aim runtime.
   * `simulated()` says whether Set may move it. `sweep` plays the beam
   * transition (visual only) and `reducedMotion()` skips it. */
  function createEchoAimHost({ store, siteId = 'echo', getContext, runtime = null, simulated = () => false,
    sweep = null, reducedMotion = () => false, onNotice = () => {}, onToast = () => {}, onChange = () => {},
    shortcut = 'Ctrl+Z', now = Date.now } = {}) {
    if (!store || typeof store.propose !== 'function' || typeof getContext !== 'function') throw TypeError('echo_aim_host_dependencies_required');
    const history = { undo: [], redo: [] }, listeners = new Set();
    let version = 0, snapshot = null;
    const changed = () => { version++; snapshot = null; for (const listener of [...listeners]) try { listener(); } catch (_) { /* isolated */ }
      try { onChange(); } catch (_) { /* observer */ } };
    const say = message => { try { onNotice(message); } catch (_) { /* observer */ } };

    function held(id) {
      let context = null;
      try { context = typeof id === 'string' ? getContext(id) : null; } catch (_) { context = null; }
      const scope = context ? store.scopeFromDescriptor(siteId, context.descriptor) : null;
      return scope ? { context, scope, proposal: store.current(scope) } : null;
    }
    const aimOf = context => context?.current ? { point_m: context.current.point_m.slice(), target_id: context.current.target_id || null,
      name: context.current.name || null } : null;
    function record(id, before, after) {
      history.undo.push({ fixture_id: id, before, after, at: now() });
      const mine = history.undo.filter(entry => entry.fixture_id === id);
      if (mine.length > HISTORY_STEPS) history.undo.splice(history.undo.indexOf(mine[0]), 1);
      history.redo = history.redo.filter(entry => entry.fixture_id !== id);
      changed();
    }

    // A drop, tap, key, recalled aim or Chat reply only proposes.
    function propose({ fixture_id: id, origin = 'manual', target } = {}) {
      const cur = held(id);
      const result = cur ? store.propose(cur.scope, { origin, target }) : null;
      if (!result?.ok) return null;
      record(id, { aim: aimOf(cur.context), proposal: cur.proposal }, { aim: aimOf(cur.context), proposal: result.proposal });
      say(result.proposal.target.valid ? COPY.proposed : COPY.invalid);
      return result.proposal;
    }

    // The simulation's destination for a proposal: a snapped named aim keeps
    // its name and id, so the Aim tab can mark it current.
    function destinationFor(context, target) {
      const aim = (context.namedAims || []).find(item => near(item.point_m, target.point_m));
      return aim ? { kind: 'named', id: aim.id, name: aim.name, pos: aim.point_m.slice(), normal: aim.normal_m.slice(), key: `named:${aim.id}` }
        : { kind: 'mesh', id: null, name: target.surface || 'Aimed spot', pos: target.point_m.slice(),
          normal: vector(target.normal_m) ? target.normal_m.slice() : null, key: `point:${target.point_m.map(value => value.toFixed(3)).join(',')}` };
    }
    function playSweep(context, fromPoint, toPoint) {
      if (typeof sweep !== 'function' || reducedMotion() || !vector(fromPoint) || !vector(toPoint)) return;
      try { sweep({ fixture: context.fixture, from_point_m: fromPoint, to_point_m: toPoint }); } catch (_) { /* visual only */ }
    }

    /* The one Set. The bar, the Aim tab, Enter and a Chat card all call it,
     * so each gives the same simulated aim, history entry and toast. */
    function set({ fixture_id: id, proposal } = {}) {
      if (!simulated() || typeof runtime?.setDestination !== 'function') { say(COPY.physical); return { ok: false, reason: 'not_simulated' }; }
      const cur = held(id), target = proposal?.target;
      if (!cur?.proposal || cur.proposal.id !== proposal?.id || target?.valid !== true || !vector(target.point_m)) {
        say(COPY.stale); return { ok: false, reason: 'stale' };
      }
      const before = { aim: aimOf(cur.context), proposal: cur.proposal };
      if (!store.take(cur.scope, proposal.id).ok) { say(COPY.stale); return { ok: false, reason: 'stale' }; }
      const destination = destinationFor(cur.context, target);
      const result = runtime.setDestination(id, destination);
      if (!result?.current_destination || !near(result.current_destination.pos, destination.pos)) {
        store.propose(cur.scope, { origin: proposal.origin, target });
        say(COPY.stale); return { ok: false, reason: 'runtime_refused' };
      }
      const after = held(id);
      record(id, before, { aim: aimOf(after?.context) || { point_m: destination.pos, target_id: destination.id, name: destination.name }, proposal: null });
      playSweep(cur.context, before.aim?.point_m, destination.pos);
      say(COPY.set); try { onToast(COPY.toast); } catch (_) { /* observer */ }
      return { ok: true, destination };
    }

    function discard({ fixture_id: id, proposal } = {}) {
      const cur = held(id);
      if (!cur?.proposal || cur.proposal.id !== proposal?.id || !store.discard(cur.scope, proposal.id).ok) return false;
      record(id, { aim: aimOf(cur.context), proposal: cur.proposal }, { aim: aimOf(cur.context), proposal: null });
      say(COPY.discarded(shortcut));
      return true;
    }

    // History restores where the light points and its proposal. A moved
    // scope refuses; the light's other settings are not touched.
    function apply(id, state) {
      const cur = held(id);
      if (!cur || !state) return false;
      const want = state.aim, have = aimOf(cur.context);
      if (want && !(have && near(want.point_m, have.point_m))) {
        if (!simulated() || typeof runtime?.setDestination !== 'function') return false;
        const named = want.target_id ? (cur.context.namedAims || []).find(aim => aim.id === want.target_id) : null;
        runtime.setDestination(id, named ? { kind: 'named', id: named.id, name: named.name, pos: named.point_m.slice(), normal: named.normal_m.slice(), key: `named:${named.id}` }
          : { kind: 'mesh', id: null, name: want.name || 'Aimed spot', pos: want.point_m.slice(), key: `point:${want.point_m.join(',')}` });
      }
      const live = held(id);
      if (!live) return false;
      if (live.proposal && live.proposal.id !== state.proposal?.id) store.take(live.scope, live.proposal.id);
      if (state.proposal && live.proposal?.id !== state.proposal.id && !store.restore(live.scope, state.proposal).ok)
        store.propose(live.scope, { origin: state.proposal.origin, target: state.proposal.target });
      return true;
    }
    function step(direction, id) {
      const source = direction === 'undo' ? history.undo : history.redo;
      const index = source.findLastIndex(entry => entry.fixture_id === id), entry = source[index];
      if (!entry) return false;
      if (!apply(id, direction === 'undo' ? entry.before : entry.after)) { say(COPY.earlier); return false; }
      source.splice(index, 1); (direction === 'undo' ? history.redo : history.undo).push(entry);
      changed(); say(direction === 'undo' ? COPY.undone : COPY.redone);
      return true;
    }

    // Recalling a named aim proposes it for this light (SPEC › Selection
    // precedence 3); a spot outside the profile's limits is an invalid proposal.
    function recall({ fixture_id: id, aim, origin = 'manual', reach = null } = {}) {
      if (!aim || !vector(aim.point_m)) return null;
      const target = { point_m: aim.point_m.slice(), valid: reach?.state !== 'out_of_reach', surface: aim.name,
        normal_m: vector(aim.normal_m) ? aim.normal_m.slice() : undefined };
      if (!target.valid) target.reason = 'out_of_reach';
      return propose({ fixture_id: id, origin, target });
    }

    return Object.freeze({
      COPY, held, propose, set, discard, recall,
      undo: id => step('undo', id), redo: id => step('redo', id),
      historyLength: (direction, id) => history[direction].filter(entry => entry.fixture_id === id).length,
      clear() { if (!history.undo.length && !history.redo.length) return; history.undo = []; history.redo = []; changed(); },
      subscribe(listener) { if (typeof listener !== 'function') return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
      getSnapshot() { if (!snapshot) snapshot = Object.freeze({ version }); return snapshot; },
    });
  }

  return Object.freeze({ createEchoAimHost, HISTORY_STEPS });
});
