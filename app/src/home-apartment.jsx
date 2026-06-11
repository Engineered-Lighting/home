/* eslint-disable */
/**
 * home-apartment.jsx — the /apartment full-screen 3D spatial command center.
 *
 * P0: white cloud + constrained isometric nav. P2: apartment_model devices/
 * zones, live HA state on markers, hover labels + control cards, edit mode,
 * and the person dot (precise) / room-pulse (room-level) honesty ladder fed
 * by the spatial-tracker WS. P3: photo (splat) + mesh modes.
 *
 * Bridge contract: touches ONLY window.Home3D.ready/.error; live engine is
 * cached on window.__APT_ENGINE for instant warm reopen.
 */

const { useState, useEffect, useRef, useCallback } = React;

const APT_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const APT_FONT_SANS = '"Geist", "Inter", sans-serif';

function AptHudButton({ label, onClick, active, disabled, title }) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title} className="hg-focusable"
      style={{
        background: active ? "var(--hg-ice)" : "rgba(10,12,16,0.55)",
        border: "1px solid " + (active ? "var(--hg-ice)" : "var(--hg-border-soft)"),
        color: active ? "#0b0d11" : (disabled ? "var(--hg-fg-5)" : "var(--hg-fg-1)"),
        padding: "5px 12px", fontFamily: APT_FONT_MONO, fontSize: 10.5,
        letterSpacing: "0.1em", textTransform: "lowercase",
        cursor: disabled ? "default" : "pointer", backdropFilter: "blur(6px)",
      }}
    >{label}</button>
  );
}

