/* eslint-disable */
/* Focused engineered-fixture inspector and Aim workflow.
 * Selection and preview are side-effect free. The only HA writes exposed here
 * are deliberate spotlight/radial light service actions. There is no gimbal
 * command method in this module.
 */

const { useState, useEffect, useMemo, useRef, useCallback } = React;
const AIM_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const AIM_SANS = '"Geist", "Inter", sans-serif';
const AIM_CYAN = "#45dfff";
const AIM_WARM = "#ffddb0";
const AIM_AMBER = "#ffb45f";
const AIM_GREEN = "#91e6bd";
const AIM_RED = "#ff625f";

function AimButton({ children, onClick, active, disabled, danger, title, style }) {
  return <button type="button" className="hg-focusable" onClick={onClick} disabled={disabled} title={title}
    style={{ minHeight: 40, padding: "9px 12px", borderRadius: 9,
      minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
      border: `1px solid ${danger ? AIM_RED : active ? AIM_CYAN : "var(--hg-border-soft)"}`,
      background: active ? "#f4f6f8" : "rgba(255,255,255,0.035)",
      color: danger ? AIM_RED : active ? "#071014" : disabled ? "var(--hg-fg-5)" : "var(--hg-fg-1)",
      fontFamily: AIM_SANS, fontSize: 11, fontWeight: 540, letterSpacing: "0.01em",
      cursor: disabled ? "default" : "pointer", ...style }}>{children}</button>;
}

function AimStatus({ label, status, detail }) {
  const colors = { verified: AIM_GREEN, calibrated: AIM_CYAN, measured: AIM_AMBER,
    proposed: "var(--hg-fg-5)", current: AIM_GREEN, pending: AIM_AMBER,
    accepted: AIM_CYAN, failed: AIM_RED, unavailable: "var(--hg-fg-5)" };
  return <div title={detail || ""} style={{ display: "flex", justifyContent: "space-between", gap: 10,
    fontFamily: AIM_MONO, fontSize: 9, lineHeight: 1.45 }}>
    <span style={{ color: "var(--hg-fg-4)" }}>{label}</span>
    <span style={{ color: colors[status] || "var(--hg-fg-2)" }}>{status || "unavailable"}</span>
  </div>;
}

function ProfileStatus({ label, detail, tone = "offline" }) {
  const color = tone === "online" ? AIM_GREEN : tone === "preview" ? AIM_CYAN
    : tone === "warning" ? AIM_AMBER : "var(--hg-fg-5)";
  return <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 7,
    fontFamily: AIM_SANS, fontSize: 10.5, color: "var(--hg-fg-2)" }}>
    <span aria-hidden="true" style={{ width: 6, height: 6, flex: "0 0 auto", borderRadius: "50%",
      background: tone === "preview" ? "transparent" : color,
      border: `1px solid ${color}`, boxShadow: tone === "online" ? `0 0 10px ${color}` : "none" }} />
    <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
    <span style={{ marginLeft: "auto", color, fontFamily: AIM_MONO, fontSize: 8.5,
      whiteSpace: "nowrap" }}>{detail}</span>
  </div>;
}

function AimSelect({ value, onChange, children, label, disabled }) {
  return <label style={{ display: "grid", gap: 4, fontFamily: AIM_MONO, fontSize: 8.5,
    color: "var(--hg-fg-4)", letterSpacing: "0.06em", minWidth: 0 }}>
    {label}
    <select className="hg-focusable" value={value || ""} disabled={disabled} onChange={(e) => onChange(e.target.value || null)}
      style={{ minHeight: 36, width: "100%", minWidth: 0, background: "#080b10", color: "var(--hg-fg-1)",
        border: "1px solid var(--hg-border-soft)", borderRadius: 9, fontFamily: AIM_SANS, fontSize: 11, padding: "7px 9px" }}>
      {children}
    </select>
  </label>;
}

function AimTextInput({ value, onCommit, label, placeholder, type = "text", min, max, step }) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  return <label style={{ display: "grid", gap: 4, minWidth: 0, fontFamily: AIM_MONO,
    fontSize: 8.5, color: "var(--hg-fg-4)", letterSpacing: "0.06em" }}>
    {label}
    <input className="hg-focusable" type={type} min={min} max={max} step={step} value={draft}
      placeholder={placeholder} onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onCommit(type === "number" ? (draft === "" ? null : +draft) : (String(draft).trim() || null))}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      style={{ minHeight: 36, width: "100%", minWidth: 0, boxSizing: "border-box",
        background: "#080b10", color: "var(--hg-fg-1)", border: "1px solid var(--hg-border-soft)",
        fontFamily: AIM_MONO, fontSize: 9, padding: "5px 7px" }} />
  </label>;
}

function entityName(entity, states) {
  return states?.[entity.entity_id]?.attributes?.friendly_name || entity.name || entity.original_name || entity.entity_id;
}

function fixtureBindingId(fixture) {
  const binding = fixture?.gimbal?.device_binding;
  return binding?.stable_id || binding?.serial || (typeof binding === "string" ? binding : null);
}

function targetLabel(destination) {
  if (!destination) return "choose a named target or click the apartment mesh";
  if (destination.kind === "mesh") return `exact mesh hit · ${destination.pos.map((v) => (+v).toFixed(2)).join(", ")} m`;
  return destination.name || destination.id || "named target";
}

function destinationKey(destination) {
  if (!destination) return null;
  if (destination.key) return destination.key;
  if (destination.id) return `${destination.kind || "target"}:${destination.id}`;
  if (Array.isArray(destination.pos)) return `${destination.kind || "point"}:${destination.pos.map((value) => (+value).toFixed(3)).join(",")}`;
  return destination.kind || null;
}

