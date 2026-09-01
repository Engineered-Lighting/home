/* eslint-disable */
/**
 * home-apartment-edit.jsx — /apartment edit mode.
 *
 * Orbitable survey pose, dimmed cloud as basemap. Place devices from the
 * HA-registry palette (click item → click floor), drag existing markers,
 * height presets, zone polygon drawing, undo/redo (snapshot stack), save
 * through the apartment_model endpoint (409-aware upstream).
 */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

const ED_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const ED_SANS = '"Geist", "Inter", sans-serif';

const HEIGHT_PRESETS = [
  ["floor", 0.0], ["table", 0.75], ["counter", 0.9],
  ["shelf", 1.5], ["wall", 1.8], ["ceiling", null], // null → zone ceiling
];
const DOMAIN_DEFAULT_HEIGHT = { light: 2.35, media_player: 1.5, camera: 2.2, switch: 1.0 };
const DOMAIN_TYPE = { light: "light", media_player: "speaker", camera: "camera", switch: "light" };
const ZONE_COLORS = ["#b8d8ff", "#a8ffd8", "#ffe2a8", "#d8a8ff", "#ffb8c8", "#c8ffb8"];
const SURVEY_ORANGE = "#ffb45f";
const FIXTURE_STATUS = {
  proposed: { symbol: "○", color: "var(--hg-fg-3)", detail: "seeded or estimated position · no tape entered" },
  measured: { symbol: "◔", color: "#f2cf87", detail: "some tape values entered · calibration incomplete" },
  calibrated: { symbol: "◐", color: SURVEY_ORANGE, detail: "two walls + vertical measurements solve fixture bottom" },
  verified: { symbol: "●", color: "#91e6bd", detail: "optional floor-to-bottom check recorded" },
};
const TARGET_PRESETS = [
  { category: "table", shape: "surface", label: "table", size_m: [1.2, 0.75], horizontal: true },
  { category: "island", shape: "surface", label: "island", size_m: [1.2, 0.65], horizontal: true },
  { category: "art", shape: "surface", label: "art", size_m: [0.9, 0.65], wallOnly: true },
  { category: "custom", shape: "point", label: "custom point" },
  { category: "custom", shape: "surface", label: "custom surface", size_m: [0.6, 0.6] },
];
const WALL_LABELS = [
  ["west", "west wall"], ["east", "east wall"],
  ["south", "south wall"], ["north", "north wall"],
];

function edSlug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function uniqueZoneId(name, zones) {
  const base = edSlug(name) || "zone";
  const used = new Set((zones || []).map((zone) => zone.id));
  if (!used.has(base)) return base;
  let suffix = 2;
  while (used.has(`${base}_${suffix}`)) suffix += 1;
  return `${base}_${suffix}`;
}

function EdButton({ label, onClick, active, danger, disabled, title, ariaLabel }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} aria-label={ariaLabel}
      className="hg-focusable" style={{
      background: active ? "var(--hg-ice)" : "transparent",
      border: "1px solid " + (danger ? "var(--hg-crit)" : active ? "var(--hg-ice)" : "var(--hg-border-soft)"),
      color: danger ? "var(--hg-crit)" : active ? "#0b0d11" : disabled ? "var(--hg-fg-5)" : "var(--hg-fg-1)",
      padding: "4px 10px", fontFamily: ED_MONO, fontSize: 9.5, letterSpacing: "0.08em",
      textTransform: "lowercase", cursor: disabled ? "default" : "pointer",
    }}>{label}</button>
  );
}

function EdTapeInput({ meters, onCommit, optional = false, label }) {
  const format = window.HomeApartmentData.formatTapeMeasurement;
  const parse = window.HomeApartmentData.parseTapeMeasurement;
  const [draft, setDraft] = useState(() => format(meters));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { setDraft(format(meters)); setInvalid(false); }, [meters]);
  const commit = () => {
    if (!String(draft).trim()) { setInvalid(false); onCommit(null); return; }
    const next = parse(draft);
    if (!Number.isFinite(next) || next < 0) { setInvalid(true); return; }
    setInvalid(false);
    setDraft(format(next));
    onCommit(Math.round(next * 10000) / 10000);
  };
  return (
    <label style={{ display: "grid", gap: 4 }}>
      <span style={{ fontSize: 8.5, color: "var(--hg-fg-4)", letterSpacing: "0.08em" }}>
        {label}{optional ? " · optional" : ""}
      </span>
      <input value={draft} placeholder={optional ? "optional" : "e.g. 8′ 0″"}
        onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }}
        className="hg-focusable" aria-invalid={invalid ? "true" : "false"}
        style={{ width: "100%", background: "var(--hg-bg-0)",
                 border: `1px solid ${invalid ? "var(--hg-crit)" : "var(--hg-border-soft)"}`,
                 color: invalid ? "var(--hg-crit)" : "var(--hg-fg-1)", fontFamily: ED_MONO,
                 fontSize: 10, padding: "5px 6px" }} />
      {invalid && <span style={{ fontSize: 8, color: "var(--hg-crit)" }}>use inches, 8′ 0″, cm, or m</span>}
    </label>
  );
}

function targetDisplayName(preset, targets) {
  const base = preset.category === "custom"
    ? (preset.shape === "point" ? "point" : "surface")
    : preset.category;
  const used = new Set((targets || []).map((target) => String(target.name || "").toLowerCase()));
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(`${base} ${index}`)) index += 1;
  return `${base} ${index}`;
}

function targetUp(normal) {
  return Math.abs(normal[2]) < 0.75 ? [0, 0, 1] : [0, 1, 0];
}

function vecDot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function vecCross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function vecUnit(value, fallback = [0, 0, 1]) {
  const v = Array.isArray(value) ? value.map((n) => +n || 0) : fallback;
  const length = Math.hypot(v[0], v[1], v[2]);
  return length > 0.00001 ? v.map((n) => n / length) : [...fallback];
}

function targetReferenceBasis(normalValue) {
  const normal = vecUnit(normalValue);
  let up = vecUnit(targetUp(normal));
  let right = vecCross(up, normal);
  if (Math.hypot(...right) < 0.00001) {
    up = Math.abs(normal[2]) < 0.75 ? [0, 0, 1] : [0, 1, 0];
    right = vecCross(up, normal);
  }
  right = vecUnit(right, [1, 0, 0]);
  up = vecUnit(vecCross(normal, right), [0, 1, 0]);
  return { normal, right, up };
}

function targetRotationDegrees(target) {
  const basis = targetReferenceBasis(target?.normal || [0, 0, 1]);
  const currentUp = vecUnit(target?.up || basis.up, basis.up);
  const currentRight = vecUnit(vecCross(currentUp, basis.normal), basis.right);
  const sin = vecDot(basis.normal, vecCross(basis.right, currentRight));
  const cos = Math.max(-1, Math.min(1, vecDot(basis.right, currentRight)));
  return Math.round(Math.atan2(sin, cos) * 180 / Math.PI * 10) / 10;
}

function targetUpForRotation(normalValue, degrees) {
  const basis = targetReferenceBasis(normalValue);
  const radians = (+degrees || 0) * Math.PI / 180;
  const normalCrossRight = vecCross(basis.normal, basis.right);
  const right = vecUnit(basis.right.map((value, index) =>
    value * Math.cos(radians) + normalCrossRight[index] * Math.sin(radians)), basis.right);
  return vecUnit(vecCross(basis.normal, right), basis.up)
    .map((value) => Math.round(value * 10000) / 10000);
}

function zoneIdAt(zones, x, y) {
  for (const z of zones || []) {
    const poly = z.floor_polygon || [];
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      if (((poly[i][1] > y) !== (poly[j][1] > y))
          && (x < (poly[j][0] - poly[i][0]) * (y - poly[i][1])
            / (poly[j][1] - poly[i][1]) + poly[i][0])) inside = !inside;
    }
    if (inside) return z.id;
  }
  return null;
}

function refreshRoomAssignments(model) {
  for (const item of [...(model.devices || []), ...(model.targets || [])]) {
    if (!Array.isArray(item.pos) || item.pos.length < 2) continue;
    item.room_id = zoneIdAt(model.zones, item.pos[0], item.pos[1]);
  }
}

