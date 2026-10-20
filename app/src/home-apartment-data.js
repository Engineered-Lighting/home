/* home-apartment-data.js — data layer for the /apartment view.
 *
 * Three concerns, no React:
 *   1. apartment_model load/save — HA endpoint (revision optimistic
 *      concurrency, 409 → rebase callback) with localStorage draft fallback
 *      so edit mode never loses work.
 *   2. HA entity state binding — one get_states snapshot + one state_changed
 *      subscription filtered to bound entity ids.
 *   3. spatial-tracker WS client — live person tracks (room-level Stage A /
 *      precise Stage B), auto-reconnect, replay-aware.
 */
(function () {
  const TRACKER_KEY = "apartment3d.trackerBase";
  const DRAFT_KEY = "apartment3d.modelDraft";
  const REMOTE_CACHE_KEY = "apartment3d.remoteCache";
  const VIEW_KEY_PREFIX = "apartment3d.view.";
  const DEFAULT_TRACKER = "ws://192.168.0.100:8098";
  const rehearsalIsolated = () => window.HomeLiveSceneShadow?.active === true || window.HomeMapRehearsalOfflineState?.active === true;

  function defaultTrackerBase() {
    try {
      const resolved = window.HomeServices?.get?.("tracker");
      if (resolved) return resolved;
    } catch (e) { /* */ }
    return (window.HG_WEB_MODE && window.HG_DEFAULT_TRACKER_BASE) || DEFAULT_TRACKER;
  }

  function toWsBase(base) {
    const clean = String(base || "").replace(/\/+$/, "");
    if (!clean) return "";
    if (clean.startsWith("/")) {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${window.location.host}${clean}`;
    }
    if (clean.startsWith("http://")) return "ws://" + clean.slice("http://".length);
    if (clean.startsWith("https://")) return "wss://" + clean.slice("https://".length);
    return clean;
  }

  function toHttpBase(base) {
    const clean = String(base || "").replace(/\/+$/, "");
    if (!clean) return "";
    if (clean.startsWith("ws://")) return "http://" + clean.slice("ws://".length);
    if (clean.startsWith("wss://")) return "https://" + clean.slice("wss://".length);
    return clean;
  }

  const EMPTY_MODEL = {
    schema_version: 1, revision: 0, exists: false,
    meta: { frame: "z_up_metric_floor0" },
    zones: [], devices: [], targets: [],
  };

  // Private models remain in process memory. Only discrete, coordinate-free
  // navigation preferences go to browser storage; the legacy draft/cache keys
  // remain Echo-owned and retain the security bootstrap's existing purge.
  const workspaces = new Map();
  let workspaceAuthority = null;
  let workspaceCredentialScope = null;
  let workspaceAuthoritySequence = 0;
  const DEFAULT_VIEW = Object.freeze({ mode: "points", az: 1, el: 0, zoom: 0 });

  function stateScope(input = {}) {
    if (!input || typeof input !== "object" || Array.isArray(input)) return null;
    const { siteId = "echo", contentRevision = null, registrationVersion = 0, frameDigest = null } = input;
    if (typeof siteId !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(siteId)
        || !Number.isInteger(registrationVersion) || registrationVersion < 0
        || (frameDigest !== null && !/^[a-f0-9]{64}$/.test(frameDigest))) return null;
    return { siteId, contentRevision, registrationVersion, frameDigest };
  }

  function cloneState(value) {
    try { return JSON.parse(JSON.stringify(value)); } catch (_) { return null; }
  }

  function modelBelongsToSite(model, siteId) {
    return [model?.site, model?.site_id, model?.source_detail?.site_id, model?.meta?.site_id]
      .every((declared) => declared === undefined || declared === null || declared === siteId);
  }

  function workspaceKey(scope) {
    return JSON.stringify([scope.siteId, scope.registrationVersion, scope.frameDigest,
      ...(scope.siteId === "victoria" ? [scope.contentRevision] : [])]);
  }

  function clearWorkspaces() {
    if (window.HomeLiveSceneShadow?.active) return 0;
    const count = workspaces.size;
    workspaces.clear();
    return count;
  }

  function setWorkspaceAuthority(authorityKey = "") {
    if (window.HomeLiveSceneShadow?.active) return false;
    const next = String(authorityKey);
    if (next === workspaceAuthority) return false;
    clearWorkspaces();
    workspaceAuthority = next;
    workspaceCredentialScope = null;
    return true;
  }

  function ensureWorkspaceAuthority({ endpoint = "", token = "", simulation = false } = {}) {
    if (window.HomeLiveSceneShadow?.active) return { key: null, changed: false, shadow: true };
    // Credentials are compared privately in this app-lifetime closure. They
    // never appear in workspace snapshots, returned keys or browser storage.
    const nextScope = {
      endpoint: typeof endpoint === "string" ? endpoint.trim().replace(/\/+$/, "") : "",
      token: typeof token === "string" ? token : "",
      simulation: simulation === true,
    };
    if (workspaceCredentialScope && workspaceCredentialScope.endpoint === nextScope.endpoint
        && workspaceCredentialScope.token === nextScope.token
        && workspaceCredentialScope.simulation === nextScope.simulation) {
      return { key: workspaceAuthority, changed: false };
    }
    let key;
    do { key = `apartment-authority-${++workspaceAuthoritySequence}`; } while (key === workspaceAuthority);
    setWorkspaceAuthority(key);
    workspaceCredentialScope = nextScope;
    return { key, changed: true };
  }

  function workspaceScope(input = {}) {
    if (!input || typeof input !== "object") return null;
    if (Object.prototype.hasOwnProperty.call(input, "authorityKey")) {
      if (workspaceAuthority === null) setWorkspaceAuthority(input.authorityKey);
      if (String(input.authorityKey) !== workspaceAuthority) return null;
    }
    return stateScope(input);
  }

  function normalizedView(scope, view = {}) {
    if (!view || typeof view !== "object") view = {};
    return {
      version: 1, ...scope,
      mode: ["points", "splat", "mesh"].includes(view.mode) ? view.mode : DEFAULT_VIEW.mode,
      az: Number.isInteger(view.az) && view.az >= 0 && view.az < 8 ? view.az : DEFAULT_VIEW.az,
      el: Number.isInteger(view.el) && view.el >= 0 && view.el < 2 ? view.el : DEFAULT_VIEW.el,
      zoom: Number.isInteger(view.zoom) && view.zoom >= 0 && view.zoom < 5 ? view.zoom : DEFAULT_VIEW.zoom,
    };
  }

  function captureWorkspace(input, { model = null, dirty = false, view = null } = {}) {
    if (window.HomeLiveSceneShadow?.active) return null;
    const scope = workspaceScope(input);
    if (!scope || rehearsalIsolated()) return null;
    const copy = cloneState(model);
    if (model !== null && (copy === null || !modelBelongsToSite(copy, scope.siteId))) return null;
    const placementWorkspace = scope.siteId === "victoria" && copy?._equipment_registration?.scope?.site_id === "victoria";
    const saved = { scope, model: copy, dirty: (scope.siteId === "echo" || placementWorkspace) && dirty === true,
      view: view ? normalizedView(scope, view) : null };
    workspaces.set(workspaceKey(scope), saved);
    return cloneState(saved);
  }

  function restoreWorkspace(input) {
    const scope = workspaceScope(input);
    if (!scope || rehearsalIsolated()) return null;
    return cloneState(workspaces.get(workspaceKey(scope)) || null);
  }

  // Which homes hold parked unsaved work under the current authority, for the
  // switcher's per-home mark. Site ids only; models never leave this closure.
  function dirtySites() {
    if (window.HomeLiveSceneShadow?.active || rehearsalIsolated()) return [];
    return [...new Set([...workspaces.values()].filter(saved => saved.dirty === true).map(saved => saved.scope.siteId))].sort();
  }

  function readViewPreference(input) {
    const scope = stateScope(input);
    if (!scope) return { ...DEFAULT_VIEW };
    try {
      const saved = JSON.parse(localStorage.getItem(VIEW_KEY_PREFIX + scope.siteId) || "null");
      if (saved?.version === 1 && saved.siteId === scope.siteId
          && saved.registrationVersion === scope.registrationVersion
          && (saved.frameDigest ?? null) === scope.frameDigest) return normalizedView(scope, saved);
    } catch (_) { /* blocked or malformed preference storage uses normal navigation */ }
    return normalizedView(scope);
  }

  function saveViewPreference(input, view) {
    if (window.HomeLiveSceneShadow?.active) return false;
    const scope = stateScope(input);
    if (!scope || rehearsalIsolated()) return false;
    const saved = normalizedView(scope, view);
    // contentRevision is useful for in-memory workspace provenance, but does
    // not belong in the preference or invalidate navigation on cosmetic packs.
    delete saved.contentRevision;
    try {
      localStorage.setItem(VIEW_KEY_PREFIX + scope.siteId, JSON.stringify(saved));
      return true;
    } catch (_) { return false; }
  }

  const MODEL_SOURCE_META = {
    simulation: { label: "Simulation", tone: "sim", writable: false, live: false },
    local_draft: { label: "Local draft", tone: "warn", writable: true, live: false },
    seed_model: { label: "Seed model", tone: "proposed", writable: true, live: false },
    live_ha_model: { label: "Live Home Assistant model", tone: "live", writable: true, live: true },
    tracker_live: { label: "Tracker / live spatial data", tone: "tracker", writable: false, live: true },
    cached_live_model: { label: "Cached live model", tone: "offline", writable: true, live: false },
    empty: { label: "No Apartment model", tone: "offline", writable: true, live: false },
    equipment_registration: { label: "Private Victoria equipment", tone: "live", writable: true, live: false },
    equipment_unavailable: { label: "Victoria equipment unavailable", tone: "offline", writable: false, live: false },
  };

  function withModelSource(model, sourceKind, detail = {}) {
    return {
      ...model,
      source_kind: sourceKind,
      source_detail: { ...(model?.source_detail || {}), ...detail },
    };
  }

  function modelSourceMeta(model) {
    if (rehearsalIsolated()) return { kind: model?.source_kind || "empty", label: "Read-only rehearsal map", tone: "sim", writable: false, live: false };
    const kind = model?.source_kind || (model?.seeded ? "seed_model" : "empty");
    const meta = { kind, ...(MODEL_SOURCE_META[kind] || MODEL_SOURCE_META.empty) };
    if (model?.source_detail?.read_only === true) meta.writable = false;
    if (kind === "equipment_unavailable" && model?.source_detail?.error) meta.label += ` · ${model.source_detail.error}`;
    if (kind === "simulation" && Number.isInteger(model?.source_detail?.layout_revision)) {
      meta.label = "Simulation · saved layout snapshot";
    }
    return meta;
  }

  // Read-only evaluation input for Simulation. This intentionally ignores the
  // recovery draft: only the last model successfully read from the
  // authoritative Apartment endpoint may seed a simulated spatial preview.
  async function getSavedLayoutSnapshot({ siteId = "echo", contentRevision } = {}) {
    if (window.HomeLiveSceneShadow?.active) return null;
    if (siteId !== "echo") return null;
    if (rehearsalIsolated()) return window.HomeMapRehearsalOfflineState.bootstrap()?.model || null;
    let cachedModel = null;
    try {
      const cached = JSON.parse(localStorage.getItem(REMOTE_CACHE_KEY) || "null");
      if (cached && modelBelongsToSite(cached, siteId) && cached.source_kind !== "simulation" && !cached?.source_detail?.isolated) {
        cachedModel = ensureModelShape(JSON.parse(JSON.stringify(cached)));
      }
    } catch (e) { /* continue to the local runtime recovery export */ }
    try {
      const response = await fetch("assets/apartment/backups/current-layout.json", { cache: "no-store" });
      if (!response.ok) return cachedModel;
      const backup = await response.json();
      const model = backup?.apartment_model || backup?.model || backup;
      const backupModel = model?.devices && model?.targets && modelBelongsToSite(model, siteId) ? ensureModelShape(model) : null;
      if (!backupModel) return cachedModel;
      if (!cachedModel || (+backupModel.revision || 0) >= (+cachedModel.revision || 0)) return backupModel;
      return cachedModel;
    } catch (e) { return cachedModel; }
  }

  const TRANSIENT_MODEL_KEYS = new Set([
    "exists", "conflict", "seeded", "tracker_cached", "remote_cached", "offline_draft",
    "calibration_enriched", "source_kind", "source_detail",
  ]);

  function modelForPersistence(model) {
    const doc = ensureModelShape(model);
    for (const key of TRANSIENT_MODEL_KEYS) delete doc[key];
    doc.schema_version = 1;
    return doc;
  }

  function stableModelValue(value) {
    if (Array.isArray(value)) return value.map(stableModelValue);
    if (!value || typeof value !== "object") return value;
    return Object.keys(value).sort().reduce((out, key) => {
      out[key] = stableModelValue(value[key]);
      return out;
    }, {});
  }

  function stableModelJson(value) {
    return JSON.stringify(stableModelValue(value));
  }

  function compareModelCollection(localItems, liveItems) {
    const local = Array.isArray(localItems) ? localItems : [];
    const live = Array.isArray(liveItems) ? liveItems : [];
    const localById = new Map(local.map((item, index) => [item?.id || `__index_${index}`, item]));
    const liveById = new Map(live.map((item, index) => [item?.id || `__index_${index}`, item]));
    const added = [];
    const removed = [];
    const changed = [];
    const unchanged = [];
    for (const [id, item] of localById) {
      if (!liveById.has(id)) added.push(id);
      else if (stableModelJson(item) !== stableModelJson(liveById.get(id))) changed.push(id);
      else unchanged.push(id);
    }
    for (const id of liveById.keys()) {
      if (!localById.has(id)) removed.push(id);
    }
    return { local: local.length, live: live.length, added, removed, changed, unchanged };
  }

  /* A read-only bypass around recovery-draft precedence. Reconciliation uses
   * this to inspect the current authoritative revision before a local draft is
   * eligible to publish. It deliberately does not mutate either browser
   * cache: comparing live state must never make the recovery draft disappear. */
  async function getAuthoritativeModel({ endpoint, token, siteId = "echo", contentRevision } = {}) {
    if (siteId !== "echo") return { ok: false, site_read_only: true, error: "This home has no configured model authority" };
    if (rehearsalIsolated()) return { ok: false, rehearsal: true, error: "Authoritative reads are disabled in rehearsal" };
    if (!endpoint || !token) return { ok: false, credentials_required: true };
    try {
      const base = endpoint.replace(/\/+$/, "");
      const fetcher = window.tauriFetch || fetch;
      const r = await fetcher(`${base}/api/extended_openai_conversation/apartment_model`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (!r.ok) return { ok: false, status: r.status, error: `HTTP ${r.status}` };
      const raw = await r.json();
      if (!raw || typeof raw.revision !== "number" || !modelBelongsToSite(raw, siteId)) {
        return { ok: false, error: "invalid Apartment model response" };
      }
      const model = withModelSource(ensureModelShape(raw), "live_ha_model", {
        endpoint: base, revision: raw.revision, read_only: true, site_id: siteId, content_revision: contentRevision,
      });
      return { ok: true, model };
    } catch (e) {
      return { ok: false, error: String(e && e.message || e) };
    }
  }

  function compareApartmentModels(localModel, liveModel) {
    const local = modelForPersistence(localModel);
    const live = modelForPersistence(liveModel);
    const collections = {
      zones: compareModelCollection(local.zones, live.zones),
      targets: compareModelCollection(local.targets, live.targets),
      devices: compareModelCollection(local.devices, live.devices),
    };
    const changeCount = Object.values(collections).reduce((total, collection) =>
      total + collection.added.length + collection.removed.length + collection.changed.length, 0);
    const sameRevision = Number.isInteger(local.revision)
      && Number.isInteger(live.revision)
      && local.revision === live.revision;
    return {
      localRevision: local.revision,
      liveRevision: live.revision,
      sameRevision,
      canPublish: sameRevision,
      hasChanges: changeCount > 0 || stableModelJson(local.meta || {}) !== stableModelJson(live.meta || {}),
      changeCount,
      collections,
    };
  }

  function spatialGeometrySnapshot(model) {
    const shaped = ensureModelShape(model);
    const pick = (item, keys) => keys.reduce((out, key) => {
      if (item?.[key] !== undefined) out[key] = stableModelValue(item[key]);
      return out;
    }, {});
    return stableModelValue({
      schema_version: 1,
      zones: (shaped.zones || []).map((zone) => pick(zone,
        ["id", "floor_polygon", "ceiling_height_m"])),
      targets: (shaped.targets || []).map((target) => pick(target,
        ["id", "category", "shape", "pos", "normal", "up", "size_m", "rotation_deg", "room_id"])),
      devices: (shaped.devices || []).map((device) => pick(device,
        ["id", "type", "pos", "yaw_rad", "room_id", "height_preset", "aiming_origin", "fixture_calibration"])),
    });
  }

  function compareSpatialGeometry(baselineModel, candidateModel) {
    const baseline = spatialGeometrySnapshot(baselineModel);
    const candidate = spatialGeometrySnapshot(candidateModel);
    const collections = {
      zones: compareModelCollection(baseline.zones, candidate.zones),
      targets: compareModelCollection(baseline.targets, candidate.targets),
      devices: compareModelCollection(baseline.devices, candidate.devices),
    };
    const changed = Object.values(collections).reduce((total, collection) =>
      total + collection.added.length + collection.removed.length + collection.changed.length, 0);
    return {
      unchanged: changed === 0,
      changed,
      collections,
      baseline_json: stableModelJson(baseline),
      candidate_json: stableModelJson(candidate),
    };
  }

  /* Deliberate link-only mutation. Geometry is compared before returning so
   * identity reconciliation cannot accidentally move a fixture, target, room,
   * or tape-derived aiming origin. Names remain suggestions; this helper never
   * chooses an entity automatically. */
  function reconcileFixtureEntityLink(model, fixtureId, nextEntityId, timestamp = new Date().toISOString()) {
    const entityId = String(nextEntityId || "").trim() || null;
    const source = ensureModelShape(JSON.parse(JSON.stringify(model || EMPTY_MODEL)));
    const fixture = (source.devices || []).find((device) => device.id === fixtureId);
    if (!fixture || fixture.type !== "light") {
      return { ok: false, error: "fixture not found" };
    }
    if (entityId && !entityId.startsWith("light.")) {
      return { ok: false, error: "fixture links require an explicit light.* entity" };
    }
    const duplicate = entityId && (source.devices || []).find((device) =>
      device.id !== fixtureId && device.ha_entity_id === entityId);
    if (duplicate) {
      return { ok: false, duplicate: true, error: `${entityId} is already linked to ${duplicate.name || duplicate.id}` };
    }
    const previousEntityId = fixture.ha_entity_id || null;
    fixture.ha_entity_id = entityId;
    fixture.link_updated_at = timestamp;
    if (entityId) delete fixture.suggested_ha_entity_id;
    else if (previousEntityId) fixture.suggested_ha_entity_id = previousEntityId;
    const geometry = compareSpatialGeometry(model, source);
    if (!geometry.unchanged) {
      return { ok: false, geometry_changed: true, error: "link reconciliation changed spatial geometry", geometry };
    }
    return {
      ok: true, model: source, fixture_id: fixtureId,
      previous_entity_id: previousEntityId, entity_id: entityId, geometry,
    };
  }

  const WALL_AXIS = {
    west: [0, 1], east: [0, -1], south: [1, 1], north: [1, -1],
  };

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function isCeilingLight(device) {
    if (!device || device.type !== "light") return false;
    if (device.ha_entity_id?.startsWith("switch.")) return false;
    return device.height_preset === "ceiling" || (Array.isArray(device.pos) && +device.pos[2] >= 2);
  }

  function zoneBounds(zone) {
    const points = zone?.floor_polygon || [];
    if (points.length < 3) return null;
    const xs = points.map((p) => +p[0]).filter(Number.isFinite);
    const ys = points.map((p) => +p[1]).filter(Number.isFinite);
    if (!xs.length || !ys.length) return null;
    return {
      west: Math.min(...xs), east: Math.max(...xs),
      south: Math.min(...ys), north: Math.max(...ys),
    };
  }

  function closestWallReferences(device, zone) {
    const bounds = zoneBounds(zone);
    const pos = device?.pos || [0, 0, 0];
    if (!bounds) return ["west", "south"];
    return [
      Math.abs(+pos[0] - bounds.west) <= Math.abs(bounds.east - +pos[0]) ? "west" : "east",
      Math.abs(+pos[1] - bounds.south) <= Math.abs(bounds.north - +pos[1]) ? "south" : "north",
    ];
  }

  function fixtureCalibrationStatus(calibration) {
    const c = calibration || {};
    const walls = (c.wall_distances || []).filter((w) => w?.wall && isFiniteNumber(w.distance_m));
    const axes = new Set(walls.map((w) => WALL_AXIS[w.wall]?.[0]).filter((v) => v != null));
    const verticalValid = isFiniteNumber(c.floor_to_ceiling_m) && c.floor_to_ceiling_m > 0
      && isFiniteNumber(c.ceiling_to_fixture_bottom_m) && c.ceiling_to_fixture_bottom_m >= 0
      && c.ceiling_to_fixture_bottom_m <= c.floor_to_ceiling_m;
    const coreComplete = walls.length >= 2 && axes.size >= 2 && verticalValid;
    const hasAnyTape = walls.length > 0
      || isFiniteNumber(c.floor_to_ceiling_m)
      || isFiniteNumber(c.ceiling_to_fixture_bottom_m)
      || isFiniteNumber(c.floor_to_bottom_verification_m);
    if (!coreComplete) return hasAnyTape ? "measured" : "proposed";
    return isFiniteNumber(c.floor_to_bottom_verification_m) ? "verified" : "calibrated";
  }

  function ensureFixtureCalibration(device, zone) {
    if (!isCeilingLight(device)) return device;
    const refs = closestWallReferences(device, zone);
    const prior = device.fixture_calibration || {};
    const wallDistances = Array.isArray(prior.wall_distances)
      ? prior.wall_distances.slice(0, 2).map((w, i) => ({
          wall: WALL_AXIS[w?.wall] ? w.wall : refs[i],
          distance_m: isFiniteNumber(w?.distance_m) ? w.distance_m : null,
        }))
      : [];
    while (wallDistances.length < 2) {
      const i = wallDistances.length;
      wallDistances.push({ wall: refs[i], distance_m: null });
    }
    const calibration = {
      ...prior,
      aiming_origin: "fixture_bottom",
      wall_distances: wallDistances,
      floor_to_ceiling_m: isFiniteNumber(prior.floor_to_ceiling_m)
        ? prior.floor_to_ceiling_m : null,
      ceiling_to_fixture_bottom_m: isFiniteNumber(prior.ceiling_to_fixture_bottom_m)
        ? prior.ceiling_to_fixture_bottom_m : null,
      floor_to_bottom_verification_m: isFiniteNumber(prior.floor_to_bottom_verification_m)
        ? prior.floor_to_bottom_verification_m : null,
    };
    calibration.wall_distances = wallDistances;
    calibration.aiming_origin = "fixture_bottom";
    calibration.status = fixtureCalibrationStatus(calibration);
    if (isFiniteNumber(calibration.floor_to_ceiling_m) && calibration.floor_to_ceiling_m > 0
        && isFiniteNumber(calibration.ceiling_to_fixture_bottom_m)
        && calibration.ceiling_to_fixture_bottom_m >= 0
        && calibration.ceiling_to_fixture_bottom_m <= calibration.floor_to_ceiling_m) {
      calibration.derived_floor_to_bottom_m =
        calibration.floor_to_ceiling_m - calibration.ceiling_to_fixture_bottom_m;
      calibration.verification_error_m = isFiniteNumber(calibration.floor_to_bottom_verification_m)
        ? calibration.floor_to_bottom_verification_m - calibration.derived_floor_to_bottom_m
        : null;
    } else {
      calibration.derived_floor_to_bottom_m = null;
      calibration.verification_error_m = null;
    }
    return { ...device, aiming_origin: "fixture_bottom", fixture_calibration: calibration };
  }

  function ensureModelShape(model) {
    const src = model && typeof model === "object" ? model : EMPTY_MODEL;
    const zones = Array.isArray(src.zones) ? src.zones : [];
    const byId = new Map(zones.map((z) => [z.id, z]));
    return {
      ...src,
      zones,
      devices: (Array.isArray(src.devices) ? src.devices : []).map((device) => {
        const measured = ensureFixtureCalibration(device, byId.get(device.room_id));
        return window.HomeApartmentAiming?.normalizeEngineeredFixture
          ? window.HomeApartmentAiming.normalizeEngineeredFixture(measured)
          : measured;
      }),
      targets: Array.isArray(src.targets) ? src.targets : [],
    };
  }

  function adoptEngineeredFixture(device) {
    if (!device || device.fixture_kind === "engineered_gimbal_v1") return device;
    const defaults = window.HomeApartmentAiming?.defaultEngineeredFixtureFields?.();
    if (!defaults) throw new Error("Apartment aiming module is unavailable");
    return window.HomeApartmentAiming.normalizeEngineeredFixture({ ...device, ...defaults });
  }

  function validateEngineeredMappings(model) {
    return window.HomeApartmentAiming?.validateEntityMappings?.(model?.devices || [])
      || { ok: true, duplicates: [], uses: new Map() };
  }

  function validateEngineeredFixtureModel(model) {
    const mapping = validateEngineeredMappings(model);
    const errors = mapping.duplicates.map((d) => `duplicate engineered mapping ${d.entity_id}`);
    const sha = /^[0-9a-f]{64}$/i;
    for (const device of model?.devices || []) {
      if (device?.fixture_kind !== "engineered_gimbal_v1") continue;
      const fwhm = +device.spotlight?.optic_profile?.configured_fwhm_deg;
      if (!(fwhm > 0 && fwhm < 180)) errors.push(`${device.id}: configured FWHM must be between 0 and 180 degrees`);
      const zones = device.radial_zones?.zones;
      if (!Array.isArray(zones) || zones.length !== 6
          || zones.map((z) => +z.number).sort().join(",") !== "1,2,3,4,5,6") {
        errors.push(`${device.id}: exactly six stable radial zone numbers are required`);
      }
      const profile = device.gimbal?.product_profile_sha256;
      if (profile != null && !sha.test(profile)) errors.push(`${device.id}: Product profile must be a full SHA-256`);
      const binding = device.gimbal?.device_binding;
      if (binding != null && (typeof binding !== "object" || !String(binding.stable_id || "").trim())) {
        errors.push(`${device.id}: stable gimbal device binding required`);
      }
      const cal = device.gimbal?.visualization_calibration;
      if (cal) {
        if (cal.pan_sign !== 1 && cal.pan_sign !== -1) errors.push(`${device.id}: explicit pan sign required`);
        if (cal.tilt_sign !== 1 && cal.tilt_sign !== -1) errors.push(`${device.id}: explicit tilt sign required`);
        if (!sha.test(cal.collision_geometry_sha256 || "")) errors.push(`${device.id}: full collision SHA-256 required`);
        if (!Number.isInteger(cal.based_on_model_revision)) errors.push(`${device.id}: calibration model revision required`);
      }
      const radial = device.radial_zones?.orientation_calibration;
      if (radial && (!Number.isInteger(radial.based_on_model_revision)
          || ![1, 2, 3, 4, 5, 6].includes(+radial.anchor_zone)
          || !["clockwise", "counterclockwise"].includes(radial.order))) {
        errors.push(`${device.id}: radial orientation calibration is incomplete`);
      }
    }
    return { ok: errors.length === 0, errors, mapping };
  }

  function reconcileFixturePosition(device, zone) {
    if (!isCeilingLight(device)) return device;
    const normalized = ensureFixtureCalibration(device, zone);
    const calibration = normalized.fixture_calibration;
    const bounds = zoneBounds(zone);
    const pos = Array.isArray(normalized.pos) ? [...normalized.pos] : [0, 0, 0];
    if (bounds) {
      for (const measurement of calibration.wall_distances || []) {
        const rule = WALL_AXIS[measurement?.wall];
        if (!rule || !isFiniteNumber(measurement.distance_m)) continue;
        const [axis, sign] = rule;
        const wallCoordinate = bounds[measurement.wall];
        pos[axis] = wallCoordinate + sign * measurement.distance_m;
      }
    }
    if (isFiniteNumber(calibration.derived_floor_to_bottom_m)) {
      pos[2] = calibration.derived_floor_to_bottom_m;
    }
    return {
      ...normalized,
      pos,
      aiming_origin: "fixture_bottom",
      fixture_calibration: {
        ...calibration,
        status: fixtureCalibrationStatus(calibration),
        updated_at: new Date().toISOString(),
      },
    };
  }

  function parseTapeMeasurement(raw) {
    if (isFiniteNumber(raw)) return raw * 0.0254;
    const text = String(raw ?? "").trim().toLowerCase()
      .replace(/[\u2032\u2018\u2019]/g, "'").replace(/[\u2033\u201c\u201d]/g, '"');
    if (!text) return null;
    const metric = text.match(/^(-?\d+(?:\.\d+)?)\s*(m|cm|mm)$/);
    if (metric) {
      const value = +metric[1];
      return metric[2] === "m" ? value : metric[2] === "cm" ? value / 100 : value / 1000;
    }
    const feet = text.match(/^\s*(\d+(?:\.\d+)?)\s*(?:'|ft|feet)\s*(?:(\d+(?:\.\d+)?)\s*(?:\"|in|inches?)?)?\s*$/);
    if (feet) return (+feet[1] * 12 + +(feet[2] || 0)) * 0.0254;
    const inches = text.match(/^(-?\d+(?:\.\d+)?)\s*(?:\"|in|inches?)?$/);
    return inches ? +inches[1] * 0.0254 : NaN;
  }

  function formatTapeMeasurement(meters) {
    if (!isFiniteNumber(meters)) return "";
    const totalInches = meters / 0.0254;
    const feet = Math.floor(totalInches / 12);
    const inches = totalInches - feet * 12;
    return `${feet}\u2032 ${inches.toFixed(1)}\u2033`;
  }

  function trackerBase() {
    try {
      const resolved = window.HomeServices?.get?.("tracker");
      if (resolved) return resolved;
    } catch (e) { /* */ }
    try { return localStorage.getItem(TRACKER_KEY) || defaultTrackerBase(); }
    catch (e) { return defaultTrackerBase(); }
  }

  /* seed-model.json — generated device/zone inventory (55_seed_model.py).
   * Tries the data dir (asset protocol manual URL) then the bundled copy. */
  /*
   * The seed model for ONE home.
   *
   * seed-model.json is a per-site content-pack asset: Echo's packs carry one and
   * Victoria's do not. Every path below this comment is an Echo path -- two
   * `assets/apartment/...` relatives and an absolute
   * `C:/Claude/home/app/data/apartment/...` -- so without a site the function
   * answered for Echo whoever was asking, and a second home would have been
   * furnished with Echo's zones, devices and targets. That is invisible in the
   * data (a seeded model looks like a real one) and only shows up as another
   * home's furniture in your house.
   *
   * With a siteId, the pack is the answer and its absence is also an answer:
   * a home whose pack has no seed model gets null, not Echo's. Without one the
   * legacy behaviour is preserved exactly, so existing callers do not shift.
   */
  async function fetchSeed(siteId, contentRevision) {
    const urls = [];
    if (siteId) {
      /*
       * The site's own pack, and ONLY it. Falling through to the paths below on
       * a miss or a failed fetch would hand this home Echo's furniture, which
       * is the exact failure this argument exists to prevent -- so a site that
       * has no seed model, or whose fetch fails, gets null.
       */
      let packUrl = null;
      try { packUrl = window.HomeContentPacks?.resolve?.(siteId, "seed-model.json", contentRevision) || null; } catch (e) { /* */ }
      if (!packUrl) return null;
      try {
        const fetcher = ((window.IS_TAURI || window.__TAURI__) && window.tauriFetch) || fetch;
        const r = await fetcher(packUrl, { cache: "no-store" });
        if (r.ok) {
          const seed = await r.json();
          return modelBelongsToSite(seed, siteId) ? seed : null;
        }
      } catch (e) { /* fall through to null, never to another home */ }
      return null;
    }
    try {
      const resolved = window.HomeServices?.get?.("apartmentAssets");
      if (resolved) urls.push(`${String(resolved).replace(/\/+$/, "")}/seed-model.json`);
    } catch (e) { /* */ }
    if (window.HG_WEB_MODE && window.HG_DEFAULT_APARTMENT_ASSET_BASE) {
      urls.push(`${String(window.HG_DEFAULT_APARTMENT_ASSET_BASE).replace(/\/+$/, "")}/seed-model.json`);
    } else if (window.IS_TAURI || window.__TAURI__) {
      urls.push(`http://asset.localhost/${encodeURIComponent("C:/Claude/home/app/data/apartment/seed-model.json")}`);
    } else {
      urls.push("assets/apartment/seed-model.json");
    }
    urls.push("assets/apartment/seed-model.json");
    for (const u of urls) {
      try {
        const fetcher = ((window.IS_TAURI || window.__TAURI__) && window.tauriFetch) || fetch;
        const r = await fetcher(u, { cache: "no-store" });
        if (r.ok) {
          const seed = await r.json();
          if (modelBelongsToSite(seed, "echo")) return seed;
        }
      } catch (e) { /* next */ }
    }
    return null;
  }

  async function getSeedModel(siteId, { contentRevision } = {}) {
    if (siteId && typeof siteId === "object") ({ siteId = "echo", contentRevision } = siteId);
    if (siteId === undefined && contentRevision !== undefined) siteId = "echo";
    if (siteId !== undefined && (typeof siteId !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(siteId))) return null;
    if (rehearsalIsolated()) return null;
    const seed = await fetchSeed(siteId, contentRevision);
    if (seed && !modelBelongsToSite(seed, siteId || "echo")) return null;
    return seed ? ensureModelShape(withModelSource(seed, "seed_model", {
      site_id: siteId || "echo", content_revision: contentRevision, read_only: !!siteId && siteId !== "echo",
    })) : null;
  }

  async function fetchTrackerModel() {
    const bases = [];
    try { bases.push(defaultTrackerBase()); } catch (e) { /* */ }
    try {
      const resolved = window.HomeServices?.get?.("tracker");
      if (resolved) bases.push(resolved);
    } catch (e) { /* */ }
    for (const raw of [...new Set(bases.filter(Boolean))]) {
      const base = toHttpBase(raw);
      if (!base) continue;
      try {
        const fetcher = ((window.IS_TAURI || window.__TAURI__) && window.tauriFetch) || fetch;
        const r = await fetcher(`${base}/model`, { cache: "no-store" });
        if (!r.ok) continue;
        const model = await r.json();
        if (model && Array.isArray(model.devices) && modelBelongsToSite(model, "echo")) {
          try { localStorage.setItem(REMOTE_CACHE_KEY, JSON.stringify(model)); } catch (e) { /* */ }
          return { ...model, tracker_cached: true };
        }
      } catch (e) { /* next */ }
    }
    return null;
  }

  function cameraCalibrationComplete(dev) {
    const intr = dev?.camera?.intrinsics;
    const extr = dev?.camera?.extrinsics;
    return !!(intr?.K && Array.isArray(intr.image_size)
      && extr?.q_wxyz && Array.isArray(extr.C));
  }

  function cameraKeys(dev) {
    return [dev?.id, dev?.camera?.frigate_name, dev?.ha_entity_id].filter(Boolean);
  }

  function mergeTrackerCameraCalibration(model, trackerModel) {
    if (!model || !Array.isArray(model.devices)
        || !trackerModel || !Array.isArray(trackerModel.devices)) return model;
    const byKey = new Map();
    for (const dev of trackerModel.devices) {
      if (!cameraCalibrationComplete(dev)) continue;
      for (const key of cameraKeys(dev)) byKey.set(key, dev);
    }
    let changed = false;
    const devices = model.devices.map((dev) => {
      const trackerDev = cameraKeys(dev).map((key) => byKey.get(key)).find(Boolean);
      if (!trackerDev) return dev;
      const trackerCamera = trackerDev.camera || {};
      const nextCamera = { ...(dev.camera || {}) };
      let cameraChanged = false;
      for (const key of ["intrinsics", "extrinsics"]) {
        if (!trackerCamera[key]) continue;
        if (JSON.stringify(nextCamera[key] || null) === JSON.stringify(trackerCamera[key])) continue;
        nextCamera[key] = trackerCamera[key];
        cameraChanged = true;
      }
      if (!cameraChanged) return dev;
      changed = true;
      return { ...dev, camera: nextCamera };
    });
    return changed ? { ...model, devices, calibration_enriched: true } : model;
  }

  /* ---------------- model ---------------- */

  const EQUIPMENT_REGISTRATION_URL = "/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration";

  async function equipmentDigest(value) {
    const api = window.HomeEquipmentRegistration;
    if (!api?.canonicalJson || !globalThis.crypto?.subtle) throw new Error("registration_verifier_unavailable");
    const bytes = new TextEncoder().encode(api.canonicalJson(value));
    const hash = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(hash), (v) => v.toString(16).padStart(2, "0")).join("");
  }

  async function equipmentBinding({ siteId, contentRevision, registrationVersion, frameDigest } = {}) {
    if (siteId !== "victoria" || rehearsalIsolated()) throw new Error("registration_scope_denied");
    const pack = window.HomeContentPacks?.get?.(siteId, contentRevision)?.manifest;
    if (!pack || pack.siteId !== siteId || pack.revision !== contentRevision
        || !pack.assets?.["frame.json"] || !pack.assets?.["collision.glb"]
        || registrationVersion !== undefined && pack.registrationVersion !== registrationVersion
        || frameDigest && pack.assets["frame.json"].sha256 !== frameDigest) throw new Error("registration_pack_mismatch");
    return { content_revision: pack.revision, registration_version: pack.registrationVersion,
      manifest_digest: await equipmentDigest(pack), frame_digest: pack.assets["frame.json"].sha256,
      canonical_collision_digest: pack.assets["collision.glb"].sha256 };
  }

  async function verifyEquipmentResponse(payload, binding) {
    const doc = payload?.registration;
    const result = window.HomeEquipmentRegistration?.validateDocument?.(doc, { binding });
    if (!payload?.ok || !result?.ok || !/^[a-f0-9]{64}$/.test(doc?.digest || "")) throw new Error("registration_response_invalid");
    const unsigned = { ...doc }; delete unsigned.digest;
    if (await equipmentDigest(unsigned) !== doc.digest) throw new Error("registration_digest_mismatch");
    return doc;
  }

  async function getEquipmentRegistration(options = {}) {
    const binding = await equipmentBinding(options);
    const revision = options.revision;
    if (revision !== undefined && (!Number.isSafeInteger(revision) || revision < 0)) throw new Error("registration_revision_invalid");
    const url = EQUIPMENT_REGISTRATION_URL + (revision === undefined ? "" : `/revisions/${revision}`);
    const response = await fetch(url, { signal: options.signal, cache: "no-store", credentials: "same-origin", redirect: "error" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.error?.code || payload?.error || "registration_unavailable");
    const registration = await verifyEquipmentResponse(payload, binding);
    if (options.digest && options.digest !== registration.digest) throw new Error("registration_snapshot_mismatch");
    return { registration, capabilities: payload.capabilities || { read: true, write: false } };
  }

  async function getEquipmentModel(options) {
    try {
      const { registration, capabilities } = await getEquipmentRegistration(options);
      return window.HomeEquipmentRegistration.toModel(registration, { writable: capabilities.write === true });
    } catch (error) {
      return withModelSource({ ...cloneState(EMPTY_MODEL), site: "victoria" }, "equipment_unavailable", {
        site_id: "victoria", read_only: true, error: error.message || "registration_unavailable",
      });
    }
  }

  async function saveEquipmentModel(model, options) {
    if (options.sim || rehearsalIsolated()) return { ok: false, rehearsal: true, error: "Placement writes are unavailable during rehearsal" };
    try {
      const binding = await equipmentBinding(options);
      if (!modelBelongsToSite(model, "victoria") || model.source_detail?.read_only === true) throw new Error("registration_scope_denied");
      const doc = window.HomeEquipmentRegistration?.toDocument(model);
      if (!window.HomeEquipmentRegistration?.validateDocument(doc, { binding, verifyDigest: false, verifyPositions: false }).ok) throw new Error("registration_document_invalid");
      const response = await fetch(EQUIPMENT_REGISTRATION_URL, {
        method: "PUT", signal: options.signal, cache: "no-store", credentials: "same-origin", redirect: "error",
        headers: { "Content-Type": "application/json", "X-Home-Equipment-CSRF": "1" }, body: JSON.stringify(doc),
      });
      const payload = await response.json();
      if (response.status === 409) return { ok: false, conflict: true, stored: payload.registration || payload.stored,
        error: "Equipment changed elsewhere; your session edits are preserved" };
      if (!response.ok) return { ok: false, error: payload?.error?.code || payload?.error || "registration_save_failed" };
      const registration = await verifyEquipmentResponse(payload, binding);
      return { ok: true, revision: registration.revision, registration,
        model: window.HomeEquipmentRegistration.toModel(registration, { writable: payload.capabilities?.write === true }) };
    } catch (error) { return { ok: false, error: error.message || "registration_save_failed" }; }
  }

  async function getModel({ endpoint, token, sim, siteId = "echo", contentRevision, equipmentRegistration = false,
    registrationVersion, frameDigest, signal } = {}) {
    if (window.HomeLiveSceneShadow?.active) return cloneState(window.HomeLiveResidentModel || EMPTY_MODEL);
    if (siteId === "victoria" && equipmentRegistration && !sim && !rehearsalIsolated()) {
      return getEquipmentModel({ siteId, contentRevision, registrationVersion, frameDigest, signal });
    }
    // Importing another home's scene grants no Echo model/tracker authority.
    // This branch is before simulation and legacy recovery storage too.
    if (siteId !== "echo") {
      const seed = rehearsalIsolated() ? null : await getSeedModel(siteId, { contentRevision });
      return seed || withModelSource(ensureModelShape(cloneState(EMPTY_MODEL)), "empty", {
        site_id: siteId, content_revision: contentRevision, read_only: true,
      });
    }
    if (rehearsalIsolated()) {
      try {
        const value = await window.HomeMapRehearsalBinding.loadBootstrap();
        return value.model ? JSON.parse(JSON.stringify(value.model)) : JSON.parse(JSON.stringify(EMPTY_MODEL));
      } catch (_) { return ensureModelShape(JSON.parse(JSON.stringify(EMPTY_MODEL))); }
    }
    if (sim) {
      try {
        if (typeof window.__SIM_APARTMENT_MODEL_FIXTURE === "function") {
          const savedLayout = await getSavedLayoutSnapshot({ siteId, contentRevision });
          return withModelSource(
            ensureModelShape(window.__SIM_APARTMENT_MODEL_FIXTURE(savedLayout) || EMPTY_MODEL),
            "simulation",
            { isolated: true, layout_source: savedLayout ? "cached_live_model" : "synthetic",
              layout_revision: Number.isInteger(savedLayout?.revision) ? savedLayout.revision : undefined,
              read_only_layout: !!savedLayout },
          );
        }
      } catch (e) { /* */ }
      return withModelSource(ensureModelShape({ ...EMPTY_MODEL, exists: true }), "simulation", { isolated: true });
    }
    // A draft only remains after an explicit offline/failed save. Recover it
    // before consulting any live source so reconnecting or reloading cannot
    // make unsaved apartment work appear to vanish. A successful authoritative
    // save removes this key.
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      if (draft && modelBelongsToSite(draft, siteId)) return withModelSource(ensureModelShape({ ...draft, offline_draft: true }), "local_draft", {
        revision: draft.revision,
      });
    } catch (e) { /* */ }
    if (endpoint && token) {
      try {
        const base = endpoint.replace(/\/+$/, "");
        const fetcher = window.tauriFetch || fetch;
        const r = await fetcher(`${base}/api/extended_openai_conversation/apartment_model`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        });
        if (r.ok) {
          const model = await r.json();
          if (model && typeof model.revision === "number" && modelBelongsToSite(model, siteId)) {
            // Empty model -> merge the generated seed (real light/camera
            // inventory at proposed positions). Saving in edit mode persists
            // it for real; until then it's an in-memory overlay.
            if (!(model.zones || []).length && !(model.devices || []).length) {
              const seed = await fetchSeed(contentRevision === undefined ? undefined : siteId, contentRevision);
              if (seed) return withModelSource(ensureModelShape({
                ...model, zones: seed.zones, devices: seed.devices,
                targets: seed.targets || [], seeded: true,
              }), "seed_model", { authoritative_revision: model.revision, ready_to_save: true });
            }
            const camerasNeedCalibration = (model.devices || []).some((d) =>
              d?.camera?.frigate_name && !cameraCalibrationComplete(d));
            const enriched = camerasNeedCalibration
              ? mergeTrackerCameraCalibration(model, await fetchTrackerModel())
              : model;
            // stash the last-good REMOTE doc — boot races (tauriFetch not
            // ready yet) must fall back to THIS, never to the seed, or the
            // user sees their edits "overwritten" until the next remote load
            const prepared = ensureModelShape(enriched);
            const sourced = withModelSource(prepared, "live_ha_model", {
              endpoint: base, revision: prepared.revision,
            });
            try { localStorage.setItem(REMOTE_CACHE_KEY, JSON.stringify(sourced)); } catch (e) { /* */ }
            return sourced;
          }
        }
      } catch (e) { /* fall through */ }
    }
    const trackerModel = await fetchTrackerModel();
    if (trackerModel) return withModelSource(ensureModelShape(trackerModel), "tracker_live", {
      revision: trackerModel.revision,
    });
    // Without a recovery draft, fall back to the last-good remote copy and
    // finally the generated seed.
    // The remote cache outranks everything — a boot race must show the
    // user's real saved layout, not the seed.
    try {
      const cached = JSON.parse(localStorage.getItem(REMOTE_CACHE_KEY) || "null");
      if (cached && modelBelongsToSite(cached, siteId)) return withModelSource(ensureModelShape({ ...cached, remote_cached: true }), "cached_live_model", {
        revision: cached.revision,
      });
    } catch (e) { /* */ }
    const seed = await fetchSeed(contentRevision === undefined ? undefined : siteId, contentRevision);
    if (seed) return withModelSource(ensureModelShape({
      ...EMPTY_MODEL, zones: seed.zones, devices: seed.devices,
      targets: seed.targets || [], seeded: true,
    }), "seed_model", { ready_to_save: false });
    return withModelSource(ensureModelShape(EMPTY_MODEL), "empty");
  }

  /* Save the full model. Returns {ok, revision} | {conflict, stored} | {offline}. */
  async function saveModel(model, { endpoint, token, sim, siteId = "echo", contentRevision,
    equipmentRegistration = false, registrationVersion, frameDigest, signal } = {}) {
    if (siteId === "victoria" && equipmentRegistration && model?._equipment_registration) {
      return saveEquipmentModel(model, { siteId, contentRevision, registrationVersion, frameDigest, sim, signal });
    }
    if (siteId !== "echo" || !modelBelongsToSite(model, siteId)) {
      return { ok: false, site_read_only: true, error: "This home has no configured model authority" };
    }
    if (rehearsalIsolated()) return { ok: false, rehearsal: true, error: "Saved Apartment state is read-only during rehearsal" };
    // Simulation is fully isolated: it cannot POST to HA and cannot replace
    // the non-simulation recovery draft or last-good live cache.
    if (sim) return { ok: false, sim: true };
    const validation = validateEngineeredFixtureModel(model);
    if (!validation.ok) return { ok: false, validation: true, error: validation.errors.join("; ") };
    const doc = modelForPersistence(model);
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(doc)); } catch (e) { /* */ }
    if (!endpoint || !token) return { ok: false, offline: true };
    try {
      const base = endpoint.replace(/\/+$/, "");
      const fetcher = window.tauriFetch || fetch;
      const r = await fetcher(`${base}/api/extended_openai_conversation/apartment_model`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      });
      if (r.status === 409) {
        const stored = await r.json();
        return { ok: false, conflict: true, stored };
      }
      if (!r.ok) return { ok: false, error: `HTTP ${r.status}` };
      const res = await r.json();
      const cached = withModelSource({ ...doc, revision: res.revision, updated_at: res.updated_at },
        "live_ha_model", { endpoint: base, revision: res.revision });
      try {
        localStorage.setItem(REMOTE_CACHE_KEY, JSON.stringify(cached));
        localStorage.removeItem(DRAFT_KEY);
      } catch (e) { /* */ }
      return { ok: true, revision: res.revision, updated_at: res.updated_at };
    } catch (e) {
      return { ok: false, error: String(e && e.message || e) };
    }
  }

  /* ---------------- HA registries (edit-mode palette) ---------------- */

  async function getRegistry(client, { siteId = "echo" } = {}) {
    if (siteId !== "echo") return { entities: [], areas: [], devices: [], states: {} };
    if (rehearsalIsolated()) return { entities: [], areas: [], devices: [], states: {} };
    // entity + area + device registries via the existing HAClient WS
    const out = { entities: [], areas: [], devices: [], states: {} };
    if (!client) return out;
    try {
      const [ents, areas, devs, states] = await Promise.all([
        client.call({ type: "config/entity_registry/list" }),
        client.call({ type: "config/area_registry/list" }),
        client.call({ type: "config/device_registry/list" }),
        client.call({ type: "get_states" }),
      ]);
      out.entities = ents || [];
      out.areas = areas || [];
      out.devices = devs || [];
      for (const s of states || []) out.states[s.entity_id] = s;
    } catch (e) {
      console.warn("[apartment] registry fetch failed", e);
    }
    return out;
  }

  /* Palette: placeable HA entities grouped by area, minus already-bound ids. */
  function buildPalette(registry, model) {
    const bound = new Set((model.devices || []).map((d) => d.ha_entity_id).filter(Boolean));
    const areaName = {};
    for (const a of registry.areas) areaName[a.area_id] = a.name;
    const deviceArea = {};
    for (const d of registry.devices) deviceArea[d.id] = d.area_id;
    const groups = {};
    for (const e of registry.entities) {
      const domain = e.entity_id.split(".")[0];
      if (!["light", "media_player", "camera", "switch"].includes(domain)) continue;
      if (bound.has(e.entity_id)) continue;
      if (e.disabled_by || e.hidden_by) continue;
      const area = areaName[e.area_id || deviceArea[e.device_id]] || "unassigned";
      (groups[area] = groups[area] || []).push({
        entity_id: e.entity_id,
        name: e.name || e.original_name ||
          (registry.states[e.entity_id]?.attributes?.friendly_name) || e.entity_id,
        domain,
      });
    }
    return groups;
  }

  function registryLightEntities(registry = {}) {
    const areaName = {};
    for (const area of registry.areas || []) areaName[area.area_id] = area.name;
    const deviceArea = {};
    for (const device of registry.devices || []) deviceArea[device.id] = device.area_id;
    return (registry.entities || [])
      .filter((entity) => entity?.entity_id?.startsWith("light.") && !entity.disabled_by && !entity.hidden_by)
      .map((entity) => ({
        entity_id: entity.entity_id,
        name: entity.name || entity.original_name
          || registry.states?.[entity.entity_id]?.attributes?.friendly_name || entity.entity_id,
        area_id: entity.area_id || deviceArea[entity.device_id] || null,
        area_name: areaName[entity.area_id || deviceArea[entity.device_id]] || "unassigned",
        device_id: entity.device_id || null,
        state: registry.states?.[entity.entity_id] || null,
      }))
      .sort((a, b) => `${a.area_name}/${a.name}`.localeCompare(`${b.area_name}/${b.name}`));
  }

  function normalizedFixtureName(value) {
    return String(value || "").toLowerCase()
      .replace(/\b(light|lights|fixture|ceiling|lamp)\b/g, " ")
      .replace(/[^a-z0-9]+/g, " ").trim();
  }

  function buildFixtureMapping(registry = {}, model = EMPTY_MODEL, seedModel = null) {
    const devices = Array.isArray(model.devices) ? model.devices : [];
    const mappedByEntity = new Map();
    for (const device of devices) {
      if (!device?.ha_entity_id) continue;
      const list = mappedByEntity.get(device.ha_entity_id) || [];
      list.push(device);
      mappedByEntity.set(device.ha_entity_id, list);
    }
    const allLights = registryLightEntities(registry);
    const registryAvailable = Array.isArray(registry.entities) && registry.entities.length > 0;
    const liveLightIds = new Set(allLights.map((entity) => entity.entity_id));
    const unplacedLights = allLights.filter((entity) => !mappedByEntity.has(entity.entity_id));
    const mappedFixtures = devices.filter(isCeilingLight);
    const nonFixtureLights = devices.filter((device) => device?.type === "light" && !isCeilingLight(device));
    const duplicateLinks = [...mappedByEntity.entries()]
      .filter(([, linked]) => linked.length > 1)
      .map(([entity_id, linked]) => ({ entity_id, device_ids: linked.map((device) => device.id) }));
    const unresolvedFixtureLinks = mappedFixtures
      .filter((fixture) => !fixture.ha_entity_id
        || (registryAvailable && !liveLightIds.has(fixture.ha_entity_id)))
      .map((fixture) => {
        const normalized = normalizedFixtureName(fixture.name);
        const suggestions = unplacedLights.filter((entity) =>
          normalized && normalizedFixtureName(entity.name) === normalized);
        return {
          fixture,
          reason: fixture.ha_entity_id ? "entity_not_found" : "unlinked",
          suggestions,
        };
      });
    const modelIds = new Set(devices.map((device) => device.id).filter(Boolean));
    const seedFixtures = (seedModel?.devices || []).filter(isCeilingLight);
    const unresolvedSeedFixtures = seedFixtures
      .filter((seed) => !modelIds.has(seed.id)
        && !(seed.ha_entity_id && mappedByEntity.has(seed.ha_entity_id)))
      .map((seed) => {
        const normalized = normalizedFixtureName(seed.name);
        const suggestions = unplacedLights.filter((entity) =>
          normalized && normalizedFixtureName(entity.name) === normalized);
        return { seed, suggestions };
      });
    return {
      allLights, mappedFixtures, nonFixtureLights, unplacedLights,
      duplicateLinks, unresolvedFixtureLinks, unresolvedSeedFixtures, registryAvailable,
    };
  }

  /* ---------------- entity state binding ---------------- */

  async function readStates(client, ids = [], { siteId = "echo" } = {}) {
    if (siteId !== "echo") return {};
    if (rehearsalIsolated()) return {};
    if (!client || typeof client.call !== "function") return {};
    const wanted = new Set((ids || []).filter(Boolean));
    const states = await client.call({ type: "get_states" });
    const out = {};
    for (const s of states || []) {
      if (!s?.entity_id) continue;
      if (!wanted.size || wanted.has(s.entity_id)) out[s.entity_id] = s;
    }
    return out;
  }

  function bindStates(client, model, onState, { siteId = "echo", isCurrent = () => true, signal } = {}) {
    if (siteId !== "echo" || signal?.aborted || !isCurrent()) return () => {};
    if (rehearsalIsolated()) return () => {};
    if (!client) return () => {};
    const ids = new Set((model.devices || []).map((d) => d.ha_entity_id).filter(Boolean));
    // Travel Mode is a control guard, even when this model has no devices.
    // Observe the existing helper through this same owned subscription.
    ids.add(TRAVEL_MODE_ENTITY);
    let unsub = null, closed = false;
    const authority = workspaceAuthority;
    const current = () => {
      const selected = window.HomeSpatialViewport?.getSelectedSiteId?.();
      return !closed && !signal?.aborted && !rehearsalIsolated() && isCurrent()
        && workspaceAuthority === authority && (!selected || selected === "echo")
        && (!window.__hav_haClient || window.__hav_haClient === client);
    };
    const close = () => {
      if (closed) return;
      closed = true;
      const stop = unsub;
      unsub = null;
      try { stop?.(); } catch (e) { /* */ }
    };
    signal?.addEventListener?.("abort", close, { once: true });
    (async () => {
      try {
        const states = await readStates(client, [...ids], { siteId });
        if (!current()) return;
        publishTravelModeState(states[TRAVEL_MODE_ENTITY]?.state);
        for (const [entityId, state] of Object.entries(states)) {
          if (!current()) return;
          onState(entityId, state);
        }
        if (!current()) return;
        const nextUnsub = await client.subscribeEvents("state_changed", (ev) => {
          if (!current()) return;
          const d = ev?.data;
          if (d?.entity_id === TRAVEL_MODE_ENTITY) publishTravelModeState(d.new_state?.state);
          if (current() && d && ids.has(d.entity_id) && d.new_state) onState(d.entity_id, d.new_state);
        });
        if (!current()) nextUnsub?.(); else unsub = nextUnsub;
      } catch (e) {
        console.warn("[apartment] state binding failed", e);
      }
    })();
    return () => { signal?.removeEventListener?.("abort", close); close(); };
  }

  function buildServicePayload(domain, service, data = {}) {
    const targetKeys = new Set(["entity_id", "area_id", "device_id"]);
    const target = {};
    const serviceData = {};
    for (const [key, value] of Object.entries(data || {})) {
      if (value == null) continue;
      if (targetKeys.has(key)) target[key] = value;
      else serviceData[key] = value;
    }
    const payload = { type: "call_service", domain, service, service_data: serviceData };
    if (Object.keys(target).length > 0) payload.target = target;
    return payload;
  }

  const TRAVEL_MODE_ENTITY = "input_boolean.living_lights_travel_mode";
  const TRAVEL_MODE_CACHE_KEY = "home.lights.travelMode.state.v1";

  function publishTravelModeState(state) {
    const normalized = state === "on" || state === "off" ? state : null;
    // Unknown withdraws a previous global off, while retaining any cached on
    // until an actual current helper observation confirms off.
    window.__HOME_TRAVEL_MODE_STATE = normalized;
    try { window.dispatchEvent?.(new CustomEvent("home-travel-mode-change", { detail: { state: normalized } })); } catch (_) { /* older webview */ }
    try {
      const storage = window.localStorage || localStorage;
      if (normalized === "on") storage.setItem(TRAVEL_MODE_CACHE_KEY, "on");
      else if (normalized === "off") storage.removeItem(TRAVEL_MODE_CACHE_KEY);
    } catch (_) { /* unavailable browser storage does not change live state */ }
  }

  function serviceMayEnergizeLights(domain, service) {
    const kind = String(domain || "").toLowerCase(), action = String(service || "").toLowerCase();
    if (kind === "scene") return action === "turn_on" || action === "apply";
    // An arbitrary script or aggregate HA target can include lighting. The
    // Apartment does not have a reviewed effect map for either kind.
    if (kind === "script") return action !== "turn_off";
    return ["light", "switch", "homeassistant"].includes(kind) && ["turn_on", "toggle"].includes(action);
  }

  function cachedTravelModeOn() {
    if (window.__HOME_TRAVEL_MODE_STATE === "on") return true;
    try { return (window.localStorage || localStorage).getItem(TRAVEL_MODE_CACHE_KEY) === "on"; }
    catch (_) { return false; }
  }

  async function verifyTravelModeForOutput(client, isCurrent) {
    if (typeof isCurrent !== "function") throw new Error("Current Home control context is required for lighting output");
    const authority = workspaceAuthority;
    const requireCurrent = () => {
      const selected = window.HomeSpatialViewport?.getSelectedSiteId?.();
      if (isCurrent() !== true || rehearsalIsolated() || workspaceAuthority !== authority
          || selected && selected !== "echo" || window.__hav_haClient && window.__hav_haClient !== client) {
        throw new Error("Home control context changed; no command was sent");
      }
    };
    requireCurrent();
    if (typeof client.call !== "function") throw new Error("Travel Mode cannot be verified; lighting output is blocked");
    const initiallyCachedOn = cachedTravelModeOn();
    let observedOn = false, observedUnknown = false, timeout;
    const travelChanged = event => {
      if (event?.detail?.state === "on") observedOn = true;
      else if (event?.detail?.state !== "off") observedUnknown = true;
    };
    const storageChanged = event => { if (event?.key === TRAVEL_MODE_CACHE_KEY && event.newValue === "on") observedOn = true; };
    window.addEventListener?.("home-travel-mode-change", travelChanged);
    window.addEventListener?.("storage", storageChanged);
    const dispose = () => {
      clearTimeout(timeout);
      window.removeEventListener?.("home-travel-mode-change", travelChanged);
      window.removeEventListener?.("storage", storageChanged);
    };
    const requireDispatch = () => {
      requireCurrent();
      if (observedOn || !initiallyCachedOn && cachedTravelModeOn()) throw new Error("Travel Mode is on; lighting output is blocked");
      if (observedUnknown) throw new Error("Travel Mode is unavailable; lighting output is blocked");
    };
    try {
      let states;
      try {
        states = await Promise.race([
          client.call({ type: "get_states" }),
          new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("Travel Mode read timed out")), 4000); }),
        ]);
      } catch (_) { throw new Error("Travel Mode is unavailable; lighting output is blocked"); }
      requireDispatch();
      const helpers = Array.isArray(states) ? states.filter(state => state?.entity_id === TRAVEL_MODE_ENTITY) : [];
      if (helpers.length !== 1 || helpers[0].state !== "off") {
        throw new Error(helpers.length === 1 && helpers[0].state === "on"
          ? "Travel Mode is on; lighting output is blocked" : "Travel Mode is unavailable; lighting output is blocked");
      }
      // A cached on is a lock until this exact client confirms the helper is
      // off. No cached off or prior successful request can admit a later call.
      requireDispatch();
      // Retain the pending-on latch through the caller's final microtask and
      // release it immediately after invoking the selected transport.
      return { requireDispatch, dispose };
    } catch (error) { dispose(); throw error; }
  }

  async function callService(client, domain, service, data = {}, { siteId = "echo", isCurrent } = {}) {
    if (siteId !== "echo") throw new Error("Home controls are not configured for this home");
    if (rehearsalIsolated()) throw new Error("Physical controls are disabled in rehearsal");
    if (!client) throw new Error("Home Assistant client is not ready");
    if (typeof isCurrent === "function" && isCurrent() !== true) throw new Error("Home control context changed; no command was sent");
    const output = serviceMayEnergizeLights(domain, service);
    // Snapshot the submitted output before awaiting its guard. A caller may
    // not replace targets or parameters while an older helper read is pending.
    const submitted = output ? cloneState(data || {}) : data || {};
    let gate;
    if (output) {
      if (!submitted || typeof submitted !== "object" || Array.isArray(submitted)) throw new Error("Lighting service data is invalid");
      gate = await verifyTravelModeForOutput(client, isCurrent);
    }
    try {
      gate?.requireDispatch();
      if (typeof client.callService === "function") return client.callService(domain, service, submitted);
      if (typeof client.call !== "function") throw new Error("Home Assistant client cannot call services");
      return client.call(buildServicePayload(domain, service, submitted));
    } finally { gate?.dispose(); }
  }

  // Chat action cards carry no qualified command-home authority or originating
  // connection binding. This is refusal only: no client, helper read, authority
  // option or transport admission can be supplied to activate a physical call.
  // Existing qualified Apartment and simulation controls use their own paths.
  async function refuseChatServiceEnvelope(envelope) {
    if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)
        || envelope.type !== "call_service" || typeof envelope.domain !== "string"
        || typeof envelope.service !== "string") throw new Error("Chat service command is invalid");
    if (serviceMayEnergizeLights(envelope.domain, envelope.service)) {
      throw new Error("Chat lighting controls are unavailable because their home authority cannot be verified; no command was sent");
    }
    throw new Error("Chat action controls are unavailable because this card's originating connection is unverified; no command was sent");
  }

  /* ---------------- tracker WS ---------------- */

  function openTracks({ sim, replay, onTracks, onStatus, siteId = "echo", isCurrent = () => true, signal } = {}) {
    if (siteId !== "echo" || signal?.aborted || !isCurrent()) { onStatus?.("home-unconfigured"); return () => {}; }
    if (rehearsalIsolated()) { onStatus?.("rehearsal-paused"); return () => {}; }
    if (sim && typeof window.__SIM_APARTMENT_TRACKS === "function") {
      // sim fixture: scripted walk, 5 Hz
      let closed = false;
      const timer = setInterval(() => {
        if (!closed && !signal?.aborted && isCurrent() && !rehearsalIsolated()) onTracks(window.__SIM_APARTMENT_TRACKS());
      }, 200);
      onStatus && onStatus("sim");
      const close = () => { if (!closed) { closed = true; clearInterval(timer); } };
      signal?.addEventListener?.("abort", close, { once: true });
      return () => { signal?.removeEventListener?.("abort", close); close(); };
    }
    if (sim) { onStatus && onStatus("sim-empty"); return () => {}; }

    let ws = null, closed = false, backoff = 1000, retryTimer = null;
    const current = () => !closed && !signal?.aborted && !rehearsalIsolated() && isCurrent();
    const close = () => {
      if (closed) return;
      closed = true;
      clearTimeout(retryTimer);
      retryTimer = null;
      try { ws && ws.close(); } catch (e) { /* */ }
    };
    signal?.addEventListener?.("abort", close, { once: true });
    const url = `${toWsBase(trackerBase())}/ws/tracks${replay ? `?replay=${replay}` : ""}`;
    const connect = () => {
      if (!current()) return;
      try { ws = new WebSocket(url); } catch (e) { retry(); return; }
      ws.onopen = () => { if (current()) { backoff = 1000; onStatus && onStatus("live"); } };
      ws.onmessage = (m) => {
        if (!current()) return;
        try {
          const frame = JSON.parse(m.data);
          if (frame.type === "tracks") onTracks(frame);
        } catch (e) { /* */ }
      };
      ws.onclose = () => { if (current()) { onStatus && onStatus("offline"); retry(); } };
      ws.onerror = () => { try { ws.close(); } catch (e) { /* */ } };
    };
    const retry = () => {
      if (!current()) return;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    connect();
    return () => { signal?.removeEventListener?.("abort", close); close(); };
  }

  window.HomeApartmentData = {
    getModel, getAuthoritativeModel, compareApartmentModels,
    saveModel, getSeedModel, getRegistry, buildPalette,
    readStates, bindStates, callService, refuseChatServiceEnvelope, openTracks, EMPTY_MODEL,
    ensureModelShape, isCeilingLight, fixtureCalibrationStatus,
    adoptEngineeredFixture, validateEngineeredMappings, validateEngineeredFixtureModel,
    reconcileFixturePosition, zoneBounds, parseTapeMeasurement, formatTapeMeasurement,
    modelSourceMeta, modelForPersistence, registryLightEntities, buildFixtureMapping,
    spatialGeometrySnapshot, compareSpatialGeometry, reconcileFixtureEntityLink,
    getSavedLayoutSnapshot,
    getEquipmentRegistration,
    captureWorkspace, restoreWorkspace, dirtySites, clearWorkspaces, setWorkspaceAuthority, ensureWorkspaceAuthority,
    readViewPreference, saveViewPreference,
  };
})();