function HomeApartmentAim({
  model, registry, states, sim, connection, engine, fixtureId, onFixtureId,
  destination, onDestination, onModel, onDirty, onStateReadback, onClose, mobile,
  onSave, saveStatus, saving,
}) {
  const A = window.HomeApartmentAiming;
  const fixtures = useMemo(() => (model.devices || []).filter((d) => window.HomeApartmentData.isCeilingLight(d)), [model]);
  const engineered = useMemo(() => fixtures.filter((d) => d.fixture_kind === A.ENGINEERED_KIND), [fixtures]);
  const fixture = fixtures.find((d) => d.id === fixtureId) || engineered[0] || fixtures[0] || null;
  const [tab, setTab] = useState("aim");
  const [telemetryPacket, setTelemetryPacket] = useState(null);
  const [telemetryStatus, setTelemetryStatus] = useState({ state: sim ? "simulation" : "unavailable" });
  const [clock, setClock] = useState(Date.now());
  const [roleStatus, setRoleStatus] = useState({});
  const [notice, setNotice] = useState("");
  const [profileView, setProfileView] = useState("auto");
  const [confirmAdopt, setConfirmAdopt] = useState(false);
  const [aimTransitioning, setAimTransitioning] = useState(false);
  const [beamAnalysis, setBeamAnalysis] = useState(null);
  const [restoreCandidates, setRestoreCandidates] = useState({});
  const [selectedZone, setSelectedZone] = useState(1);
  const restoreCandidatesRef = useRef({});
  const restoreTimers = useRef(new Map());
  const aimTransitionCancelRef = useRef(null);
  const [calDraft, setCalDraft] = useState({ pan_sign: 1, tilt_sign: 1, verification: [] });
  const [radialDraft, setRadialDraft] = useState({ anchor_zone: 1, order: "clockwise", fine_adjust_deg: 0 });

  useEffect(() => {
    if (fixture && fixture.id !== fixtureId) onFixtureId(fixture.id);
  }, [fixture?.id]);
  useEffect(() => { setConfirmAdopt(false); setProfileView("auto"); }, [fixture?.id]);

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 120);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => { restoreCandidatesRef.current = restoreCandidates; }, [restoreCandidates]);

  useEffect(() => {
    if (!fixture || fixture.fixture_kind !== A.ENGINEERED_KIND) return undefined;
    if (sim) {
      const runtime = window.__SIM_APARTMENT_AIM_RUNTIME?.read?.(fixture.id);
      setTelemetryPacket(runtime ? { snapshot: runtime.telemetry, received_at_ms: Date.now(), simulation: true } : null);
      setTelemetryStatus({ state: "simulation", detail: "synthetic measured pose · never live" });
      return undefined;
    }
    const poller = window.HomeApartmentGimbalTelemetry?.createPoller?.({
      onSnapshot: setTelemetryPacket,
      onStatus: setTelemetryStatus,
    });
    poller?.start?.();
    return () => poller?.stop?.();
  }, [fixture?.id, fixture?.fixture_kind, sim]);

  useEffect(() => () => {
    aimTransitionCancelRef.current?.();
    aimTransitionCancelRef.current = null;
    for (const timer of restoreTimers.current.values()) clearTimeout(timer);
    restoreTimers.current.clear();
    engine?.overlay?.setTargetHover?.(null, null);
  }, [engine]);

  const runtimeStates = sim ? window.__SIM_APARTMENT_AIM_RUNTIME?.read?.(fixture?.id) : null;
  const currentDestination = runtimeStates?.current_destination || null;
  const selectedDestinationKey = destinationKey(destination);
  const currentDestinationKey = destinationKey(currentDestination);
  const destinationIsCurrent = !!(selectedDestinationKey && selectedDestinationKey === currentDestinationKey);
  const spotlightEntity = fixture?.spotlight?.entity_id;
  const spotlightState = sim ? runtimeStates?.spotlight : states?.[spotlightEntity];
  const primaryEntity = fixture?.ha_entity_id;
  const primaryState = states?.[primaryEntity];
  const primaryCapabilities = A.lightCapabilities(primaryState);
  const spotlightHaState = sim ? { state: runtimeStates?.spotlight?.state, attributes: {
    brightness: runtimeStates?.spotlight?.brightness,
    color_temp_kelvin: runtimeStates?.spotlight?.color_temp_kelvin,
    supported_color_modes: ["brightness", "color_temp"], min_color_temp_kelvin: 1800, max_color_temp_kelvin: 6500,
  } } : spotlightState;
  const spotlightCapabilities = A.lightCapabilities(spotlightHaState);
  const calibration = fixture?.gimbal?.visualization_calibration;
  const telemetry = useMemo(() => {
    if (!telemetryPacket || !fixture) return { valid: false, blockers: [sim ? "simulation_pose_unavailable" : "telemetry_unavailable"] };
    if (sim) {
      const pan = telemetryPacket.snapshot?.angle?.pan;
      const tilt = telemetryPacket.snapshot?.angle?.tilt;
      return { valid: Number.isFinite(+pan?.deg) && Number.isFinite(+tilt?.deg),
        raw: { pan: +pan?.deg, tilt: +tilt?.deg }, blockers: [], source: "bench simulation",
        ages_ms: { pan: 0, tilt: 0 }, identity: A.telemetryIdentity(telemetryPacket.snapshot),
        readiness: telemetryPacket.snapshot?.ready, activity: telemetryPacket.snapshot?.activity };
    }
    return A.evaluateTelemetry(telemetryPacket.snapshot, {
      now_ms: clock, received_at_ms: telemetryPacket.received_at_ms,
      expected_device: fixtureBindingId(fixture), expected_profile: fixture.gimbal?.product_profile_sha256,
    });
  }, [telemetryPacket, fixture, clock, sim]);

  const calibrationCompatible = !!(calibration
    && ["calibrated", "verified"].includes(calibration.status)
    && calibration.product_profile_sha256 === fixture?.gimbal?.product_profile_sha256
    && (sim || calibration.collision_geometry_sha256 === A.COLLISION_GEOMETRY_SHA256));
  const mechanical = telemetry.valid && calibrationCompatible
    ? A.rawToMechanical(telemetry.raw, calibration) : { ok: false };
  const solve = fixture && destination ? A.solveAim(fixture.pos, destination.pos, {
    calibration: calibrationCompatible ? calibration : undefined,
    limits: fixture.gimbal?.limits,
    current_pan_deg: mechanical.ok ? mechanical.pan : undefined,
    current_raw: telemetry.valid ? telemetry.raw : undefined,
  }) : null;
  const product = fixture && destination
    ? A.productAimRequest(destination.pos, sim ? fixture.gimbal?.product_target_plane_descriptor : null, 0,
      fixture.gimbal?.product_profile_sha256)
    : null;
  const profileResolution = A.resolveFixtureProfileView(profileView, {
    current_available: primaryCapabilities.available,
    engineered_available: sim || spotlightCapabilities.available || telemetry.valid,
    engineered_configured: fixture?.fixture_kind === A.ENGINEERED_KIND,
  });
  const canSetDestination = !!(sim && destination && !destinationIsCurrent && !aimTransitioning
    && (destination.kind === "mirror" || solve?.executable));

  const setSimulatedDestination = () => {
    if (!sim) { setNotice("Live movement is unavailable until Home has movement authority and a qualified target-plane binding."); return; }
    if (!fixture || !destination) { setNotice("Choose a destination first."); return; }
    if (destination.kind !== "mirror" && !solve?.executable) {
      setNotice(`This preview cannot be set · ${solve?.ambiguity || solve?.violations?.join(", ") || "destination is not executable"}`);
      return;
    }
    const defaultTarget = (model.targets || []).find((target) => target.id === fixture.gimbal?.default_target_id);
    const currentTarget = currentDestination?.pos ? currentDestination : defaultTarget;
    let fromDirection = [0, 0, -1];
    let fromDistance = Math.max(0.35, +fixture.pos?.[2] || 2.3);
    if (currentDestination?.kind === "mirror") {
      fromDirection = [0, 0, 1]; fromDistance = 0.2;
    } else if (currentTarget?.pos) {
      const delta = currentTarget.pos.map((value, index) => value - fixture.pos[index]);
      fromDistance = Math.hypot(...delta); fromDirection = delta.map((value) => value / fromDistance);
    }
    if (!sim && !currentDestination && mechanical.ok) fromDirection = A.directionFromAim(mechanical.pan, mechanical.tilt, calibration);
    const toDirection = destination.kind === "mirror" ? [0, 0, 1]
      : solve.delta.map((value) => value / solve.distance_m);
    const toDistance = destination.kind === "mirror" ? 0.2 : solve.distance_m;
    const committedDestination = { ...destination, key: selectedDestinationKey, raw_destination: solve?.raw_destination };
    const commit = () => {
      aimTransitionCancelRef.current = null;
      const next = window.__SIM_APARTMENT_AIM_RUNTIME?.setDestination?.(fixture.id, committedDestination);
      setAimTransitioning(false);
      if (!next) { setNotice("Simulation destination could not be updated."); return; }
      setTelemetryPacket({ snapshot: next.telemetry, received_at_ms: Date.now(), simulation: true });
      setClock(Date.now());
      setNotice(`${targetLabel(destination)} set as the simulated current aim · no hardware command sent`);
    };
    setAimTransitioning(true);
    setNotice(`Aiming toward ${targetLabel(destination)} · simulation`);
    aimTransitionCancelRef.current = engine?.overlay?.animateAimTransition?.({
      origin: fixture.pos, from_direction: fromDirection, to_direction: toDirection,
      from_distance_m: fromDistance, to_distance_m: toDistance,
      full_fwhm_deg: fixture.spotlight?.optic_profile?.configured_fwhm_deg || 20,
      color_temp_kelvin: spotlightState?.color_temp_kelvin || spotlightHaState?.attributes?.color_temp_kelvin,
      duration_ms: 880,
    }, commit) || null;
    if (!aimTransitionCancelRef.current) commit();
  };

  useEffect(() => {
    if (!fixture || fixture.fixture_kind !== A.ENGINEERED_KIND || !engine?.overlay?.setAimBeams) {
      engine?.overlay?.setAimBeams?.([]); setBeamAnalysis(null); return;
    }
    const collision = engine.modes?.getCollision?.();
    const allRuntime = sim ? (window.__SIM_APARTMENT_AIM_RUNTIME?.readAll?.() || {}) : {};
    const targetById = new Map((model.targets || []).map((target) => [target.id, target]));
    const beamSpecs = engineered.map((candidate) => {
      const origin = candidate.pos;
      const fwhm = candidate.spotlight?.optic_profile?.configured_fwhm_deg || 20;
      const runtime = sim ? allRuntime[candidate.id] : null;
      const lightState = sim ? runtime?.spotlight : states?.[candidate.spotlight?.entity_id];
      const candidateView = A.resolveFixtureProfileView(profileView, {
        current_available: A.lightCapabilities(states?.[candidate.ha_entity_id]).available,
        engineered_available: sim || A.lightCapabilities(lightState).available
          || (candidate.id === fixture.id && telemetry.valid),
        engineered_configured: true,
      });
      if (!candidateView.show_engineered) return null;
      const radialCal = candidate.radial_zones?.orientation_calibration;
      const radial = radialCal ? (candidate.radial_zones?.zones || []).map((zone) => ({
        number: zone.number, angle_deg: A.radialZoneAngle(zone.number, radialCal),
        active: sim ? runtime?.zones?.[zone.number]?.state === "on" : states?.[zone.entity_id]?.state === "on",
        color_temp_kelvin: sim ? runtime?.zones?.[zone.number]?.color_temp_kelvin : states?.[zone.entity_id]?.attributes?.color_temp_kelvin,
      })) : [];
      const runtimeDestination = runtime?.current_destination;
      let direction, distance = Math.max(0.35, origin?.[2] || 2.3), currentMode = null;
      const defaultTarget = targetById.get(candidate.gimbal?.default_target_id);
      const currentTarget = runtimeDestination?.pos ? runtimeDestination : defaultTarget;
      if (runtimeDestination?.kind === "mirror") {
        direction = [0, 0, 1]; distance = 0.2; currentMode = "mirror_bounce";
      } else if (currentTarget?.pos) {
        const delta = currentTarget.pos.map((value, index) => value - origin[index]);
        distance = Math.hypot(...delta);
        direction = delta.map((value) => value / distance);
      } else {
        direction = [0, 0, -1];
      }
      let currentPoseCorrelated = !!currentTarget;
      if (!sim && !runtimeDestination && candidate.id === fixture.id && mechanical.ok && telemetry.valid && calibrationCompatible) {
        direction = A.directionFromAim(mechanical.pan, mechanical.tilt, calibration);
        currentPoseCorrelated = false;
      }
      const trace = collision ? engine.picking.beamTrace(engine.apartmentRoot, [collision], origin, direction, fwhm, 15) : null;
      const currentPlaneCandidate = currentMode || !currentPoseCorrelated ? null : currentTarget?.normal
        ? A.projectBeamToPlane({ origin, direction, plane_point: currentTarget.pos,
          plane_normal: currentTarget.normal, full_fwhm_deg: fwhm }) : null;
      const currentPlane = currentPlaneCandidate?.kind === "ellipse" ? currentPlaneCandidate : null;
      const renderedState = candidateView.engineered_preview ? "simulated" : (lightState?.state || "unknown");
      const spec = { fixture_id: candidate.id, origin,
        color_temp_kelvin: sim ? lightState?.color_temp_kelvin : lightState?.attributes?.color_temp_kelvin,
        current: { origin, direction, distance_m: currentPlane?.distance_m || trace?.center?.distance_m || distance,
          full_fwhm_deg: fwhm, state: renderedState,
          color_temp_kelvin: sim ? lightState?.color_temp_kelvin : lightState?.attributes?.color_temp_kelvin,
          mode: candidateView.engineered_preview ? "simulated_preview" : currentMode,
          footprint: currentPlane?.points?.length ? currentPlane : trace?.footprint,
          surface_aligned: currentPlane?.kind === "ellipse" }, radial };
      if (candidate.id === fixture.id && destination?.kind === "mirror" && !destinationIsCurrent) {
        spec.preview = { origin, direction: [0, 0, 1], distance_m: 0.2, full_fwhm_deg: fwhm,
          state: "preview", mode: "mirror_bounce", color_temp_kelvin: spec.color_temp_kelvin };
        setBeamAnalysis({ collision_available: !!collision, blocked: false,
          footprint_kind: "soft mirror wash", footprint_hit_fraction: null });
      } else if (candidate.id === fixture.id && solve?.ok && !destinationIsCurrent) {
        const previewDirection = solve.delta.map((value) => value / solve.distance_m);
        const previewTrace = collision ? engine.picking.beamTrace(engine.apartmentRoot, [collision], origin,
          previewDirection, fwhm, solve.distance_m) : null;
        const previewPlaneCandidate = Array.isArray(destination?.normal)
          ? A.projectBeamToPlane({ origin, direction: previewDirection, plane_point: destination.pos,
            plane_normal: destination.normal, full_fwhm_deg: fwhm }) : null;
        const previewPlane = previewPlaneCandidate?.kind === "ellipse" ? previewPlaneCandidate : null;
        spec.preview = { origin, direction: previewDirection, distance_m: solve.distance_m, full_fwhm_deg: fwhm,
          state: "preview", footprint: previewPlane?.points?.length ? previewPlane : previewTrace?.footprint,
          surface_aligned: previewPlane?.kind === "ellipse", obstruction_point: previewTrace?.obstruction_point };
        setBeamAnalysis({ collision_available: !!collision, blocked: !!previewTrace?.blocked,
          footprint_kind: previewPlaneCandidate?.kind || previewTrace?.footprint?.kind || "unavailable",
          footprint_hit_fraction: previewPlane?.hit_fraction ?? previewTrace?.footprint?.hit_fraction ?? null });
      } else if (candidate.id === fixture.id && destinationIsCurrent) {
        setBeamAnalysis({ collision_available: !!collision, blocked: false,
          footprint_kind: currentPlane?.kind || "current aim", footprint_hit_fraction: currentPlane?.hit_fraction ?? null });
      }
      return spec;
    }).filter(Boolean);
    if (!destination || (destination.kind !== "mirror" && !solve?.ok)) setBeamAnalysis(null);
    engine.overlay.setAimBeams(beamSpecs, fixture.id);
    engine.overlay.setTargetHover?.(null, destination?.kind === "named" ? destination.id : null);
  }, [fixture, engineered, model.targets, solve?.target?.join?.("|"), mechanical.pan, mechanical.tilt, telemetry.valid, calibrationCompatible,
    spotlightState?.state, fixture?.radial_zones, states, engine, destination?.id, destination?.kind,
    selectedDestinationKey, currentDestinationKey, sim, profileView,
    (fixture?.radial_zones?.zones || []).map((z) => sim
      ? runtimeStates?.zones?.[z.number]?.state : states?.[z.entity_id]?.state).join("|")]);

  const updateFixture = useCallback((nextFixture) => {
    const next = { ...model, devices: (model.devices || []).map((d) => d.id === nextFixture.id ? nextFixture : d) };
    const validation = window.HomeApartmentData.validateEngineeredMappings(next);
    if (!validation.ok) {
      setNotice(`duplicate blocked · ${validation.duplicates[0].entity_id} already has an engineered role`);
      return false;
    }
    onModel(next); onDirty?.(); setNotice("semantic change is unsaved");
    return true;
  }, [model, onModel, onDirty]);

  const adopt = () => {
    if (!fixture) return;
    const adopted = window.HomeApartmentData.adoptEngineeredFixture(fixture);
    if (updateFixture(adopted)) {
      onFixtureId(adopted.id);
      setProfileView("engineered");
      setConfirmAdopt(false);
      setNotice("Engineered profile added at the existing mount · position and measurements unchanged");
    }
  };

  const setMapping = (role, entityId, zoneNumber = null) => {
    if (!fixture || fixture.fixture_kind !== A.ENGINEERED_KIND) return;
    const previous = role === "spotlight" ? fixture.spotlight?.entity_id
      : fixture.radial_zones?.zones?.find((z) => z.number === zoneNumber)?.entity_id;
    if (previous && previous !== entityId && !window.confirm(
      entityId ? `Change ${role} link from ${previous} to ${entityId}?` : `Remove ${role} link to ${previous}?`)) return;
    let next;
    if (role === "spotlight") next = { ...fixture, spotlight: { ...fixture.spotlight, entity_id: entityId } };
    else next = { ...fixture, radial_zones: { ...fixture.radial_zones,
      zones: fixture.radial_zones.zones.map((z) => z.number === zoneNumber ? { ...z, entity_id: entityId } : z) } };
    updateFixture(next);
  };

  const readback = async (entityIds) => {
    if (sim) return {};
    const client = window.__hav_haClient;
    const latest = await window.HomeApartmentData.readStates(client, entityIds);
    onStateReadback?.(latest);
    return latest;
  };

  const sendLights = async (entityIds, service, data, statusKey = "batch") => {
    const ids = [...new Set(entityIds.filter(Boolean))];
    if (!ids.length) return {};
    setRoleStatus((s) => ({ ...s, [statusKey]: "pending" }));
    try {
      if (sim) {
        if (statusKey === "spotlight") window.__SIM_APARTMENT_AIM_RUNTIME?.setSpotlight?.(
          fixture.id, { state: service === "turn_off" ? "off" : "on", ...data });
        else if (statusKey.startsWith("zone-")) window.__SIM_APARTMENT_AIM_RUNTIME?.setZone?.(fixture.id, +statusKey.slice(5),
          { state: service === "turn_off" ? "off" : "on", ...data });
        else window.__SIM_APARTMENT_AIM_RUNTIME?.setZones?.(
          fixture.id,
          fixture.radial_zones.zones.filter((z) => ids.includes(z.entity_id)).map((z) => z.number),
          { state: service === "turn_off" ? "off" : "on", ...data });
        setRoleStatus((s) => ({ ...s, [statusKey]: "verified" }));
        setClock(Date.now());
        return {};
      }
      if (connection !== "online") throw new Error("Home Assistant is not connected");
      await window.HomeApartmentData.callService(window.__hav_haClient, "light", service,
        { entity_id: ids, ...data });
      setRoleStatus((s) => ({ ...s, [statusKey]: "accepted" }));
      await new Promise((resolve) => setTimeout(resolve, 420));
      const latest = await readback(ids);
      setRoleStatus((s) => ({ ...s, [statusKey]: "verified" }));
      return latest;
    } catch (error) {
      setRoleStatus((s) => ({ ...s, [statusKey]: "failed" }));
      setNotice(String(error?.message || error));
      return null;
    }
  };

  const restoreZone = async (number, deliberate = true) => {
    const candidate = restoreCandidatesRef.current[number];
    if (!candidate) return;
    clearTimeout(restoreTimers.current.get(number));
    restoreTimers.current.delete(number);
    const entity = candidate.entity_id;
    if (!deliberate && !sim) {
      const latest = await readback([entity]);
      const current = latest[entity];
      const signature = candidate.identify_signature;
      if (current?.state !== "on" || +current?.attributes?.brightness !== candidate.identify_brightness
          || (signature?.last_updated && current?.last_updated !== signature.last_updated)
          || (signature && (current?.attributes?.color_temp_kelvin ?? null) !== signature.color_temp_kelvin)) {
        setRoleStatus((s) => ({ ...s, [`zone-${number}`]: "current" }));
        setNotice(`zone ${number} changed elsewhere · automatic restore cancelled`);
        return;
      }
    }
    const prev = candidate.previous;
    const data = {};
    if (Number.isFinite(+prev?.attributes?.brightness)) data.brightness = +prev.attributes.brightness;
    if (Number.isFinite(+prev?.attributes?.color_temp_kelvin)) data.color_temp_kelvin = +prev.attributes.color_temp_kelvin;
    await sendLights([entity], prev?.state === "on" ? "turn_on" : "turn_off", data, `zone-${number}`);
    setRestoreCandidates((all) => {
      const next = { ...all }; delete next[number]; restoreCandidatesRef.current = next; return next;
    });
  };

  const identifyZone = async (zone) => {
    const entity = zone.entity_id;
    const current = sim ? { state: runtimeStates?.zones?.[zone.number]?.state || "off", attributes: {
      brightness: runtimeStates?.zones?.[zone.number]?.brightness || 0,
      color_temp_kelvin: runtimeStates?.zones?.[zone.number]?.color_temp_kelvin,
    } } : states?.[entity];
    if (!A.lightCapabilities(current).brightness) { setNotice(`zone ${zone.number} does not report brightness support`); return; }
    if (!window.confirm(`Identify zone ${zone.number} at low brightness for three seconds? Its prior state will be restored only if no other change occurs.`)) return;
    const identifyBrightness = 18;
    const candidate = {
      entity_id: entity, previous: JSON.parse(JSON.stringify(current || { state: "off", attributes: {} })),
      identify_brightness: identifyBrightness,
    };
    restoreCandidatesRef.current = { ...restoreCandidatesRef.current, [zone.number]: candidate };
    setRestoreCandidates(restoreCandidatesRef.current);
    const identified = await sendLights([entity], "turn_on", { brightness: identifyBrightness }, `zone-${zone.number}`);
    const observed = identified?.[entity];
    if (observed) {
      candidate.identify_signature = {
        last_updated: observed.last_updated || null,
        color_temp_kelvin: observed.attributes?.color_temp_kelvin ?? null,
      };
      restoreCandidatesRef.current = { ...restoreCandidatesRef.current, [zone.number]: candidate };
      setRestoreCandidates(restoreCandidatesRef.current);
    }
    const timer = setTimeout(() => restoreZone(zone.number, false), 3000);
    restoreTimers.current.set(zone.number, timer);
  };

  const copyToAll = async () => {
    const source = spotlightState;
    const sourceBrightness = sim ? source?.brightness : source?.attributes?.brightness;
    const sourceKelvin = sim ? source?.color_temp_kelvin : source?.attributes?.color_temp_kelvin;
    const compatible = [], skipped = [];
    for (const zone of fixture.radial_zones?.zones || []) {
      if (!zone.entity_id) { skipped.push(`zone ${zone.number} unmapped`); continue; }
      const state = sim ? { state: runtimeStates?.zones?.[zone.number]?.state, attributes: {
        brightness: runtimeStates?.zones?.[zone.number]?.brightness,
        supported_color_modes: ["brightness", "color_temp"], min_color_temp_kelvin: 1800, max_color_temp_kelvin: 6500,
      } } : states?.[zone.entity_id];
      const cap = A.lightCapabilities(state);
      if (!cap.available || !cap.brightness) { skipped.push(`zone ${zone.number} incompatible`); continue; }
      if (Number.isFinite(+sourceKelvin) && (!cap.color_temp
          || +sourceKelvin < cap.min_kelvin || +sourceKelvin > cap.max_kelvin)) {
        skipped.push(`zone ${zone.number} CT incompatible`); continue;
      }
      compatible.push({ zone, cap });
    }
    const data = Number.isFinite(+sourceBrightness) ? { brightness: +sourceBrightness } : {};
    if (Number.isFinite(+sourceKelvin)) data.color_temp_kelvin = +sourceKelvin;
    await sendLights(compatible.map(({ zone }) => zone.entity_id), "turn_on", data, "copy-all");
    setNotice(`${compatible.length} zone${compatible.length === 1 ? "" : "s"} updated${skipped.length ? ` · skipped ${skipped.join(", ")}` : ""}`);
  };

  const saveVisualizationCalibration = () => {
    if (!sim) { setNotice("live direction calibration is blocked by the owner-authority handoff"); return; }
    if (!destination || destination.kind !== "mesh") { setNotice("select an exact collision-mesh corner first"); return; }
    const verification = calDraft.verification || [];
    const nextCalibration = {
      model: "ideal_two_axis_pan_tilt", authority: "visualization_only",
      status: verification.length >= 2 ? "verified" : "calibrated",
      pan_sign: calDraft.pan_sign, tilt_sign: calDraft.tilt_sign,
      selected_collision_corner: destination.pos,
      horizontal_world_reference: [0, 1, 0],
      raw_encoder_reference_deg: telemetry.raw || { pan: 0, tilt: 0 },
      derived_offsets_deg: { yaw: 0, tilt_zero: 0 },
      assumptions: ["ideal orthogonal axes", "vertical pan axis", "mount roll not modeled"],
      confidence: verification.length >= 2 ? 0.9 : 0.65,
      device_binding: fixture.gimbal.device_binding,
      capture_session_identity: telemetry.identity,
      collision_geometry_sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      product_profile_sha256: fixture.gimbal.product_profile_sha256,
      based_on_model_revision: model.revision,
      timestamp: new Date().toISOString(), source: "simulation",
      verification_destinations: verification,
    };
    updateFixture({ ...fixture, gimbal: { ...fixture.gimbal, visualization_calibration: nextCalibration } });
  };

  const saveRadialCalibration = () => {
    if (!destination || destination.kind !== "mesh") { setNotice("select the exact mesh corner faced by the anchor zone"); return; }
    const zone = fixture.radial_zones?.zones?.find((z) => z.number === +radialDraft.anchor_zone);
    if (!zone?.entity_id) { setNotice("map the anchor radial zone first"); return; }
    const dx = destination.pos[0] - fixture.pos[0], dy = destination.pos[1] - fixture.pos[1];
    const angle = Math.atan2(dx, dy) * 180 / Math.PI;
    const orientation = {
      status: "calibrated", anchor_zone: +radialDraft.anchor_zone,
      anchor_collision_corner: destination.pos, anchor_world_angle_deg: angle,
      order: radialDraft.order, fine_adjust_deg: +radialDraft.fine_adjust_deg,
      viewing_convention: "floor_looking_up", based_on_model_revision: model.revision,
      timestamp: new Date().toISOString(), source: sim ? "simulation" : "manual_home_assistant_identification",
    };
    updateFixture({ ...fixture, radial_zones: { ...fixture.radial_zones, orientation_calibration: orientation } });
  };

  const lightEntities = (registry.entities || []).filter((e) => e.entity_id?.startsWith("light."));
  const used = window.HomeApartmentData.validateEngineeredMappings(model).uses || new Map();
  const roleOptions = (current) => lightEntities.filter((e) => !used.has(e.entity_id) || e.entity_id === current);
  const mappedZones = fixture?.radial_zones?.zones?.filter((z) => z.entity_id) || [];
  const engineeredConfigured = fixture?.fixture_kind === A.ENGINEERED_KIND;
  const currentProfileName = primaryState?.attributes?.friendly_name || primaryEntity || "Current light";
  const currentProfileStatus = !primaryEntity ? { detail: "unmapped", tone: "offline" }
    : primaryCapabilities.available ? { detail: primaryState?.state || "online", tone: "online" }
      : { detail: "offline", tone: "offline" };
  const engineeredProfileStatus = !engineeredConfigured ? { detail: "not added", tone: "offline" }
    : sim ? { detail: "simulated", tone: "preview" }
      : spotlightCapabilities.available || telemetry.valid ? { detail: "online", tone: "online" }
        : { detail: "preview only", tone: "preview" };
  const liveBlockers = [
    "no authoritative Apartment → Product target-plane binding",
    "no Home owner-authority delegation",
    "Product adoption is false",
  ];

  return <aside data-apt-aim-inspector="1" role="dialog" aria-label="Engineered fixture aiming inspector"
    style={{ position: "absolute", zIndex: 8, pointerEvents: "auto", overflow: "hidden",
      ...(mobile ? { left: 10, right: 10, bottom: "calc(10px + env(safe-area-inset-bottom, 0px))", maxHeight: "52dvh", borderRadius: 16 }
        : { top: 86, right: 18, bottom: 18, width: 338, borderRadius: 16 }),
      display: "flex", flexDirection: "column", background: "rgba(12,14,18,0.86)",
      border: "1px solid rgba(255,255,255,0.10)", boxShadow: "0 24px 80px rgba(0,0,0,0.5)",
      backdropFilter: "blur(24px) saturate(1.25)", color: "var(--hg-fg-1)", minWidth: 0 }}>
    <div style={{ padding: "15px 15px 12px", borderBottom: "1px solid rgba(255,255,255,0.07)", display: "grid", gap: 11 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: AIM_SANS, fontSize: 17, fontWeight: 560, letterSpacing: "-0.025em" }}>Lighting</div>
          <div style={{ fontFamily: AIM_MONO, fontSize: 9, color: "var(--hg-fg-4)", letterSpacing: "0.08em", marginTop: 3 }}>
            {sim ? "simulation · saved layout · controls simulated" : "live model · fixture-bottom origin"}
          </div>
        </div>
        {!sim && <AimButton onClick={onSave} disabled={saving || saveStatus?.state === "saved"}
          title={saveStatus?.detail}>
          {saving ? "saving" : saveStatus?.state === "saved" ? "saved" : "save"}
        </AimButton>}
        <AimButton onClick={onClose} title="Close lighting" style={{ minHeight: 36, width: 36, padding: 0, borderRadius: 18 }}>×</AimButton>
      </div>
      <AimSelect label="fixture" value={fixture?.id} onChange={onFixtureId} disabled={aimTransitioning}>
        {fixtures.map((f) => <option key={f.id} value={f.id}>{f.name}{f.fixture_kind === A.ENGINEERED_KIND ? " · engineered" : " · ordinary"}</option>)}
      </AimSelect>
      {fixture && <section data-apt-shared-mount="1" style={{ padding: 10, borderRadius: 12,
        border: "1px solid rgba(255,255,255,0.085)", background: "rgba(255,255,255,0.025)", display: "grid", gap: 9 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontFamily: AIM_SANS, fontSize: 12, fontWeight: 570 }}>One ceiling position</span>
          <span style={{ marginLeft: "auto", fontFamily: AIM_MONO, fontSize: 10.5, color: "var(--hg-fg-3)" }}>shared measurements</span>
        </div>
        <div data-apt-profile-view={profileView} role="group" aria-label="Lighting profile view"
          style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 3,
            padding: 3, borderRadius: 10, background: "rgba(0,0,0,0.22)" }}>
          {[["auto", "Auto"], ["current", "Current"], ["engineered", "Engineered"], ["both", "Both"]].map(([mode, label]) =>
            <AimButton key={mode} active={profileView === mode}
              disabled={!engineeredConfigured && ["engineered", "both"].includes(mode)}
              title={!engineeredConfigured && ["engineered", "both"].includes(mode)
                ? "Add an Engineered profile at this mount first" : undefined}
              onClick={() => setProfileView(mode)} style={{ minHeight: 32, padding: "6px 4px", border: 0,
                borderRadius: 7, fontSize: 9 }}>{label}</AimButton>)}
        </div>
        <div style={{ display: "grid", gap: 6 }}>
          <ProfileStatus label={currentProfileName} {...currentProfileStatus} />
          <ProfileStatus label="Engineered lighting" {...engineeredProfileStatus} />
        </div>
        <div style={{ fontFamily: AIM_MONO, fontSize: 11, lineHeight: 1.4, color: "var(--hg-fg-3)" }}>
          {profileView === "auto" ? `Auto is showing ${profileResolution.resolved === "current" ? "the current light" : "Engineered lighting"} · status remains visible for both.`
            : profileView === "both" ? "Both identities share this exact mount; no second fixture is created."
              : `${profileView === "current" ? "Current light" : "Engineered lighting"} view selected.`}
        </div>
      </section>}
      <div role="tablist" aria-label="Lighting inspector sections" style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 5,
        padding: 3, borderRadius: 11, background: "rgba(255,255,255,0.035)" }}>
        {[["aim", "Light"], ["zones", "Radials"], ["setup", "Setup"]].map(([name, label]) =>
          <AimButton key={name} active={tab === name} onClick={() => {
            setTab(name);
            if (name === "zones" && engineeredConfigured) setProfileView("engineered");
          }} style={{ border: 0, minHeight: 36 }}>{label}</AimButton>)}
      </div>
    </div>

    <div style={{ overflowY: "auto", overflowX: "hidden", padding: 15, display: "grid", gap: 15,
      width: "100%", minWidth: 0, boxSizing: "border-box" }}>
      {!fixture && <div style={{ fontFamily: AIM_MONO, fontSize: 10 }}>No ceiling fixtures are available.</div>}
      {fixture && fixture.fixture_kind !== A.ENGINEERED_KIND && <section style={{ display: "grid", gap: 10 }}>
        <div style={{ fontFamily: AIM_SANS, fontSize: 14, fontWeight: 570 }}>Add Engineered lighting here</div>
        <div style={{ fontFamily: AIM_SANS, fontSize: 11, lineHeight: 1.5, color: "var(--hg-fg-3)" }}>
          Reuse this exact ceiling position and all of its measurements. The current light remains linked; no second fixture is placed.
        </div>
        {!confirmAdopt ? <AimButton onClick={() => setConfirmAdopt(true)}>Add Engineered profile</AimButton> :
          <div style={{ border: "1px solid rgba(69,223,255,0.28)", borderRadius: 12, padding: 10,
            background: "rgba(69,223,255,0.045)", display: "grid", gap: 9 }}>
            <div style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: AIM_CYAN, lineHeight: 1.5 }}>
              Position, room, target relationships, and Fixture position measurements stay unchanged. This sends no light or gimbal command.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              <AimButton onClick={() => setConfirmAdopt(false)}>Cancel</AimButton>
              <AimButton active onClick={adopt}>Add profile</AimButton>
            </div>
          </div>}
      </section>}

      {fixture?.fixture_kind === A.ENGINEERED_KIND && tab === "aim" && <>
        {profileResolution.show_current && <section data-apt-current-profile="1" style={{ border: "1px solid rgba(255,255,255,0.085)", borderRadius: 13,
          padding: 12, display: "grid", gap: 11, background: "rgba(255,255,255,0.025)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: AIM_SANS, fontSize: 14, fontWeight: 560,
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{currentProfileName}</div>
              <div style={{ fontFamily: AIM_MONO, fontSize: 9,
                color: primaryCapabilities.available ? (primaryState?.state === "on" ? AIM_WARM : "var(--hg-fg-4)") : AIM_AMBER,
                marginTop: 3 }}>
                {!primaryCapabilities.available ? "Offline · position retained"
                  : primaryState?.state === "on" ? `${Math.round((+primaryState?.attributes?.brightness || 0) / 2.55)}% · ${primaryState?.attributes?.color_temp_kelvin || "—"} K`
                    : "Off · position retained"}
              </div>
            </div>
            <AimButton active={primaryState?.state === "on"}
              onClick={() => sendLights([primaryEntity], primaryState?.state === "on" ? "turn_off" : "turn_on", {}, "primary")}
              disabled={sim || !primaryEntity || !primaryCapabilities.available}
              title={sim ? "The current-light profile is not simulated" : undefined}
              style={{ minWidth: 62 }}>{primaryState?.state === "on" ? "On" : "Off"}</AimButton>
          </div>
          {primaryCapabilities.brightness && <label style={{ display: "grid", gap: 5, fontFamily: AIM_SANS, fontSize: 11, color: "var(--hg-fg-3)" }}>
            <span style={{ display: "flex", justifyContent: "space-between" }}><span>Brightness</span><span>{Math.round((+primaryState?.attributes?.brightness || 0) / 2.55)}%</span></span>
            <input aria-label="Current light brightness" type="range" min="1" max="255" value={+primaryState?.attributes?.brightness || 1}
              onChange={(e) => sendLights([primaryEntity], "turn_on", { brightness: +e.target.value }, "primary")} />
          </label>}
          {primaryCapabilities.color_temp && <label style={{ display: "grid", gap: 5, fontFamily: AIM_SANS, fontSize: 11, color: "var(--hg-fg-3)" }}>
            <span style={{ display: "flex", justifyContent: "space-between" }}><span>Warmth</span><span>{primaryState?.attributes?.color_temp_kelvin || "—"} K</span></span>
            <input aria-label="Current light color temperature" type="range" min={Math.round(primaryCapabilities.min_kelvin || 1800)}
              max={Math.round(primaryCapabilities.max_kelvin || 6500)} value={+primaryState?.attributes?.color_temp_kelvin || 3000}
              onChange={(e) => sendLights([primaryEntity], "turn_on", { color_temp_kelvin: +e.target.value }, "primary")} />
          </label>}
          <div style={{ fontFamily: AIM_MONO, fontSize: 11, color: "var(--hg-fg-3)", lineHeight: 1.45 }}>
            Existing Home Assistant light · shares this mount and its Fixture position measurements.
          </div>
        </section>}
        {profileResolution.show_engineered && <>
        <section style={{ border: "1px solid rgba(255,255,255,0.085)", borderRadius: 13,
          padding: 12, display: "grid", gap: 11, background: "rgba(255,255,255,0.025)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: AIM_SANS, fontSize: 14, fontWeight: 560, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>Engineered spotlight</div>
              <div style={{ fontFamily: AIM_MONO, fontSize: 9,
                color: !spotlightCapabilities.available && !sim ? AIM_CYAN : spotlightHaState?.state === "on" ? AIM_WARM : "var(--hg-fg-4)", marginTop: 3 }}>
                {!spotlightCapabilities.available && !sim ? "Offline · simulated preview only"
                  : spotlightHaState?.state === "on" ? `${Math.round((+spotlightHaState?.attributes?.brightness || 0) / 2.55)}% · ${spotlightHaState?.attributes?.color_temp_kelvin || "—"} K`
                    : "Off · aim retained"}
              </div>
            </div>
            <AimButton active={spotlightHaState?.state === "on"}
              onClick={() => sendLights([spotlightEntity], spotlightHaState?.state === "on" ? "turn_off" : "turn_on", {}, "spotlight")}
              disabled={!spotlightEntity || !spotlightCapabilities.available}
              style={{ minWidth: 62 }}>{spotlightHaState?.state === "on" ? "On" : "Off"}</AimButton>
          </div>
          {spotlightCapabilities.brightness && <label style={{ display: "grid", gap: 5, fontFamily: AIM_SANS, fontSize: 11, color: "var(--hg-fg-3)" }}>
            <span style={{ display: "flex", justifyContent: "space-between" }}><span>Brightness</span><span>{Math.round((+spotlightHaState?.attributes?.brightness || 0) / 2.55)}%</span></span>
            <input aria-label="Spotlight brightness" type="range" min="1" max="255" value={+spotlightHaState?.attributes?.brightness || 1}
              onChange={(e) => sendLights([spotlightEntity], "turn_on", { brightness: +e.target.value }, "spotlight")} />
          </label>}
          {spotlightCapabilities.color_temp && <label style={{ display: "grid", gap: 5, fontFamily: AIM_SANS, fontSize: 11, color: "var(--hg-fg-3)" }}>
            <span style={{ display: "flex", justifyContent: "space-between" }}><span>Warmth</span><span>{spotlightHaState?.attributes?.color_temp_kelvin || "—"} K</span></span>
            <input aria-label="Spotlight color temperature" type="range" min={Math.round(spotlightCapabilities.min_kelvin || 1800)}
              max={Math.round(spotlightCapabilities.max_kelvin || 6500)} value={+spotlightHaState?.attributes?.color_temp_kelvin || 3000}
              onChange={(e) => sendLights([spotlightEntity], "turn_on", { color_temp_kelvin: +e.target.value }, "spotlight")} />
          </label>}
        </section>
        <section style={{ display: "grid", gap: 7 }}>
          <div style={{ fontFamily: AIM_MONO, fontSize: 9.5, color: "var(--hg-fg-3)", letterSpacing: "0.12em" }}>DESTINATION</div>
          <div style={{ fontFamily: AIM_SANS, fontSize: 13, color: destination ? AIM_CYAN : "var(--hg-fg-4)", lineHeight: 1.35 }}>{targetLabel(destination)}{destination?.kind === "mirror" ? " · approximate soft reflection" : ""}</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, maxHeight: 122, overflowY: "auto" }}>
            <AimButton active={destination?.kind === "mirror"}
              disabled={aimTransitioning}
              onClick={() => onDestination({ kind: "mirror", id: "mirror-wash", name: "Mirror wash",
                pos: [fixture.pos[0], fixture.pos[1], fixture.pos[2] + 0.2] })}>Mirror wash</AimButton>
            {(model.targets || []).map((target) => <AimButton key={target.id}
              disabled={aimTransitioning}
              active={destination?.id === target.id}
              onClick={() => onDestination({ kind: "named", id: target.id, name: target.name, pos: target.pos,
                normal: target.normal, target })}>{target.name}</AimButton>)}
          </div>
          <div style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)", lineHeight: 1.5 }}>
            Choose a saved location, Mirror wash, or click the mesh. Dragging still orbits.
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
            minHeight: 26, padding: "0 2px", fontFamily: AIM_MONO, fontSize: 8.5 }}>
            <span style={{ color: "var(--hg-fg-5)", textTransform: "uppercase", letterSpacing: "0.09em" }}>Current aim</span>
            <span style={{ color: currentDestination ? AIM_WARM : "var(--hg-fg-4)", textAlign: "right",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {currentDestination ? targetLabel(currentDestination) : "measured pose"}
            </span>
          </div>
          <AimButton active={destinationIsCurrent}
            disabled={!canSetDestination}
            title={!sim ? "Live movement requires owner authority and an authoritative target-plane binding"
              : destinationIsCurrent ? "This is the simulated current aim"
                : !destination ? "Choose a destination first" : undefined}
            onClick={setSimulatedDestination}
            style={{ minHeight: 42,
              ...(canSetDestination ? { borderColor: AIM_CYAN, background: AIM_CYAN, color: "#071014",
                boxShadow: "0 8px 24px rgba(69,223,255,0.13)" } : {}),
              ...(destinationIsCurrent ? { borderColor: "rgba(255,221,176,0.38)",
                background: "rgba(255,221,176,0.10)", color: AIM_WARM } : {}) }}>
            {!sim ? "Set destination unavailable" : aimTransitioning ? "Aiming…"
              : destinationIsCurrent ? "Simulated aim set" : "Set simulated aim"}
          </AimButton>
          {!sim && <div style={{ fontFamily: AIM_MONO, fontSize: 8, color: AIM_AMBER, lineHeight: 1.45 }}>
            Preview only · live movement authority and frame binding are not available.
          </div>}
        </section>

        <details style={{ borderTop: "1px solid rgba(255,255,255,0.07)", paddingTop: 10 }}>
          <summary className="hg-focusable" style={{ cursor: "pointer", listStyle: "none", fontFamily: AIM_SANS,
            fontSize: 11, color: "var(--hg-fg-3)", padding: "7px 0" }}>Details & calibration status <span style={{ float: "right", color: "var(--hg-fg-5)" }}>⌄</span></summary>
        <section style={{ paddingTop: 8, display: "grid", gap: 5 }}>
          <div style={{ display: "grid", gap: 4, fontFamily: AIM_MONO, fontSize: 9, lineHeight: 1.45, marginBottom: 6 }}>
            <div style={{ color: "var(--hg-fg-4)" }}>HA · <span style={{ color: "var(--hg-fg-2)" }}>{fixture.ha_entity_id || "none"} / {spotlightEntity || "unmapped"}</span></div>
            <div style={{ color: "var(--hg-fg-4)" }}>gimbal · <span style={{ color: "var(--hg-fg-2)" }}>{fixtureBindingId(fixture) || "unbound"}</span></div>
            <div style={{ color: "var(--hg-fg-4)" }}>optic · <span style={{ color: "var(--hg-fg-2)" }}>{fixture.spotlight?.optic_profile?.manufacturer || "unknown"} {fixture.spotlight?.optic_profile?.part || ""} · {fixture.spotlight?.optic_profile?.configured_fwhm_deg || "—"}° FWHM</span></div>
          </div>
          <AimStatus label="telemetry" status={sim ? "measured" : telemetry.valid ? "current" : "unavailable"}
            detail={telemetry.blockers?.join(", ")} />
          <AimStatus label="tape" status={fixture.fixture_calibration?.status || "proposed"} />
          <AimStatus label="visualization" status={calibration ? calibrationCompatible ? calibration.status : "failed" : "proposed"}
            detail={calibration && !calibrationCompatible ? "invalidated by status, Product profile, or collision geometry digest" : ""} />
          <AimStatus label="radial" status={fixture.radial_zones?.orientation_calibration?.status || "proposed"} />
          <div style={{ marginTop: 4, fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)", lineHeight: 1.5 }}>
            walls · {(fixture.fixture_calibration?.wall_distances || []).map((w) => `${w.wall} ${Number.isFinite(w.distance_m) ? w.distance_m.toFixed(3) + " m" : "—"}`).join(" · ") || "—"}<br />
            floor → ceiling · {fixture.fixture_calibration?.floor_to_ceiling_m ?? "—"} m<br />
            ceiling → fixture bottom · {fixture.fixture_calibration?.ceiling_to_fixture_bottom_m ?? "—"} m<br />
            derived fixture-bottom height · {Number.isFinite(fixture.fixture_calibration?.derived_floor_to_bottom_m) ? fixture.fixture_calibration.derived_floor_to_bottom_m.toFixed(3) : "—"} m<br />
            floor → bottom verification · {fixture.fixture_calibration?.floor_to_bottom_verification_m ?? "optional · not recorded"}
          </div>
          <div style={{ marginTop: 5, fontFamily: AIM_MONO, fontSize: 9, lineHeight: 1.55 }}>
            <div style={{ color: AIM_WARM }}>raw encoder · {telemetry.raw ? `${telemetry.raw.pan.toFixed(2)}° / ${telemetry.raw.tilt.toFixed(2)}°` : "unavailable"}</div>
            <div style={{ color: mechanical.ok ? AIM_CYAN : "var(--hg-fg-5)" }}>mechanical · {mechanical.ok ? `${mechanical.pan.toFixed(2)}° pan / ${mechanical.tilt.toFixed(2)}° tilt` : "not calibrated"}</div>
            <div style={{ color: "var(--hg-fg-4)" }}>source · {sim ? "bench simulation" : telemetry.source || telemetryStatus.state} · age {telemetry.ages_ms ? `${Math.max(telemetry.ages_ms.pan, telemetry.ages_ms.tilt).toFixed(0)} ms` : "—"}</div>
            <div style={{ color: "var(--hg-fg-4)" }}>activity/readiness · {telemetry.activity || "idle"} / {String(telemetry.readiness ?? "unknown")}</div>
          </div>
        </section>

        {solve && <section style={{ borderTop: "1px solid var(--hg-border-soft)", paddingTop: 10, display: "grid", gap: 4,
          fontFamily: AIM_MONO, fontSize: 9, lineHeight: 1.5 }}>
          <div style={{ color: AIM_CYAN }}>Apartment visualization solve</div>
          <div>destination · {solve.target.map((v) => v.toFixed(3)).join(", ")} m</div>
          <div>pan / tilt · {solve.pan_deg.toFixed(2)}° / {solve.tilt_deg.toFixed(2)}°</div>
          <div>raw encoder destination · {solve.raw_destination ? `${solve.raw_destination.pan.toFixed(2)}° / ${solve.raw_destination.tilt.toFixed(2)}°` : "visualization calibration unavailable"}</div>
          <div>reachability · <span style={{ color: solve.executable ? AIM_GREEN : AIM_RED }}>{solve.executable ? "reachable preview" : solve.ambiguity || solve.violations.join(", ") || solve.raw_conversion_error || "not executable"}</span></div>
          <div>movement · {solve.physical_travel ? `${solve.physical_travel.pan_deg.toFixed(2)}° pan / ${solve.physical_travel.tilt_deg.toFixed(2)}° tilt` : "fresh measured pose unavailable"}</div>
          <div>mesh footprint · {beamAnalysis?.footprint_kind || "unavailable"}{beamAnalysis?.footprint_hit_fraction != null ? ` · ${Math.round(beamAnalysis.footprint_hit_fraction * 100)}% sampled` : ""}</div>
          <div style={{ color: beamAnalysis?.blocked ? AIM_RED : "var(--hg-fg-3)" }}>obstruction · {beamAnalysis?.blocked ? "geometry hit before destination" : beamAnalysis?.collision_available ? "none before requested destination" : "collision proxy unavailable"}</div>
          <div style={{ color: "var(--hg-fg-5)" }}>beam overlap with named surfaces is estimated; no completed operation is correlated.</div>
        </section>}

        <section style={{ borderTop: "1px solid var(--hg-border-soft)", paddingTop: 10, display: "grid", gap: 7 }}>
          <div style={{ fontFamily: AIM_MONO, fontSize: 9, color: sim && product?.qualified ? AIM_CYAN : AIM_AMBER }}>Authoritative Product Aim request</div>
          {sim && product?.qualified ? <>
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-2)", lineHeight: 1.45 }}>{`SIMULATION ONLY\nPOST ${product.endpoint}\n${JSON.stringify(product.body, null, 2)}`}</pre>
            <AimButton onClick={() => navigator.clipboard?.writeText(JSON.stringify(product.body))}>copy simulated request</AimButton>
          </> : <>
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)", lineHeight: 1.45 }}>{`POST /api/group/aim\n{ target_x_m, target_y_m, post_dwell_ms }`}</pre>
            {liveBlockers.map((b) => <div key={b} style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: AIM_AMBER }}>blocked · {b}</div>)}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 5 }}>
              <AimButton disabled>copy command</AimButton><AimButton disabled>move light</AimButton>
            </div>
          </>}
        </section>

        <AimButton onClick={() => window.open("http://127.0.0.1:8765/", "_blank", "noopener")}>open bench console</AimButton>
        <div style={{ fontFamily: AIM_MONO, fontSize: 11, color: "var(--hg-fg-3)", lineHeight: 1.45 }}>
          FWHM is the half-maximum contour. Cone falloff and screen color temperature are qualitative, not photometric claims.
        </div>
        </details>
        </>}
      </>}

      {fixture?.fixture_kind === A.ENGINEERED_KIND && tab === "setup" && <section style={{ display: "grid", gap: 12 }}>
        <div style={{ fontFamily: AIM_SANS, fontSize: 14, fontWeight: 560 }}>Entity mapping</div>
        <div style={{ fontFamily: AIM_MONO, fontSize: 9, lineHeight: 1.5, color: "var(--hg-fg-3)" }}>
          Primary fixture link · {fixture.ha_entity_id || "none"}<br />It does not imply a spotlight or radial role.
        </div>
        <div style={{ border: "1px solid var(--hg-border-soft)", padding: 9, display: "grid", gap: 8 }}>
          <div style={{ fontFamily: AIM_MONO, fontSize: 8, color: AIM_CYAN, letterSpacing: "0.1em" }}>ENGINEERED IDENTITY</div>
          <AimTextInput label="stable gimbal device ID" value={fixtureBindingId(fixture)} placeholder="explicit device binding"
            onCommit={(value) => updateFixture({ ...fixture, gimbal: { ...fixture.gimbal,
              device_binding: value ? { ...(fixture.gimbal.device_binding || {}), stable_id: value } : null } })} />
          <AimTextInput label="stable USB identity · optional" value={fixture.gimbal?.device_binding?.usb_identity} placeholder="VID:PID:serial"
            onCommit={(value) => updateFixture({ ...fixture, gimbal: { ...fixture.gimbal,
              device_binding: value || fixtureBindingId(fixture) ? { ...(fixture.gimbal.device_binding || {}), usb_identity: value } : null } })} />
          <AimTextInput label="Product profile SHA-256" value={fixture.gimbal?.product_profile_sha256} placeholder="64 hexadecimal characters"
            onCommit={(value) => updateFixture({ ...fixture, gimbal: { ...fixture.gimbal, product_profile_sha256: value } })} />
          <AimTextInput label="configured full FWHM · degrees" type="number" min="0.1" max="179" step="0.1"
            value={fixture.spotlight?.optic_profile?.configured_fwhm_deg}
            onCommit={(value) => updateFixture({ ...fixture, spotlight: { ...fixture.spotlight,
              optic_profile: { ...fixture.spotlight.optic_profile, configured_fwhm_deg: value } } })} />
        </div>
        <AimSelect label="explicit spotlight entity" value={fixture.spotlight?.entity_id}
          onChange={(id) => setMapping("spotlight", id)}>
          <option value="">unmapped</option>
          {fixture.spotlight?.entity_id && !lightEntities.some((e) => e.entity_id === fixture.spotlight.entity_id)
            && <option value={fixture.spotlight.entity_id}>{fixture.spotlight.entity_id} · {sim ? "simulation" : "not in current registry"}</option>}
          {roleOptions(fixture.spotlight?.entity_id).map((e) => <option key={e.entity_id} value={e.entity_id}>{entityName(e, states)} · {e.entity_id}</option>)}
        </AimSelect>
        <div style={{ display: "grid", gap: 7 }}>
          {fixture.radial_zones.zones.map((zone) => <AimSelect key={zone.number} label={`radial zone ${zone.number}`}
            value={zone.entity_id} onChange={(id) => setMapping("radial", id, zone.number)}>
            <option value="">unmapped</option>
            {zone.entity_id && !lightEntities.some((e) => e.entity_id === zone.entity_id)
              && <option value={zone.entity_id}>{zone.entity_id} · {sim ? "simulation" : "not in current registry"}</option>}
            {roleOptions(zone.entity_id).map((e) => <option key={e.entity_id} value={e.entity_id}>{entityName(e, states)} · {e.entity_id}</option>)}
          </AimSelect>)}
        </div>
        <div style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)", lineHeight: 1.5 }}>
          Available Home Assistant lights · {lightEntities.length}. Names are never used to map roles automatically. Duplicate placement is blocked across fixtures.
        </div>
      </section>}

      {fixture?.fixture_kind === A.ENGINEERED_KIND && tab === "zones" && (() => {
        const zone = fixture.radial_zones.zones.find((item) => item.number === selectedZone) || fixture.radial_zones.zones[0];
        const zoneState = sim ? { state: runtimeStates?.zones?.[zone.number]?.state, attributes: {
          brightness: runtimeStates?.zones?.[zone.number]?.brightness,
          color_temp_kelvin: runtimeStates?.zones?.[zone.number]?.color_temp_kelvin,
          supported_color_modes: ["brightness", "color_temp"], min_color_temp_kelvin: 1800, max_color_temp_kelvin: 6500,
        } } : states?.[zone.entity_id];
        const cap = A.lightCapabilities(zoneState);
        const mappedIds = fixture.radial_zones.zones.map((item) => item.entity_id).filter(Boolean);
        return <section style={{ display: "grid", gap: 13 }}>
          <div>
            <div style={{ fontFamily: AIM_SANS, fontSize: 14, fontWeight: 560 }}>Radial glow</div>
            <div style={{ fontFamily: AIM_SANS, fontSize: 11, lineHeight: 1.45, color: "var(--hg-fg-4)", marginTop: 4 }}>
              Tap a segment here—or around the fixture in the room—to toggle it.
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
            {fixture.radial_zones.zones.map((item) => {
              const itemState = sim ? runtimeStates?.zones?.[item.number] : states?.[item.entity_id];
              const on = itemState?.state === "on";
              return <button key={item.number} type="button" className="hg-focusable"
                aria-label={`Toggle radial zone ${item.number}`} aria-pressed={on}
                onClick={() => { setSelectedZone(item.number); sendLights([item.entity_id], on ? "turn_off" : "turn_on", {}, `zone-${item.number}`); }}
                disabled={!item.entity_id}
                style={{ minHeight: 62, borderRadius: 14, border: `1px solid ${selectedZone === item.number ? "rgba(255,255,255,0.34)" : "rgba(255,255,255,0.08)"}`,
                  background: on ? "radial-gradient(circle at 50% 40%, rgba(255,238,202,0.28), rgba(255,198,112,0.06) 70%)" : "rgba(255,255,255,0.025)",
                  boxShadow: on ? "inset 0 0 24px rgba(255,211,150,0.08)" : "none",
                  color: on ? "#fff3dc" : "var(--hg-fg-4)", fontFamily: AIM_SANS, fontSize: 12, cursor: item.entity_id ? "pointer" : "default" }}>
                <span style={{ display: "block", fontSize: 16, marginBottom: 3 }}>{item.number}</span>
                <span style={{ fontFamily: AIM_MONO, fontSize: 8 }}>{item.entity_id ? (on ? "ON" : "OFF") : "UNMAPPED"}</span>
              </button>;
            })}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7 }}>
            <AimButton onClick={() => sendLights(mappedIds, "turn_on", {}, "radials-all")}>All on</AimButton>
            <AimButton onClick={() => sendLights(mappedIds, "turn_off", {}, "radials-all")}>All off</AimButton>
          </div>
          <div style={{ border: "1px solid rgba(255,255,255,0.085)", borderRadius: 13, padding: 12, display: "grid", gap: 10,
            background: "rgba(255,255,255,0.025)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ fontFamily: AIM_SANS, fontSize: 13, fontWeight: 560 }}>Zone {zone.number}</div>
              <div style={{ marginLeft: "auto", fontFamily: AIM_MONO, fontSize: 9, color: zoneState?.state === "on" ? AIM_WARM : "var(--hg-fg-4)" }}>{cap.available ? zoneState?.state : "unavailable"}</div>
            </div>
            {cap.brightness && <label style={{ display: "grid", gap: 5, fontFamily: AIM_SANS, fontSize: 11, color: "var(--hg-fg-3)" }}>
              <span style={{ display: "flex", justifyContent: "space-between" }}><span>Brightness</span><span>{Math.round((+zoneState?.attributes?.brightness || 0) / 2.55)}%</span></span>
              <input aria-label={`Radial zone ${zone.number} brightness`} type="range" min="1" max="255" value={+zoneState?.attributes?.brightness || 1}
                onChange={(e) => sendLights([zone.entity_id], "turn_on", { brightness: +e.target.value }, `zone-${zone.number}`)} />
            </label>}
            {cap.color_temp && <label style={{ display: "grid", gap: 5, fontFamily: AIM_SANS, fontSize: 11, color: "var(--hg-fg-3)" }}>
              <span style={{ display: "flex", justifyContent: "space-between" }}><span>Warmth</span><span>{zoneState?.attributes?.color_temp_kelvin || "—"} K</span></span>
              <input aria-label={`Radial zone ${zone.number} color temperature`} type="range" min={Math.round(cap.min_kelvin || 1800)} max={Math.round(cap.max_kelvin || 6500)}
                value={+zoneState?.attributes?.color_temp_kelvin || 3000}
                onChange={(e) => sendLights([zone.entity_id], "turn_on", { color_temp_kelvin: +e.target.value }, `zone-${zone.number}`)} />
            </label>}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7 }}>
              <AimButton onClick={() => identifyZone(zone)} disabled={!cap.available || !cap.brightness}>Identify</AimButton>
              <AimButton onClick={() => restoreZone(zone.number, true)} disabled={!restoreCandidates[zone.number]}>Restore</AimButton>
            </div>
          </div>
          <AimButton onClick={copyToAll} disabled={!spotlightEntity || !mappedZones.length}>Match spotlight</AimButton>
        </section>;
      })()}

      {fixture?.fixture_kind === A.ENGINEERED_KIND && tab === "setup" && <section style={{ display: "grid", gap: 13,
        borderTop: "1px solid rgba(255,255,255,0.07)", paddingTop: 14 }}>
        <div style={{ fontFamily: AIM_SANS, fontSize: 14, fontWeight: 560 }}>Calibration</div>
        <div style={{ display: "grid", gap: 7 }}>
          <div style={{ fontFamily: AIM_MONO, fontSize: 9, color: AIM_CYAN }}>Gimbal direction · visualization only</div>
          {!sim && <>{liveBlockers.slice(0, 2).map((b) => <div key={b} style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: AIM_AMBER }}>blocked · {b}</div>)}</>}
          <div style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)", lineHeight: 1.5 }}>
            Corner · {destination?.kind === "mesh" ? targetLabel(destination) : "click an exact collision-mesh corner"}<br />
            Horizontal reference · apartment +Y<br />
            Ideal axes cannot correct mounting roll, a nonvertical pan axis, or mechanical nonorthogonality.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 5 }}>
            <AimButton active={calDraft.pan_sign === 1} onClick={() => setCalDraft((d) => ({ ...d, pan_sign: d.pan_sign * -1 }))}>pan sign · {calDraft.pan_sign > 0 ? "+" : "−"}</AimButton>
            <AimButton active={calDraft.tilt_sign === 1} onClick={() => setCalDraft((d) => ({ ...d, tilt_sign: d.tilt_sign * -1 }))}>tilt sign · {calDraft.tilt_sign > 0 ? "+" : "−"}</AimButton>
          </div>
          {sim && <AimButton onClick={() => {
            if (!destination) return setNotice("select a known destination first");
            setCalDraft((d) => ({ ...d, verification: [...new Set([...(d.verification || []), destination.id || targetLabel(destination)])] }));
          }}>add current destination to verification · {calDraft.verification.length}</AimButton>}
          <AimButton onClick={saveVisualizationCalibration} disabled={!sim}>save visualization calibration</AimButton>
          <AimStatus label="result" status={calDraft.verification.length >= 2 ? "verified" : destination?.kind === "mesh" ? "calibrated" : "proposed"}
            detail="One corner can calibrate; multiple known destinations verify." />
        </div>

        <div style={{ borderTop: "1px solid var(--hg-border-soft)", paddingTop: 11, display: "grid", gap: 8 }}>
          <div style={{ fontFamily: AIM_MONO, fontSize: 9, color: AIM_WARM }}>Radial orientation · floor looking upward</div>
          <div style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)" }}>Identify the mapped zone facing the selected exact mesh corner. Entering this workflow never illuminates a zone.</div>
          <AimSelect label="anchor zone" value={String(radialDraft.anchor_zone)} onChange={(v) => setRadialDraft((d) => ({ ...d, anchor_zone: +v }))}>
            {fixture.radial_zones.zones.map((z) => <option key={z.number} value={z.number}>zone {z.number}{z.entity_id ? " · mapped" : " · unmapped"}</option>)}
          </AimSelect>
          <AimSelect label="order as viewed from floor" value={radialDraft.order} onChange={(v) => setRadialDraft((d) => ({ ...d, order: v }))}>
            <option value="clockwise">clockwise</option><option value="counterclockwise">counterclockwise</option>
          </AimSelect>
          <label style={{ fontFamily: AIM_MONO, fontSize: 8.5, color: "var(--hg-fg-4)" }}>
            angular fine adjustment · {radialDraft.fine_adjust_deg}°
            <input className="hg-focusable" type="range" min="-30" max="30" step="0.5" value={radialDraft.fine_adjust_deg}
              onChange={(e) => setRadialDraft((d) => ({ ...d, fine_adjust_deg: +e.target.value }))} style={{ width: "100%" }} />
          </label>
          <AimButton onClick={saveRadialCalibration}>save radial orientation</AimButton>
        </div>
      </section>}

      {notice && <div role="status" style={{ fontFamily: AIM_MONO, fontSize: 8.5, lineHeight: 1.5,
        color: notice.includes("blocked") || notice.includes("failed") ? AIM_RED : AIM_AMBER,
        borderTop: "1px solid var(--hg-border-soft)", paddingTop: 9 }}>{notice}</div>}
    </div>
  </aside>;
}

window.HomeApartmentAim = HomeApartmentAim;