function HomeApartmentView({ open, onClose, endpoint, token, sim }) {
  const hostRef = useRef(null);
  const canvasRef = useRef(null);
  const engineRef = useRef(null);
  const detachRef = useRef(null);
  const wasMaximizedRef = useRef(null);
  const [phase, setPhase] = useState("boot");
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [stats, setStats] = useState(null);
  const [mode, setMode] = useState("points");
  const [toast, setToast] = useState(null);
  const [azIdx, setAzIdx] = useState(1);
  const [editing, setEditing] = useState(false);
  const [model, setModel] = useState(window.HomeApartmentData.EMPTY_MODEL);
  const [registry, setRegistry] = useState({ entities: [], areas: [], devices: [], states: {} });
  const [saving, setSaving] = useState(false);
  const [track, setTrack] = useState(null);          // primary person track
  const [trackerStatus, setTrackerStatus] = useState("connecting");
  const [hoverId, setHoverId] = useState(null);
  const [cardId, setCardId] = useState(null);
  const [inCamPose, setInCamPose] = useState(false);
  const [anchors, setAnchors] = useState({});        // id -> {x, y, visible}
  const statesRef = useRef({});                      // entity_id -> ha state
  const simActive = !!(sim && sim.active);

  const showToast = useCallback((text) => {
    setToast(text);
    setTimeout(() => setToast(null), 2800);
  }, []);

  /* ---------------- engine boot ---------------- */
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;

    (async () => {
      try {
        try {
          const w = await window.getTauriWindow?.();
          if (w) {
            wasMaximizedRef.current = await w.isMaximized?.();
            if (!wasMaximizedRef.current) await w.maximize?.();
          }
        } catch (e) { /* browser mode */ }

        let engine = window.__APT_ENGINE;
        if (!engine) {
          setPhase("boot");
          const mod = await window.Home3D.ready;
          if (!mod) throw (window.Home3D.error || new Error("3d engine failed to load"));
          if (cancelled) return;
          engine = await mod.createEngine({
            canvas: canvasRef.current, hostEl: hostRef.current, sim: simActive,
          });
          window.__APT_ENGINE = engine;
          engine.on("progress", (p) => setProgress({ ...p }));
          engine.on("stats", (s) => setStats(s));
          setPhase("loading");
          engine.pointsPromise.then(
            () => !cancelled && setPhase("ready"),
            (e) => { if (!cancelled) { setError(String(e?.message || e)); setPhase("error"); } },
          );
        } else {
          setPhase("ready");
        }
        engineRef.current = engine;

        const host = hostRef.current;
        const size = () => engine.setSize(host.clientWidth, host.clientHeight);
        size();
        const ro = new ResizeObserver(size);
        ro.observe(host);
        const detachInput = engine.attachInput(host);
        engine.setRunning(true);
        const azTimer = setInterval(() => {
          setAzIdx(engine.rig.azimuthIndex());
          setInCamPose(!!engine.rig.inCameraPose?.());
        }, 200);
        detachRef.current = () => { ro.disconnect(); detachInput(); clearInterval(azTimer); };
      } catch (e) {
        if (!cancelled) { setError(String(e?.message || e)); setPhase("error"); }
      }
    })();

    return () => {
      cancelled = true;
      detachRef.current?.();
      detachRef.current = null;
      engineRef.current?.setRunning(false);
      (async () => {
        try {
          const w = await window.getTauriWindow?.();
          if (w && wasMaximizedRef.current === false) await w.unmaximize?.();
        } catch (e) { /* */ }
      })();
    };
  }, [open, simActive]);

  /* ---------------- model + registry load ---------------- */
  useEffect(() => {
    if (!open) return undefined;
    let dead = false;
    (async () => {
      const m = await window.HomeApartmentData.getModel({ endpoint, token, sim: simActive });
      if (!dead) setModel(m);
      const client = window.__hav_haClient;
      if (client && !simActive) {
        const reg = await window.HomeApartmentData.getRegistry(client);
        if (!dead) setRegistry(reg);
      }
    })();
    return () => { dead = true; };
  }, [open, endpoint, token, simActive]);

  /* model -> overlay sync */
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || phase !== "ready") return;
    engine.overlay.setDevices(model.devices || []);
    engine.overlay.setZones(model.zones || []);
    for (const [eid, st] of Object.entries(statesRef.current)) {
      const dev = (model.devices || []).find((d) => d.ha_entity_id === eid);
      if (dev) engine.overlay.setDeviceState(dev.id, st);
    }
    if (editing) engine.overlay.setZonesVisible(0.7);
  }, [model, phase, editing]);

  /* HA entity state binding */
  useEffect(() => {
    if (!open || phase !== "ready" || simActive) return undefined;
    const client = window.__hav_haClient;
    if (!client) return undefined;
    return window.HomeApartmentData.bindStates(client, model, (entityId, st) => {
      statesRef.current[entityId] = st;
      const dev = (model.devices || []).find((d) => d.ha_entity_id === entityId);
      if (dev) engineRef.current?.overlay.setDeviceState(dev.id, st);
    });
  }, [open, phase, model, simActive]);

  /* tracker WS -> person dot / room pulse */
  useEffect(() => {
    if (!open || phase !== "ready") return undefined;
    return window.HomeApartmentData.openTracks({
      sim: simActive,
      onStatus: setTrackerStatus,
      onTracks: (frame) => {
        const t = (frame.tracks || [])[0] || null;
        setTrack(t);
        engineRef.current?.overlay.setPerson(t);
      },
    });
  }, [open, phase, simActive]);

  /* hover + click picking (view mode only) */
  useEffect(() => {
    if (!open || phase !== "ready" || editing) return undefined;
    const engine = engineRef.current;
    const host = hostRef.current;
    if (!engine || !host) return undefined;
    let raf = 0;
    const onMove = (e) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
        const id = hits.length ? hits[0].object.userData.deviceId : null;
        setHoverId(id);
        engine.overlay.setHover(id);
        host.style.cursor = id ? "pointer" : "grab";
      });
    };
    const onClick = (e) => {
      const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
      setCardId(hits.length ? hits[0].object.userData.deviceId : null);
    };
    const onDbl = (e) => {
      const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
      if (!hits.length) return;
      const dev = (model.devices || []).find((d) => d.id === hits[0].object.userData.deviceId);
      if (dev && dev.ha_entity_id && (dev.type === "light" || dev.ha_entity_id.startsWith("switch."))) {
        // double-click = instant toggle (the anti-"slower than 2D" rule)
        callSvc(dev.ha_entity_id.split(".")[0], "toggle", { entity_id: dev.ha_entity_id });
      }
    };
    host.addEventListener("pointermove", onMove);
    host.addEventListener("click", onClick);
    host.addEventListener("dblclick", onDbl);
    return () => {
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("click", onClick);
      host.removeEventListener("dblclick", onDbl);
      cancelAnimationFrame(raf);
    };
  }, [open, phase, editing, model]);

  /* label/card screen anchors — updated on a light timer (rAF-driven HUD) */
  useEffect(() => {
    if (!open || phase !== "ready") return undefined;
    const timer = setInterval(() => {
      const engine = engineRef.current;
      if (!engine) return;
      const out = {};
      const px = (obj) => engine.picking.projectToScreen(
        obj.getWorldPosition(new (Object.getPrototypeOf(obj.position).constructor)()));
      // person anchor
      if (engine.overlay.personGroup.visible) {
        out.__person = px(engine.overlay.personGroup);
      }
      const want = new Set([hoverId, cardId].filter(Boolean));
      for (const id of want) {
        const m = engine.overlay.markersById.get(id);
        if (m) out[id] = px(m.group);
      }
      setAnchors(out);
    }, 80);
    return () => clearInterval(timer);
  }, [open, phase, hoverId, cardId]);

  /* keyboard */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
      const rig = engineRef.current?.rig;
      if (e.key === "Escape") {
        if (cardId) { setCardId(null); return; }
        if (engineRef.current?.rig.inCameraPose?.()) { engineRef.current.rig.returnToOverview(); return; }
        if (editing) { setEditing(false); return; }
        onClose?.(); return;
      }
      if (!rig) return;
      if (e.key === "ArrowLeft") rig.stepAzimuth(-1);
      else if (e.key === "ArrowRight") rig.stepAzimuth(1);
      else if (e.key === "ArrowUp") rig.stepElevation(1);
      else if (e.key === "ArrowDown") rig.stepElevation(-1);
      else if (e.key === "h" || e.key === "Home") rig.snapHome();
      else if (e.key === "+" || e.key === "=") rig.stepZoom(1);
      else if (e.key === "-") rig.stepZoom(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, editing, cardId]);

  const pickMode = useCallback(async (m) => {
    const engine = engineRef.current;
    if (!engine || m === mode) return;
    try {
      await engine.modes.setMode(m);
      setMode(m);
    } catch (e) {
      showToast(m === "splat" ? "photo unavailable — check apartment.spz asset"
                              : "mesh unavailable — check collision.glb asset");
    }
  }, [mode, showToast]);

  const callSvc = useCallback(async (domain, service, data) => {
    const client = window.__hav_haClient;
    if (!client || simActive) { showToast("sim mode — controls disabled"); return; }
    try { await window.HomeApartmentData.callService(client, domain, service, data); }
    catch (e) { showToast(`${data.entity_id || domain} didn't respond`); }
  }, [simActive, showToast]);

  const saveModel = useCallback(async () => {
    setSaving(true);
    const res = await window.HomeApartmentData.saveModel(model, { endpoint, token, sim: simActive });
    setSaving(false);
    if (res.ok) {
      setModel((m) => ({ ...m, revision: res.revision }));
      showToast(`saved · revision ${res.revision}`);
    } else if (res.conflict) {
      showToast("model changed elsewhere — reloaded server copy (your draft is kept locally)");
      setModel(res.stored);
    } else if (res.offline || res.sim) {
      showToast("offline — kept as local draft");
    } else {
      showToast(`save failed — ${res.error}`);
    }
  }, [model, endpoint, token, simActive, showToast]);

  if (!open) return null;

  const pct = progress.total ? Math.floor((progress.loaded / progress.total) * 100) : 0;
  const cardDevice = (model.devices || []).find((d) => d.id === cardId) || null;
  const hoverDevice = (model.devices || []).find((d) => d.id === hoverId) || null;

  return (
    <div
      role="dialog" aria-modal="true" aria-label="3d apartment"
      style={{
        position: "fixed", inset: 0, zIndex: 1000, background: "#000",
        display: "flex", flexDirection: "column", overflow: "hidden",
        animation: "apt-fade-in 260ms ease-out",
      }}
    >
      <style>{`
        @keyframes apt-fade-in { from { opacity: 0; } to { opacity: 1; } }
        @keyframes apt-toast-in { from { transform: translateY(8px); opacity: 0; }
                                  to { transform: translateY(0); opacity: 1; } }
      `}</style>

      <div ref={hostRef} style={{ position: "absolute", inset: 0, touchAction: "none", cursor: "grab" }}>
        <canvas ref={canvasRef} style={{ display: "block", width: "100%", height: "100%" }} />
      </div>

      {/* top bar */}
      {!editing && (
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, display: "flex",
          alignItems: "center", gap: 10, padding: "12px 18px", pointerEvents: "none",
        }}>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.05 }}>
            <span style={{ fontFamily: APT_FONT_SANS, fontSize: 15, fontWeight: 500,
                           color: "var(--hg-fg-0)", letterSpacing: "-0.02em" }}>apartment</span>
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.24em",
                           color: "var(--hg-fg-4)", marginTop: 3 }}>spatial command center</span>
          </div>
          {simActive && (
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
                           color: "var(--hg-warn)", border: "1px solid var(--hg-border-soft)",
                           padding: "3px 8px", marginLeft: 6 }}>sim</span>
          )}
          <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.1em",
                         color: trackerStatus === "live" ? "var(--hg-ice)" : "var(--hg-fg-5)",
                         marginLeft: 8 }}>
            tracker · {trackerStatus}
          </span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 6, pointerEvents: "auto" }}>
            {inCamPose && (
              <AptHudButton label="← back" onClick={() => engineRef.current?.rig.returnToOverview()} />
            )}
            {!inCamPose && (
              <AptHudButton label="edit" onClick={() => { setCardId(null); setEditing(true); }} />
            )}
            <AptHudButton label="close · esc" onClick={onClose} />
          </span>
        </div>
      )}

      {/* loading / error */}
      {(phase === "boot" || phase === "loading") && (
        <div style={{
          position: "absolute", left: 0, right: 0, top: "46%", textAlign: "center",
          fontFamily: APT_FONT_MONO, color: "var(--hg-fg-3)", fontSize: 11,
          letterSpacing: "0.14em", pointerEvents: "none",
        }}>
          {phase === "boot" ? "initializing renderer" : `loading apartment · ${pct}%`}
          <div style={{ margin: "10px auto 0", width: 220, height: 1,
                        background: "var(--hg-border-soft)", position: "relative" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0,
                          width: `${pct}%`, background: "var(--hg-ice)", transition: "width 200ms" }} />
          </div>
        </div>
      )}
      {phase === "error" && (
        <div style={{ position: "absolute", left: 0, right: 0, top: "44%", textAlign: "center",
                      fontFamily: APT_FONT_MONO, fontSize: 11, color: "var(--hg-crit)" }}>
          renderer failed — {error}
        </div>
      )}

      {/* bottom HUD (view mode) */}
      {!editing && (
        <div style={{
          position: "absolute", left: 0, right: 0, bottom: 0, display: "flex",
          alignItems: "flex-end", padding: "14px 18px", pointerEvents: "none", gap: 12,
        }}>
          <div style={{ fontFamily: APT_FONT_MONO, fontSize: 9, color: "var(--hg-fg-5)",
                        letterSpacing: "0.08em", lineHeight: 1.6, minWidth: 170 }}>
            {stats && (<>
              {Math.round(stats.fps)} fps · {(stats.points / 1000).toFixed(0)}k pts · dpr {stats.pixelRatio}
              <br />{String(stats.gpu).slice(0, 48).toLowerCase()}
            </>)}
          </div>
          <div style={{ margin: "0 auto", display: "flex", gap: 6, pointerEvents: "auto" }}>
            <AptHudButton label="cloud" active={mode === "points"} onClick={() => pickMode("points")} />
            <AptHudButton label="photo" active={mode === "splat"} onClick={() => pickMode("splat")} />
            <AptHudButton label="mesh" active={mode === "mesh"} onClick={() => pickMode("mesh")} />
          </div>
          <div style={{ textAlign: "right", pointerEvents: "auto" }}>
            <div style={{ display: "flex", gap: 5, justifyContent: "flex-end", marginBottom: 6 }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <button key={i} className="hg-focusable"
                  onClick={() => engineRef.current?.rig.goTo({ az: i, dur: 600 })}
                  style={{ width: 9, height: 9, borderRadius: 9, padding: 0, cursor: "pointer",
                           border: "1px solid var(--hg-border)",
                           background: i === azIdx ? "var(--hg-ice)" : "transparent" }} />
              ))}
            </div>
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, color: "var(--hg-fg-5)",
                           letterSpacing: "0.1em" }}>
              drag · wheel zoom · ←→↑↓ · h home · dbl-click toggles lights
            </span>
          </div>
        </div>
      )}

      {/* labels + cards layer */}
      {phase === "ready" && !editing && (
        <>
          {hoverDevice && !cardDevice && (
            <window.AptDeviceLabel device={hoverDevice} screen={anchors[hoverId]} />
          )}
          {track && track.pos && (
            <window.AptPersonLabel track={track} screen={anchors.__person} />
          )}
          {track && !track.pos && <window.AptRoomChip track={track} />}
          {cardDevice && (
            <window.AptControlCard
              device={cardDevice}
              state={statesRef.current[cardDevice.ha_entity_id]}
              screen={anchors[cardId] || { x: window.innerWidth / 2, y: 100 }}
              onClose={() => setCardId(null)}
              onService={callSvc}
              onFlyTo={(dev) => engineRef.current?.flyToDevice(dev)}
              sim={simActive}
            />
          )}
        </>
      )}

      {/* edit mode */}
      {editing && phase === "ready" && window.HomeApartmentEdit && (
        <window.HomeApartmentEdit
          engine={engineRef.current}
          model={model}
          onModel={setModel}
          registry={registry}
          onSave={saveModel}
          onExit={() => setEditing(false)}
          sim={simActive}
          saving={saving}
        />
      )}

      {toast && (
        <div style={{
          position: "absolute", bottom: 74, left: "50%", transform: "translateX(-50%)",
          fontFamily: APT_FONT_MONO, fontSize: 10, color: "var(--hg-fg-1)",
          background: "rgba(10,12,16,0.85)", border: "1px solid var(--hg-border-soft)",
          padding: "7px 13px", animation: "apt-toast-in 180ms ease-out", zIndex: 6,
        }}>{toast}</div>
      )}
    </div>
  );
}

window.HomeApartmentView = HomeApartmentView;
