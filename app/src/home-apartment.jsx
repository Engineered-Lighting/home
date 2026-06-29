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

const APT_SERVICE_BY_RUNTIME_KEY = {
  HG_DEFAULT_FRIGATE_BASE: "frigate",
  HG_DEFAULT_TRACKER_BASE: "tracker",
  HG_DEFAULT_APARTMENT_ASSET_BASE: "apartmentAssets",
};

function readAptViewport() {
  if (typeof window === "undefined") return { width: 1024, height: 768, mobile: false, phone: false };
  const vv = window.visualViewport;
  const width = Math.round(vv?.width || window.innerWidth || 1024);
  const height = Math.round(vv?.height || window.innerHeight || 768);
  return { width, height, mobile: width < 700, phone: width < 480 };
}

function useAptViewport() {
  const [viewport, setViewport] = useState(readAptViewport);
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewport(readAptViewport()));
    };
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);
  return viewport;
}

function aptServiceBase(storageKey, runtimeKey, fallback) {
  try {
    const service = APT_SERVICE_BY_RUNTIME_KEY[runtimeKey];
    const resolved = service && window.HomeServices?.get?.(service);
    if (resolved) return String(resolved).replace(/\/+$/, "");
  } catch (e) { /* */ }
  try {
    if (window.HG_WEB_MODE && window[runtimeKey]) {
      return String(window[runtimeKey]).replace(/\/+$/, "");
    }
  } catch (e) { /* */ }
  try {
    if (!window.HomeServices) {
      const saved = localStorage.getItem(storageKey);
      if (saved) return saved.replace(/\/+$/, "");
    }
  } catch (e) { /* */ }
  try {
    if (window[runtimeKey]) return String(window[runtimeKey]).replace(/\/+$/, "");
  } catch (e) { /* */ }
  return fallback;
}

function AptHudButton({ label, onClick, active, disabled, title, mobile = readAptViewport().mobile }) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title} className="hg-focusable"
      style={{
        background: active ? "var(--hg-ice)" : "rgba(10,12,16,0.55)",
        border: "1px solid " + (active ? "var(--hg-ice)" : "var(--hg-border-soft)"),
        color: active ? "#0b0d11" : (disabled ? "var(--hg-fg-5)" : "var(--hg-fg-1)"),
        minHeight: mobile ? 40 : "auto",
        minWidth: mobile ? 44 : "auto",
        padding: mobile ? "8px 11px" : "5px 12px", fontFamily: APT_FONT_MONO, fontSize: mobile ? 10 : 10.5,
        letterSpacing: "0.1em", textTransform: "lowercase",
        cursor: disabled ? "default" : "pointer", backdropFilter: "blur(6px)",
      }}
    >{label}</button>
  );
}

/* Live MJPEG feed, undistorted on the fly. When the snapped camera carries
   solved intrinsics with real distortion (dist[0] != 0) the raw <img> stays in
   the DOM hidden (opacity 0, NOT display:none — Chrome stops decoding
   multipart frames for undisplayed images) as the stream source, and a rAF
   loop warps its current frame through a WebGL fragment shader: each output
   (undistorted) pixel is normalized via K, pushed through the Brown forward
   model — radial k1..k3, rational k4..k6 when the solver picked that model,
   tangential p1,p2 — and samples the distorted source there. A plain 2D
   canvas can't do this per-pixel warp at 720p/30; raw WebGL is fine here
   (no THREE — that stays inside home-3d). Falls back to the plain <img>
   when there are no intrinsics, no WebGL, shader trouble, or the stream is
   CORS-blocked (crossOrigin load error). */
