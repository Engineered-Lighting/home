/* eslint-disable */
/**
 * home-spatial.jsx — Addendum 38 Phase 1 (revised): the /spatial light-map
 * DRAW tool.
 *
 * The photometric light-footprint calibration was abandoned after three
 * failed runs (camera auto-exposure + uncontrollable ambient light defeat
 * it). It is replaced by this: the user hand-draws each light's footprint
 * as a polygon on the camera image. Same spatial_model.json output, the
 * auto-exposure problem gone, and the /spatial drawer gets one job.
 *
 * Flow: pick a light -> (optionally) flash it on -> draw a polygon around
 * where it shines -> Save. Polygon vertices are stored normalised [0,1]
 * (they share Frigate's zone-coordinate space + the person-position data
 * Phase 3 consumes). On Save the polygon is rasterised to footprint cells;
 * the polygon (canonical) and the cells (derived) both POST to
 *   POST /api/extended_openai_conversation/spatial_model
 * The skeleton model is produced by tools/init-spatial-model.py.
 *
 * Phase 3 gradient lighting discriminates near-vs-far lights by distance
 * from the person to each light's footprint-polygon CENTROID.
 *
 * Draw mode is real-connection only; sim mode renders the mock read-only.
 */

const { useState, useEffect, useRef, useCallback } = React;

const SP_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const SP_FONT_SANS = '"Geist", "Inter", sans-serif';
const SP_FRIGATE_DEFAULT = "http://192.168.0.125:5000";
const SP_VW = 1280, SP_VH = 720;                 // SVG viewBox units
const SP_LIVING_LIGHTS = "input_boolean.living_lights_enabled";
const SP_VERTEX_HIT = 0.022;                     // vertex grab radius, norm

function _spClamp01(n) {
  return Math.max(0, Math.min(1, Number(n) || 0));
}
function _spSleep(ms) {
  return new Promise(function (res) { setTimeout(res, ms); });
}
function _spDomain(entity) {
  return String(entity || "").split(".")[0];
}
function _spRound3(n) {
  return Math.round((Number(n) || 0) * 1000) / 1000;
}

/* drawn / no-footprint / not-drawn status for one light's record. */
function spDrawnStatus(rec) {
  const poly = rec && rec.footprint_polygon;
  if (poly && typeof poly === "object") {
    const cams = Object.keys(poly);
    for (let i = 0; i < cams.length; i++) {
      const p = poly[cams[i]];
      if (Array.isArray(p) && p.length >= 3) {
        return { key: "drawn", label: "drawn", color: "var(--hg-ice)" };
      }
    }
  }
  if (rec && rec.human_verdict === "hand_drawn") {
    return { key: "empty", label: "no footprint", color: "var(--hg-fg-4)" };
  }
  return { key: "todo", label: "not drawn", color: "var(--hg-fg-5)" };
}

// ── Status dot ──────────────────────────────────────────────────────

function _SpDot({ color, title }) {
  return (
    <span title={title} style={{
      width: 8, height: 8, borderRadius: 8,
      background: color, display: "inline-block", flex: "0 0 auto",
    }} />
  );
}

// ── Tab strip — Draw active; future phases ghosted ──────────────────

function SpTabStrip() {
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 14,
      padding: "8px 24px", borderBottom: "1px solid var(--hg-border-soft)",
      fontFamily: SP_FONT_MONO,
    }}>
      <span style={{
        fontSize: 10.5, letterSpacing: "0.14em", textTransform: "uppercase",
        color: "var(--hg-fg-0)", borderBottom: "2px solid var(--hg-ice)",
        paddingBottom: 4,
      }}>draw</span>
      <span style={{
        fontSize: 9, letterSpacing: "0.12em", color: "var(--hg-fg-5)",
      }}>zones · gradient · predict — later phases</span>
    </div>
  );
}

// ── Step strip — the pick → flash → draw → save flow ────────────────

function SpStepStrip() {
  const steps = [
    ["1", "pick a light"],
    ["2", "flash it on"],
    ["3", "draw where it shines"],
    ["4", "save"],
  ];
  return (
    <div style={{
      margin: "10px 24px 2px", padding: "9px 13px",
      border: "1px solid var(--hg-border-soft)", background: "var(--hg-bg-1)",
      borderRadius: 3, display: "flex", gap: 13, flexWrap: "wrap",
      alignItems: "center",
    }}>
      <span style={{
        fontFamily: SP_FONT_MONO, fontSize: 9, letterSpacing: "0.16em",
        textTransform: "uppercase", color: "var(--hg-fg-3)",
      }}>how to draw</span>
      {steps.map(function (s) {
        return (
          <span key={s[0]} style={{ display: "flex", alignItems: "center",
                                    gap: 6 }}>
            <span style={{
              width: 15, height: 15, borderRadius: 15,
              border: "1px solid var(--hg-border)", display: "flex",
              alignItems: "center", justifyContent: "center",
              fontFamily: SP_FONT_MONO, fontSize: 8.5,
              color: "var(--hg-fg-3)", flex: "0 0 auto",
            }}>{s[0]}</span>
            <span style={{ fontFamily: SP_FONT_SANS, fontSize: 10.5,
                           color: "var(--hg-fg-3)" }}>{s[1]}</span>
          </span>
        );
      })}
    </div>
  );
}