function HomeApartmentEdit({
  engine, model, onModel, seedModel, registry, onSave, onDirtyChange,
  onExit, sim, connection, sourceMeta, saveStatus, saving,
  liveReview, liveComparison, onCompareLive,
}) {
  const [tool, setTool] = useState("select");          // select | add | targets | links | measure | zones
  const [placing, setPlacing] = useState(null);        // palette item being placed
  const [placingTarget, setPlacingTarget] = useState(null);
  const [selectedId, setSelectedId] = useState(null);  // device id
  const [selectedTargetId, setSelectedTargetId] = useState(null);
  const [moveTargetId, setMoveTargetId] = useState(null);
  const [targetDraftId, setTargetDraftId] = useState(null);
  const [selectedZone, setSelectedZone] = useState(null);
  const [selectedZoneVertex, setSelectedZoneVertex] = useState(null);
  const [zoneDraft, setZoneDraft] = useState([]);      // [[x,y]...] while drawing
  const [zoneDrawing, setZoneDrawing] = useState(false);
  const [notice, setNotice] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pendingLinkEntity, setPendingLinkEntity] = useState("");
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const undoRef = useRef({ stack: [], redo: [] });
  const dragRef = useRef(null);
  const placementGestureRef = useRef(null);
  const targetModeRef = useRef(null);

  const palette = useMemo(
    () => window.HomeApartmentData.buildPalette(registry, model),
    [registry, model],
  );
  const selected = (model.devices || []).find((d) => d.id === selectedId) || null;
  const selectedTarget = (model.targets || []).find((target) => target.id === selectedTargetId) || null;
  const zone = (model.zones || []).find((z) => z.id === selectedZone) || null;
  const selectedRoom = (model.zones || []).find((z) => z.id === selected?.room_id) || null;
  const ceilingLights = (model.devices || []).filter(window.HomeApartmentData.isCeilingLight);
  const fixtureMapping = useMemo(
    () => window.HomeApartmentData.buildFixtureMapping(registry, model, seedModel),
    [registry, model, seedModel],
  );
  const linkableLightEntities = useMemo(() => {
    const linkedElsewhere = new Set((model.devices || [])
      .filter((device) => device.id !== selectedId && device.ha_entity_id)
      .map((device) => device.ha_entity_id));
    return fixtureMapping.allLights.filter((entity) => !linkedElsewhere.has(entity.entity_id));
  }, [fixtureMapping.allLights, model.devices, selectedId]);

  const mutate = useCallback((fn, { undoable = true } = {}) => {
    onModel((prev) => {
      if (undoable) {
        undoRef.current.stack.push(JSON.parse(JSON.stringify(prev)));
        if (undoRef.current.stack.length > 50) undoRef.current.stack.shift();
        undoRef.current.redo = [];
      }
      const next = JSON.parse(JSON.stringify(prev));
      fn(next);
      return next;
    });
    setDirty(true);
    onDirtyChange?.();
  }, [onModel, onDirtyChange]);

  const undo = useCallback(() => {
    const u = undoRef.current;
    if (!u.stack.length) return;
    onModel((prev) => { u.redo.push(JSON.parse(JSON.stringify(prev))); return u.stack.pop(); });
    setDirty(true);
    onDirtyChange?.();
  }, [onModel, onDirtyChange]);
  const redo = useCallback(() => {
    const u = undoRef.current;
    if (!u.redo.length) return;
    onModel((prev) => { u.stack.push(JSON.parse(JSON.stringify(prev))); return u.redo.pop(); });
    setDirty(true);
    onDirtyChange?.();
  }, [onModel, onDirtyChange]);

  useEffect(() => {
    setPendingLinkEntity(selected?.ha_entity_id || "");
    setConfirmUnlink(false);
  }, [selectedId, selected?.ha_entity_id]);

  useEffect(() => {
    if (saveStatus?.state === "saved") setDirty(false);
  }, [saveStatus?.state]);

  /* Enter an immediately usable isometric survey pose. Re-apply the canvas
     size on the next frame so a just-mounted editor does not wait for a real
     browser resize before its camera fit becomes correct. */
  useEffect(() => {
    if (!engine) return undefined;
    const host = engine.renderer.domElement.parentElement;
    const syncSurveyView = () => {
      const width = host?.clientWidth || window.innerWidth || 1;
      const height = host?.clientHeight || window.innerHeight || 1;
      engine.setSize?.(width, height);
      engine.rig.goEditPose(0);
    };
    syncSurveyView();
    const frame = requestAnimationFrame(syncSurveyView);
    engine.overlay.setZonesVisible(0.7);
    const prevOpacity = engine.pointsMaterial.uniforms.uOpacity.value;
    engine.pointsMaterial.uniforms.uOpacity.value = Math.min(prevOpacity, 0.4);
    return () => {
      cancelAnimationFrame(frame);
      engine.rig.exitEditPose();
      engine.overlay.setZonesVisible(0);
      engine.overlay.setTargetHover?.(null, null);
      engine.overlay.setFixtureCalibration?.(null, null);
      engine.pointsMaterial.uniforms.uOpacity.value = 1.0;
    };
  }, [engine]);

  useEffect(() => {
    if (!engine?.overlay.setZoneEdit) return undefined;
    engine.overlay.setZoneEdit(selectedZone, tool === "zones", selectedZoneVertex);
    return () => engine.overlay.setZoneEdit(null, false);
  }, [engine, tool, selectedZone, selectedZoneVertex, model.zones]);

  useEffect(() => {
    if (!engine?.overlay.setZoneDraft) return undefined;
    engine.overlay.setZoneDraft(tool === "zones" && zoneDrawing ? zoneDraft : []);
    return () => engine.overlay.setZoneDraft([]);
  }, [engine, tool, zoneDrawing, zoneDraft]);

  useEffect(() => {
    if (!engine) return;
    engine.overlay.setTargetHover?.(null, selectedTargetId);
  }, [engine, selectedTargetId, model.targets]);

  useEffect(() => {
    if (!engine) return;
    const fixture = window.HomeApartmentData.isCeilingLight(selected) ? selected : null;
    engine.overlay.setFixtureCalibration?.(fixture, selectedRoom);
  }, [engine, selected, selectedRoom]);

  useEffect(() => {
    if (!engine || tool !== "targets") return undefined;
    targetModeRef.current = engine.modes.mode;
    let cancelled = false;
    setNotice("loading the collision mesh for exact surface picks…");
    engine.modes.setMode("mesh", { duration: 180 }).then(() => {
      if (!cancelled) setNotice(placingTarget ? "click the matching mesh surface" : "choose a target kind");
    }).catch(() => {
      if (!cancelled) setNotice("mesh unavailable · point targets can still use the floor");
    });
    return () => {
      cancelled = true;
      const previous = targetModeRef.current;
      targetModeRef.current = null;
      if (previous && engine.modes.mode !== previous) {
        engine.modes.setMode(previous, { duration: 120 }).catch(() => {});
      }
    };
  }, [engine, tool]);

  const roomAt = useCallback((x, y) => {
    return zoneIdAt(model.zones, x, y);
  }, [model.zones]);

  const finishZone = useCallback(() => {
    if (zoneDraft.length < 3) {
      setNotice("a room zone needs at least three corners");
      return;
    }
    const response = prompt("zone name (e.g. living room)");
    if (response == null) {
      setNotice("Finish cancelled · the new boundary is still available");
      return;
    }
    const name = response.trim() || `zone ${(model.zones || []).length + 1}`;
    const id = uniqueZoneId(name, model.zones);
    const draft = zoneDraft.map((point) => [...point]);
    mutate((m) => {
      m.zones = m.zones || [];
      m.zones.push({
        id, name: name.toLowerCase(),
        color: ZONE_COLORS[m.zones.length % ZONE_COLORS.length],
        floor_polygon: draft, ceiling_height_m: 2.4, frigate_camera: id,
      });
      refreshRoomAssignments(m);
    });
    setZoneDraft([]);
    setZoneDrawing(false);
    setSelectedZone(id);
    setSelectedZoneVertex(null);
    setNotice(`${name} created · drag its corners to match the scan`);
  }, [zoneDraft, model.zones, mutate]);

  /* canvas interactions while edit mode is active */
  useEffect(() => {
    if (!engine) return undefined;
    const host = engine.renderer.domElement.parentElement;

    const placeTargetAt = (clientX, clientY) => {
      if (tool !== "targets" || !placingTarget) return false;
      const fp = engine.picking.floorPoint(engine.apartmentRoot, clientX, clientY);
      const mesh = engine.modes.getMesh?.();
      const hit = mesh
        ? engine.picking.surfaceHit(engine.apartmentRoot, [mesh], clientX, clientY)
        : null;
      if (placingTarget.wallOnly && (!hit || Math.abs(hit.normal[2]) > 0.65)) {
        setNotice("art targets need a vertical wall face · drag to orbit, then click the wall");
        return true;
      }
      if (placingTarget.horizontal && hit && Math.abs(hit.normal[2]) < 0.65) {
        setNotice(`${placingTarget.label} targets need a horizontal face`);
        return true;
      }
      if (!hit && placingTarget.shape !== "point") {
        setNotice("surface targets require the collision mesh · check the Apartment asset service");
        return true;
      }
      const point = hit?.point || (fp ? [fp[0], fp[1], 0] : null);
      if (!point) {
        setNotice("no mesh surface under the pointer");
        return true;
      }
      const normal = placingTarget.horizontal ? [0, 0, 1] : (hit?.normal || [0, 0, 1]);
      const pos = point.map((value) => Math.round(value * 1000) / 1000);
      const name = targetDisplayName(placingTarget, model.targets);
      const id = `target-${edSlug(name)}-${Date.now().toString(36)}`;
      mutate((m) => {
        m.targets = m.targets || [];
        m.targets.push({
          id, name, category: placingTarget.category, shape: placingTarget.shape,
          pos, normal: normal.map((value) => Math.round(value * 10000) / 10000),
          up: targetUp(normal),
          ...(placingTarget.size_m ? { size_m: [...placingTarget.size_m] } : {}),
          room_id: roomAt(pos[0], pos[1]), source: "manual", confidence: 1,
          updated_at: new Date().toISOString(),
        });
      });
      setSelectedTargetId(id);
      setTargetDraftId(id);
      setSelectedId(null);
      setSelectedZone(null);
      setPlacingTarget(null);
      setMoveTargetId(null);
      setNotice(`${name} placed · drag inside it, rotate, resize, then Finish placement`);
      return true;
    };

    const beginOrbitGesture = (e, placesTarget = false) => {
      const now = performance.now();
      placementGestureRef.current = {
        pointerId: e.pointerId, x0: e.clientX, y0: e.clientY,
        lastX: e.clientX, lastT: now, vx: 0, moved: false, placesTarget,
      };
      try { host.setPointerCapture?.(e.pointerId); } catch (err) { /* optional */ }
      e.preventDefault();
      e.stopPropagation();
    };

    const onDown = (e) => {
      if (e.button !== 0) return;
      if (e.target?.closest?.("[data-apt-edit-ui]")) return;
      const fp = engine.picking.floorPoint(engine.apartmentRoot, e.clientX, e.clientY);
      if (tool === "zones") {
        if (zoneDrawing && fp) {
          setZoneDraft((d) => [...d, [Math.round(fp[0] * 100) / 100, Math.round(fp[1] * 100) / 100]]);
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        const zoneHit = engine.picking.pick(engine.overlay.zonePickObjects?.() || [], e.clientX, e.clientY)[0];
        const hitObject = zoneHit?.object;
        const zoneId = hitObject?.userData?.zoneId;
        const hitZone = (model.zones || []).find((candidate) => candidate.id === zoneId);
        if (zoneId && hitZone && fp) {
          const vertexIndex = Number.isInteger(hitObject.userData.zoneVertexIndex)
            ? hitObject.userData.zoneVertexIndex : null;
          setSelectedZone(zoneId);
          setSelectedZoneVertex(vertexIndex);
          setSelectedId(null);
          setSelectedTargetId(null);
          dragRef.current = {
            id: zoneId,
            kind: vertexIndex == null ? "zone-body" : "zone-vertex",
            vertexIndex,
            start: [...fp],
            originalPoly: (hitZone.floor_polygon || []).map((point) => [...point]),
            moved: false,
          };
          try { host.setPointerCapture?.(e.pointerId); } catch (err) { /* optional */ }
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        beginOrbitGesture(e, false);
        return;
      }
      if (tool === "targets" && moveTargetId) {
        const target = (model.targets || []).find((candidate) => candidate.id === moveTargetId);
        const mesh = engine.modes.getMesh?.();
        const hit = mesh
          ? engine.picking.surfaceHit(engine.apartmentRoot, [mesh], e.clientX, e.clientY)
          : null;
        if (!target) {
          setMoveTargetId(null);
          return;
        }
        if (target.category === "art" && (!hit || Math.abs(hit.normal[2]) > 0.65)) {
          setNotice("art stays vertical · click a wall face");
          e.stopPropagation();
          return;
        }
        if (["table", "island"].includes(target.category) && hit && Math.abs(hit.normal[2]) < 0.65) {
          setNotice(`${target.category} stays horizontal · click a horizontal face`);
          e.stopPropagation();
          return;
        }
        if (!hit && target.shape !== "point") {
          setNotice("surface move needs the collision mesh · check the Apartment asset service");
          e.stopPropagation();
          return;
        }
        const point = hit?.point || (fp ? [fp[0], fp[1], target.pos?.[2] || 0] : null);
        if (!point) {
          setNotice("no valid surface under the pointer");
          e.stopPropagation();
          return;
        }
        const previousRotation = targetRotationDegrees(target);
        const normal = ["table", "island"].includes(target.category)
          ? [0, 0, 1] : (hit?.normal || target.normal || [0, 0, 1]);
        const pos = point.map((value) => Math.round(value * 1000) / 1000);
        mutate((m) => {
          const next = (m.targets || []).find((candidate) => candidate.id === moveTargetId);
          if (!next) return;
          next.pos = pos;
          next.normal = normal.map((value) => Math.round(value * 10000) / 10000);
          next.up = targetUpForRotation(next.normal, previousRotation);
          next.room_id = roomAt(pos[0], pos[1]);
          next.updated_at = new Date().toISOString();
        });
        setMoveTargetId(null);
        setNotice(`${target.name} moved · drag inside the rectangle for another adjustment`);
        e.stopPropagation();
        e.preventDefault();
        return;
      }
      if (tool === "targets" && placingTarget) {
        // Edit mode owns this gesture instead of relying on the overview
        // listener beneath its capture/picking layer. A click places; a drag
        // always orbits.
        beginOrbitGesture(e, true);
        return;
      }
      if (tool === "add" && placing && fp) {
        if ((model.devices || []).some((device) => device.ha_entity_id === placing.entity_id)) {
          setNotice(`${placing.entity_id} is already linked · duplicate placement blocked`);
          setPlacing(null);
          e.stopPropagation();
          return;
        }
        const sx = Math.round(fp[0] * 10) / 10, sy = Math.round(fp[1] * 10) / 10;
        const roomId = roomAt(sx, sy);
        const room = (model.zones || []).find((candidate) => candidate.id === roomId);
        const ceilingFixture = placing.domain === "light" && placing.placement_kind === "ceiling_fixture";
        const h = ceilingFixture
          ? Math.max(2, +(room?.ceiling_height_m || 2.4) - 0.1)
          : placing.domain === "light" || placing.domain === "switch"
            ? 0.8 : (DOMAIN_DEFAULT_HEIGHT[placing.domain] ?? 1.0);
        const id = `dev-${edSlug(placing.entity_id)}`;
        mutate((m) => {
          m.devices = m.devices || [];
          let device = {
            id, type: DOMAIN_TYPE[placing.domain] || "other", name: placing.name.toLowerCase(),
            ha_entity_id: placing.entity_id, pos: [sx, sy, h], yaw_rad: 0,
            height_preset: ceilingFixture ? "ceiling" : "custom", room_id: roomId, controllable: true,
            confidence: ceilingFixture ? 0.5 : 1, source: ceilingFixture ? "proposed" : "manual",
            updated_at: new Date().toISOString(),
          };
          if (ceilingFixture) device = window.HomeApartmentData.ensureModelShape({
            zones: m.zones || [], devices: [device], targets: [],
          }).devices[0];
          m.devices.push(device);
        });
        setSelectedId(id);
        setSelectedTargetId(null);
        setPlacing(null);
        setTool("select");
        setNotice(ceilingFixture
          ? "fixture placed as proposed · enter tape measurements to calibrate its fixture-bottom origin"
          : "device placed · non-ceiling lights do not receive fixture calibration");
        e.stopPropagation();
        return;
      }
      // select / start drag
      const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
      if (hits.length) {
        const id = hits[0].object.userData.deviceId;
        setSelectedId(id);
        setSelectedTargetId(null);
        setSelectedZone(null);
        dragRef.current = tool === "select" ? { id, kind: "device", moved: false } : null;
        e.stopPropagation();
      } else {
        const targetHits = engine.picking.pick(engine.overlay.targetPickObjects?.() || [], e.clientX, e.clientY);
        if (targetHits.length) {
          const id = targetHits[0].object.userData.targetId;
          setSelectedTargetId(id);
          setSelectedId(null);
          setSelectedZone(null);
          setPlacingTarget(null);
          setMoveTargetId(null);
          dragRef.current = tool === "select" ? { id, kind: "target", moved: false } : null;
          if (tool === "select") {
            try { host.setPointerCapture?.(e.pointerId); } catch (err) { /* optional */ }
            e.preventDefault();
          }
          e.stopPropagation();
        } else {
          setSelectedId(null);
          setSelectedTargetId(null);
          beginOrbitGesture(e, false);
        }
      }
    };
    const onMove = (e) => {
      const placementGesture = placementGestureRef.current;
      if (placementGesture && placementGesture.pointerId === e.pointerId) {
        const now = performance.now();
        const dx = e.clientX - placementGesture.x0;
        const dy = e.clientY - placementGesture.y0;
        const dt = Math.max(1, now - placementGesture.lastT);
        placementGesture.vx = (e.clientX - placementGesture.lastX) / dt;
        placementGesture.lastX = e.clientX;
        placementGesture.lastT = now;
        if (Math.hypot(dx, dy) > 8) placementGesture.moved = true;
        engine.rig.dragPreview(dx, dy, host.clientWidth || 800);
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const d = dragRef.current;
      if (!d) return;
      if (d.kind === "zone-vertex" || d.kind === "zone-body") {
        const fp = engine.picking.floorPoint(engine.apartmentRoot, e.clientX, e.clientY);
        if (!fp) return;
        const point = [Math.round(fp[0] * 100) / 100, Math.round(fp[1] * 100) / 100];
        const poly = d.originalPoly.map((vertex) => [...vertex]);
        if (d.kind === "zone-vertex") {
          poly[d.vertexIndex] = point;
        } else {
          const dx = point[0] - d.start[0];
          const dy = point[1] - d.start[1];
          for (const vertex of poly) {
            vertex[0] = Math.round((vertex[0] + dx) * 100) / 100;
            vertex[1] = Math.round((vertex[1] + dy) * 100) / 100;
          }
        }
        d.moved = Math.hypot(point[0] - d.start[0], point[1] - d.start[1]) > 0.01;
        d.lastPoly = poly;
        engine.overlay.previewZone?.(d.id, poly);
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (d.kind === "target") {
        const target = (model.targets || []).find((candidate) => candidate.id === d.id);
        if (!target) return;
        const mesh = engine.modes.getMesh?.();
        const hit = mesh
          ? engine.picking.surfaceHit(engine.apartmentRoot, [mesh], e.clientX, e.clientY)
          : null;
        if (target.category === "art" && (!hit || Math.abs(hit.normal[2]) > 0.65)) return;
        if (["table", "island"].includes(target.category) && hit && Math.abs(hit.normal[2]) < 0.65) return;
        if (target.shape === "surface" && !hit) return;
        const fallbackFloor = !hit && target && (["table", "island"].includes(target.category) || target.shape === "point")
          ? engine.picking.floorPoint(engine.apartmentRoot, e.clientX, e.clientY)
          : null;
        if (!hit && !fallbackFloor) return;
        const point = hit?.point || [fallbackFloor[0], fallbackFloor[1], target.pos?.[2] || 0];
        const normal = ["table", "island"].includes(target?.category)
          ? [0, 0, 1] : (hit?.normal || target?.normal || [0, 0, 1]);
        d.moved = true;
        d.last = {
          point: point.map((value) => Math.round(value * 1000) / 1000),
          normal: normal.map((value) => Math.round(value * 10000) / 10000),
        };
        const rendered = engine.overlay.targetsById?.get(d.id);
        if (rendered) rendered.group.position.set(...d.last.point);
        e.stopPropagation();
        return;
      }
      const fp = engine.picking.floorPoint(engine.apartmentRoot, e.clientX, e.clientY);
      if (!fp) return;
      d.moved = true;
      const sx = Math.round(fp[0] * 10) / 10, sy = Math.round(fp[1] * 10) / 10;
      // live-move the marker only; commit to the model on release
      const m = engine.overlay.markersById.get(d.id);
      if (m) m.group.position.set(sx, sy, m.group.position.z);
      d.last = [sx, sy];
      e.stopPropagation();
    };
    const onUp = (e) => {
      const placementGesture = placementGestureRef.current;
      if (placementGesture && placementGesture.pointerId === e.pointerId) {
        placementGestureRef.current = null;
        const moved = placementGesture.moved
          || Math.hypot(e.clientX - placementGesture.x0, e.clientY - placementGesture.y0) > 8;
        const dx = e.clientX - placementGesture.x0;
        const dy = e.clientY - placementGesture.y0;
        engine.rig.dragRelease(dx, dy, placementGesture.vx);
        if (e.type !== "pointercancel" && placementGesture.placesTarget && !moved) {
          placeTargetAt(e.clientX, e.clientY);
        }
        try { host.releasePointerCapture?.(e.pointerId); } catch (err) { /* optional */ }
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const d = dragRef.current;
      dragRef.current = null;
      if (d && (d.kind === "zone-vertex" || d.kind === "zone-body")) {
        if (d.moved && d.lastPoly) {
          mutate((m) => {
            const editedZone = (m.zones || []).find((candidate) => candidate.id === d.id);
            if (editedZone) editedZone.floor_polygon = d.lastPoly;
            refreshRoomAssignments(m);
          });
          setNotice(d.kind === "zone-vertex"
            ? `corner ${d.vertexIndex + 1} moved · room assignments refreshed`
            : "zone moved · room assignments refreshed");
        }
        try { host.releasePointerCapture?.(e.pointerId); } catch (err) { /* optional */ }
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (d && d.moved && d.last) {
        mutate((m) => {
          if (d.kind === "target") {
            const target = (m.targets || []).find((x) => x.id === d.id);
            if (target) {
              const previousRotation = targetRotationDegrees(target);
              target.pos = d.last.point;
              if (target.category !== "table" && target.category !== "island") {
                target.normal = d.last.normal;
              }
              target.up = targetUpForRotation(target.normal || d.last.normal, previousRotation);
              target.room_id = roomAt(d.last.point[0], d.last.point[1]);
              target.updated_at = new Date().toISOString();
            }
            return;
          }
          const dev = (m.devices || []).find((x) => x.id === d.id);
          if (dev) {
            dev.pos[0] = d.last[0]; dev.pos[1] = d.last[1];
            dev.room_id = roomAt(d.last[0], d.last[1]);
            dev.updated_at = new Date().toISOString();
          }
        });
        setNotice(d.kind === "target" ? "target moved · Finish when the outline matches the real surface" : "");
        e.stopPropagation();
      }
      try { host.releasePointerCapture?.(e.pointerId); } catch (err) { /* optional */ }
    };
    const onKey = (e) => {
      if (e.key === "Enter" && tool === "zones" && zoneDrawing) {
        e.preventDefault();
        finishZone();
      }
      if (e.key === "z" && (e.ctrlKey || e.metaKey) && !e.shiftKey) { e.preventDefault(); undo(); }
      if (e.key === "z" && (e.ctrlKey || e.metaKey) && e.shiftKey) { e.preventDefault(); redo(); }
    };

    host.addEventListener("pointerdown", onDown, true);   // capture: beat the rig
    host.addEventListener("pointermove", onMove, true);
    host.addEventListener("pointerup", onUp, true);
    host.addEventListener("pointercancel", onUp, true);
    window.addEventListener("keydown", onKey);
    return () => {
      host.removeEventListener("pointerdown", onDown, true);
      host.removeEventListener("pointermove", onMove, true);
      host.removeEventListener("pointerup", onUp, true);
      host.removeEventListener("pointercancel", onUp, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [engine, tool, placing, placingTarget, moveTargetId, zoneDrawing, finishZone,
    mutate, roomAt, undo, redo, model.zones, model.targets]);

  /* Overlay layer: while a new zone is being drawn, Escape cancels the draft
     via HomeOverlay (topmost-only — no more double-firing with the view's
     own Escape chain). */
  window.HomeOverlay.useOverlayLayer({
    key: "apartment-zones",
    active: !!(tool === "zones" && zoneDrawing),
    onEscape: () => {
      setZoneDraft([]);
      setZoneDrawing(false);
      setNotice("new zone cancelled · existing room boundaries were not changed");
    },
    initialFocus: "none",
  });

  const setHeight = (h, preset) => mutate((m) => {
    const dev = (m.devices || []).find((x) => x.id === selectedId);
    if (!dev) return;
    let v = h;
    if (preset === "ceiling") {
      const z = (m.zones || []).find((zz) => zz.id === dev.room_id);
      v = (z && z.ceiling_height_m) || 2.4;
    }
    dev.pos[2] = v;
    dev.height_preset = preset;
  });

  const updateFixture = (updater) => mutate((m) => {
    const index = (m.devices || []).findIndex((device) => device.id === selectedId);
    if (index < 0) return;
    const device = m.devices[index];
    const room = (m.zones || []).find((candidate) => candidate.id === device.room_id);
    device.fixture_calibration = device.fixture_calibration || {};
    updater(device.fixture_calibration, device);
    m.devices[index] = window.HomeApartmentData.reconcileFixturePosition(device, room);
  });

  const updateTarget = (updater, options = { undoable: false }) => mutate((m) => {
    const target = (m.targets || []).find((candidate) => candidate.id === selectedTargetId);
    if (!target) return;
    updater(target);
    target.updated_at = new Date().toISOString();
  }, options);

  const updateZoneVertex = (vertexIndex, axisIndex, rawValue) => {
    const value = +rawValue;
    if (!Number.isFinite(value)) return;
    mutate((m) => {
      const editedZone = (m.zones || []).find((candidate) => candidate.id === selectedZone);
      const vertex = editedZone?.floor_polygon?.[vertexIndex];
      if (!vertex) return;
      vertex[axisIndex] = Math.round(value * 100) / 100;
      refreshRoomAssignments(m);
    }, { undoable: false });
  };

  const addZoneCorner = () => {
    const poly = zone?.floor_polygon || [];
    if (poly.length < 2) return;
    let longestEdge = 0;
    let longestLength = -1;
    for (let index = 0; index < poly.length; index += 1) {
      const nextIndex = (index + 1) % poly.length;
      const length = Math.hypot(poly[nextIndex][0] - poly[index][0], poly[nextIndex][1] - poly[index][1]);
      if (length > longestLength) { longestLength = length; longestEdge = index; }
    }
    const insertAt = longestEdge + 1;
    mutate((m) => {
      const editedZone = (m.zones || []).find((candidate) => candidate.id === selectedZone);
      const points = editedZone?.floor_polygon;
      if (!points?.length) return;
      const start = points[longestEdge];
      const end = points[(longestEdge + 1) % points.length];
      points.splice(insertAt, 0, [
        Math.round(((start[0] + end[0]) / 2) * 100) / 100,
        Math.round(((start[1] + end[1]) / 2) * 100) / 100,
      ]);
      refreshRoomAssignments(m);
    });
    setSelectedZoneVertex(insertAt);
    setNotice(`corner ${insertAt + 1} added on the longest boundary edge · drag it into place`);
  };

  const removeZoneCorner = () => {
    if (selectedZoneVertex == null || (zone?.floor_polygon || []).length <= 3) return;
    const removedIndex = selectedZoneVertex;
    mutate((m) => {
      const editedZone = (m.zones || []).find((candidate) => candidate.id === selectedZone);
      if (!editedZone?.floor_polygon || editedZone.floor_polygon.length <= 3) return;
      editedZone.floor_polygon.splice(removedIndex, 1);
      refreshRoomAssignments(m);
    });
    setSelectedZoneVertex(null);
    setNotice(`corner ${removedIndex + 1} removed · room assignments refreshed`);
  };

  const beginPlaceLight = (entity, placementKind = "ceiling_fixture") => {
    setTool("add");
    setSelectedId(null); setSelectedTargetId(null); setSelectedZone(null);
    setPlacing({
      entity_id: entity.entity_id,
      name: entity.name || entity.entity_id,
      domain: "light",
      placement_kind: placementKind,
    });
    setNotice(placementKind === "ceiling_fixture"
      ? "click the fixture-bottom location · it starts proposed until tape measurements are entered"
      : "click the non-ceiling light location · it will not receive gimbal-fixture calibration");
  };

  const restoreSeedFixture = (entry) => {
    const seed = entry?.seed;
    if (!seed) return;
    const existing = (model.devices || []).find((device) => device.id === seed.id
      || (seed.ha_entity_id && device.ha_entity_id === seed.ha_entity_id));
    if (existing) {
      setNotice(`${seed.name} already resolves to ${existing.name} · duplicate blocked`);
      return;
    }
    const exactEntity = fixtureMapping.unplacedLights.find((entity) => entity.entity_id === seed.ha_entity_id);
    mutate((m) => {
      const copy = JSON.parse(JSON.stringify(seed));
      copy.source = "proposed";
      copy.confidence = Math.min(0.5, +(copy.confidence || 0.5));
      if (exactEntity) copy.ha_entity_id = exactEntity.entity_id;
      else {
        copy.suggested_ha_entity_id = copy.ha_entity_id || null;
        copy.ha_entity_id = null;
      }
      const normalized = window.HomeApartmentData.ensureModelShape({
        zones: m.zones || [], devices: [copy], targets: [],
      }).devices[0];
      m.devices.push(normalized);
    });
    setSelectedId(seed.id);
    setNotice(exactEntity
      ? `${seed.name} restored and linked by exact entity ID`
      : `${seed.name} restored as proposed · choose its Home Assistant link manually`);
  };

  const applySelectedLink = () => {
    if (!selected || selected.type !== "light") return;
    const nextEntity = String(pendingLinkEntity || "").trim() || null;
    const timestamp = new Date().toISOString();
    const result = window.HomeApartmentData.reconcileFixtureEntityLink(
      model, selected.id, nextEntity, timestamp,
    );
    if (!result.ok) {
      setNotice(`${result.error} · no changes made`);
      return;
    }
    mutate((m) => {
      const applied = window.HomeApartmentData.reconcileFixtureEntityLink(
        m, selected.id, nextEntity, timestamp,
      );
      if (applied.ok) Object.assign(m, applied.model);
    });
    setConfirmUnlink(false);
    setNotice(nextEntity
      ? `linked ${selected.name} → ${nextEntity} · geometry unchanged`
      : `${selected.name} link removed · geometry unchanged`);
  };

  const removeSelectedLink = () => {
    if (!selected || selected.type !== "light" || !selected.ha_entity_id) return;
    const timestamp = new Date().toISOString();
    const result = window.HomeApartmentData.reconcileFixtureEntityLink(
      model, selected.id, null, timestamp,
    );
    if (!result.ok) {
      setNotice(`${result.error} · no changes made`);
      return;
    }
    mutate((m) => {
      const applied = window.HomeApartmentData.reconcileFixtureEntityLink(
        m, selected.id, null, timestamp,
      );
      if (applied.ok) Object.assign(m, applied.model);
    });
    setPendingLinkEntity("");
    setConfirmUnlink(false);
    setNotice(`${selected.name} link removed · its position and calibration were preserved · geometry unchanged`);
  };

  const wallAxisConflict = selected?.fixture_calibration?.wall_distances?.length >= 2
    && ["west", "east"].includes(selected.fixture_calibration.wall_distances[0]?.wall)
      === ["west", "east"].includes(selected.fixture_calibration.wall_distances[1]?.wall);
  const verificationError = selected?.fixture_calibration?.verification_error_m;
  const fixtureStatus = selected?.fixture_calibration?.status || "proposed";
  const selectedTargetRotation = selectedTarget ? targetRotationDegrees(selectedTarget) : 0;
  const verticalConflict = Number.isFinite(selected?.fixture_calibration?.floor_to_ceiling_m)
    && Number.isFinite(selected?.fixture_calibration?.ceiling_to_fixture_bottom_m)
    && selected.fixture_calibration.ceiling_to_fixture_bottom_m
      > selected.fixture_calibration.floor_to_ceiling_m;

  const panel = {
    position: "absolute", top: 52, bottom: 64, width: 230,
    zIndex: 4,
    background: "rgba(10,12,16,0.88)", border: "1px solid var(--hg-border-soft)",
    backdropFilter: "blur(8px)", overflowY: "auto", padding: "10px 12px",
    fontFamily: ED_MONO,
  };
  const heading = { fontSize: 9, letterSpacing: "0.16em", textTransform: "uppercase",
                    color: "var(--hg-fg-4)", margin: "10px 0 6px" };
  const draftOnline = !sim && sourceMeta?.kind === "local_draft" && connection === "online";
  const reviewedDraftReady = draftOnline && liveReview?.state === "ready" && liveComparison?.canPublish;
  const saveLabel = saving ? "saving…"
    : draftOnline && liveReview?.state === "loading" ? "comparing…"
      : reviewedDraftReady ? "publish draft"
        : draftOnline ? "compare live"
          : saveStatus?.state === "conflict" ? "conflict !"
            : dirty ? (connection === "online" ? "save changes •" : "save local draft •")
              : saveStatus?.state === "saved" ? "saved ✓" : "save";
  const saveDisabled = saving || sim || (draftOnline ? false : !dirty);
  const saveTitle = sim ? "simulation cannot save"
    : reviewedDraftReady ? "Publish the reviewed local draft to the authoritative Home Assistant Apartment model"
      : draftOnline ? "Read and compare the authoritative Home Assistant model; this does not write anything"
        : connection === "online" ? "Save to the authoritative Home Assistant Apartment model"
          : "Home Assistant is offline; keep a local recovery draft";

  return (
    <>
      {/* toolbar */}
      <div style={{
        position: "absolute", top: 10, left: "50%", transform: "translateX(-50%)",
        display: "flex", gap: 6, alignItems: "center", zIndex: 4,
      }} data-apt-edit-ui="toolbar">
        <span style={{ fontFamily: ED_MONO, fontSize: 10, color: "var(--hg-fg-2)",
                       letterSpacing: "0.14em", marginRight: 8 }}>edit</span>
        <EdButton label="select" active={tool === "select"} onClick={() => {
          setTool("select"); setPlacing(null); setPlacingTarget(null); setMoveTargetId(null);
        }} />
        <EdButton label="+ smart device" active={tool === "add"} onClick={() => {
          setTool("add"); setSelectedTargetId(null); setPlacingTarget(null); setMoveTargetId(null);
        }} />
        <EdButton label="+ target" active={tool === "targets"} onClick={() => {
          setTool("targets"); setPlacing(null); setSelectedId(null); setSelectedZone(null);
          setMoveTargetId(null); setNotice("Step 1 · choose a target type");
        }} />
        <EdButton label="fixture links" active={tool === "links"} onClick={() => {
          setTool("links"); setPlacing(null); setPlacingTarget(null); setSelectedTargetId(null);
          setSelectedZone(null); setMoveTargetId(null);
          setSelectedId((current) => ceilingLights.some((light) => light.id === current)
            ? current : (ceilingLights[0]?.id || null));
          setNotice("geometry locked · reconcile identity without moving the saved layout");
        }} />
        <EdButton label="fixture position" title="Measure a ceiling light from two walls and set its fixture-bottom height"
          active={tool === "measure"} onClick={() => {
          setTool("measure"); setPlacing(null); setPlacingTarget(null); setSelectedTargetId(null);
          setSelectedZone(null); setMoveTargetId(null);
          setSelectedId((current) => ceilingLights.some((light) => light.id === current)
            ? current : (ceilingLights[0]?.id || null));
          setNotice("");
        }} />
        <EdButton label="zones" active={tool === "zones"} onClick={() => {
          setTool("zones"); setZoneDraft([]); setZoneDrawing(false);
          setSelectedId(null); setSelectedTargetId(null); setMoveTargetId(null);
          setSelectedZone((current) => current || model.zones?.[0]?.id || null);
          setSelectedZoneVertex(null);
          setNotice("select a shaded room · drag a round corner to reshape it, or drag inside to move it");
        }} />
        <span style={{ width: 12 }} />
        <EdButton label="undo" onClick={undo} disabled={!undoRef.current.stack.length} />
        <EdButton label="redo" onClick={redo} disabled={!undoRef.current.redo.length} />
        <span style={{ width: 12 }} />
        <EdButton label={saveLabel}
                  active={dirty || draftOnline || saveStatus?.state === "conflict"} disabled={saveDisabled}
                  onClick={() => reviewedDraftReady ? onSave({ reviewedDraft: true })
                    : draftOnline ? onCompareLive() : onSave()}
                  title={saveTitle} />
        <EdButton label="done" onClick={onExit} />
      </div>

      {/* Bottom survey navigator: drag remains the fastest path; the arrows
          provide deterministic 45° orbit steps when precise wall selection
          matters. Keep this visually quiet so it reads as an instrument, not
          another primary toolbar. */}
      <div role="group" aria-label="Apartment edit orbit controls" data-apt-edit-ui="orbit-controls" style={{
        position: "absolute", bottom: 16, left: "50%", transform: "translateX(-50%)",
        display: "flex", alignItems: "center", gap: 8, zIndex: 5,
        padding: "6px 8px", background: "rgba(10,12,16,0.86)",
        border: "1px solid var(--hg-border-soft)", backdropFilter: "blur(8px)",
        fontFamily: ED_MONO,
      }}>
        <EdButton label="←" ariaLabel="Orbit room left" title="Orbit room 45° left"
          onClick={() => engine?.rig.stepAzimuth(-1)} />
        <span style={{ color: "var(--hg-fg-4)", fontSize: 8.5, letterSpacing: "0.08em",
          whiteSpace: "nowrap" }}>drag canvas to orbit · click surface to place</span>
        <EdButton label="→" ariaLabel="Orbit room right" title="Orbit room 45° right"
          onClick={() => engine?.rig.stepAzimuth(1)} />
      </div>

      {/* left: task rail */}
      <div style={{ ...panel, left: 72 }} data-apt-edit-ui="targets-and-palette">
        <div role="status" data-apartment-source={sourceMeta?.kind || "empty"} style={{
          margin: "0 0 8px", padding: "7px 8px", border: `1px solid ${sourceMeta?.live
            ? "rgba(145,230,189,0.35)" : sourceMeta?.kind === "simulation"
              ? "rgba(255,180,95,0.4)" : "var(--hg-border-soft)"}`,
          color: sourceMeta?.live ? "#91e6bd" : sourceMeta?.kind === "simulation" ? SURVEY_ORANGE : "var(--hg-fg-3)",
          fontSize: 8, lineHeight: 1.45,
        }}>
          source · {sourceMeta?.label || "No Apartment model"}<br />
          revision · {Number.isFinite(model.revision) ? model.revision : "—"}
          {!sim && <> · HA {connection === "online" ? "connected" : "offline"}</>}<br />
          save · {sim ? "simulation cannot save" : saveStatus?.state || "idle"}
        </div>
        {!sim && sourceMeta?.kind === "local_draft" && (
          <div data-apartment-live-reconciliation={liveReview?.state || "idle"} style={{
            margin: "0 0 10px", padding: "8px", border: `1px solid ${liveComparison?.canPublish
              ? "rgba(145,230,189,0.4)" : saveStatus?.state === "conflict"
                ? "var(--hg-crit)" : "rgba(255,180,95,0.38)"}`,
            color: "var(--hg-fg-3)", fontSize: 8.5, lineHeight: 1.55,
          }}>
            <div style={{ color: liveComparison?.canPublish ? "#91e6bd" : SURVEY_ORANGE,
              letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 5 }}>
              local draft protection
            </div>
            {connection !== "online" ? (
              <div>Connect Home Assistant with a long-lived token to compare this draft. The draft remains local and protected.</div>
            ) : liveReview?.state === "loading" ? (
              <div>Reading the authoritative Apartment model… no write is being performed.</div>
            ) : liveReview?.state === "error" ? (
              <>
                <div style={{ color: "var(--hg-warn)", marginBottom: 6 }}>{liveReview.detail}</div>
                <EdButton label="Retry live comparison" onClick={onCompareLive} />
              </>
            ) : liveReview?.state === "ready" && liveComparison ? (
              <>
                <div>draft rev {liveComparison.localRevision} · live rev {liveComparison.liveRevision}</div>
                {Object.entries(liveComparison.collections).map(([name, result]) => (
                  <div key={name}>
                    {name} · draft {result.local} / live {result.live}
                    {` · +${result.added.length} ~${result.changed.length} −${result.removed.length}`}
                  </div>
                ))}
                <div style={{ margin: "6px 0", color: liveComparison.canPublish ? "#91e6bd" : "var(--hg-crit)" }}>
                  {liveComparison.canPublish
                    ? `Revisions match. Publish is explicit and will create Home Assistant revision ${liveComparison.liveRevision + 1}.`
                    : "Revisions differ. Publishing is blocked; the local draft remains unchanged."}
                </div>
                <EdButton label={liveComparison.canPublish ? "Publish reviewed draft" : "Refresh comparison"}
                  active={liveComparison.canPublish}
                  onClick={() => liveComparison.canPublish ? onSave({ reviewedDraft: true }) : onCompareLive()}
                  disabled={saving} />
              </>
            ) : (
              <>
                <div style={{ marginBottom: 6 }}>Compare the local draft with Home Assistant before any authoritative write.</div>
                <EdButton label="Compare with live" onClick={onCompareLive} disabled={saving} />
              </>
            )}
          </div>
        )}
        {tool === "targets" ? (
          <>
            <div style={heading}>Add target · Step 1 of 2</div>
            <div style={{ fontFamily: ED_SANS, fontSize: 10, lineHeight: 1.45, color: "var(--hg-fg-3)", marginBottom: 10 }}>
              Choose what the rectangle or point represents. Drag the apartment to orbit, then click its real surface.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 5 }}>
              {TARGET_PRESETS.map((preset) => {
                const active = placingTarget?.category === preset.category && placingTarget?.shape === preset.shape;
                return <EdButton key={`${preset.category}-${preset.shape}`} label={preset.label} active={active}
                  onClick={() => {
                    setPlacingTarget(preset); setSelectedTargetId(null); setMoveTargetId(null); setTargetDraftId(null);
                    setNotice(preset.wallOnly
                      ? "Step 2 · drag to orbit until the wall is visible, then click the vertical art face"
                      : `Step 2 · click the ${preset.shape === "point" ? "exact point" : "matching surface"}`);
                  }} />;
              })}
            </div>
            {notice && <div role="status" style={{ margin: "10px 0", padding: "7px 8px", borderLeft: "2px solid var(--hg-ice)",
                                    color: "var(--hg-fg-3)", fontSize: 8.5, lineHeight: 1.5 }}>{notice}</div>}
            {(placingTarget || targetDraftId) && (
              <div style={{ display: "flex", gap: 6, margin: "8px 0 12px" }}>
                {targetDraftId && <EdButton label="Finish placement" active onClick={() => {
                  setTargetDraftId(null); setSelectedTargetId(null); setMoveTargetId(null);
                  setPlacingTarget(null); setTool("select");
                  setNotice(sim
                    ? "placement finished in simulation · the live model is untouched"
                    : connection === "online"
                      ? "placement finished · click Save changes to write the Home Apartment model"
                      : "placement finished · Home Assistant is offline, so Save creates only a local draft");
                }} />}
                <EdButton label="Cancel" onClick={() => {
                  const draftId = targetDraftId;
                  if (draftId) mutate((m) => {
                    m.targets = (m.targets || []).filter((target) => target.id !== draftId);
                  });
                  setTargetDraftId(null); setSelectedTargetId(null); setMoveTargetId(null);
                  setPlacingTarget(null); setNotice("target placement cancelled");
                }} />
              </div>
            )}
            <div style={heading}>placed · {(model.targets || []).length}</div>
            {(model.targets || []).map((target) => (
              <button type="button" key={target.id} onClick={() => {
                setSelectedTargetId(target.id); setSelectedId(null); setSelectedZone(null);
                setPlacingTarget(null); setMoveTargetId(null); setTargetDraftId(null);
              }} className="hg-focusable" style={{
                display: "grid", gridTemplateColumns: "8px 1fr", gap: 7, width: "100%",
                alignItems: "center", textAlign: "left", padding: "6px", cursor: "pointer",
                color: selectedTargetId === target.id ? "#0b0d11" : "var(--hg-fg-2)",
                background: selectedTargetId === target.id ? "var(--hg-ice)" : "transparent",
                border: 0, fontFamily: ED_MONO, fontSize: 9.5,
              }}>
                <span>{target.shape === "point" ? "·" : "□"}</span>
                <span>{target.name}</span>
              </button>
            ))}
          </>
        ) : tool === "measure" || tool === "links" ? (
          <>
            <div style={heading}>Mapped fixtures · {fixtureMapping.mappedFixtures.length}</div>
            <div style={{ fontFamily: ED_SANS, fontSize: 10, lineHeight: 1.45, color: "var(--hg-fg-3)", marginBottom: 9 }}>
              {tool === "links"
                ? "Geometry locked. Link real Home Assistant identities to these exact placed fixtures; names are suggestions only."
                : "Measure from two perpendicular walls, then establish the fixture-bottom aiming origin vertically."}
            </div>
            {tool === "links" && <div data-apartment-geometry-lock="active" style={{
              padding: "7px 8px", marginBottom: 9, border: "1px solid rgba(145,230,189,0.34)",
              background: "rgba(145,230,189,0.05)", color: "#91e6bd", fontSize: 11, lineHeight: 1.5,
            }}>
              fixture, target, zone, and tape geometry cannot be changed in this workflow
            </div>}
            {tool === "measure" && <div style={{ display: "grid", gap: 4, marginBottom: 9, paddingBottom: 8,
              borderBottom: "1px solid var(--hg-border-soft)" }}>
              {Object.entries(FIXTURE_STATUS).map(([status, meta]) => (
                <div key={status} style={{ display: "grid", gridTemplateColumns: "10px 72px 1fr", gap: 5,
                  alignItems: "start", fontSize: 11, lineHeight: 1.35, color: "var(--hg-fg-3)" }}>
                  <span style={{ color: meta.color }}>{meta.symbol}</span>
                  <span style={{ color: meta.color }}>{status}</span>
                  <span>{meta.detail}</span>
                </div>
              ))}
            </div>}
            {ceilingLights.map((light) => {
              const status = light.fixture_calibration?.status || "proposed";
              const statusMeta = FIXTURE_STATUS[status] || FIXTURE_STATUS.proposed;
              return <button type="button" key={light.id} onClick={() => {
                setSelectedId(light.id); setSelectedTargetId(null); setSelectedZone(null);
              }} className="hg-focusable" style={{
                display: "grid", gridTemplateColumns: "9px 1fr auto", gap: 7, alignItems: "center",
                width: "100%", padding: "7px 5px", cursor: "pointer", textAlign: "left",
                border: 0, borderBottom: "1px solid var(--hg-border-soft)",
                background: selectedId === light.id ? "rgba(255,180,95,0.12)" : "transparent",
                color: selectedId === light.id ? SURVEY_ORANGE : "var(--hg-fg-2)",
                fontFamily: ED_MONO, fontSize: 9.5,
              }}>
                <span style={{ color: statusMeta.color }}>{statusMeta.symbol}</span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis" }}>{light.name}</span>
                  <span style={{ display: "block", color: "var(--hg-fg-3)", fontSize: 10.5,
                    overflow: "hidden", textOverflow: "ellipsis" }}>{light.ha_entity_id || "unlinked"}</span>
                </span>
                <span style={{ fontSize: 10.5, color: "var(--hg-fg-3)" }}>{status}</span>
              </button>;
            })}
            {fixtureMapping.unresolvedFixtureLinks.length > 0 && (
              <>
                <div style={heading}>Fixture links needing review · {fixtureMapping.unresolvedFixtureLinks.length}</div>
                {fixtureMapping.unresolvedFixtureLinks.map((entry) => (
                  <button type="button" key={entry.fixture.id} onClick={() => setSelectedId(entry.fixture.id)}
                    className="hg-focusable" style={{ width: "100%", padding: "6px", textAlign: "left",
                      border: "1px solid rgba(255,180,95,0.28)", background: "rgba(255,180,95,0.05)",
                      color: SURVEY_ORANGE, fontFamily: ED_MONO, fontSize: 8.5, cursor: "pointer",
                      marginBottom: 4 }}>
                    {entry.fixture.name} · {entry.reason === "unlinked" ? "unlinked" : `${entry.fixture.ha_entity_id} not found`}
                    {entry.suggestions.length > 0 && (
                      <span style={{ display: "block", marginTop: 3, color: "var(--hg-fg-5)", fontSize: 7.3 }}>
                        possible match · {entry.suggestions.map((item) => item.name).join(", ")}
                      </span>
                    )}
                  </button>
                ))}
              </>
            )}
            {fixtureMapping.duplicateLinks.length > 0 && (
              <div style={{ marginTop: 8, padding: 7, border: "1px solid var(--hg-crit)",
                color: "var(--hg-crit)", fontSize: 8, lineHeight: 1.45 }}>
                duplicate links detected · {fixtureMapping.duplicateLinks.map((entry) => entry.entity_id).join(", ")}
              </div>
            )}
            {!sim && fixtureMapping.unresolvedSeedFixtures.length > 0 && (
              <>
                <div style={heading}>Seed fixtures needing review · {fixtureMapping.unresolvedSeedFixtures.length}</div>
                {fixtureMapping.unresolvedSeedFixtures.map((entry) => (
                  <div key={entry.seed.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--hg-border-soft)" }}>
                    <div style={{ color: SURVEY_ORANGE, fontSize: 9 }}>{entry.seed.name}</div>
                    <div style={{ color: "var(--hg-fg-5)", fontSize: 7.5, lineHeight: 1.4, margin: "3px 0 5px" }}>
                      calibrated seed position preserved · {entry.suggestions.length
                        ? `possible match: ${entry.suggestions.map((item) => item.name).join(", ")}`
                        : "no exact live match"}
                    </div>
                    <EdButton label="restore proposed fixture" onClick={() => restoreSeedFixture(entry)} />
                  </div>
                ))}
              </>
            )}
            <div style={heading}>Unplaced Home Assistant lights · {fixtureMapping.unplacedLights.length}</div>
            {fixtureMapping.unplacedLights.map((entity) => (
              <div key={entity.entity_id} style={{ padding: "6px 0", borderBottom: "1px solid var(--hg-border-soft)" }}>
                <div style={{ color: "var(--hg-fg-2)", fontSize: 9 }}>{entity.name}</div>
                <div style={{ color: "var(--hg-fg-5)", fontSize: 7.2, margin: "2px 0 5px",
                  overflow: "hidden", textOverflow: "ellipsis" }}>{entity.entity_id} · {entity.area_name}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {tool === "links" ? (
                    <span style={{ color: "var(--hg-fg-5)", fontSize: 7.8 }}>unplaced · no geometry created</span>
                  ) : <>
                    <EdButton label="place fixture" onClick={() => beginPlaceLight(entity, "ceiling_fixture")} />
                    <EdButton label="place other" onClick={() => beginPlaceLight(entity, "other_light")} />
                  </>}
                </div>
              </div>
            ))}
            {!fixtureMapping.registryAvailable && (
              <div style={{ color: "var(--hg-fg-5)", fontSize: 8.5, lineHeight: 1.5 }}>
                {sim ? "Simulation has no live Home Assistant inventory."
                  : "Home Assistant entity registry unavailable · reconnect with a valid long-lived access token."}
              </div>
            )}
            {fixtureMapping.nonFixtureLights.length > 0 && (
              <>
                <div style={heading}>Other mapped lights · {fixtureMapping.nonFixtureLights.length}</div>
                <div style={{ color: "var(--hg-fg-5)", fontSize: 7.8, lineHeight: 1.45, marginBottom: 4 }}>
                  Preserved as lamps or other lights · not treated as ceiling/gimbal fixtures.
                </div>
                {fixtureMapping.nonFixtureLights.map((light) => (
                  <button type="button" key={light.id} onClick={() => setSelectedId(light.id)}
                    className="hg-focusable" style={{ width: "100%", padding: "5px", textAlign: "left",
                      border: 0, borderBottom: "1px solid var(--hg-border-soft)", background: "transparent",
                      color: "var(--hg-fg-3)", fontFamily: ED_MONO, fontSize: 8.5, cursor: "pointer" }}>
                    {light.name} · {light.ha_entity_id || "unlinked"}
                  </button>
                ))}
              </>
            )}
          </>
        ) : tool === "add" ? (
          <>
            <div style={heading}>Add smart-home device</div>
            <div style={{ fontFamily: ED_SANS, fontSize: 10, lineHeight: 1.5, color: "var(--hg-fg-3)", marginBottom: 9 }}>
              Choose an unplaced Home Assistant entity, then click its real location in the apartment.
            </div>
            {sim && <div style={{ padding: "8px", border: "1px solid rgba(255,180,95,0.35)",
              color: SURVEY_ORANGE, fontSize: 8.5, lineHeight: 1.5, marginBottom: 9 }}>
              Simulation is disconnected from Home Assistant. Exit simulation and connect Home to load your real entity registry.
            </div>}
            {placing && <div style={{ marginBottom: 8, padding: "7px 8px", borderLeft: "2px solid var(--hg-ice)",
              color: "var(--hg-fg-2)", fontSize: 8.5, lineHeight: 1.5 }}>
              placing {placing.name.toLowerCase()} · click the floor, then refine height and linking in the inspector
            </div>}
            {Object.entries(palette).sort().map(([area, items]) => (
              <div key={area}>
                <div style={{ ...heading, color: "var(--hg-fg-3)" }}>{area.toLowerCase()}</div>
                {items.map((it) => (
                  <div key={it.entity_id} style={{
                      padding: "5px 6px", fontSize: 9.5,
                      color: placing?.entity_id === it.entity_id ? "#0b0d11" : "var(--hg-fg-2)",
                      background: placing?.entity_id === it.entity_id ? "var(--hg-ice)" : "transparent",
                      borderBottom: "1px solid var(--hg-border-soft)",
                    }}
                    title={it.entity_id}>
                    <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {it.domain === "light" ? "◉" : it.domain === "media_player" ? "♪" : it.domain === "camera" ? "▣" : "◌"} {it.name.toLowerCase()}
                    </div>
                    <div style={{ display: "flex", gap: 5, marginTop: 5 }}>
                      {it.domain === "light" && <EdButton label="ceiling fixture" onClick={() => {
                        setPlacing({ ...it, placement_kind: "ceiling_fixture" });
                        setNotice("click the fixture-bottom location · tape calibration follows");
                      }} />}
                      <EdButton label={it.domain === "light" ? "other light" : "place"} onClick={() => {
                        setPlacing({ ...it, placement_kind: it.domain === "light" ? "other_light" : "device" });
                        setNotice(it.domain === "light"
                          ? "click the non-ceiling light location · no fixture calibration will be inferred"
                          : "click the device location");
                      }} />
                    </div>
                  </div>
                ))}
              </div>
            ))}
            {!Object.keys(palette).length && (
              <div style={{ fontSize: 9.5, color: "var(--hg-fg-5)", lineHeight: 1.55 }}>
                {sim ? "No live entities in simulation." : registry.entities.length
                  ? "Every supported entity is already mapped."
                  : "Home Assistant registry unavailable · connect with a valid long-lived access token."}
              </div>
            )}
          </>
        ) : tool === "select" ? (
          <>
            <div style={heading}>Apartment editing</div>
            <div style={{ fontFamily: ED_SANS, fontSize: 10, lineHeight: 1.5, color: "var(--hg-fg-3)", marginBottom: 10 }}>
              Select a marker or target directly in 3D. Drag it to move it, or start one of the explicit workflows below.
            </div>
            <button type="button" className="hg-focusable" onClick={() => {
              setTool("targets"); setNotice("Step 1 · choose a target type");
            }} style={{ width: "100%", padding: "10px", marginBottom: 7, textAlign: "left", cursor: "pointer",
              background: "rgba(184,216,255,0.09)", border: "1px solid rgba(184,216,255,0.35)",
              color: "var(--hg-fg-1)", fontFamily: ED_SANS, fontSize: 11 }}>
              <span style={{ color: "var(--hg-ice)", fontFamily: ED_MONO }}>+ Add target</span><br />
              <span style={{ color: "var(--hg-fg-4)", fontSize: 9 }}>table · island · art · custom point or surface</span>
            </button>
            <button type="button" className="hg-focusable" onClick={() => setTool("add")}
              style={{ width: "100%", padding: "10px", textAlign: "left", cursor: "pointer",
                background: "transparent", border: "1px solid var(--hg-border-soft)",
                color: "var(--hg-fg-1)", fontFamily: ED_SANS, fontSize: 11 }}>
              <span style={{ color: "var(--hg-fg-2)", fontFamily: ED_MONO }}>+ Add smart-home device</span><br />
              <span style={{ color: "var(--hg-fg-4)", fontSize: 9 }}>place an unplaced Home Assistant entity</span>
            </button>
            {notice && <div role="status" style={{ marginTop: 10, padding: "7px 8px", borderLeft: "2px solid var(--hg-ice)",
              color: "var(--hg-fg-3)", fontSize: 8.5, lineHeight: 1.5 }}>{notice}</div>}
            {sim && <div style={{ marginTop: 12, color: SURVEY_ORANGE, fontSize: 8.5, lineHeight: 1.5 }}>
              source · Simulation · 5 mock devices, including 2 mock lights. Live inventory is intentionally hidden.
            </div>}
          </>
        ) : (
          <>
            <div style={heading}>Edit room zones</div>
            <div style={{ fontFamily: ED_SANS, fontSize: 10, lineHeight: 1.5, color: "var(--hg-fg-3)" }}>
              Select a room below or click its shaded floor. Drag a round corner to reshape it. Drag inside the shade to move the whole room.
            </div>
            <div style={{ display: "grid", gap: 4, marginTop: 10 }}>
              {(model.zones || []).map((candidate) => (
                <button type="button" key={candidate.id} className="hg-focusable"
                  onClick={() => {
                    setSelectedZone(candidate.id); setSelectedZoneVertex(null);
                    setSelectedId(null); setSelectedTargetId(null);
                    setZoneDrawing(false); setZoneDraft([]);
                    setNotice(`${candidate.name} selected · drag a corner or the shaded interior`);
                  }} style={{
                    display: "grid", gridTemplateColumns: "9px 1fr auto", gap: 7, alignItems: "center",
                    width: "100%", padding: "7px 6px", textAlign: "left", cursor: "pointer",
                    background: selectedZone === candidate.id ? "rgba(184,216,255,0.11)" : "transparent",
                    border: `1px solid ${selectedZone === candidate.id ? "rgba(184,216,255,0.38)" : "var(--hg-border-soft)"}`,
                    color: selectedZone === candidate.id ? "var(--hg-ice)" : "var(--hg-fg-2)",
                    fontFamily: ED_MONO, fontSize: 9,
                  }}>
                  <span style={{ width: 7, height: 7, background: candidate.color, display: "block" }} />
                  <span>{candidate.name}</span>
                  <span style={{ color: "var(--hg-fg-3)", fontSize: 10.5 }}>
                    {(candidate.floor_polygon || []).length} corners
                  </span>
                </button>
              ))}
            </div>
            {zoneDrawing ? (
              <div style={{ marginTop: 10, padding: "9px", border: "1px solid rgba(184,216,255,0.38)",
                background: "rgba(184,216,255,0.06)" }}>
                <div style={{ color: "var(--hg-ice)", fontSize: 9 }}>drawing a new room · {zoneDraft.length} corners</div>
                <div style={{ color: "var(--hg-fg-4)", fontFamily: ED_SANS, fontSize: 9, lineHeight: 1.45, marginTop: 5 }}>
                  Click floor corners in order. Add at least three, then finish the boundary.
                </div>
                <div style={{ display: "flex", gap: 5, marginTop: 8 }}>
                  <EdButton label="Finish zone" active disabled={zoneDraft.length < 3} onClick={finishZone} />
                  <EdButton label="Cancel" onClick={() => {
                    setZoneDraft([]); setZoneDrawing(false);
                    setSelectedZone(model.zones?.[0]?.id || null);
                    setNotice("new zone cancelled · existing room boundaries were not changed");
                  }} />
                </div>
              </div>
            ) : (
              <div style={{ marginTop: 10 }}>
                <EdButton label="+ new zone" onClick={() => {
                  setZoneDrawing(true); setZoneDraft([]); setSelectedZone(null); setSelectedZoneVertex(null);
                  setNotice("click the first floor corner · Finish zone becomes available after three corners");
                }} />
              </div>
            )}
            {notice && <div role="status" style={{ marginTop: 10, padding: "7px 8px", borderLeft: "2px solid var(--hg-ice)",
              color: "var(--hg-fg-3)", fontSize: 8.5, lineHeight: 1.5 }}>{notice}</div>}
          </>
        )}
      </div>

      {/* right: inspector */}
      <div style={{ ...panel, right: 12 }} data-apt-edit-ui="inspector">
        {selectedTarget ? (
          <>
            <div style={heading}>named target</div>
            <input value={selectedTarget.name}
              onChange={(e) => updateTarget((target) => { target.name = e.target.value; })}
              className="hg-focusable"
              style={{ width: "100%", background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                       color: "var(--hg-fg-0)", fontFamily: ED_SANS, fontSize: 12, padding: "4px 6px" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 7 }}>
              <span style={{ padding: "3px 6px", border: "1px solid var(--hg-border-soft)", color: "var(--hg-ice)",
                             fontSize: 8.5 }}>{selectedTarget.category} · {selectedTarget.shape}</span>
              <span style={{ color: "var(--hg-fg-5)", fontSize: 8 }}>{selectedTarget.room_id || "unassigned"}</span>
            </div>
            <div style={{ fontSize: 9, color: "var(--hg-fg-3)", marginTop: 8, lineHeight: 1.5 }}>
              x {selectedTarget.pos[0].toFixed(2)} · y {selectedTarget.pos[1].toFixed(2)} · z {selectedTarget.pos[2].toFixed(2)} m
            </div>
            <div style={heading}>transform · meters</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
              {["x", "y", "z"].map((axis, index) => (
                <label key={axis} style={{ fontSize: 8.5, color: "var(--hg-fg-4)" }}>{axis}
                  <input type="number" step="0.01" aria-label={`target ${axis}`}
                    value={selectedTarget.pos[index]}
                    onChange={(e) => {
                      const value = +e.target.value;
                      if (!Number.isFinite(value)) return;
                      updateTarget((target) => {
                        target.pos[index] = Math.round(value * 1000) / 1000;
                        target.room_id = roomAt(target.pos[0], target.pos[1]);
                      });
                    }} className="hg-focusable" style={{ width: "100%", marginTop: 4,
                      background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                      color: "var(--hg-fg-1)", fontFamily: ED_MONO, padding: "4px" }} />
                </label>
              ))}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8 }}>
              <EdButton label={moveTargetId === selectedTarget.id ? "click destination…" : "move on surface"}
                active={moveTargetId === selectedTarget.id}
                onClick={() => {
                  setTool("targets"); setPlacingTarget(null); setTargetDraftId(null);
                  setMoveTargetId(selectedTarget.id);
                  setNotice(selectedTarget.category === "art"
                    ? "Move armed · click a new vertical wall face"
                    : "Move armed · click the new surface position");
                }} />
              <EdButton label="x −5cm" onClick={() => updateTarget((target) => {
                target.pos[0] = Math.round((target.pos[0] - 0.05) * 1000) / 1000;
                target.room_id = roomAt(target.pos[0], target.pos[1]);
              })} />
              <EdButton label="x +5cm" onClick={() => updateTarget((target) => {
                target.pos[0] = Math.round((target.pos[0] + 0.05) * 1000) / 1000;
                target.room_id = roomAt(target.pos[0], target.pos[1]);
              })} />
              <EdButton label="y −5cm" onClick={() => updateTarget((target) => {
                target.pos[1] = Math.round((target.pos[1] - 0.05) * 1000) / 1000;
                target.room_id = roomAt(target.pos[0], target.pos[1]);
              })} />
              <EdButton label="y +5cm" onClick={() => updateTarget((target) => {
                target.pos[1] = Math.round((target.pos[1] + 0.05) * 1000) / 1000;
                target.room_id = roomAt(target.pos[0], target.pos[1]);
              })} />
            </div>
            <div style={{ fontSize: 8.5, color: "var(--hg-fg-5)", lineHeight: 1.5, marginTop: 7 }}>
              Drag anywhere inside the highlighted target, use Move on surface, or enter exact coordinates.
            </div>
            {selectedTarget.shape === "surface" && (
              <>
                <div style={heading}>aim surface · meters</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7 }}>
                  <label style={{ fontSize: 8.5, color: "var(--hg-fg-4)" }}>width
                    <input type="number" min="0.05" max="8" step="0.05" value={selectedTarget.size_m?.[0] || 0.8}
                      onChange={(e) => updateTarget((target) => {
                        target.size_m = [Math.max(0.05, +e.target.value), target.size_m?.[1] || 0.6];
                      })} className="hg-focusable" style={{ width: "100%", marginTop: 4, background: "var(--hg-bg-0)",
                        border: "1px solid var(--hg-border-soft)", color: "var(--hg-fg-1)", fontFamily: ED_MONO, padding: "4px" }} />
                  </label>
                  <label style={{ fontSize: 8.5, color: "var(--hg-fg-4)" }}>height / depth
                    <input type="number" min="0.05" max="8" step="0.05" value={selectedTarget.size_m?.[1] || 0.6}
                      onChange={(e) => updateTarget((target) => {
                        target.size_m = [target.size_m?.[0] || 0.8, Math.max(0.05, +e.target.value)];
                      })} className="hg-focusable" style={{ width: "100%", marginTop: 4, background: "var(--hg-bg-0)",
                        border: "1px solid var(--hg-border-soft)", color: "var(--hg-fg-1)", fontFamily: ED_MONO, padding: "4px" }} />
                  </label>
                </div>
                <div style={heading}>rotate on surface · {selectedTargetRotation.toFixed(1)}°</div>
                <input type="range" min="-180" max="180" step="1" value={selectedTargetRotation}
                  aria-label="target rotation"
                  onChange={(e) => updateTarget((target) => {
                    target.up = targetUpForRotation(target.normal || [0, 0, 1], +e.target.value);
                  })} style={{ width: "100%" }} />
                <div style={{ display: "flex", gap: 5, marginTop: 5 }}>
                  <EdButton label="−5°" onClick={() => updateTarget((target) => {
                    target.up = targetUpForRotation(target.normal || [0, 0, 1], targetRotationDegrees(target) - 5);
                  })} />
                  <EdButton label="+5°" onClick={() => updateTarget((target) => {
                    target.up = targetUpForRotation(target.normal || [0, 0, 1], targetRotationDegrees(target) + 5);
                  })} />
                  <EdButton label="snap 90°" onClick={() => updateTarget((target) => {
                    const angle = targetRotationDegrees(target);
                    target.up = targetUpForRotation(target.normal || [0, 0, 1], Math.round(angle / 90) * 90);
                  })} />
                </div>
                <div style={{ fontSize: 8.5, color: "var(--hg-fg-5)", lineHeight: 1.5, marginTop: 8 }}>
                  surface normal · {(selectedTarget.normal || [0, 0, 1]).map((v) => (+v).toFixed(2)).join(" · ")}<br />
                  {["table", "island"].includes(selectedTarget.category)
                    ? "horizontal surface locked · rotation stays in the floor plane"
                    : selectedTarget.category === "art"
                      ? "vertical wall surface locked · rotation stays in the wall plane"
                      : "rotation stays in the selected surface plane"}
                </div>
              </>
            )}
            <div style={{ marginTop: 14 }}>
              <EdButton label="delete target" danger onClick={() => {
                mutate((m) => { m.targets = (m.targets || []).filter((target) => target.id !== selectedTargetId); });
                setSelectedTargetId(null);
              }} />
            </div>
          </>
        ) : selected ? (
          <>
            <div style={heading}>device</div>
            <input value={selected.name} disabled={tool === "links"}
              onChange={(e) => mutate((m) => {
                const d = m.devices.find((x) => x.id === selectedId); if (d) d.name = e.target.value;
              }, { undoable: false })}
              className="hg-focusable"
              style={{ width: "100%", background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                       color: "var(--hg-fg-0)", fontFamily: ED_SANS, fontSize: 12, padding: "4px 6px" }} />
            <div style={{ fontSize: 8.5, color: "var(--hg-fg-5)", marginTop: 4 }}>{selected.ha_entity_id || "unbound"}</div>
            <div style={{ fontSize: 9.5, color: "var(--hg-fg-3)", marginTop: 6 }}>
              x {selected.pos[0].toFixed(1)} · y {selected.pos[1].toFixed(1)} · room {selected.room_id || "—"}
            </div>
            {selected.type === "light" && (
              <>
                <div style={heading}>{sim ? "Simulation entity link" : "Home Assistant link"}</div>
                <div style={{ padding: "7px 8px", border: "1px solid var(--hg-border-soft)",
                  color: selected.ha_entity_id ? "#91e6bd" : SURVEY_ORANGE, fontSize: 8.5, lineHeight: 1.45 }}>
                  {selected.ha_entity_id
                    ? <>{sim ? "mock link" : "linked"} · {selected.ha_entity_id}</>
                    : sim ? "unlinked simulation light" : "unlinked apartment light"}
                </div>
                <select value={pendingLinkEntity} onChange={(event) => {
                  setPendingLinkEntity(event.target.value);
                  setConfirmUnlink(false);
                }} className="hg-focusable" aria-label="Home Assistant light entity" style={{
                  width: "100%", marginTop: 6, padding: "5px 4px", background: "var(--hg-bg-0)",
                  border: "1px solid var(--hg-border-soft)", color: "var(--hg-fg-1)",
                  fontFamily: ED_MONO, fontSize: 8.5,
                }}>
                  <option value="" disabled>choose an unplaced light…</option>
                  {selected.ha_entity_id && !fixtureMapping.allLights.some((entity) =>
                    entity.entity_id === selected.ha_entity_id) && (
                    <option value={selected.ha_entity_id}>{selected.ha_entity_id} · registry unavailable</option>
                  )}
                  {linkableLightEntities.map((entity) => (
                    <option key={entity.entity_id} value={entity.entity_id}>
                      {entity.name} · {entity.entity_id}
                    </option>
                  ))}
                </select>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 6 }}>
                  <EdButton label={selected.ha_entity_id ? "change link" : "link entity"}
                    active={Boolean(pendingLinkEntity && pendingLinkEntity !== selected.ha_entity_id)}
                    disabled={!pendingLinkEntity || pendingLinkEntity === selected.ha_entity_id}
                    onClick={applySelectedLink} />
                  {selected.ha_entity_id && !confirmUnlink && (
                    <EdButton label="remove link…" danger onClick={() => setConfirmUnlink(true)} />
                  )}
                  {selected.ha_entity_id && confirmUnlink && (
                    <>
                      <EdButton label="confirm remove link" danger onClick={removeSelectedLink} />
                      <EdButton label="cancel" onClick={() => setConfirmUnlink(false)} />
                    </>
                  )}
                </div>
                <div style={{ color: "var(--hg-fg-5)", fontSize: 7.8, lineHeight: 1.45, marginTop: 6 }}>
                  Link changes preserve this fixture's position and tape calibration. No light command is sent.
                </div>
                {fixtureMapping.duplicateLinks.some((entry) => entry.entity_id === selected.ha_entity_id) && (
                  <div style={{ color: "var(--hg-crit)", fontSize: 8, lineHeight: 1.4, marginTop: 5 }}>
                    conflict · this entity is linked to more than one apartment device
                  </div>
                )}
              </>
            )}
            {tool === "links" ? (
              <div data-apartment-selected-geometry-lock="active" style={{
                marginTop: 10, padding: "8px", border: "1px solid rgba(145,230,189,0.34)",
                color: "#91e6bd", fontSize: 8.5, lineHeight: 1.5,
              }}>
                geometry locked<br />
                position · {selected.pos.map((value) => (+value).toFixed(3)).join(" · ")} m<br />
                room · {selected.room_id || "—"}<br />
                tape · {selected.fixture_calibration?.status || "not applicable"}
              </div>
            ) : window.HomeApartmentData.isCeilingLight(selected) ? (
              <>
                <div style={{ ...heading, color: SURVEY_ORANGE }}>fixture position</div>
                <div style={{ padding: "8px", border: "1px solid rgba(255,180,95,0.35)",
                              background: "rgba(255,180,95,0.06)", marginBottom: 9 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, color: SURVEY_ORANGE, fontSize: 9 }}>
                    <span>aim origin · fixture bottom</span>
                    <span style={{ color: (FIXTURE_STATUS[fixtureStatus] || FIXTURE_STATUS.proposed).color }}>
                      {(FIXTURE_STATUS[fixtureStatus] || FIXTURE_STATUS.proposed).symbol} {fixtureStatus}
                    </span>
                  </div>
                  <div style={{ color: "var(--hg-fg-4)", fontFamily: ED_SANS, fontSize: 9, lineHeight: 1.45, marginTop: 5 }}>
                    These measurements set the light's exact 3D position. Two walls set its floor location; ceiling height and fixture drop set the practical aiming origin at the fixture bottom.
                  </div>
                  <div style={{ color: "var(--hg-fg-5)", fontSize: 8, lineHeight: 1.4, marginTop: 5 }}>
                    {(FIXTURE_STATUS[fixtureStatus] || FIXTURE_STATUS.proposed).detail}
                  </div>
                </div>
                {(selected.fixture_calibration?.wall_distances || []).slice(0, 2).map((measurement, index) => (
                  <div key={index} style={{ display: "grid", gridTemplateColumns: "104px 1fr", gap: 7,
                                           alignItems: "end", marginBottom: 8 }}>
                    <label style={{ display: "grid", gap: 4, fontSize: 8.5, color: "var(--hg-fg-4)", letterSpacing: "0.08em" }}>
                      wall {index + 1}
                      <select value={measurement.wall} onChange={(e) => updateFixture((calibration) => {
                        calibration.wall_distances[index] = { wall: e.target.value, distance_m: null };
                      })} className="hg-focusable" style={{ background: "var(--hg-bg-0)",
                        border: "1px solid var(--hg-border-soft)", color: "var(--hg-fg-1)",
                        fontFamily: ED_MONO, fontSize: 9, padding: "5px 3px" }}>
                        {WALL_LABELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </label>
                    <EdTapeInput label="distance" meters={measurement.distance_m}
                      onCommit={(value) => updateFixture((calibration) => {
                        calibration.wall_distances[index].distance_m = value;
                      })} />
                  </div>
                ))}
                {wallAxisConflict && <div style={{ color: "var(--hg-crit)", fontSize: 8.5, lineHeight: 1.4, marginBottom: 8 }}>
                  choose one east/west wall and one north/south wall
                </div>}
                <div style={{ display: "grid", gap: 8, paddingTop: 3 }}>
                  <EdTapeInput label="floor → ceiling" meters={selected.fixture_calibration?.floor_to_ceiling_m}
                    onCommit={(value) => updateFixture((calibration) => { calibration.floor_to_ceiling_m = value; })} />
                  <EdTapeInput label="ceiling → fixture bottom" meters={selected.fixture_calibration?.ceiling_to_fixture_bottom_m}
                    onCommit={(value) => updateFixture((calibration) => { calibration.ceiling_to_fixture_bottom_m = value; })} />
                  {verticalConflict && <div style={{ color: "var(--hg-crit)", fontSize: 8.5, lineHeight: 1.4 }}>
                    fixture drop cannot exceed the floor-to-ceiling height
                  </div>}
                  <EdTapeInput label="floor → fixture bottom" optional
                    meters={selected.fixture_calibration?.floor_to_bottom_verification_m}
                    onCommit={(value) => updateFixture((calibration) => { calibration.floor_to_bottom_verification_m = value; })} />
                </div>
                {Number.isFinite(selected.fixture_calibration?.derived_floor_to_bottom_m) ? (
                  <div style={{ marginTop: 8, paddingTop: 7, borderTop: "1px solid var(--hg-border-soft)",
                                fontSize: 8.5, lineHeight: 1.55, color: "var(--hg-fg-4)" }}>
                    derived floor → bottom · {window.HomeApartmentData.formatTapeMeasurement(selected.fixture_calibration.derived_floor_to_bottom_m)}
                    {Number.isFinite(verificationError) && <><br /><span style={{ color: Math.abs(verificationError) <= 0.0127 ? "#91e6bd" : "var(--hg-crit)" }}>
                      verification delta · {(verificationError / 0.0254).toFixed(2)} in
                    </span></>}
                  </div>
                ) : (
                  <div style={{ marginTop: 8, paddingTop: 7, borderTop: "1px solid var(--hg-border-soft)",
                    fontSize: 8.5, color: "var(--hg-fg-5)" }}>
                    derived floor → fixture bottom · pending ceiling height and fixture drop
                  </div>
                )}
              </>
            ) : (
              <>
                <div style={heading}>height · {selected.pos[2].toFixed(2)} m</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {HEIGHT_PRESETS.map(([label, h]) => (
                    <EdButton key={label} label={label} active={selected.height_preset === label}
                              onClick={() => setHeight(h, label)} />
                  ))}
                </div>
                <input type="range" min="0" max="3" step="0.05" value={selected.pos[2]}
                  aria-label="device height" className="hg-slider hg-focusable"
                  onChange={(e) => mutate((m) => {
                    const d = m.devices.find((x) => x.id === selectedId);
                    if (d) { d.pos[2] = +e.target.value; d.height_preset = "custom"; }
                  }, { undoable: false })}
                  style={{ width: "100%", marginTop: 8 }} />
              </>
            )}
            {["camera", "tv", "speaker"].includes(selected.type) && (
              <>
                <div style={heading}>
                  aim · {Math.round(((selected.yaw_rad || 0) * 180 / Math.PI + 360) % 360)}°
                </div>
                <input type="range" min="-180" max="180" step="2"
                  aria-label="device yaw" className="hg-slider hg-focusable"
                  value={Math.round((selected.yaw_rad || 0) * 180 / Math.PI)}
                  onChange={(e) => mutate((m) => {
                    const d = m.devices.find((x) => x.id === selectedId);
                    if (d) d.yaw_rad = +e.target.value * Math.PI / 180;
                  }, { undoable: false })}
                  style={{ width: "100%", marginTop: 8 }} />
                <div style={{ fontSize: 8.5, color: "var(--hg-fg-5)", marginTop: 2 }}>
                  0° = east · 90° = north — the frustum cone follows live
                </div>
              </>
            )}
            {tool !== "links" && <div style={{ marginTop: 14 }}>
              <EdButton label="delete device" danger onClick={() => {
                mutate((m) => { m.devices = m.devices.filter((x) => x.id !== selectedId); });
                setSelectedId(null);
              }} />
            </div>}
          </>
        ) : zone ? (
          <>
            <div style={heading}>zone</div>
            <input value={zone.name}
              onChange={(e) => mutate((m) => {
                const z = m.zones.find((x) => x.id === selectedZone); if (z) z.name = e.target.value;
              }, { undoable: false })}
              className="hg-focusable"
              style={{ width: "100%", background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                       color: "var(--hg-fg-0)", fontFamily: ED_SANS, fontSize: 12, padding: "4px 6px" }} />
            <div style={heading}>boundary · {(zone.floor_polygon || []).length} corners</div>
            <div style={{ color: "var(--hg-fg-4)", fontFamily: ED_SANS, fontSize: 9, lineHeight: 1.45, marginBottom: 7 }}>
              Drag a round handle in 3D, or enter an exact floor coordinate here. Drag the shaded interior to move every corner together.
            </div>
            <div style={{ display: "grid", gap: 4 }}>
              {(zone.floor_polygon || []).map((vertex, vertexIndex) => (
                <div key={`${zone.id}-${vertexIndex}`} style={{
                  display: "grid", gridTemplateColumns: "57px 1fr 1fr", gap: 5, alignItems: "end",
                  padding: "5px", border: `1px solid ${selectedZoneVertex === vertexIndex
                    ? "rgba(184,216,255,0.48)" : "var(--hg-border-soft)"}`,
                  background: selectedZoneVertex === vertexIndex ? "rgba(184,216,255,0.08)" : "transparent",
                }}>
                  <button type="button" className="hg-focusable" onClick={() => setSelectedZoneVertex(vertexIndex)}
                    style={{ height: 27, padding: "0 4px", cursor: "pointer",
                      border: 0, background: selectedZoneVertex === vertexIndex ? "var(--hg-ice)" : "transparent",
                      color: selectedZoneVertex === vertexIndex ? "#0b0d11" : "var(--hg-fg-3)",
                      fontFamily: ED_MONO, fontSize: 7.8 }}>corner {vertexIndex + 1}</button>
                  {["x", "y"].map((axis, axisIndex) => (
                    <label key={axis} style={{ color: "var(--hg-fg-5)", fontSize: 7.5 }}>{axis}
                      <input type="number" step="0.01" value={vertex[axisIndex]}
                        aria-label={`zone ${zone.id} corner ${vertexIndex + 1} ${axis}`}
                        onFocus={() => setSelectedZoneVertex(vertexIndex)}
                        onChange={(event) => updateZoneVertex(vertexIndex, axisIndex, event.target.value)}
                        className="hg-focusable" style={{ width: "100%", marginTop: 3, padding: "4px",
                          background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                          color: "var(--hg-fg-1)", fontFamily: ED_MONO, fontSize: 8.5 }} />
                    </label>
                  ))}
                </div>
              ))}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 7 }}>
              <EdButton label="add corner" onClick={addZoneCorner} />
              <EdButton label="remove selected" danger onClick={removeZoneCorner}
                disabled={selectedZoneVertex == null || (zone.floor_polygon || []).length <= 3}
                title={(zone.floor_polygon || []).length <= 3 ? "A room boundary needs at least three corners" : "Remove selected corner"} />
            </div>
            <div style={heading}>frigate camera</div>
            <input value={zone.frigate_camera || ""}
              onChange={(e) => mutate((m) => {
                const z = m.zones.find((x) => x.id === selectedZone); if (z) z.frigate_camera = e.target.value;
              }, { undoable: false })}
              className="hg-focusable"
              style={{ width: "100%", background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                       color: "var(--hg-fg-1)", fontFamily: ED_MONO, fontSize: 10, padding: "4px 6px" }} />
            <div style={heading}>ceiling m</div>
            <input type="number" step="0.05" value={zone.ceiling_height_m}
              onChange={(e) => mutate((m) => {
                const z = m.zones.find((x) => x.id === selectedZone); if (z) z.ceiling_height_m = +e.target.value;
              }, { undoable: false })}
              className="hg-focusable"
              style={{ width: 80, background: "var(--hg-bg-0)", border: "1px solid var(--hg-border-soft)",
                       color: "var(--hg-fg-1)", fontFamily: ED_MONO, fontSize: 10, padding: "4px 6px" }} />
            <div style={{ marginTop: 14 }}>
              <EdButton label="delete zone" danger onClick={() => {
                mutate((m) => {
                  m.zones = m.zones.filter((x) => x.id !== selectedZone);
                  refreshRoomAssignments(m);
                });
                setSelectedZone(null); setSelectedZoneVertex(null);
              }} />
            </div>
          </>
        ) : (
          <>
            <div style={heading}>zones</div>
            {(model.zones || []).map((z) => (
              <div key={z.id} onClick={() => { setSelectedZone(z.id); setSelectedId(null); }}
                style={{ padding: "4px 6px", cursor: "pointer", fontSize: 10,
                         color: "var(--hg-fg-2)" }}>
                <span style={{ color: z.color }}>■</span> {z.name}
              </div>
            ))}
            <div style={{ fontSize: 9, color: "var(--hg-fg-5)", marginTop: 10, lineHeight: 1.6 }}>
              {tool === "zones"
                ? zoneDrawing
                  ? `new zone: click floor corners (${zoneDraft.length}) · Finish zone after three`
                  : "click a shaded room to select · drag inside to move · drag round handles to reshape"
                : "select a marker to edit · zones tool draws rooms"}
            </div>
          </>
        )}
      </div>
    </>
  );
}

window.HomeApartmentEdit = HomeApartmentEdit;
