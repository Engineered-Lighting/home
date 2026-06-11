/* eslint-disable */
/**
 * home-apartment-edit.jsx — /apartment edit mode.
 *
 * Top-down north-up pose, dimmed cloud as basemap. Place devices from the
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

function edSlug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function EdButton({ label, onClick, active, danger, disabled, title }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title} className="hg-focusable" style={{
      background: active ? "var(--hg-ice)" : "transparent",
      border: "1px solid " + (danger ? "var(--hg-crit)" : active ? "var(--hg-ice)" : "var(--hg-border-soft)"),
      color: danger ? "var(--hg-crit)" : active ? "#0b0d11" : disabled ? "var(--hg-fg-5)" : "var(--hg-fg-1)",
      padding: "4px 10px", fontFamily: ED_MONO, fontSize: 9.5, letterSpacing: "0.08em",
      textTransform: "lowercase", cursor: disabled ? "default" : "pointer",
    }}>{label}</button>
  );
}

function HomeApartmentEdit({ engine, model, onModel, registry, onSave, onExit, sim, saving }) {
  const [tool, setTool] = useState("select");          // select | add | zones
  const [placing, setPlacing] = useState(null);        // palette item being placed
  const [selectedId, setSelectedId] = useState(null);  // device id
  const [selectedZone, setSelectedZone] = useState(null);
  const [zoneDraft, setZoneDraft] = useState([]);      // [[x,y]...] while drawing
  const [dirty, setDirty] = useState(false);
  const undoRef = useRef({ stack: [], redo: [] });
  const dragRef = useRef(null);

  const palette = useMemo(
    () => window.HomeApartmentData.buildPalette(registry, model),
    [registry, model],
  );
  const selected = (model.devices || []).find((d) => d.id === selectedId) || null;
  const zone = (model.zones || []).find((z) => z.id === selectedZone) || null;

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
  }, [onModel]);

  const undo = useCallback(() => {
    const u = undoRef.current;
    if (!u.stack.length) return;
    onModel((prev) => { u.redo.push(JSON.parse(JSON.stringify(prev))); return u.stack.pop(); });
    setDirty(true);
  }, [onModel]);
  const redo = useCallback(() => {
    const u = undoRef.current;
    if (!u.redo.length) return;
    onModel((prev) => { u.stack.push(JSON.parse(JSON.stringify(prev))); return u.redo.pop(); });
    setDirty(true);
  }, [onModel]);

  /* enter/exit the top-down pose + dimmed basemap */
  useEffect(() => {
    if (!engine) return undefined;
    engine.rig.goEditPose();
    engine.overlay.setZonesVisible(0.7);
    const prevOpacity = engine.pointsMaterial.uniforms.uOpacity.value;
    engine.pointsMaterial.uniforms.uOpacity.value = Math.min(prevOpacity, 0.4);
    return () => {
      engine.rig.exitEditPose();
      engine.overlay.setZonesVisible(0);
      engine.pointsMaterial.uniforms.uOpacity.value = 1.0;
    };
  }, [engine]);

  const roomAt = useCallback((x, y) => {
    for (const z of model.zones || []) {
      const poly = z.floor_polygon || [];
      let inside = false; // ray-cast point-in-polygon
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        if (((poly[i][1] > y) !== (poly[j][1] > y)) &&
            (x < (poly[j][0] - poly[i][0]) * (y - poly[i][1]) / (poly[j][1] - poly[i][1]) + poly[i][0])) {
          inside = !inside;
        }
      }
      if (inside) return z.id;
    }
    return null;
  }, [model.zones]);

  /* canvas interactions while edit mode is active */
  useEffect(() => {
    if (!engine) return undefined;
    const host = engine.renderer.domElement.parentElement;

    const onDown = (e) => {
      if (e.button !== 0) return;
      const fp = engine.picking.floorPoint(engine.apartmentRoot, e.clientX, e.clientY);
      if (tool === "zones") {
        if (fp) setZoneDraft((d) => [...d, [Math.round(fp[0] * 100) / 100, Math.round(fp[1] * 100) / 100]]);
        e.stopPropagation();
        return;
      }
      if (tool === "add" && placing && fp) {
        const sx = Math.round(fp[0] * 10) / 10, sy = Math.round(fp[1] * 10) / 10;
        const h = DOMAIN_DEFAULT_HEIGHT[placing.domain] ?? 1.0;
        const id = `dev-${edSlug(placing.entity_id)}`;
        mutate((m) => {
          m.devices = m.devices || [];
          m.devices.push({
            id, type: DOMAIN_TYPE[placing.domain] || "other", name: placing.name.toLowerCase(),
            ha_entity_id: placing.entity_id, pos: [sx, sy, h], yaw_rad: 0,
            height_preset: "custom", room_id: roomAt(sx, sy), controllable: true,
            confidence: 1, source: "manual", updated_at: new Date().toISOString(),
          });
        });
        setSelectedId(id);
        setPlacing(null);
        setTool("select");
        e.stopPropagation();
        return;
      }
      // select / start drag
      const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
      if (hits.length) {
        const id = hits[0].object.userData.deviceId;
        setSelectedId(id);
        setSelectedZone(null);
        dragRef.current = { id, moved: false };
        e.stopPropagation();
      } else {
        setSelectedId(null);
      }
    };
    const onMove = (e) => {
      const d = dragRef.current;
      if (!d) return;
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
      const d = dragRef.current;
      dragRef.current = null;
      if (d && d.moved && d.last) {
        mutate((m) => {
          const dev = (m.devices || []).find((x) => x.id === d.id);
          if (dev) {
            dev.pos[0] = d.last[0]; dev.pos[1] = d.last[1];
            dev.room_id = roomAt(d.last[0], d.last[1]);
            dev.updated_at = new Date().toISOString();
          }
        });
        e.stopPropagation();
      }
    };
    const onKey = (e) => {
      if (e.key === "Enter" && tool === "zones" && zoneDraft.length >= 3) {
        const name = prompt("zone name (e.g. living room)") || `zone ${(model.zones || []).length + 1}`;
        const id = edSlug(name);
        const draft = zoneDraft;
        mutate((m) => {
          m.zones = m.zones || [];
          m.zones.push({
            id, name: name.toLowerCase(),
            color: ZONE_COLORS[m.zones.length % ZONE_COLORS.length],
            floor_polygon: draft, ceiling_height_m: 2.4, frigate_camera: id,
          });
        });
        setZoneDraft([]);
        setSelectedZone(id);
      }
      if (e.key === "z" && (e.ctrlKey || e.metaKey) && !e.shiftKey) { e.preventDefault(); undo(); }
      if (e.key === "z" && (e.ctrlKey || e.metaKey) && e.shiftKey) { e.preventDefault(); redo(); }
    };

    host.addEventListener("pointerdown", onDown, true);   // capture: beat the rig
    host.addEventListener("pointermove", onMove, true);
    host.addEventListener("pointerup", onUp, true);
    window.addEventListener("keydown", onKey);
    return () => {
      host.removeEventListener("pointerdown", onDown, true);
      host.removeEventListener("pointermove", onMove, true);
      host.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [engine, tool, placing, zoneDraft, mutate, roomAt, undo, redo, model.zones]);

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

  const panel = {
    position: "absolute", top: 52, bottom: 64, width: 230,
    background: "rgba(10,12,16,0.88)", border: "1px solid var(--hg-border-soft)",
    backdropFilter: "blur(8px)", overflowY: "auto", padding: "10px 12px",
    fontFamily: ED_MONO,
  };
  const heading = { fontSize: 9, letterSpacing: "0.16em", textTransform: "uppercase",
                    color: "var(--hg-fg-4)", margin: "10px 0 6px" };

  return (
    <>
      {/* toolbar */}
      <div style={{
        position: "absolute", top: 10, left: "50%", transform: "translateX(-50%)",
        display: "flex", gap: 6, alignItems: "center", zIndex: 4,
      }}>
        <span style={{ fontFamily: ED_MONO, fontSize: 10, color: "var(--hg-fg-2)",
                       letterSpacing: "0.14em", marginRight: 8 }}>edit</span>
        <EdButton label="select" active={tool === "select"} onClick={() => { setTool("select"); setPlacing(null); }} />
        <EdButton label="add" active={tool === "add"} onClick={() => setTool("add")} />
        <EdButton label="zones" active={tool === "zones"} onClick={() => { setTool("zones"); setZoneDraft([]); }} />
        <span style={{ width: 12 }} />
        <EdButton label="undo" onClick={undo} disabled={!undoRef.current.stack.length} />
        <EdButton label="redo" onClick={redo} disabled={!undoRef.current.redo.length} />
        <span style={{ width: 12 }} />
        <EdButton label={saving ? "saving…" : dirty ? "save •" : "save"} active={dirty} disabled={saving || sim}
                  onClick={() => { onSave(); setDirty(false); }}
                  title={sim ? "sim mode — saves disabled" : "POST apartment_model"} />
        <EdButton label="done" onClick={onExit} />
      </div>

      {/* left: palette */}
      <div style={{ ...panel, left: 12 }}>
        <div style={heading}>palette · {tool === "add" ? "click an item, then the floor" : "switch to add tool"}</div>
        {Object.entries(palette).sort().map(([area, items]) => (
          <div key={area}>
            <div style={{ ...heading, color: "var(--hg-fg-3)" }}>{area.toLowerCase()}</div>
            {items.map((it) => (
              <div key={it.entity_id}
                onClick={() => { setTool("add"); setPlacing(it); }}
                style={{
                  padding: "4px 6px", cursor: "pointer", fontSize: 10,
                  color: placing?.entity_id === it.entity_id ? "#0b0d11" : "var(--hg-fg-2)",
                  background: placing?.entity_id === it.entity_id ? "var(--hg-ice)" : "transparent",
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}
                title={it.entity_id}>
                {it.domain === "light" ? "◉" : it.domain === "media_player" ? "♪" : it.domain === "camera" ? "▣" : "◌"} {it.name.toLowerCase()}
              </div>
            ))}
          </div>
        ))}
        {!Object.keys(palette).length && (
          <div style={{ fontSize: 9.5, color: "var(--hg-fg-5)" }}>
            no unplaced entities{sim ? " (sim)" : registry.entities.length ? "" : " — registry unavailable (admin token?)"}
          </div>
        )}
      </div>

      {/* right: inspector */}
      <div style={{ ...panel, right: 12 }}>
        {selected ? (
          <>
            <div style={heading}>device</div>
            <input value={selected.name}
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
            <div style={heading}>height · {selected.pos[2].toFixed(2)} m</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {HEIGHT_PRESETS.map(([label, h]) => (
                <EdButton key={label} label={label} active={selected.height_preset === label}
                          onClick={() => setHeight(h, label)} />
              ))}
            </div>
            <input type="range" min="0" max="3" step="0.05" value={selected.pos[2]}
              onChange={(e) => mutate((m) => {
                const d = m.devices.find((x) => x.id === selectedId);
                if (d) { d.pos[2] = +e.target.value; d.height_preset = "custom"; }
              }, { undoable: false })}
              style={{ width: "100%", marginTop: 8 }} />
            {["camera", "tv", "speaker"].includes(selected.type) && (
              <>
                <div style={heading}>
                  aim · {Math.round(((selected.yaw_rad || 0) * 180 / Math.PI + 360) % 360)}°
                </div>
                <input type="range" min="-180" max="180" step="2"
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
            <div style={{ marginTop: 14 }}>
              <EdButton label="delete device" danger onClick={() => {
                mutate((m) => { m.devices = m.devices.filter((x) => x.id !== selectedId); });
                setSelectedId(null);
              }} />
            </div>
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
                mutate((m) => { m.zones = m.zones.filter((x) => x.id !== selectedZone); });
                setSelectedZone(null);
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
                ? `zone tool: click floor to add vertices (${zoneDraft.length}) · enter closes`
                : "select a marker to edit · zones tool draws rooms"}
            </div>
          </>
        )}
      </div>
    </>
  );
}

window.HomeApartmentEdit = HomeApartmentEdit;