// ── Draw canvas — camera backdrop + faint grid + the polygon editor ─

function SpDrawCanvas({ cam, gridCols, gridRows, polygon, onChange,
                        zones, showZones, frigateUrl, nonce, readOnly,
                        hasLight }) {
  const SH = window.HomeSpatialHelpers;
  const cols = gridCols || 32;
  const rows = gridRows || 18;
  const cw = SP_VW / cols, ch = SP_VH / rows;
  const svgRef = useRef(null);
  const dragRef = useRef(null);
  const [bgOk, setBgOk] = useState(true);

  useEffect(function () { setBgOk(true); }, [cam, nonce]);

  // clientX/Y -> normalised [0,1]. The viewBox is 16:9 and the <svg> is
  // width:100% / auto height, so its rect matches the viewBox aspect and
  // the mapping is a straight divide.
  function toNorm(e) {
    const svg = svgRef.current;
    if (!svg) return [0, 0];
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return [0, 0];
    return [
      _spClamp01((e.clientX - rect.left) / rect.width),
      _spClamp01((e.clientY - rect.top) / rect.height),
    ];
  }

  function hitVertex(nx, ny) {
    let best = -1;
    let bestD = SP_VERTEX_HIT * SP_VERTEX_HIT;
    for (let i = 0; i < polygon.length; i++) {
      const dx = polygon[i][0] - nx, dy = polygon[i][1] - ny;
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  // A single pointer model: down hit-tests existing vertices; a move
  // past a small threshold turns the gesture into a drag; a clean tap on
  // empty space (no drag) appends a vertex on up.
  function onDown(e) {
    if (readOnly || !hasLight) return;
    const n = toNorm(e);
    const hit = hitVertex(n[0], n[1]);
    dragRef.current = hit >= 0
      ? { idx: hit, moved: false }
      : { idx: -1, at: n, moved: false };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) {}
  }

  function onMove(e) {
    const d = dragRef.current;
    if (!d) return;
    const n = toNorm(e);
    if (d.idx >= 0) {
      d.moved = true;
      const next = polygon.slice();
      next[d.idx] = [_spRound3(n[0]), _spRound3(n[1])];
      onChange(next);
    } else {
      const dx = n[0] - d.at[0], dy = n[1] - d.at[1];
      if (dx * dx + dy * dy > 0.00008) d.moved = true;
    }
  }

  function onUp(e) {
    const d = dragRef.current;
    dragRef.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch (_) {}
    if (!d || readOnly || !hasLight) return;
    if (d.idx === -1 && !d.moved) {
      onChange(polygon.concat([[_spRound3(d.at[0]), _spRound3(d.at[1])]]));
    }
  }

  // faint 32×18 drawing guide
  const grid = [];
  for (let c = 1; c < cols; c++) {
    grid.push(<line key={"gv" + c} x1={c * cw} y1={0} x2={c * cw} y2={SP_VH}
                    stroke="rgba(255,255,255,0.05)" strokeWidth={1} />);
  }
  for (let r = 1; r < rows; r++) {
    grid.push(<line key={"gh" + r} x1={0} y1={r * ch} x2={SP_VW} y2={r * ch}
                    stroke="rgba(255,255,255,0.05)" strokeWidth={1} />);
  }

  // Frigate zone outlines — spatial reference landmarks for the draw.
  const zonePolys = [];
  if (showZones && zones && SH) {
    Object.keys(zones).forEach(function (zname) {
      const z = zones[zname] || {};
      const pts = SH.zonePolygonPoints(z.polygon, SP_VW, SP_VH);
      if (!pts) return;
      const col = SH.zoneColor(z.color, "rgba(184,216,255,0.7)");
      zonePolys.push(
        <polygon key={"zp" + zname} points={pts} fill="none" stroke={col}
                 strokeWidth={2} strokeLinejoin="round" opacity={0.5} />
      );
      const v0 = (Array.isArray(z.polygon) && z.polygon[0]) || [0, 0];
      zonePolys.push(
        <text key={"zt" + zname} x={(Number(v0[0]) || 0) * SP_VW + 6}
              y={(Number(v0[1]) || 0) * SP_VH + 16} fill={col}
              fontSize={13} fontFamily={SP_FONT_MONO} opacity={0.55}>
          {zname}
        </text>
      );
    });
  }

  const polyPts = SH ? SH.zonePolygonPoints(polygon, SP_VW, SP_VH) : "";
  const centroid = (SH && polygon.length >= 3)
    ? SH.polygonCentroid(polygon) : null;

  const bgUrl = frigateUrl
    ? frigateUrl.replace(/\/+$/, "") + "/api/" + cam + "/latest.jpg"
      + "?bbox=0&timestamp=0&zones=0&motion=0&regions=0&nonce=" + nonce
    : null;
  const backdrop = bgOk ? bgUrl : null;
  const drawable = hasLight && !readOnly;

  return (
    <svg ref={svgRef} viewBox={"0 0 " + SP_VW + " " + SP_VH} width="100%"
         onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
         onPointerCancel={onUp}
         style={{
           display: "block", background: "var(--hg-bg-1)",
           border: "1px solid var(--hg-border-soft)", touchAction: "none",
           cursor: drawable ? "crosshair" : "default",
         }}>
      <rect x={0} y={0} width={SP_VW} height={SP_VH} fill="var(--hg-bg-1)" />
      {backdrop && (
        <image href={backdrop} x={0} y={0} width={SP_VW} height={SP_VH}
               preserveAspectRatio="xMidYMid slice" opacity={0.62}
               onError={function () { setBgOk(false); }} />
      )}
      {grid}
      {zonePolys}
      {polygon.length >= 3 && (
        <polygon points={polyPts} fill="rgba(184,216,255,0.22)"
                 stroke="var(--hg-ice)" strokeWidth={3}
                 strokeLinejoin="round" />
      )}
      {polygon.length === 2 && (
        <polyline points={polyPts} fill="none" stroke="var(--hg-ice)"
                  strokeWidth={3} />
      )}
      {centroid && (
        <g>
          <circle cx={centroid[0] * SP_VW} cy={centroid[1] * SP_VH} r={9}
                  fill="none" stroke="var(--hg-ice)" strokeWidth={2} />
          <line x1={centroid[0] * SP_VW - 15} y1={centroid[1] * SP_VH}
                x2={centroid[0] * SP_VW + 15} y2={centroid[1] * SP_VH}
                stroke="var(--hg-ice)" strokeWidth={2} />
          <line x1={centroid[0] * SP_VW} y1={centroid[1] * SP_VH - 15}
                x2={centroid[0] * SP_VW} y2={centroid[1] * SP_VH + 15}
                stroke="var(--hg-ice)" strokeWidth={2} />
        </g>
      )}
      {polygon.map(function (p, i) {
        return (
          <circle key={"vx" + i} cx={p[0] * SP_VW} cy={p[1] * SP_VH}
                  r={i === 0 ? 16 : 13}
                  fill={i === 0 ? "var(--hg-ice)" : "rgba(184,216,255,0.92)"}
                  stroke="rgba(0,0,0,0.6)" strokeWidth={2} />
        );
      })}
      {!hasLight && (
        <text x={SP_VW / 2} y={SP_VH / 2} textAnchor="middle"
              fill="var(--hg-fg-4)" fontSize={24} fontFamily={SP_FONT_MONO}>
          select a light to begin
        </text>
      )}
      {drawable && polygon.length === 0 && (
        <text x={SP_VW / 2} y={SP_VH / 2} textAnchor="middle"
              fill="var(--hg-fg-3)" fontSize={25} fontFamily={SP_FONT_MONO}>
          click to place the first point
        </text>
      )}
      {hasLight && readOnly && polygon.length === 0 && (
        <text x={SP_VW / 2} y={SP_VH / 2} textAnchor="middle"
              fill="var(--hg-fg-4)" fontSize={24} fontFamily={SP_FONT_MONO}>
          no footprint drawn
        </text>
      )}
    </svg>
  );
}

// ── One light row in the list ───────────────────────────────────────

function SpLightRow({ entity, rec, cam, selected, onSelect }) {
  const st = spDrawnStatus(rec);
  return (
    <button
      onClick={function () { onSelect(entity); }}
      className="hg-focusable"
      style={{
        width: "100%", textAlign: "left", cursor: "pointer",
        background: selected ? "var(--hg-bg-1)" : "transparent",
        border: "none", borderTop: "1px solid var(--hg-border-soft)",
        borderLeft: selected ? "2px solid var(--hg-ice)"
                             : "2px solid transparent",
        padding: "8px 22px", display: "flex", alignItems: "center", gap: 9,
        fontFamily: SP_FONT_MONO,
      }}
    >
      <_SpDot color={st.color} title={st.label} />
      <span style={{ color: "var(--hg-fg-0)", fontSize: 11.5,
                     flex: "1 1 auto", overflow: "hidden",
                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {entity}
      </span>
      {cam && (
        <span style={{ color: "var(--hg-fg-4)", fontSize: 9.5 }}>{cam}</span>
      )}
      <span style={{ color: st.color, fontSize: 8.5, letterSpacing: "0.1em",
                     textTransform: "uppercase", flex: "0 0 auto" }}>
        {st.label}
      </span>
    </button>
  );
}

// ── Draw toolbar — flash / undo / clear / save ──────────────────────

function SpDrawTools({ hasLight, polygon, dirty, posting, flashedOn,
                       flashBusy, canFlash, onFlash, onUndo, onClear,
                       onSave }) {
  const pts = polygon.length;
  const btn = function (label, onClick, enabled, primary) {
    return (
      <button
        onClick={onClick} disabled={!enabled} className="hg-focusable"
        style={{
          background: primary && enabled ? "var(--hg-ice)" : "transparent",
          border: "1px solid " + (primary && enabled
            ? "var(--hg-ice)" : "var(--hg-border-soft)"),
          color: primary && enabled ? "var(--hg-bg-0)"
            : (enabled ? "var(--hg-fg-1)" : "var(--hg-fg-5)"),
          padding: "5px 11px", fontFamily: SP_FONT_MONO, fontSize: 10,
          letterSpacing: "0.08em", textTransform: "lowercase",
          cursor: enabled ? "pointer" : "default",
        }}
      >{label}</button>
    );
  };
  return (
    <div style={{
      display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap",
      padding: "8px 24px 4px",
    }}>
      <button
        onClick={onFlash} disabled={!canFlash || flashBusy}
        className="hg-focusable"
        title="Turn the selected light on so you can see where it lands"
        style={{
          background: flashedOn ? "var(--hg-ice)" : "transparent",
          border: "1px solid " + (flashedOn
            ? "var(--hg-ice)" : "var(--hg-border-soft)"),
          color: flashedOn ? "var(--hg-bg-0)"
            : (canFlash && !flashBusy ? "var(--hg-fg-1)" : "var(--hg-fg-5)"),
          padding: "5px 11px", fontFamily: SP_FONT_MONO, fontSize: 10,
          letterSpacing: "0.08em", textTransform: "lowercase",
          cursor: canFlash && !flashBusy ? "pointer" : "default",
        }}
      >{flashBusy ? "flashing…" : (flashedOn ? "flash off" : "flash on")}
      </button>
      <span style={{ marginLeft: "auto", color: "var(--hg-fg-4)",
                     fontFamily: SP_FONT_MONO, fontSize: 9.5,
                     marginRight: 2 }}>
        {pts} {pts === 1 ? "point" : "points"}
      </span>
      {btn("undo", onUndo, hasLight && pts > 0 && !posting, false)}
      {btn("clear", onClear, hasLight && pts > 0 && !posting, false)}
      {btn(posting ? "saving…" : "save", onSave,
           hasLight && dirty && !posting, true)}
    </div>
  );
}

// ── Main drawer ─────────────────────────────────────────────────────

function HomeSpatialDrawer({ open, onClose, endpoint, token, sim }) {
  const [model, setModel] = useState(null);    // null = loading
  const [error, setError] = useState(null);
  const [fetchMs, setFetchMs] = useState(null);
  const [selectedCamera, setSelectedCamera] = useState(null);
  const [selectedLight, setSelectedLight] = useState(null);
  const [polygon, setPolygon] = useState([]);  // working polygon, norm [0,1]
  const [dirty, setDirty] = useState(false);
  const [posting, setPosting] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [showZones, setShowZones] = useState(true);
  const [flashedLight, setFlashedLight] = useState(null);
  const [flashBusy, setFlashBusy] = useState(false);
  const [livingLightsPrior, setLivingLightsPrior] = useState(null);
  const abortRef = useRef(null);
  const cleanupRef = useRef(function () {});

  const simActive = !!(sim && sim.active);

  const simFixture = useCallback(function () {
    if (!simActive ||
        typeof window.__SIM_SPATIAL_MODEL_FIXTURE !== "function") return null;
    try { return window.__SIM_SPATIAL_MODEL_FIXTURE(); }
    catch (_) { return null; }
  }, [simActive]);

  const fetchModel = useCallback(async function () {
    setError(null);
    setFetchMs(null);
    const fixture = simFixture();
    if (fixture) { setModel(fixture); setFetchMs(0); return; }
    if (!endpoint || !token) {
      setError("not connected — /connect <url> <token>");
      setModel({});
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const t0 = Date.now();
    try {
      const base = endpoint.replace(/\/+$/, "");
      const url = base + "/api/extended_openai_conversation/spatial_model";
      const tauriFetch = window.tauriFetch || fetch;
      const r = await tauriFetch(url, {
        headers: { Authorization: "Bearer " + token },
        cache: "no-store", signal: controller.signal,
      });
      if (!r.ok) {
        setError("HTTP " + r.status);
        setModel({});
        setFetchMs(Date.now() - t0);
        return;
      }
      setModel(await r.json());
      setFetchMs(Date.now() - t0);
    } catch (e) {
      if (e && e.name === "AbortError") return;
      setError((e && e.message) || String(e));
      setModel({});
      setFetchMs(Date.now() - t0);
    }
  }, [endpoint, token, simFixture]);

  // Thin HA service executor (real mode only).
  const haService = useCallback(async function (domain, service, data) {
    if (simActive || !endpoint || !token) return;
    const base = endpoint.replace(/\/+$/, "");
    const tauriFetch = window.tauriFetch || fetch;
    const r = await tauriFetch(
      base + "/api/services/" + domain + "/" + service, {
        method: "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(data || {}),
      });
    if (!r.ok) throw new Error(domain + "." + service + " HTTP " + r.status);
  }, [endpoint, token, simActive]);

  // Restore physical state on close: flashed light off, Living Lights
  // master back to its prior value. cleanupRef always holds the freshest
  // closure so the close-effect (keyed on `open` only) sees current state.
  cleanupRef.current = function () {
    if (flashedLight) {
      haService(_spDomain(flashedLight), "turn_off",
                { entity_id: flashedLight }).catch(function () {});
    }
    if (livingLightsPrior != null) {
      haService("input_boolean",
                livingLightsPrior === "on" ? "turn_on" : "turn_off",
                { entity_id: SP_LIVING_LIGHTS }).catch(function () {});
    }
  };
  useEffect(function () {
    if (!open) return undefined;
    return function () { cleanupRef.current(); };
  }, [open]);

  // Initial fetch + state reset on open.
  useEffect(function () {
    if (!open) return undefined;
    setModel(null);
    setSelectedLight(null);
    setSelectedCamera(null);
    setPolygon([]);
    setDirty(false);
    setFlashedLight(null);
    setLivingLightsPrior(null);
    setError(null);
    fetchModel();
    return function () {
      try { abortRef.current && abortRef.current.abort(); } catch (_) {}
    };
  }, [open, fetchModel]);

  // Escape closes.
  useEffect(function () {
    if (!open) return undefined;
    const onKey = function (e) {
      if (e.key === "Escape") onClose && onClose();
    };
    window.addEventListener("keydown", onKey);
    return function () { window.removeEventListener("keydown", onKey); };
  }, [open, onClose]);

  const selectLight = useCallback(function (entity) {
    if (entity === selectedLight) return;
    if (dirty &&
        !window.confirm("Discard the unsaved drawing for the current light?")) {
      return;
    }
    // Turn off any flashed light so the room is neutral for the next draw.
    if (flashedLight) {
      haService(_spDomain(flashedLight), "turn_off",
                { entity_id: flashedLight }).catch(function () {});
      setFlashedLight(null);
    }
    const rec = model && model.lights && model.lights[entity];
    const lc = model && model.light_camera;
    const fp = (rec && rec.footprint_polygon) || {};
    const cam = (lc && lc[entity]) || Object.keys(fp)[0] ||
      (model && model.cameras && Object.keys(model.cameras)[0]) || null;
    setSelectedLight(entity);
    setSelectedCamera(cam);
    const poly = (cam && Array.isArray(fp[cam])) ? fp[cam] : [];
    setPolygon(poly.map(function (p) { return [p[0], p[1]]; }));
    setDirty(false);
  }, [selectedLight, dirty, flashedLight, model, haService]);

  const selectCamera = useCallback(function (cam) {
    if (cam === selectedCamera) return;
    if (dirty &&
        !window.confirm("Discard the unsaved drawing for this camera?")) {
      return;
    }
    setSelectedCamera(cam);
    const rec = selectedLight && model && model.lights &&
      model.lights[selectedLight];
    const fp = (rec && rec.footprint_polygon) || {};
    const poly = Array.isArray(fp[cam]) ? fp[cam] : [];
    setPolygon(poly.map(function (p) { return [p[0], p[1]]; }));
    setDirty(false);
  }, [selectedCamera, dirty, selectedLight, model]);

  const updatePolygon = useCallback(function (next) {
    setPolygon(next);
    setDirty(true);
  }, []);

  const undo = useCallback(function () {
    setPolygon(function (p) { return p.slice(0, -1); });
    setDirty(true);
  }, []);

  const clearPoly = useCallback(function () {
    setPolygon([]);
    setDirty(true);
  }, []);

  // Save: rasterise the polygon to cells, merge into the light's
  // per-camera footprint (other cameras untouched), POST. A polygon under
  // 3 vertices clears this camera's footprint (an explicit empty draw).
  const save = useCallback(async function () {
    if (!selectedLight || !selectedCamera || simActive) return;
    if (!endpoint || !token) { setError("not connected"); return; }
    setPosting(true);
    setError(null);
    try {
      const SH = window.HomeSpatialHelpers;
      const rec = (model && model.lights && model.lights[selectedLight]) || {};
      const camMeta = (model && model.cameras &&
                       model.cameras[selectedCamera]) || {};
      const cols = camMeta.grid_cols || 32;
      const rows = camMeta.grid_rows || 18;
      const fpPoly = Object.assign({}, rec.footprint_polygon || {});
      const fpCells = Object.assign({}, rec.footprint || {});
      if (polygon.length >= 3) {
        fpPoly[selectedCamera] = polygon;
        fpCells[selectedCamera] = SH.rasterizePolygon(polygon, cols, rows);
      } else {
        delete fpPoly[selectedCamera];
        delete fpCells[selectedCamera];
      }
      const base = endpoint.replace(/\/+$/, "");
      const url = base + "/api/extended_openai_conversation/spatial_model";
      const tauriFetch = window.tauriFetch || fetch;
      const r = await tauriFetch(url, {
        method: "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          entity_id: selectedLight,
          footprint_polygon: fpPoly,
          footprint: fpCells,
        }),
      });
      if (!r.ok) { setError("save HTTP " + r.status); return; }
      await fetchModel();
      setDirty(false);
    } catch (e) {
      setError((e && e.message) || String(e));
    } finally {
      setPosting(false);
    }
  }, [selectedLight, selectedCamera, simActive, endpoint, token, polygon,
      model, fetchModel]);

  // Flash toggle: turn the selected light on (pausing the Living Lights
  // master so occupancy automations can't fight it), or off if already on.
  const flash = useCallback(async function () {
    if (!selectedLight || simActive || !endpoint || !token) return;
    if (flashedLight === selectedLight) {
      setFlashBusy(true);
      try {
        await haService(_spDomain(selectedLight), "turn_off",
                        { entity_id: selectedLight });
        setFlashedLight(null);
        setNonce(function (n) { return n + 1; });
      } catch (e) {
        setError((e && e.message) || String(e));
      } finally { setFlashBusy(false); }
      return;
    }
    setFlashBusy(true);
    setError(null);
    try {
      if (livingLightsPrior === null) {
        let prior = "on";
        try {
          const base = endpoint.replace(/\/+$/, "");
          const tauriFetch = window.tauriFetch || fetch;
          const sr = await tauriFetch(
            base + "/api/states/" + SP_LIVING_LIGHTS,
            { headers: { Authorization: "Bearer " + token } });
          if (sr.ok) {
            const sj = await sr.json();
            prior = (sj && sj.state) || "on";
          }
        } catch (_) {}
        setLivingLightsPrior(prior);
        await haService("input_boolean", "turn_off",
                        { entity_id: SP_LIVING_LIGHTS });
      }
      await haService(_spDomain(selectedLight), "turn_on",
                      { entity_id: selectedLight });
      setFlashedLight(selectedLight);
      setNonce(function (n) { return n + 1; });
      await _spSleep(900);
      setNonce(function (n) { return n + 1; });
    } catch (e) {
      setError((e && e.message) || String(e));
    } finally {
      setFlashBusy(false);
    }
  }, [selectedLight, simActive, endpoint, token, flashedLight,
      livingLightsPrior, haService]);

  if (!open) return null;

  const SH = window.HomeSpatialHelpers;
  const cameras = (model && model.cameras) ? Object.keys(model.cameras) : [];
  const lights = (model && model.lights) || {};
  const lightIds = Object.keys(lights);
  const lightCamera = (model && model.light_camera) || {};
  const hasModel = !!(model && model.cameras);
  const notFound = !!(model && model.calibrated === false);
  const camKey = selectedCamera ||
    (cameras.indexOf("living_room") >= 0 ? "living_room" : cameras[0]) || null;
  const camMeta = (camKey && model && model.cameras)
    ? model.cameras[camKey] : null;
  const frigateUrl = (model && model.frigate_url) || SP_FRIGATE_DEFAULT;
  const drawnCount = lightIds.reduce(function (acc, id) {
    return acc + (spDrawnStatus(lights[id]).key === "drawn" ? 1 : 0);
  }, 0);
  const sortedIds = lightIds.slice().sort(function (a, b) {
    const ra = spDrawnStatus(lights[a]).key === "todo" ? 0 : 1;
    const rb = spDrawnStatus(lights[b]).key === "todo" ? 0 : 1;
    if (ra !== rb) return ra - rb;
    return a < b ? -1 : (a > b ? 1 : 0);
  });
  const readOnly = simActive || !selectedLight || posting;
  const canFlash = !!selectedLight && !simActive && !!endpoint && !!token;

  let caption;
  if (!selectedLight) {
    caption = "select a light below to start drawing its footprint";
  } else if (polygon.length === 0) {
    caption = selectedLight + " · " + (camKey || "?")
      + " — click on the image to outline where this light shines";
  } else if (polygon.length < 3) {
    caption = polygon.length + " point" + (polygon.length === 1 ? "" : "s")
      + " — add at least 3 to form a footprint";
  } else {
    const ct = SH ? SH.polygonCentroid(polygon) : null;
    caption = polygon.length + " points"
      + (ct ? " · centroid " + ct[0] + ", " + ct[1] : "");
  }

  return (
    <div
      role="dialog" aria-modal="true" aria-label="Spatial light-footprint map"
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: "min(560px, 100vw)", background: "var(--hg-bg-0)",
        borderLeft: "1px solid var(--hg-border)",
        boxShadow: "-12px 0 32px rgba(0,0,0,0.3)", zIndex: 1100,
        display: "flex", flexDirection: "column", fontFamily: SP_FONT_MONO,
        animation: "spatial-slide-in 220ms cubic-bezier(0.16,1,0.3,1)",
      }}
    >
      <style>{`
        @keyframes spatial-slide-in {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>

      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "14px 24px 12px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <div style={{ display: "flex", flexDirection: "column",
                      lineHeight: 1.05 }}>
          <span style={{ fontFamily: SP_FONT_SANS, fontSize: 15,
                         fontWeight: 500, color: "var(--hg-fg-0)",
                         letterSpacing: "-0.02em" }}>spatial</span>
          <span style={{ fontSize: 8.5, letterSpacing: "0.24em",
                         fontWeight: 500, color: "var(--hg-fg-4)",
                         marginTop: 3 }}>light footprint map</span>
        </div>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6,
                       alignItems: "center" }}>
          {fetchMs != null && (
            <span style={{ color: "var(--hg-fg-4)", fontSize: 10,
                           marginRight: 4 }}>{fetchMs}ms</span>
          )}
          <button
            onClick={function () {
              setNonce(function (n) { return n + 1; });
              fetchModel();
            }}
            title="Re-fetch the spatial model + refresh the camera frame"
            className="hg-focusable"
            style={{
              background: "transparent",
              border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-3)", padding: "4px 9px",
              fontFamily: SP_FONT_MONO, fontSize: 10,
              letterSpacing: "0.12em", cursor: "pointer",
              textTransform: "lowercase",
            }}
          >refresh</button>
          <button
            onClick={onClose} aria-label="Close" className="hg-focusable"
            style={{
              background: "transparent",
              border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-2)", padding: "4px 11px",
              fontFamily: SP_FONT_MONO, fontSize: 10.5,
              letterSpacing: "0.12em", cursor: "pointer",
              textTransform: "lowercase",
            }}
          >close · esc</button>
        </span>
      </div>

      <SpTabStrip />

      {/* Body — `hg-scroll` class applies the themed thin scrollbar. */}
      <div className="hg-scroll" style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
        {!SH && (
          <div style={{ padding: "30px 24px", color: "var(--hg-crit)",
                        fontSize: 11 }}>
            home-spatial-helpers.js not loaded
          </div>
        )}

        {SH && model === null && (
          <div style={{ padding: "40px 24px", textAlign: "center",
                        color: "var(--hg-fg-3)", fontSize: 11 }}>
            loading spatial model…
          </div>
        )}

        {SH && model !== null && !hasModel && notFound && (
          <div style={{ padding: "34px 24px", textAlign: "center" }}>
            <div style={{ color: "var(--hg-warn)", fontSize: 12,
                          marginBottom: 8 }}>no spatial model on the hub</div>
            <div style={{ color: "var(--hg-fg-3)", fontSize: 10.5,
                          lineHeight: 1.6 }}>
              Build the skeleton light map:<br />
              <span style={{ color: "var(--hg-fg-1)" }}>
                py -3 tools/init-spatial-model.py</span>
              {model.reason && (
                <><br /><br /><span style={{ color: "var(--hg-fg-4)" }}>
                  {model.reason}</span></>
              )}
            </div>
          </div>
        )}

        {SH && model !== null && !hasModel && !notFound && (
          <div style={{ padding: "20px 24px", color: "var(--hg-crit)",
                        fontSize: 11 }}>
            {error || "empty spatial model"}
          </div>
        )}

        {SH && hasModel && (
          <>
            <SpStepStrip />

            {simActive && (
              <div style={{
                margin: "8px 24px 0", padding: "7px 11px",
                border: "1px solid var(--hg-border-soft)",
                background: "var(--hg-bg-1)", borderRadius: 3,
                fontFamily: SP_FONT_SANS, fontSize: 10.5,
                color: "var(--hg-fg-4)", lineHeight: 1.5,
              }}>
                sim mode — the spatial map is shown read-only. Connect to the
                live hub to draw light footprints.
              </div>
            )}

            {error && (
              <div style={{
                margin: "8px 24px 0", padding: "7px 11px",
                border: "1px solid var(--hg-crit)", color: "var(--hg-crit)",
                fontFamily: SP_FONT_MONO, fontSize: 10.5, borderRadius: 3,
              }}>{error}</div>
            )}

            {/* Meta roll-up */}
            <div style={{ padding: "8px 24px 4px", color: "var(--hg-fg-4)",
                          fontSize: 9.5, fontFamily: SP_FONT_MONO }}>
              {model.source ? String(model.source).replace(/_/g, "-")
                            : "spatial"} map
              {model.created_at
                ? " · created " +
                  String(model.created_at).slice(0, 16).replace("T", " ")
                : ""}
            </div>

            {/* Camera picker + zones toggle */}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap",
                          padding: "4px 24px 8px", alignItems: "center" }}>
              <span style={{ color: "var(--hg-fg-4)", fontSize: 9,
                             letterSpacing: "0.14em",
                             textTransform: "uppercase",
                             fontFamily: SP_FONT_MONO, marginRight: 2 }}>
                camera
              </span>
              {cameras.map(function (c) {
                return (
                  <button
                    key={c}
                    onClick={function () { selectCamera(c); }}
                    className="hg-focusable"
                    style={{
                      background: c === camKey
                        ? "var(--hg-bg-1)" : "transparent",
                      border: "1px solid " + (c === camKey
                        ? "var(--hg-ice)" : "var(--hg-border-soft)"),
                      color: c === camKey
                        ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
                      padding: "3px 9px", fontFamily: SP_FONT_MONO,
                      fontSize: 9.5, letterSpacing: "0.06em",
                      cursor: "pointer",
                    }}
                  >{c}</button>
                );
              })}
              <button
                onClick={function () { setShowZones(function (v) {
                  return !v; }); }}
                className="hg-focusable"
                title="Overlay the Frigate zone polygons as draw reference"
                style={{
                  marginLeft: "auto",
                  background: showZones ? "var(--hg-bg-1)" : "transparent",
                  border: "1px solid " + (showZones
                    ? "var(--hg-ice)" : "var(--hg-border-soft)"),
                  color: showZones ? "var(--hg-fg-0)" : "var(--hg-fg-4)",
                  padding: "3px 9px", fontFamily: SP_FONT_MONO,
                  fontSize: 9.5, letterSpacing: "0.06em", cursor: "pointer",
                }}
              >frigate zones</button>
            </div>

            {/* Draw canvas */}
            {camKey && (
              <div style={{ padding: "0 24px 4px" }}>
                <SpDrawCanvas
                  cam={camKey}
                  gridCols={camMeta && camMeta.grid_cols}
                  gridRows={camMeta && camMeta.grid_rows}
                  polygon={polygon}
                  onChange={updatePolygon}
                  zones={camMeta && camMeta.zones}
                  showZones={showZones}
                  frigateUrl={simActive ? null : frigateUrl}
                  nonce={nonce}
                  readOnly={readOnly}
                  hasLight={!!selectedLight}
                />
                <div style={{ color: "var(--hg-fg-4)", fontSize: 9,
                              marginTop: 4, lineHeight: 1.45 }}>
                  {caption}
                </div>
              </div>
            )}

            {/* Draw toolbar — real mode only */}
            {!simActive && (
              <SpDrawTools
                hasLight={!!selectedLight}
                polygon={polygon}
                dirty={dirty}
                posting={posting}
                flashedOn={flashedLight === selectedLight && !!selectedLight}
                flashBusy={flashBusy}
                canFlash={canFlash}
                onFlash={flash}
                onUndo={undo}
                onClear={clearPoly}
                onSave={save}
              />
            )}

            {/* Light list */}
            <div style={{ marginTop: 8 }}>
              <div style={{ padding: "8px 24px 4px", display: "flex",
                            alignItems: "baseline", gap: 8 }}>
                <span style={{
                  color: "var(--hg-fg-3)", fontSize: 10,
                  letterSpacing: "0.16em", textTransform: "uppercase",
                  fontFamily: SP_FONT_MONO,
                }}>lights</span>
                <span style={{
                  color: drawnCount === lightIds.length && lightIds.length > 0
                    ? "var(--hg-ice)" : "var(--hg-fg-4)",
                  fontSize: 9.5, fontFamily: SP_FONT_MONO,
                }}>
                  {drawnCount} / {lightIds.length} drawn
                </span>
              </div>
              {lightIds.length === 0 ? (
                <div style={{ padding: "8px 24px", color: "var(--hg-fg-4)",
                              fontSize: 11, fontFamily: SP_FONT_MONO }}>
                  no lights in model
                </div>
              ) : (
                sortedIds.map(function (eid) {
                  return (
                    <SpLightRow
                      key={eid} entity={eid} rec={lights[eid]}
                      cam={lightCamera[eid]}
                      selected={eid === selectedLight}
                      onSelect={selectLight} />
                  );
                })
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

window.HomeSpatialDrawer = HomeSpatialDrawer;
