/* Named aims (APT-A05, A07, A10, R04): saved spots a light can be pointed at
 * again. Pure logic, shared by both homes' Aim hosts, the named-aim inspector
 * and Chat. Nothing here moves an aim, sends, persists or keeps a clock:
 * Echo saves through the existing apartment_model targets[] save path, and
 * the Den's named aims live in a Den-owned store this file only reads
 * through an adapter. Recall only proposes.
 *
 * Copy comes from SPEC › Aim lifecycle and the prototype's AimTab and
 * TargetInspector. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeApartmentNamedAims = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const INTERACTION_SCHEMA = 'home-virtual-fixture-interaction-1';
  const ENGINEERED_KIND = 'engineered_gimbal_v1';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const vector = value => Array.isArray(value) && value.length === 3 && value.every(finite);
  const text = (value, fallback = '') => typeof value === 'string' && value.trim() ? value.trim() : fallback;
  const sameName = (a, b) => text(a).toLocaleLowerCase() === text(b).toLocaleLowerCase();
  const round = (value, places) => Math.round(value * 10 ** places) / 10 ** places;
  const digestOk = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

  const COPY = Object.freeze({
    badge: Object.freeze({ saving: 'Saving', saved: 'Saved', local: 'Saved locally', conflict: 'Conflict',
      changed: 'Check point', not_saved: 'Not saved' }),
    flag: Object.freeze({
      local: name => `“${name}” is saved in this browser only.`,
      conflict: name => `“${name}” already exists.`,
      changedScan: name => `“${name}” predates the latest scan.`,
      changedProfile: name => `“${name}” predates the light's profile change.`,
      notSaved: name => `“${name}” isn't saved. The model changed elsewhere, so nothing was overwritten and your draft is kept.`,
    }),
    action: Object.freeze({ replace: 'Replace existing', keepBoth: 'Keep both', preview: 'Preview it', keep: 'Keep as is',
      save: 'Save as named aim', recall: 'Propose this aim', current: 'Current aim' }),
    naming: Object.freeze({ label: 'Name for this aim', placeholder: 'Desk reading', save: 'Save', cancel: 'Cancel' }),
    source: Object.freeze({ aimed: 'Saved from an aim', mapped: 'Mapped in Setup', tape_and_scan: 'Tape and scan',
      proposed: 'Proposed placement', configured_desk: 'Configured desk · read only', authored: 'Authored' }),
    inspector: Object.freeze({
      kind: 'Named aim',
      litBy: 'Lit by', nobody: 'No light is aimed here now.',
      reach: 'Which lights can reach it',
      aimHere: 'Aim a light here', aimHereNote: 'Creates a proposal on that light; nothing changes until you set it.',
      edit: 'Edit in Setup', back: 'Back to room',
      automation: 'Use in an automation',
      automationNote: 'Automations refer to this aim by its id, so renaming it keeps them working.',
      automationProposed: 'targets[].id is the existing schema field; how automations reference it is proposed.',
      automationUsers: 'Which automations use it isn’t available yet.',
      copyId: 'Copy id', copied: 'Id copied', geometry: 'Geometry',
    }),
    reach: Object.freeze({
      reachable: 'Can reach · simulated', modeledReach: 'Can reach · modeled',
      outOfReach: (tilt, limit) => `Out of reach · ${tilt}° from straight down, limit ${limit}°`,
      notCommissioned: 'travel limits not commissioned', notAimable: 'Can’t be aimed',
    }),
    den: Object.freeze({
      unavailable: 'Den named aims wait on the Den’s named-aim store.',
      revisionChanged: 'Den named aims are unavailable: the room registration changed. They are not moved to the new registration.',
      deskReadOnly: 'The configured desk is read only. Keep both saves this aim under another name.',
    }),
    echoUnavailable: 'Saving needs an editable Echo model.',
    // The same suggestions the room chip gives with an invalid spot.
    suggest: Object.freeze({
      no_surface: 'Drag onto a wall, furniture or the floor.',
      not_aimable: 'Choose another surface.',
      blocked: 'Move the spot clear of it, or try another light.',
      out_of_reach: 'Choose a spot closer to straight below the light.',
    }),
  });

  /* A deterministic 64-hex fence key for the proposal store's scope, built
   * from the Echo model's identity when no content-pack digest exists. It
   * identifies, it does not authenticate: eight FNV-1a lanes, each seeded. */
  function fenceDigest(value) {
    const input = typeof value === 'string' ? value : JSON.stringify(value);
    let out = '';
    for (let lane = 0; lane < 8; lane++) {
      let hash = (0x811c9dc5 ^ Math.imul(lane + 1, 0x9e3779b1)) >>> 0;
      for (let index = 0; index < input.length; index++) {
        hash ^= input.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193) >>> 0;
      }
      hash ^= lane; hash = Math.imul(hash ^ (hash >>> 15), 0x2c1b3c6d) >>> 0;
      out += hash.toString(16).padStart(8, '0');
    }
    return out;
  }

  const isEngineered = device => device?.fixture_kind === ENGINEERED_KIND && vector(device.pos);

  // Where an Echo engineered light points now: the simulation's committed
  // destination when there is one, otherwise its configured default target.
  function echoCurrentAim(model, fixture, runtime) {
    const destination = runtime?.current_destination;
    if (destination && vector(destination.pos)) {
      return { point_m: destination.pos.slice(), target_id: destination.kind === 'named' ? destination.id || null : null,
        name: text(destination.name, null) };
    }
    const target = (model?.targets || []).find(item => item?.id === fixture?.gimbal?.default_target_id);
    return target && vector(target.pos) ? { point_m: target.pos.slice(), target_id: target.id, name: text(target.name, null) } : null;
  }

  function namedAimsForPresentation(targets) {
    return (Array.isArray(targets) ? targets : []).filter(target => target && text(target.id) && vector(target.pos))
      .map(target => Object.freeze({ id: target.id, name: text(target.name, target.id), point_m: target.pos.slice(),
        normal_m: vector(target.normal) ? target.normal.slice() : [0, 0, 1],
        size_m: Array.isArray(target.size_m) ? target.size_m.filter(finite) : [] }));
  }

  /* The interaction descriptor, aim limits and named aims the room overlay
   * and the proposal store need for one Echo engineered light. The fence
   * binds the scene, the model revision and this light's mount and profile,
   * so a proposal retires when any of them changes. */
  function echoAimContext({ model, fixtureId, runtime = null, contentRevision = null, manifest = null, sim = false } = {}) {
    const fixture = (model?.devices || []).find(device => device?.id === fixtureId);
    if (!isEngineered(fixture)) return null;
    const scene = text(contentRevision, 'echo-legacy');
    const asset = name => digestOk(manifest?.assets?.[name]?.sha256) ? manifest.assets[name].sha256 : fenceDigest(['echo', scene, name]);
    const revision = Number.isSafeInteger(model?.revision) && model.revision >= 0 ? model.revision : 0;
    const profile = { pos: fixture.pos, limits: fixture.gimbal?.limits || null, calibration: fixture.gimbal?.visualization_calibration || null,
      profile: fixture.gimbal?.product_profile_sha256 || null };
    const current = echoCurrentAim(model, fixture, runtime);
    const descriptor = Object.freeze({
      schema: INTERACTION_SCHEMA, generation: 1, fixture_id: fixture.id, mount_id: fixture.id,
      binding: Object.freeze({ content_revision: scene.slice(0, 160), registration_version: Number.isSafeInteger(manifest?.registrationVersion) ? manifest.registrationVersion : 0,
        manifest_digest: digestOk(manifest?.sha256) ? manifest.sha256 : fenceDigest(['echo', scene, 'manifest']),
        frame_digest: asset('frame.json'), canonical_collision_digest: asset('collision.glb') }),
      equipment_revision: revision, equipment_digest: revision === 0 ? null : fenceDigest(['echo-fixture', revision, profile]),
      display_anchor_m: fixture.pos.slice(), display_origin_m: fixture.pos.slice(), optical_origin_m: fixture.pos.slice(),
      target: current ? Object.freeze({ point: current.point_m, surface_id: current.name || null, target_id: current.target_id }) : null,
      truth_role: sim ? 'simulated' : 'engineered_profile', physical_commands: false,
    });
    const configured = fixture.gimbal?.limits || {};
    const limits = Object.freeze({ ...configured, limit_role: 'engineered_profile', physical_travel: 'configured' });
    return Object.freeze({ fixture, descriptor, limits, current, namedAims: namedAimsForPresentation(model?.targets),
      label: sim ? 'Simulated' : 'Engineered profile' });
  }

  /* Distance and tilt from straight down, as the room chip measures them. */
  function measure(origin, point) {
    if (!vector(origin) || !vector(point)) return null;
    const ray = point.map((value, axis) => value - origin[axis]), length = Math.hypot(...ray);
    if (!(length > 0)) return null;
    const tilt = Math.acos(Math.max(-1, Math.min(1, -ray[2] / length))) * 180 / Math.PI;
    return { distance_m: length, tilt_deg: tilt, text: `${length.toFixed(2)} m · ${Math.round(tilt)}° tilt` };
  }

  /* Reach for one light, from what is actually known about it. Echo's
   * engineered profile has configured limits; Victoria's fixtures have no
   * commissioned travel, so they never claim reach either way. */
  function reachOfAim(target, fixture, { siteId } = {}) {
    if (siteId === 'victoria') return Object.freeze({ state: 'unknown', text: COPY.reach.notCommissioned });
    if (!isEngineered(fixture) || !vector(target?.pos)) return Object.freeze({ state: 'not_aimable', text: COPY.reach.notAimable });
    const measured = measure(fixture.pos, target.pos);
    const tiltMin = fixture.gimbal?.limits?.tilt_min_deg;
    const limit = finite(tiltMin) && 90 - tiltMin > 0 && 90 - tiltMin < 180 ? 90 - tiltMin : null;
    if (measured && limit != null && measured.tilt_deg > limit) {
      return Object.freeze({ state: 'out_of_reach', text: COPY.reach.outOfReach(Math.round(measured.tilt_deg), Math.round(limit)) });
    }
    return Object.freeze({ state: 'reachable', text: COPY.reach.reachable, measure: measured?.text || null });
  }

  const lightsUsingAim = (targetId, fixtures, aimOf) => (Array.isArray(fixtures) ? fixtures : [])
    .filter(fixture => { try { return !!targetId && aimOf(fixture)?.target_id === targetId; } catch (_) { return false; } });

  // The automation reference is the target id (SPEC › Aim lifecycle). The
  // fixture + target shape is only a proposal and is labelled that way.
  const automationReference = target => text(target?.id, null);
  const proposedFixtureReference = (fixtureId, targetId) => `aim ${fixtureId} → ${targetId}`;

  const slug = value => text(value, 'aim').toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'aim';
  const upFor = normal => Math.abs(normal[2]) > .9 ? [0, 1, 0] : [0, 0, 1];
  const findNameCollision = (targets, name, exceptId = null) => (Array.isArray(targets) ? targets : [])
    .find(target => target && target.id !== exceptId && sameName(target.name, name)) || null;
  function uniqueName(targets, name) {
    const base = text(name, 'Aimed spot');
    if (!findNameCollision(targets, base)) return base;
    for (let index = 2; index < 1000; index++) if (!findNameCollision(targets, `${base} ${index}`)) return `${base} ${index}`;
    return `${base} ${Date.now()}`;
  }

  /* What a scan or profile change is measured against. A targets[] record
   * saved from an aim keeps it (proposed field `aim_basis`), so the aim can
   * say it predates a later scan or profile instead of silently requalifying. */
  function aimBasis({ contentRevision = null, collisionDigest = null, fixture = null } = {}) {
    return Object.freeze({ scene: text(contentRevision, 'echo-legacy'), collision: text(collisionDigest, null),
      fixture_id: text(fixture?.id, null), profile: text(fixture?.gimbal?.product_profile_sha256, null) });
  }

  /* A targets[] record from a valid proposal: point, normal, surface and
   * source "aimed" (SPEC › Aim lifecycle). Ids are stable across renames. */
  function namedAimRecord({ proposal, name, roomId = null, basis = null, nowMs }) {
    const target = proposal?.target;
    if (!target || target.valid !== true || !vector(target.point_m)) return { ok: false, reason: 'proposal_invalid' };
    const clean = text(name);
    if (!clean) return { ok: false, reason: 'name_required' };
    if (!finite(nowMs)) return { ok: false, reason: 'clock_unavailable' };
    const normal = vector(target.normal_m) ? target.normal_m.map(value => round(value, 4)) : [0, 0, 1];
    return { ok: true, record: {
      id: `target-${slug(clean)}-${Math.floor(nowMs).toString(36)}`, name: clean.slice(0, 80), category: 'custom', shape: 'point',
      pos: target.point_m.map(value => round(value, 3)), normal, up: upFor(normal), size_m: [0, 0],
      room_id: roomId || null, surface: text(target.surface, null), source: 'aimed', confidence: 1,
      updated_at: new Date(nowMs).toISOString(), ...(basis ? { aim_basis: { ...basis } } : {}),
    } };
  }

  /* Adds a record to a model copy. An existing name is a conflict until the
   * person chooses: Replace existing keeps the existing id (so references
   * survive) and takes the new spot; Keep both saves under the next free name. */
  function applyNamedAimSave(model, record, resolution = null) {
    if (!record || !text(record.id) || !text(record.name)) return { ok: false, reason: 'record_invalid' };
    const targets = Array.isArray(model?.targets) ? model.targets : [];
    const existing = findNameCollision(targets, record.name, record.id);
    if (existing && !resolution) return { ok: false, conflict: true, existing };
    let next, targetId = record.id;
    if (existing && resolution === 'replace') {
      targetId = existing.id;
      next = targets.map(target => target.id === existing.id ? { ...target, ...record, id: existing.id, name: existing.name } : target);
    } else if (existing && resolution === 'keep_both') {
      next = [...targets, { ...record, name: uniqueName(targets, record.name) }];
    } else if (existing) {
      return { ok: false, reason: 'resolution_invalid' };
    } else next = [...targets, record];
    return { ok: true, target_id: targetId, model: { ...model, targets: next } };
  }

  /* The model-level save status as one aim's state. A revision conflict is
   * not a name conflict: nothing was written and the draft is kept. */
  function stateFromSave(save) {
    switch (save?.state) {
      case 'saving': return 'saving';
      case 'saved': return 'saved';
      case 'offline': case 'simulation': case 'unsaved': case 'reviewed': return 'local';
      case 'conflict': case 'validation': return 'not_saved';
      default: return null;
    }
  }

  function changedReason(target, current) {
    const basis = target?.aim_basis;
    if (!basis || !current) return null;
    if (basis.scene && current.scene && basis.scene !== current.scene) return 'scan';
    if (basis.collision && current.collision && basis.collision !== current.collision) return 'scan';
    if (basis.profile && current.profile && basis.fixture_id === current.fixture_id && basis.profile !== current.profile) return 'profile';
    return null;
  }

  /* One named aim's state, badge, attention flag and actions. `pending` is
   * the aim this view is saving (from Save as named aim), with the name
   * conflict it is waiting on, if any. `kept` lists ids the person kept as
   * they are after a scan or profile change. */
  function namedAimState(target, { pending = null, save = null, basis = null, kept = [] } = {}) {
    const name = text(target?.name, text(target?.id, 'This aim'));
    let state = 'saved';
    if (pending && pending.target_id === target?.id) state = pending.conflict ? 'conflict' : stateFromSave(save) || 'saved';
    else if (pending && pending.conflict && sameName(pending.name, target?.name)) state = 'saved';
    const why = state === 'saved' && !(kept || []).includes(target?.id) ? changedReason(target, basis) : null;
    if (why) state = 'changed';
    const flag = state === 'local' ? COPY.flag.local(name) : state === 'conflict' ? COPY.flag.conflict(name)
      : state === 'changed' ? (why === 'profile' ? COPY.flag.changedProfile(name) : COPY.flag.changedScan(name))
        : state === 'not_saved' ? COPY.flag.notSaved(name) : null;
    const actions = state === 'conflict' ? ['replace', 'keep_both'] : state === 'changed' ? ['preview', 'keep'] : [];
    return Object.freeze({ state, badge: COPY.badge[state], flag, actions: Object.freeze(actions),
      attention: ['local', 'conflict', 'changed', 'not_saved'].includes(state), changed: why });
  }

  const sourceLabel = target => COPY.source[target?.source] || COPY.source.authored;
  const surfaceKind = target => vector(target?.normal) && Math.abs(target.normal[2]) > .9 ? 'horizontal surface' : 'wall surface';

  /* The Den's named aims (A07 default, agreed with Den 2026-10-19): a
   * Den-owned store bound to the registration revision; the configured desk
   * shows as a linked, read-only aim; no presets. This authored adapter
   * stands in for Den's store until its contract lands. It never writes the
   * desk, the candidate or the registration, never re-projects aims to a new
   * registration, and recall creates no evidence. */
  function createAuthoredDenNamedAimStore({ registrationRevision, configuredDesk = null, now = Date.now } = {}) {
    if (!text(registrationRevision)) throw TypeError('den_named_aims_revision_required');
    const bound = registrationRevision, aims = new Map(), listeners = new Set();
    const writes = { named_aims: 0, desk: 0, candidate: 0, registration: 0, evidence: 0 };
    const desk = configuredDesk && vector(configuredDesk.point_m) ? Object.freeze({ id: text(configuredDesk.id, 'den-configured-desk'),
      name: text(configuredDesk.name, 'Desk'), point_m: Object.freeze(configuredDesk.point_m.slice()),
      normal_m: Object.freeze(vector(configuredDesk.normal_m) ? configuredDesk.normal_m.slice() : [0, 0, 1]),
      source: 'configured_desk', read_only: true, linked: true }) : null;
    let revision = 0;
    const changed = () => { revision++; for (const listener of [...listeners]) try { listener(); } catch (_) { /* isolated */ } };
    const list = () => [...(desk ? [desk] : []), ...aims.values()];
    function view(currentRevision) {
      if (currentRevision !== bound) return Object.freeze({ available: false, reason: COPY.den.revisionChanged, aims: Object.freeze([]), revision });
      return Object.freeze({ available: true, reason: null, aims: Object.freeze(list()), revision });
    }
    async function save({ name, point_m, normal_m, fixture_id } = {}, { currentRevision, resolution = null } = {}) {
      if (currentRevision !== bound) return { ok: false, reason: COPY.den.revisionChanged };
      const clean = text(name);
      if (!clean || !vector(point_m)) return { ok: false, reason: 'name_and_point_required' };
      const existing = list().find(aim => sameName(aim.name, clean));
      if (existing && !resolution) return { ok: false, conflict: true, existing, read_only: !!existing.read_only };
      if (existing && resolution === 'replace' && existing.read_only) return { ok: false, reason: COPY.den.deskReadOnly };
      await Promise.resolve();
      if (currentRevision !== bound) return { ok: false, reason: COPY.den.revisionChanged };
      let id, finalName = clean;
      if (existing && resolution === 'replace') id = existing.id;
      else {
        if (existing) { let index = 2; while (list().some(aim => sameName(aim.name, `${clean} ${index}`))) index++; finalName = `${clean} ${index}`; }
        id = `den-aim-${slug(finalName)}-${Math.floor(now()).toString(36)}`;
      }
      const aim = Object.freeze({ id, name: existing && resolution === 'replace' ? existing.name : finalName,
        point_m: Object.freeze(point_m.slice()), normal_m: Object.freeze(vector(normal_m) ? normal_m.slice() : [0, 0, 1]),
        source: 'aimed', fixture_id: text(fixture_id, null), registration_revision: bound, read_only: false });
      aims.set(id, aim); writes.named_aims++; changed();
      return { ok: true, state: 'saved', aim };
    }
    // A recall is only an aim reference: no presence, activity or target evidence.
    function recall(id, { currentRevision } = {}) {
      if (currentRevision !== bound) return { ok: false, reason: COPY.den.revisionChanged };
      const aim = list().find(item => item.id === id);
      return aim ? { ok: true, reference: Object.freeze({ named_aim_id: aim.id, point_m: aim.point_m.slice(), normal_m: aim.normal_m.slice() }), evidence: null }
        : { ok: false, reason: 'not_found' };
    }
    return Object.freeze({ kind: 'authored_den_adapter', registration_revision: bound, view, save, recall,
      subscribe(listener) { if (typeof listener !== 'function') return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
      writes: () => Object.freeze({ ...writes }) });
  }

  // The production binding until Den's store contract lands: no aims, no save.
  const denNamedAimsUnavailable = () => Object.freeze({ kind: 'unavailable', view: () => Object.freeze({ available: false,
    reason: COPY.den.unavailable, aims: Object.freeze([]), revision: 0 }), save: async () => ({ ok: false, reason: COPY.den.unavailable }),
  recall: () => ({ ok: false, reason: COPY.den.unavailable }), subscribe: () => () => {}, writes: () => Object.freeze({ named_aims: 0, desk: 0, candidate: 0, registration: 0, evidence: 0 }) });

  return Object.freeze({ COPY, ENGINEERED_KIND, fenceDigest, echoAimContext, echoCurrentAim, namedAimsForPresentation, measure,
    reachOfAim, lightsUsingAim, automationReference, proposedFixtureReference, findNameCollision, uniqueName, aimBasis,
    namedAimRecord, applyNamedAimSave, stateFromSave, namedAimState, sourceLabel, surfaceKind,
    createAuthoredDenNamedAimStore, denNamedAimsUnavailable });
});
