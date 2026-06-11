/* eslint-disable */
/**
 * home-apartment.jsx — the /apartment full-screen 3D spatial command center.
 *
 * P0 scope: white luminous point cloud of the real whole-home scan (the
 * engineered.lighting aesthetic), constrained isometric navigation (8 azimuth
 * stops, 2 elevations, 3 zoom detents, rubber-band + snap-back, micro-pivot),
 * progressive load-as-materialize, maximize-on-open, sim-mode support.
 * Photo (splat) + mesh modes arrive in P3; markers/cards in P2; the dot in P2/P5.
 *
 * Renderer lives in home-3d/ (real ESM via importmap — three.js + Spark).
 * Bridge contract: this file touches ONLY window.Home3D.ready / .error, and
 * caches the live engine on window.__APT_ENGINE for instant warm reopen.
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
  const [phase, setPhase] = useState("boot");        // boot | loading | ready | error
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [stats, setStats] = useState(null);
  const [mode, setMode] = useState("points");
  const [toast, setToast] = useState(null);
  const [azIdx, setAzIdx] = useState(1);
  const simActive = !!(sim && sim.active);

  const showToast = useCallback((text) => {
    setToast(text);
    setTimeout(() => setToast(null), 2600);
  }, []);

  /* boot the engine (once, cached across open/close) */
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;

    (async () => {
      try {
        // maximize for the takeover (restore on close)
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
            canvas: canvasRef.current,
            hostEl: hostRef.current,
            sim: simActive,
          });
          window.__APT_ENGINE = engine;
          engine.on("progress", (p) => setProgress({ ...p }));
          engine.on("stats", (s) => setStats(s));
          engine.on("loaded", () => setPhase("ready"));
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
        detachRef.current = (() => {
          const detachInput = engine.attachInput(host);
          return () => { ro.disconnect(); detachInput(); };
        })();
        engine.setRunning(true);
        const az = () => setAzIdx(engine.rig.azimuthIndex());
        const azTimer = setInterval(az, 200);
        const prevDetach = detachRef.current;
        detachRef.current = () => { prevDetach(); clearInterval(azTimer); };
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

  /* keyboard: esc close, arrows orbit/elevate, +/- zoom, h home */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      const rig = engineRef.current?.rig;
      if (e.key === "Escape") { onClose?.(); return; }
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
  }, [open, onClose]);

  const pickMode = useCallback(async (m) => {
    const engine = engineRef.current;
    if (!engine || m === mode) return;
    try {
      await engine.modes.setMode(m);
      setMode(m);
    } catch (e) {
      showToast(m === "splat" ? "photo mode lands in p3 — splat asset not wired yet"
                              : "mesh mode lands in p3");
    }
  }, [mode, showToast]);

  if (!open) return null;

  const pct = progress.total ? Math.floor((progress.loaded / progress.total) * 100) : 0;

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

      {/* 3D host */}
      <div ref={hostRef} style={{ position: "absolute", inset: 0, touchAction: "none", cursor: "grab" }}>
        <canvas ref={canvasRef} style={{ display: "block", width: "100%", height: "100%" }} />
      </div>

      {/* top bar */}
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
          <span style={{
            fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
            color: "var(--hg-warn)", border: "1px solid var(--hg-border-soft)",
            padding: "3px 8px", marginLeft: 6,
          }}>sim</span>
        )}
        <span style={{ marginLeft: "auto", pointerEvents: "auto" }}>
          <AptHudButton label="close · esc" onClick={onClose} />
        </span>
      </div>

      {/* loading / error */}
      {(phase === "boot" || phase === "loading") && (
        <div style={{
          position: "absolute", left: 0, right: 0, top: "46%", textAlign: "center",
          fontFamily: APT_FONT_MONO, color: "var(--hg-fg-3)", fontSize: 11,
          letterSpacing: "0.14em", pointerEvents: "none",
        }}>
          {phase === "boot" ? "initializing renderer" : `loading apartment · ${pct}%`}
          <div style={{
            margin: "10px auto 0", width: 220, height: 1,
            background: "var(--hg-border-soft)", position: "relative",
          }}>
            <div style={{
              position: "absolute", left: 0, top: 0, bottom: 0,
              width: `${pct}%`, background: "var(--hg-ice)", transition: "width 200ms",
            }} />
          </div>
        </div>
      )}
      {phase === "error" && (
        <div style={{
          position: "absolute", left: 0, right: 0, top: "44%", textAlign: "center",
          fontFamily: APT_FONT_MONO, fontSize: 11, color: "var(--hg-crit)",
        }}>
          renderer failed — {error}
          <div style={{ color: "var(--hg-fg-4)", marginTop: 8, fontSize: 10 }}>
            check console · vendored three/spark + assets/apartment fixtures
          </div>
        </div>
      )}

      {/* bottom HUD */}
      <div style={{
        position: "absolute", left: 0, right: 0, bottom: 0, display: "flex",
        alignItems: "flex-end", padding: "14px 18px", pointerEvents: "none", gap: 12,
      }}>
        {/* stats (doubles as the P0 verification readout) */}
        <div style={{
          fontFamily: APT_FONT_MONO, fontSize: 9, color: "var(--hg-fg-5)",
          letterSpacing: "0.08em", lineHeight: 1.6, minWidth: 170,
        }}>
          {stats && (<>
            {Math.round(stats.fps)} fps · {(stats.points / 1000).toFixed(0)}k pts · dpr {stats.pixelRatio}
            <br />{String(stats.gpu).slice(0, 48).toLowerCase()}
          </>)}
        </div>

        {/* mode toggle */}
        <div style={{ margin: "0 auto", display: "flex", gap: 6, pointerEvents: "auto" }}>
          <AptHudButton label="cloud" active={mode === "points"} onClick={() => pickMode("points")} />
          <AptHudButton label="photo" active={mode === "splat"} onClick={() => pickMode("splat")}
                        title="photoreal gaussian splat — P3" />
          <AptHudButton label="mesh" active={mode === "mesh"} onClick={() => pickMode("mesh")}
                        title="scan mesh debug view — P3" />
        </div>

        {/* orbit dots + hints */}
        <div style={{ textAlign: "right", pointerEvents: "auto" }}>
          <div style={{ display: "flex", gap: 5, justifyContent: "flex-end", marginBottom: 6 }}>
            {Array.from({ length: 8 }).map((_, i) => (
              <button key={i} className="hg-focusable"
                onClick={() => engineRef.current?.rig.goTo({ az: i, dur: 600 })}
                style={{
                  width: 9, height: 9, borderRadius: 9, padding: 0, cursor: "pointer",
                  border: "1px solid var(--hg-border)",
                  background: i === azIdx ? "var(--hg-ice)" : "transparent",
                }} />
            ))}
          </div>
          <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, color: "var(--hg-fg-5)",
                         letterSpacing: "0.1em" }}>
            drag · wheel zoom · ←→↑↓ · h home
          </span>
        </div>
      </div>

      {toast && (
        <div style={{
          position: "absolute", bottom: 74, left: "50%", transform: "translateX(-50%)",
          fontFamily: APT_FONT_MONO, fontSize: 10, color: "var(--hg-fg-1)",
          background: "rgba(10,12,16,0.85)", border: "1px solid var(--hg-border-soft)",
          padding: "7px 13px", animation: "apt-toast-in 180ms ease-out",
        }}>{toast}</div>
      )}
    </div>
  );
}

window.HomeApartmentView = HomeApartmentView;
