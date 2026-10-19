/* Pending aim proposals waiting for a person's Set or Discard. This store only
 * remembers them: it never moves an aim, sends, persists or keeps time of its
 * own, and the Den desk preview is never a proposal. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeApartmentAimProposals = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'home-virtual-fixture-interaction-1';
  const ORIGINS = ['manual', 'chat', 'perception'];
  const UNAIMABLE = ['blocked', 'out_of_reach', 'no_surface', 'not_aimable'];
  const bindingKeys = ['content_revision','registration_version','manifest_digest','frame_digest','canonical_collision_digest'];
  const scopeKeys = ['site_id','fixture_id','mount_id','generation','equipment_revision','equipment_digest','binding'];
  const targetKeys = ['point_m','valid','reason','surface','normal_m'];
  // Undo history beyond this is forgotten oldest first; restore then refuses.
  const MAX_DISCARDED = 100;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const vector = value => Array.isArray(value) && value.length === 3 && value.every(finite);
  const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 160;
  const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exact = (value, keys) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
  const list = value => Array.isArray(value) ? Array.from(value) : null;
  const field = (value, key) => { try { return value == null ? undefined : value[key]; } catch (_) { return undefined; } };
  // Every fence field is a string, integer or null, so equal fences serialize equally.
  const fence = scope => JSON.stringify([scope.site_id,scope.fixture_id,scope.mount_id,scope.generation,
    scope.equipment_revision,scope.equipment_digest,...bindingKeys.map(key => scope.binding[key])]);
  const slot = scope => JSON.stringify([scope.site_id, scope.fixture_id]);
  const shape = value => { try { return JSON.stringify(value, (_, item) => plain(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item); } catch (_) { return null; } };
  // Fields are copied once and the copy is checked, so a later edit to the
  // supplied object, or a getter that changes its answer, cannot move a fence.
  // Revision and digest follow the descriptor: an unsaved revision 0 has no digest.
  function copyScope(siteId, value) {
    const source = value.binding;
    if (!plain(source)) return null;
    const binding = Object.fromEntries(bindingKeys.map(key => [key, source[key]]));
    const scope = { site_id: siteId, fixture_id: value.fixture_id, mount_id: value.mount_id, generation: value.generation,
      equipment_revision: value.equipment_revision, equipment_digest: value.equipment_digest, binding };
    if (!identifier(scope.site_id) || !identifier(scope.fixture_id) || !identifier(scope.mount_id) || !count(scope.generation)
        || !count(scope.equipment_revision)
        || !(digest(scope.equipment_digest) || scope.equipment_revision === 0 && scope.equipment_digest === null)
        || !identifier(binding.content_revision) || !count(binding.registration_version)
        || !bindingKeys.slice(2).every(key => digest(binding[key]))) return null;
    Object.freeze(binding);
    return Object.freeze(scope);
  }
  function scopeFromDescriptor(siteId, descriptor) {
    try { return plain(descriptor) && descriptor.schema === SCHEMA ? copyScope(siteId, descriptor) : null; }
    catch (_) { return null; }
  }
  // A scope handed back must be exactly one scopeFromDescriptor could produce.
  function readScope(value) {
    try {
      return plain(value) && exact(value, scopeKeys) && plain(value.binding) && exact(value.binding, bindingKeys)
        ? copyScope(value.site_id, value) : null;
    } catch (_) { return null; }
  }
  // An optional field left undefined is absent, as after a JSON round trip.
  function readTarget(value) {
    try {
      if (!plain(value)) return null;
      const given = {};
      for (const key of Object.keys(value)) {
        const item = value[key];
        if (item === undefined) continue;
        if (!targetKeys.includes(key)) return null;
        given[key] = item;
      }
      const has = key => Object.hasOwn(given, key);
      const target = { point_m: list(given.point_m), valid: given.valid };
      if (!vector(target.point_m) || typeof target.valid !== 'boolean'
          || (target.valid ? has('reason') : !UNAIMABLE.includes(given.reason))) return null;
      if (!target.valid) target.reason = given.reason;
      if (has('surface')) { if (!identifier(given.surface)) return null; target.surface = given.surface; }
      if (has('normal_m')) { target.normal_m = list(given.normal_m); if (!vector(target.normal_m)) return null; Object.freeze(target.normal_m); }
      Object.freeze(target.point_m);
      return Object.freeze(target);
    } catch (_) { return null; }
  }
  const order = (a, b) => a.site_id < b.site_id ? -1 : a.site_id > b.site_id ? 1
    : a.fixture_id < b.fixture_id ? -1 : a.fixture_id > b.fixture_id ? 1 : 0;
  const granted = proposal => Object.freeze({ ok: true, proposal });
  const refused = reason => Object.freeze({ ok: false, reason });
  function createAimProposalStore(options) {
    // A misconfigured clock is a host bug, so only construction ever throws.
    const given = field(options, 'now'), now = given === undefined ? Date.now : given;
    if (typeof now !== 'function') throw TypeError('aim_proposal_clock_invalid');
    const pending = new Map(), discarded = new Map(), listeners = new Map();
    let sequence = 0, revision = 0, snapshot = null;
    // Listeners run synchronously after each change and never on a no-op.
    function changed() {
      revision++; snapshot = null;
      for (const [token, listener] of [...listeners]) if (listeners.has(token)) try { listener(); } catch (_) { /* isolated */ }
    }
    function propose(scope, request) {
      const admitted = readScope(scope);
      if (!admitted) return refused('invalid_scope');
      const origin = field(request, 'origin');
      if (!ORIGINS.includes(origin)) return refused('origin_refused');
      const target = readTarget(field(request, 'target'));
      if (!target) return refused('invalid_target');
      let created;
      try { created = now(); } catch (_) { created = NaN; }
      if (!finite(created)) return refused('clock_unavailable');
      sequence++;
      // One pending proposal per fixture: a newer one replaces it outright.
      const proposal = Object.freeze({ id: 'aim-proposal-' + sequence, sequence, site_id: admitted.site_id,
        fixture_id: admitted.fixture_id, scope: admitted, origin, target, created_at_ms: created });
      pending.set(slot(admitted), proposal); changed();
      return granted(proposal);
    }
    function current(scope) {
      const admitted = readScope(scope), proposal = admitted && pending.get(slot(admitted));
      return proposal && fence(proposal.scope) === fence(admitted) ? proposal : null;
    }
    function remember(proposal) {
      discarded.set(proposal.id, { proposal, shape: shape(proposal) });
      if (discarded.size > MAX_DISCARDED) discarded.delete(discarded.keys().next().value);
    }
    // Set and Discard share one fence. A proposal whose fence moved can never
    // be Set, so that refusal also retires it.
    function release(scope, id, keep) {
      const admitted = readScope(scope);
      if (!admitted) return refused('invalid_scope');
      const key = slot(admitted), proposal = pending.get(key);
      if (!proposal || proposal.id !== id) return refused('not_pending');
      const stale = fence(proposal.scope) !== fence(admitted);
      pending.delete(key);
      if (keep && !stale) remember(proposal);
      changed();
      return stale ? refused('stale_scope') : granted(proposal);
    }
    // Only this store's own discarded record returns, once per Discard. An
    // exact structural copy is accepted, but the stored original is re-admitted.
    function restore(scope, proposal) {
      const admitted = readScope(scope);
      if (!admitted) return refused('invalid_scope');
      const entry = discarded.get(field(proposal, 'id'));
      if (!entry || shape(proposal) !== entry.shape) return refused('invalid_proposal');
      if (fence(entry.proposal.scope) !== fence(admitted)) return refused('stale_scope');
      const key = slot(admitted);
      if (pending.has(key)) return refused('occupied');
      discarded.delete(entry.proposal.id); pending.set(key, entry.proposal); changed();
      return granted(entry.proposal);
    }
    // Clearing also forgets discarded records, so nothing cleared can return.
    // retainScopes keeps them: restore already refuses a moved fence.
    function drop(predicate, forget) {
      if (forget) for (const [id, entry] of discarded) if (predicate(entry.proposal)) discarded.delete(id);
      let dropped = 0;
      for (const [key, proposal] of pending) if (predicate(proposal)) { pending.delete(key); dropped++; }
      if (dropped) changed();
      return dropped;
    }
    function retainScopes(siteId, liveScopes) {
      if (!identifier(siteId) || !Array.isArray(liveScopes)) return 0;
      let live;
      try { live = new Set(Array.from(liveScopes, value => readScope(value)).filter(Boolean).map(fence)); } catch (_) { return 0; }
      return drop(proposal => proposal.site_id === siteId && !live.has(fence(proposal.scope)), false);
    }
    const clearSite = siteId => identifier(siteId) ? drop(proposal => proposal.site_id === siteId, true) : 0;
    const clearAll = () => drop(() => true, true);
    // Same object until something changes, as useSyncExternalStore requires.
    function getSnapshot() {
      if (!snapshot) snapshot = Object.freeze({ revision, proposals: Object.freeze([...pending.values()].sort(order)) });
      return snapshot;
    }
    function subscribe(listener) {
      if (typeof listener !== 'function') return () => {};
      const token = {};
      listeners.set(token, listener);
      return () => { listeners.delete(token); };
    }
    return Object.freeze({ scopeFromDescriptor, propose, current, take: (scope, id) => release(scope, id, false),
      discard: (scope, id) => release(scope, id, true), restore, retainScopes, clearSite, clearAll, getSnapshot, subscribe });
  }
  return Object.freeze({ createAimProposalStore });
});