function AptUndistortedFeed({ src, alt, intrinsics, style }) {
  const canvasRef = useRef(null);
  const imgRef = useRef(null);
  const [fallback, setFallback] = useState(false);

  const K = intrinsics && intrinsics.K;
  const dist = (intrinsics && intrinsics.dist) || [];
  const wantWarp = !fallback && !!(K && dist.length >= 1 && dist[0] !== 0);

  useEffect(() => {
    if (!wantWarp) return undefined;
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return undefined;

    let gl = null;
    try { gl = canvas.getContext("webgl", { antialias: false, depth: false, stencil: false }); }
    catch (e) { /* no webgl */ }
    if (!gl) { setFallback(true); return undefined; }

    const VS = `
      attribute vec2 aPos;
      varying vec2 vUv;
      void main() {
        // clip y=+1 (canvas top) -> vUv.y=0: DOM <img> uploads rows top-first
        // (UNPACK_FLIP_Y left false), so image pixel convention (y down)
        // matches texture v directly — no flips anywhere else
        vUv = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5);
        gl_Position = vec4(aPos, 0.0, 1.0);
      }`;
    const FS = `
      precision highp float;
      varying vec2 vUv;
      uniform sampler2D uTex;
      uniform vec2 uSize;   // texture size, px
      uniform vec4 uK;      // fx, fy, cx, cy scaled to texture px
      uniform vec3 uKr;     // k1, k2, k3
      uniform vec3 uKd;     // k4, k5, k6 (rational; zeros for 5-param)
      uniform vec2 uP;      // p1, p2
      void main() {
        vec2 pix = vUv * uSize;
        float xu = (pix.x - uK.z) / uK.x;
        float yu = (pix.y - uK.w) / uK.y;
        float r2 = xu * xu + yu * yu;
        float r4 = r2 * r2;
        float r6 = r4 * r2;
        float rad = (1.0 + uKr.x * r2 + uKr.y * r4 + uKr.z * r6)
                  / (1.0 + uKd.x * r2 + uKd.y * r4 + uKd.z * r6);
        float xd = xu * rad + 2.0 * uP.x * xu * yu + uP.y * (r2 + 2.0 * xu * xu);
        float yd = yu * rad + uP.x * (r2 + 2.0 * yu * yu) + 2.0 * uP.y * xu * yu;
        vec2 suv = vec2(uK.x * xd + uK.z, uK.y * yd + uK.w) / uSize;
        if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) {
          gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        } else {
          gl_FragColor = texture2D(uTex, suv);
        }
      }`;

    const mkShader = (type, text) => {
      const s = gl.createShader(type);
      if (!s) return null;   // lost context: createShader returns null and
                             // shaderSource(null) would THROW (no boundary
                             // above us — it would blank the whole app)
      gl.shaderSource(s, text);
      gl.compileShader(s);
      return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
    };
    const vs = mkShader(gl.VERTEX_SHADER, VS);
    const fs = vs && mkShader(gl.FRAGMENT_SHADER, FS);
    const prog = fs ? gl.createProgram() : null;
    if (prog) {
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
    }
    if (!prog || !gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      setFallback(true);
      // this effect returns no cleanup on this path — release the context
      // here or the failed attempt still burns a context slot
      try { gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch (e) { /* */ }
      return undefined;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
                  gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    // K arrives as nested 3x3 rows from the solver (calib.py K.tolist());
    // tolerate a flat-9 copy too. dist is [k1,k2,p1,p2,k3,(k4,k5,k6)].
    const kf = Array.isArray(K[0])
      ? [K[0][0], K[1][1], K[0][2], K[1][2]]
      : [K[0], K[4], K[2], K[5]];
    const d = (i) => +dist[i] || 0;
    gl.uniform1i(gl.getUniformLocation(prog, "uTex"), 0);
    gl.uniform3f(gl.getUniformLocation(prog, "uKr"), d(0), d(1), d(4));
    gl.uniform3f(gl.getUniformLocation(prog, "uKd"), d(5), d(6), d(7));
    gl.uniform2f(gl.getUniformLocation(prog, "uP"), d(2), d(3));
    const uKLoc = gl.getUniformLocation(prog, "uK");
    const uSizeLoc = gl.getUniformLocation(prog, "uSize");
    const calW = (intrinsics.image_size && +intrinsics.image_size[0]) || 0;
    const calH = (intrinsics.image_size && +intrinsics.image_size[1]) || 0;

    let raf = 0;
    let lastW = 0, lastH = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const w = img.naturalWidth, h = img.naturalHeight;
      if (!(w > 0) || !(h > 0)) return;
      if (w !== lastW || h !== lastH) {
        lastW = w; lastH = h;
        canvas.width = w; canvas.height = h;
        gl.viewport(0, 0, w, h);
        // K was solved at image_size; the MJPEG can stream at another res
        const sx = calW ? w / calW : 1, sy = calH ? h / calH : 1;
        gl.uniform4f(uKLoc, kf[0] * sx, kf[1] * sy, kf[2] * sx, kf[3] * sy);
        gl.uniform2f(uSizeLoc, w, h);
      }
      try {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      } catch (e) {
        // CORS-tainted stream — give the user the raw feed instead
        cancelAnimationFrame(raf);
        setFallback(true);
        return;
      }
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      try {
        gl.deleteTexture(tex); gl.deleteBuffer(buf); gl.deleteProgram(prog);
        gl.deleteShader(vs); gl.deleteShader(fs);
        // WebView2/Chromium caps live WebGL contexts (~16) and silently kills
        // the OLDEST on overflow — which is the 3D engine's. Without this
        // explicit release every feed open/close leaks one context slot until
        // the engine canvas goes permanently black mid-session.
        const lose = gl.getExtension("WEBGL_lose_context");
        if (lose) lose.loseContext();
      } catch (e) { /* context already gone */ }
      if (img) img.src = "";   // close the hidden MJPEG connection immediately
    };
  }, [wantWarp, src, intrinsics]);

  if (!wantWarp) {
    return <img src={src} alt={alt} style={style} />;
  }
  return (
    <div style={{ position: "relative" }}>
      <img
        ref={imgRef} src={src} alt="" aria-hidden="true" crossOrigin="anonymous"
        onError={() => setFallback(true)}
        style={{ position: "absolute", left: 0, top: 0, width: 2, height: 2,
                 opacity: 0, pointerEvents: "none" }}
      />
      <canvas ref={canvasRef} style={style} />
    </div>
  );
}

function HomeApartmentView({ open, onClose, endpoint, token, sim, embedded = false }) {
  const viewport = useAptViewport();
  const mobile = viewport.mobile;
  const hostRef = useRef(null);
  const engineRef = useRef(null);
  const detachRef = useRef(null);
  const wasMaximizedRef = useRef(null);
  const flyTimerRef = useRef(null);     // pending feed-reveal timeout
  const preSnapModeRef = useRef(null);  // render mode to restore after a camera snap
  const [phase, setPhase] = useState("boot");
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [stats, setStats] = useState(null);
  const [mode, setMode] = useState("points");
  const [modeLoading, setModeLoading] = useState(null);
  const [modeError, setModeError] = useState(null);
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
  const [liveCam, setLiveCam] = useState(null);   // camera device while snapped
  const [liveOn, setLiveOn] = useState(false);    // live feed vs 3D from the pose
  const [calibCam, setCalibCam] = useState(null); // correspondence-capture overlay
  const calibPickRef = useRef(false);
  const calibApiRef = useRef(null);
  const calibPointsRef = useRef(null);
  const [anchors, setAnchors] = useState({});        // id -> {x, y, visible}
  const [hostSize, setHostSize] = useState({ width: 0, height: 0 });
  const statesRef = useRef({});                      // entity_id -> ha state
  const simActive = !!(sim && sim.active);
  const [serviceEpoch, setServiceEpoch] = useState(0);

  useEffect(() => {
    const bump = () => setServiceEpoch((n) => n + 1);
    window.addEventListener("home-services-change", bump);
    return () => window.removeEventListener("home-services-change", bump);
  }, []);

  const showToast = useCallback((text) => {
    setToast(text);
    setTimeout(() => setToast(null), 2800);
  }, []);

  useEffect(() => {
    if (!embedded) return undefined;
    const api = {
      exitEdit: () => setEditing(false),
      closeCards: () => {
        setCardId(null);
        setHoverId(null);
      },
    };
    window.__HOME_APARTMENT_EMBEDDED_API = api;
    return () => {
      if (window.__HOME_APARTMENT_EMBEDDED_API === api) {
        delete window.__HOME_APARTMENT_EMBEDDED_API;
      }
    };
  }, [embedded]);

  /* ---------------- engine boot ---------------- */
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;

    (async () => {
      try {
        try {
          const w = await window.getTauriWindow?.();
          if (w && !embedded && !cancelled) {
            wasMaximizedRef.current = await w.isMaximized?.();
            if (!wasMaximizedRef.current && !cancelled) await w.maximize?.();
          }
        } catch (e) { /* browser mode */ }
        if (cancelled) return;

        // The engine canvas is created ONCE and owned imperatively, not by
        // React: the resident renderer is permanently bound to it, so every
        // view OPEN must re-attach THIS node — a React-owned <canvas> would
        // be torn down on close while the renderer stays bound to it (the
        // black-screen-on-reopen bug).
        let canvas = window.__APT_CANVAS;
        if (!canvas) {
          canvas = document.createElement("canvas");
          canvas.style.cssText = "display:block;width:100%;height:100%;";
          window.__APT_CANVAS = canvas;
        }
        if (hostRef.current && canvas.parentElement !== hostRef.current) {
          hostRef.current.appendChild(canvas);
        }

        // engine boot is deduped via a cached PROMISE — a fast close/reopen
        // while assets stream must not run createEngine twice against the
        // same resident canvas (two rAF loops fighting one GL context)
        let enginePromise = window.__APT_ENGINE_PROMISE;
        if (!enginePromise) {
          setPhase("boot");
          enginePromise = (async () => {
            const mod = await window.Home3D.ready;
            if (!mod) throw (window.Home3D.error || new Error("3d engine failed to load"));
            return mod.createEngine({ canvas, hostEl: hostRef.current, sim: simActive });
          })();
          window.__APT_ENGINE_PROMISE = enginePromise;
          enginePromise.catch(() => {
            if (window.__APT_ENGINE_PROMISE === enginePromise) window.__APT_ENGINE_PROMISE = null;
          });
        }
        const engine = await enginePromise;
        window.__APT_ENGINE = engine;
        if (cancelled) return;
        engineRef.current = engine;
        setPhase("loading");
        engine.pointsPromise.then(
          () => !cancelled && setPhase("ready"),
          (e) => { if (!cancelled) { setError(String(e?.message || e)); setPhase("error"); } },
        );
        engine.pointsPromise.then((p) => { calibPointsRef.current = p; }).catch(() => {});
        // keep the bottom toggle honest: calibrate/snap switch modes through
        // the engine directly, so subscribe rather than trusting local state —
        // and re-sync NOW: the close path restores the pre-snap mode AFTER
        // these listeners were unsubscribed, so local `mode` can be stale
        const unsubs = [
          engine.on("progress", (p) => setProgress({ ...p })),
          engine.on("stats", (s) => setStats(s)),
          engine.on("contextlost", () => showToast("graphics context lost — recovering")),
          engine.modes.onChange((next) => setMode(next)),
        ];
        setMode(engine.modes.mode);
        engine.picking.setHost?.(hostRef.current);

        // everything below is synchronous — detachRef is assigned before any
        // call that could throw, so a failure can't strand live listeners
        const host = hostRef.current;
        const size = () => {
          const width = host.clientWidth || window.innerWidth || 1;
          const height = host.clientHeight || window.innerHeight || 1;
          engine.setSize(width, height);
          setHostSize((prev) => (
            prev.width === width && prev.height === height ? prev : { width, height }
          ));
        };
        const ro = new ResizeObserver(size);
        const detachInput = engine.attachInput(host);
        const azTimer = setInterval(() => {
          setAzIdx(engine.rig.azimuthIndex());
          setInCamPose(!!engine.rig.inCameraPose?.());
        }, 200);
        detachRef.current = () => {
          ro.disconnect(); detachInput(); clearInterval(azTimer);
          unsubs.forEach((u) => { try { u?.(); } catch (e) { /* */ } });
        };
        size();
        ro.observe(host);
        engine.setRunning(true);
      } catch (e) {
        if (!cancelled) { setError(String(e?.message || e)); setPhase("error"); }
      }
    })();

    return () => {
      cancelled = true;
      if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
      detachRef.current?.();
      detachRef.current = null;
      // this component instance PERSISTS across open/close (home-app renders
      // it permanently; open=false just renders null) — clear per-session UI
      // state here or a live feed / calibration overlay / control card
      // resurrects the next time the view opens
      setLiveCam(null); setLiveOn(false); setCalibCam(null); setCardId(null);
      setEditing(false);
      const eng = engineRef.current;
      if (eng) {
        // never leave the resident engine in a held/locked pose or with the
        // cloud hidden — a reopen would look dead with no recovery affordance
        eng.rig.resetPose?.();
        if (preSnapModeRef.current != null) {
          const back = preSnapModeRef.current;
          preSnapModeRef.current = null;
          if (eng.modes.mode !== back) eng.modes.setMode(back, { duration: 0 }).catch(() => {});
        }
        eng.setRunning(false);
      }
      if (!embedded) {
        (async () => {
          try {
            const w = await window.getTauriWindow?.();
            if (w && wasMaximizedRef.current === false) await w.unmaximize?.();
          } catch (e) { /* */ }
        })();
      }
    };
  }, [open, simActive, showToast, embedded]);

  /* ---------------- model + registry load ---------------- */
  useEffect(() => {
    if (!open) return undefined;
    let dead = false;
    (async () => {
      // retry until a true remote load lands — the first attempt can race
      // tauriFetch readiness and fall back to a cached/seed copy, which the
      // user reads as "my saves were overwritten"
      for (let attempt = 0; attempt < 10 && !dead; attempt++) {
        const m = await window.HomeApartmentData.getModel({ endpoint, token, sim: simActive });
        if (dead) return;
        setModel(m);
        const fromRemote = typeof m.revision === "number" && !m.remote_cached
          && !m.offline_draft && !m.seeded;
        if (fromRemote || simActive || !token) break;
        await new Promise((r) => setTimeout(r, 2500));
      }
      const client = window.__hav_haClient;
      if (client && !simActive) {
        const reg = await window.HomeApartmentData.getRegistry(client);
        if (!dead) setRegistry(reg);
      }
    })();
    return () => { dead = true; };
  }, [open, endpoint, token, simActive]);

  /* correspondence pick: NATIVE capture-phase listener — the rig's input
     handlers stop propagation, so React synthetic events never fire on the
     host (this is why Step 2 clicks "did nothing") */
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const onPick = (ev) => {
      if (!calibPickRef.current || !calibApiRef.current) return;
      const e = engineRef.current;
      if (!e) return;
      // MESH-ONLY raycast: true surface intersection. Point-cloud picking
      // accepted any stray point within 4cm of the ray anywhere along it —
      // corners "snapped" to wall-top fragments behind them (user-diagnosed:
      // pairs floated once the camera rotated).
      const targets = [e.modes.getMesh && e.modes.getMesh()].filter(Boolean);
      const hits = (e.picking.pick(targets, ev.clientX, ev.clientY) || [])
        .filter((h) => h.point);
      let local = null;
      if (hits.length) {
        local = e.apartmentRoot.worldToLocal(hits[0].point.clone());
      } else {
        const f = e.picking.floorPoint(e.apartmentRoot, ev.clientX, ev.clientY);
        if (f) local = { x: f[0], y: f[1], z: 0 };
      }
      if (local) {
        calibApiRef.current.acceptScenePoint([
          +local.x.toFixed(3), +local.y.toFixed(3), +local.z.toFixed(3)]);
        ev.stopPropagation();
      }
    };
    // drag-to-adjust: grab a green pair marker, slide it along the mesh
    let dragIdx = -1;
    const meshTargets = () => {
      const e = engineRef.current;
      return [e && e.modes.getMesh && e.modes.getMesh()].filter(Boolean);
    };
    const onDown = (ev) => {
      const e = engineRef.current;
      if (!e || !calibApiRef.current || !e.overlay.calibGroup) return;
      const hits = e.picking.pick([e.overlay.calibGroup], ev.clientX, ev.clientY) || [];
      const hit = hits.find((h) => h.object && h.object.geometry &&
                                   h.object.geometry.type === "SphereGeometry");
      if (!hit) return;
      dragIdx = Math.floor(e.overlay.calibGroup.children.indexOf(hit.object) / 2);
      ev.stopPropagation(); ev.preventDefault();
    };
    const onMove = (ev) => {
      if (dragIdx < 0) return;
      const e = engineRef.current;
      const hits = (e.picking.pick(meshTargets(), ev.clientX, ev.clientY) || [])
        .filter((h) => h.point);
      if (hits.length) {
        const l = e.apartmentRoot.worldToLocal(hits[0].point.clone());
        e.overlay.moveCalibMarker(dragIdx, [l.x, l.y, l.z]);
      }
      ev.stopPropagation();
    };
    const onUp = (ev) => {
      if (dragIdx < 0) return;
      const e = engineRef.current;
      const s = e.overlay.calibGroup.children[dragIdx * 2];
      if (s && calibApiRef.current && calibApiRef.current.updatePair) {
        calibApiRef.current.updatePair(dragIdx,
          [+s.position.x.toFixed(3), +s.position.y.toFixed(3), +s.position.z.toFixed(3)]);
      }
      dragIdx = -1;
      ev.stopPropagation();
    };
    host.addEventListener("click", onPick, true);
    host.addEventListener("pointerdown", onDown, true);
    host.addEventListener("pointermove", onMove, true);
    host.addEventListener("pointerup", onUp, true);
    return () => {
      host.removeEventListener("click", onPick, true);
      host.removeEventListener("pointerdown", onDown, true);
      host.removeEventListener("pointermove", onMove, true);
      host.removeEventListener("pointerup", onUp, true);
    };
  }, [open, phase]);

  /* 3D confirmation markers for banked pairs — rendered by the engine's
     overlay (the Babel layer has no working dynamic import of three) */
  const syncCalibMarkers = (pairs) => {
    const e = engineRef.current;
    if (e && e.overlay.setCalibMarkers) {
      e.overlay.setCalibMarkers((pairs || []).map((p) => p.xyz));
    }
  };

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
  }, [open, phase, simActive, serviceEpoch]);

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

  /* leave the camera snap: shared by esc, the '← back' button, and the
     resurrect-guard — closes the feed, cancels the pending feed timer,
     restores the pre-snap render mode (the calibrated snap forced mesh;
     without this the cloud stays hidden at uOpacity 0 — black screen),
     then flies home */
  const exitCameraPose = useCallback(() => {
    if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
    setLiveCam(null); setLiveOn(false); setCalibCam(null);
    const e = engineRef.current;
    if (!e) return;
    const back = preSnapModeRef.current;
    preSnapModeRef.current = null;
    if (back && e.modes.mode !== back) e.modes.setMode(back, { duration: 0 }).catch(() => {});
    e.rig.returnToOverview();
  }, []);

  /* keyboard */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
      const rig = engineRef.current?.rig;
      if (e.key === "Escape") {
        if (cardId) { setCardId(null); return; }
        if (engineRef.current?.rig.inCameraPose?.()) {
          exitCameraPose();
          return;
        }
        if (editing) { setEditing(false); return; }
        if (embedded) return;
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
  }, [open, onClose, editing, cardId, exitCameraPose, embedded]);

  const pickMode = useCallback(async (m) => {
    const engine = engineRef.current;
    if (!engine) return;
    // explicit pick overrides any pending post-snap restore
    preSnapModeRef.current = null;
    // ALWAYS delegate to the engine — modes.setMode dedups same-mode (cheaply
    // re-asserting visibility) and reconciles raced transitions by generation.
    // The old `if (m === engine.modes.mode) return` short-circuit dropped a
    // 'cloud' click made while a slow mesh load was still in flight (the
    // engine's mode had not flipped yet), leaving the mesh on screen.
    setModeLoading(m);
    setModeError(null);
    try {
      await engine.modes.setMode(m);
      setMode(m);
    } catch (e) {
      const assetHint = m === "splat" ? "check apartment.spz" : "check mesh.glb / collision.glb";
      setModeError({
        mode: m,
        message: m === "splat" ? "photo unavailable" : "mesh unavailable",
        detail: `${e?.message || String(e || "asset load failed")} - ${assetHint}`,
      });
      showToast(m === "splat" ? "photo unavailable — check apartment.spz asset"
                              : "mesh unavailable — check collision.glb asset");
    } finally {
      setModeLoading(null);
    }
  }, [showToast]);

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

  const flyToDeviceView = useCallback((dev) => {
    if (!dev) return false;
    const eng = engineRef.current;
    const calib = !!(dev.camera && dev.camera.extrinsics && dev.camera.intrinsics);
    // Calibrated desktop snaps use the textured mesh behind the live feed.
    // Mobile keeps the full canvas width so the video is not cropped by the
    // 46/54 calibration split.
    if (calib && eng) {
      if (preSnapModeRef.current == null) preSnapModeRef.current = eng.modes.mode;
      eng.modes.setMode("mesh", { duration: 0 }).catch(() => {});
    }
    eng?.flyToDevice(dev, { fovScale: calib && !mobile ? 1 / 0.74 : 1 });
    if (flyTimerRef.current) clearTimeout(flyTimerRef.current);
    if (!simActive && dev.camera?.frigate_name) {
      flyTimerRef.current = setTimeout(() => {
        flyTimerRef.current = null;
        if (engineRef.current?.rig.inCameraPose?.()) {
          setLiveCam(dev); setLiveOn(true);
        }
      }, 950);
    } else {
      setLiveCam(dev); setLiveOn(false);
    }
    return true;
  }, [mobile, simActive]);

  useEffect(() => {
    if (!open || typeof window === "undefined") return undefined;
    const api = {
      snapshot: () => ({
        phase,
        mode,
        liveOn,
        liveCam: liveCam?.id || liveCam?.name || null,
        mobile,
        viewport,
        devices: (model.devices || []).length,
      }),
      setMode: pickMode,
      flyFirstCamera: () => {
        const dev = (model.devices || []).find((d) => d.type === "camera" || d.camera?.frigate_name);
        return dev ? flyToDeviceView(dev) : false;
      },
    };
    window.__havApartmentDebug = api;
    return () => {
      if (window.__havApartmentDebug === api) delete window.__havApartmentDebug;
    };
  }, [open, phase, mode, liveOn, liveCam, mobile, viewport, model.devices, pickMode, flyToDeviceView]);

  if (!open) return null;

  const pct = progress.total ? Math.floor((progress.loaded / progress.total) * 100) : 0;
  const cardDevice = (model.devices || []).find((d) => d.id === cardId) || null;
  const hoverDevice = (model.devices || []).find((d) => d.id === hoverId) || null;
  const topPad = mobile ? "calc(10px + env(safe-area-inset-top, 0px)) 12px 8px" : "12px 18px";
  const bottomPad = mobile ? "10px 10px calc(12px + env(safe-area-inset-bottom, 0px))" : "14px 18px";
  const liveFeedStyle = mobile
    ? {
        width: "calc(100vw - 20px)",
        maxWidth: "100vw",
        maxHeight: "calc(100dvh - 150px - env(safe-area-inset-top, 0px) - env(safe-area-inset-bottom, 0px))",
        aspectRatio: "16 / 9",
        objectFit: "contain",
        background: "#000",
        boxShadow: "0 0 42px 6px rgba(0,0,0,0.35)",
      }
    : {
        height: "74vh",
        aspectRatio: "16 / 9",
        objectFit: "cover",
        boxShadow: "0 0 60px 10px rgba(0,0,0,0.35)",
      };

  return (
    <div
      role={embedded ? "region" : "dialog"}
      aria-modal={embedded ? undefined : true}
      aria-label="3d apartment"
      style={{
        position: embedded ? "absolute" : "fixed",
        inset: 0,
        zIndex: embedded ? 0 : 1000,
        background: "#000",
        display: "flex", flexDirection: "column", overflow: "hidden",
        minHeight: mobile ? "100dvh" : 0,
        animation: "apt-fade-in 260ms ease-out",
      }}
    >
      <style>{`
        @keyframes apt-fade-in { from { opacity: 0; } to { opacity: 1; } }
        @keyframes apt-toast-in { from { transform: translateY(8px); opacity: 0; }
                                  to { transform: translateY(0); opacity: 1; } }
      `}</style>

      {/* engine canvas is appended imperatively (persistent across mounts —
          see the boot effect); React must never own or recreate it */}
      <div ref={hostRef} style={{ position: "absolute", top: 0, bottom: 0, right: 0,
                                  left: calibCam && !mobile ? "46%" : 0,
                                  touchAction: "none", cursor: "grab" }}
      />

      {/* top bar */}
      {!editing && (
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, display: "flex",
          alignItems: mobile ? "flex-start" : "center",
          gap: mobile ? 7 : 10,
          padding: topPad,
          pointerEvents: "none",
          flexWrap: mobile ? "wrap" : "nowrap",
          zIndex: 5,
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
                         marginLeft: mobile ? 0 : 8 }}>
            tracker · {trackerStatus}
          </span>
          <span style={{
            marginLeft: "auto",
            display: "flex",
            gap: 6,
            pointerEvents: "auto",
            flexWrap: mobile ? "wrap" : "nowrap",
            justifyContent: "flex-end",
            maxWidth: mobile ? "100%" : "none",
          }}>
            {inCamPose && (
              <AptHudButton label="← back" onClick={exitCameraPose} />
            )}
            {inCamPose && liveCam && !simActive && window.HomeApartmentCalibrate && (
              <AptHudButton label="calibrate" active={!!calibCam} onClick={() => {
                setLiveOn(false);
                const e = engineRef.current;
                if (e && preSnapModeRef.current == null) preSnapModeRef.current = e.modes.mode;
                e?.modes.setMode("mesh", { duration: 0 }).catch(() => {});
                // snapped pose sits point-blank in the geometry — pull back to
                // the overview so the room is visible and orbit/zoom unlock
                e?.rig.returnToOverview();
                setCalibCam(liveCam);
              }} mobile={mobile} />
            )}
            {!inCamPose && (
              <AptHudButton label="edit" onClick={() => { setCardId(null); setEditing(true); }} mobile={mobile} />
            )}
            {!embedded && <AptHudButton label="close · esc" onClick={onClose} />}
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
          alignItems: mobile ? "stretch" : "flex-end",
          padding: bottomPad,
          pointerEvents: "none",
          gap: mobile ? 8 : 12,
          flexDirection: mobile ? "column" : "row",
          zIndex: 5,
        }}>
          <div style={{ fontFamily: APT_FONT_MONO, fontSize: 9, color: "var(--hg-fg-5)",
                        letterSpacing: "0.08em", lineHeight: 1.6, minWidth: mobile ? 0 : 170,
                        display: mobile ? "none" : "block" }}>
            {stats && (<>
              {Math.round(stats.fps)} fps · {(stats.points / 1000).toFixed(0)}k pts · dpr {stats.pixelRatio}
              {stats.calls != null ? ` · ${stats.calls} dc` : ""}
              {stats.tris ? ` · ${(stats.tris / 1e6).toFixed(1)}m tri` : ""}
              <br />{String(stats.gpu).slice(0, 48).toLowerCase()}
              {mode === "splat" && stats.modeDebug && (
                <><br />splat {stats.modeDebug.numSplats ? (stats.modeDebug.numSplats / 1000).toFixed(0) + "k" : "—"}
                · vis {String(stats.modeDebug.splatVisible)} · sr {stats.modeDebug.srParent}</>
              )}
            </>)}
          </div>
          <div style={{
            margin: mobile ? "0" : "0 auto",
            display: "flex",
            gap: 6,
            pointerEvents: "auto",
            justifyContent: mobile ? "center" : "flex-start",
            flexWrap: "wrap",
            width: mobile ? "100%" : "auto",
          }}>
            {liveCam && (
              <AptHudButton label="live" active={liveOn} onClick={() => setLiveOn(true)} />
            )}
            <AptHudButton label="cloud" active={!liveOn && mode === "points"}
              onClick={() => { setLiveOn(false); pickMode("points"); }} disabled={modeLoading === "points"} />
            <AptHudButton label="photo" active={!liveOn && mode === "splat"}
              onClick={() => { setLiveOn(false); pickMode("splat"); }} disabled={modeLoading === "splat"} title={modeError?.mode === "splat" ? modeError.detail : ""} />
            <AptHudButton label="mesh" active={!liveOn && mode === "mesh"}
              onClick={() => { setLiveOn(false); pickMode("mesh"); }} disabled={modeLoading === "mesh"} title={modeError?.mode === "mesh" ? modeError.detail : ""} />
          </div>
          {(modeLoading || modeError) && (
            <div style={{
              pointerEvents: "none",
              alignSelf: mobile ? "center" : "flex-end",
              maxWidth: mobile ? "calc(100vw - 24px)" : 260,
              fontFamily: APT_FONT_MONO,
              fontSize: mobile ? 9 : 9.5,
              letterSpacing: "0.08em",
              color: modeError ? "var(--hg-warn)" : "var(--hg-fg-3)",
              background: "rgba(10,12,16,0.72)",
              border: "1px solid var(--hg-border-soft)",
              padding: "6px 9px",
              textAlign: mobile ? "center" : "left",
            }}>
              {modeLoading ? `loading ${modeLoading === "splat" ? "photo" : modeLoading}` : modeError.message}
            </div>
          )}
          <div style={{ textAlign: "right", pointerEvents: "auto", display: mobile ? "none" : "block" }}>
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

      {/* live camera feed — full-viewport MJPEG from Frigate, shown over the
          3D canvas while snapped; the HUD stays on top so cloud/photo/mesh
          render the SAME pose one toggle away */}
      {liveCam && liveOn && !calibCam && (
        <div style={{ position: "absolute", inset: 0,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      padding: mobile ? "calc(58px + env(safe-area-inset-top, 0px)) 10px calc(82px + env(safe-area-inset-bottom, 0px))" : 0,
                      pointerEvents: "none" }}>
          {/* big-but-not-fullscreen: the feed hovers over the 3D view, which
              holds the same pose behind it — true 1:1 alignment lands with
              per-camera calibration (fov + principal point). Calibrated
              cameras render through the WebGL undistorter; uncalibrated ones
              keep the raw <img> (AptUndistortedFeed falls back internally) */}
          <AptUndistortedFeed
            key={`${liveCam.id}-${serviceEpoch}`}
            // key forces a REMOUNT on camera switch: the cleanup deliberately
            // loses the WebGL context (context-budget hygiene), so a reused
            // canvas would come back with a dead context and no warp
            src={`${aptServiceBase("apartment3d.frigateBase", "HG_DEFAULT_FRIGATE_BASE", "http://192.168.0.125:5000")}/api/${liveCam.camera.frigate_name}`}
            alt={liveCam.name}
            intrinsics={liveCam.camera?.intrinsics || null}
            style={liveFeedStyle}
          />
          <div style={{ position: "absolute", top: mobile ? "calc(54px + env(safe-area-inset-top, 0px))" : 52, left: mobile ? 12 : 18, fontFamily: APT_FONT_MONO,
                        fontSize: 9.5, letterSpacing: "0.12em", color: "var(--hg-ice)",
                        background: "rgba(10,12,16,0.6)", padding: "4px 9px" }}>
            live · {liveCam.name} · mjpeg
          </div>
        </div>
      )}

      {calibCam && window.HomeApartmentCalibrate && React.createElement(
        window.HomeApartmentCalibrate.Component, {
          key: `${calibCam.id}-${serviceEpoch}`,
          cam: calibCam.camera.frigate_name,
          savedLens: calibCam.camera?.intrinsics || null,
          trackerBase: aptServiceBase("apartment3d.trackerBase", "HG_DEFAULT_TRACKER_BASE", "http://192.168.0.100:8098"),
          onPickRequest: (active) => { calibPickRef.current = active; },
          onIntrinsics: (lens) => {
            // persist solved intrinsics into the camera device (app = single writer)
            setModel((m) => {
              const next = { ...m, devices: (m.devices || []).map((d) =>
                d.id === calibCam.id
                  ? { ...d, camera: { ...(d.camera || {}), intrinsics: {
                      K: lens.K, dist: lens.dist, image_size: lens.image_size,
                      rms_px: lens.rms_px } } }
                  : d) };
              window.HomeApartmentData.saveModel(next, { endpoint, token, sim: simActive });
              return next;
            });
          },
          onPairsChanged: (pairs) => { syncCalibMarkers(pairs); },
          onDone: (result) => {
            // persist the solved extrinsics into the camera device
            if (result && (result.q || result.R || result.C)) {
              setModel((m) => {
                const next = { ...m, devices: (m.devices || []).map((d) =>
                  d.id === calibCam.id
                    ? { ...d, source: "calibrated",
                        camera: { ...(d.camera || {}), extrinsics: result } }
                    : d) };
                window.HomeApartmentData.saveModel(next, { endpoint, token, sim: simActive });
                return next;
              });
            }
            calibPickRef.current = false; setCalibCam(null);
            syncCalibMarkers([]);
          },
          registerApi: (api) => { calibApiRef.current = api; },
        })}

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
              screen={anchors[cardId] || { x: (hostSize.width || window.innerWidth) / 2, y: 100 }}
              bounds={hostSize}
              onClose={() => setCardId(null)}
              onService={callSvc}
              onFlyTo={flyToDeviceView}
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
