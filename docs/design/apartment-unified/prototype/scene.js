/* Apartment unified prototype — room renderer (three r149 UMD).
 * Owns the canvas, orbit camera, point cloud / mesh surfaces, light pools,
 * fixtures, aim reticle, body layer and anchored DOM labels. All product UI
 * lives in React siblings; this module only emits events. Nothing here sends
 * a command anywhere. */
(function () {
  "use strict";
  const T = window.THREE;
  const REDUCED = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const V = (a) => new T.Vector3(a[0], a[1], a[2]);

  function kelvin(k) {
    const t = clamp(k || 2700, 1000, 12000) / 100; let r, g, b;
    if (t <= 66) { r = 255; g = 99.47 * Math.log(t) - 161.12; b = t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04; }
    else { r = 329.7 * Math.pow(t - 60, -0.1332); g = 288.12 * Math.pow(t - 60, -0.0755); b = 255; }
    return [clamp(r, 0, 255) / 255, clamp(g, 0, 255) / 255, clamp(b, 0, 255) / 255];
  }
  function rng(seed) { let s = seed >>> 0 || 1; return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 1000003) / 1000003; }; }

  const VS = `
uniform float uPR; uniform float uSize; uniform float uOpacity; uniform float uCut; uniform vec3 uCam;
uniform vec4 uPool[16]; uniform vec3 uPoolN[16]; uniform vec3 uPoolC[16]; uniform int uPoolCount;
attribute float intensity; attribute vec3 wn;
varying float vA; varying vec3 vCol;
void main(){
  vec3 p = position;
  float rnd = fract(sin(dot(p.xy + p.z, vec2(12.9898,78.233))) * 43758.5453);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float depth = -mv.z;
  gl_PointSize = clamp(uSize * uPR * (13.0 / max(depth, 0.5)), 0.8, 4.6);
  gl_Position = projectionMatrix * mv;
  float a = 1.0;
  if (uCut > 0.5 && dot(wn, wn) > 0.5) { float f = dot(wn, normalize(uCam - p)); a *= 1.0 - 0.96 * smoothstep(-0.02, 0.16, f); }
  float b = (0.30 + 0.70 * intensity) * (0.85 + 0.15 * rnd);
  vec3 col = vec3(0.80, 0.83, 0.90) * b * 0.82;
  vec3 lit = vec3(0.0);
  for (int i = 0; i < 16; i++) {
    if (i >= uPoolCount) break;
    vec3 d = p - uPool[i].xyz; float r = uPool[i].w; vec3 n = uPoolN[i]; float f;
    if (dot(n, n) < 0.5) { f = exp(-dot(d, d) / (r * r) * 1.6) * 0.55; }
    else { float off = dot(d, n); vec3 ip = d - off * n; f = exp(-dot(ip, ip) / (r * r) * 2.0) * (1.0 - smoothstep(0.03, 0.2, abs(off))); }
    lit += uPoolC[i] * f;
  }
  vCol = col + lit * (0.85 + 0.95 * intensity);
  vA = a * uOpacity;
}`;
  const FS = `
varying float vA; varying vec3 vCol;
void main(){ vec2 c = gl_PointCoord - 0.5; float d = length(c); if (d > 0.5) discard; gl_FragColor = vec4(vCol, (1.0 - smoothstep(0.16, 0.5, d)) * vA); }`;

  function gradientTexture(kind) {
    const s = 128, c = document.createElement("canvas"); c.width = c.height = s; const g = c.getContext("2d");
    if (kind === "spot") {
      const gr = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
      gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.45, "rgba(255,255,255,.55)"); gr.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = gr; g.fillRect(0, 0, s, s);
    } else {
      g.strokeStyle = "#fff"; g.lineWidth = kind === "ringDash" ? 4 : 3;
      if (kind === "ringDash") g.setLineDash([9, 7]);
      g.beginPath(); g.arc(s / 2, s / 2, s / 2 - 4, 0, Math.PI * 2); g.stroke();
      if (kind === "ring") { g.globalAlpha = 0.16; g.fillStyle = "#fff"; g.beginPath(); g.arc(s / 2, s / 2, s / 2 - 5, 0, Math.PI * 2); g.fill(); }
    }
    const t = new T.CanvasTexture(c); t.needsUpdate = true; return t;
  }

  class AptScene {
    constructor(host, cb) {
      this.host = host; this.cb = cb || {};
      const r = this.renderer = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
      r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); r.setClearColor(0x030405, 1);
      this.canvas = r.domElement; this.canvas.className = "apt-canvas"; this.canvas.setAttribute("aria-hidden", "true");
      host.appendChild(this.canvas);
      this.labels = document.createElement("div"); this.labels.className = "apt-labels"; host.appendChild(this.labels);
      this.scene = new T.Scene();
      this.camera = new T.PerspectiveCamera(40, 1, 0.05, 220); this.camera.up.set(0, 0, 1);
      this.orbit = { az: -2.25, el: 0.66, r: 14, t: new T.Vector3(), goal: null };
      this.insets = { top: 0, bottom: 0 }; this.state = {}; this.drag = null; this.aimAnim = {};
      this.ray = new T.Raycaster(); this.ray2 = new T.Raycaster();
      this.tex = { spot: gradientTexture("spot"), ring: gradientTexture("ring"), ringDash: gradientTexture("ringDash") };
      this.decalGeo = new T.PlaneGeometry(1, 1);
      this.scene.add(new T.HemisphereLight(0xdfe6ff, 0x101012, 0.9));
      const dl = new T.DirectionalLight(0xffffff, 0.5); dl.position.set(4, -6, 9); this.scene.add(dl);
      this.mat = new T.ShaderMaterial({
        uniforms: {
          uPR: { value: r.getPixelRatio() }, uSize: { value: 1 }, uOpacity: { value: 1 }, uCut: { value: 1 }, uCam: { value: new T.Vector3() },
          uPool: { value: Array.from({ length: 16 }, () => new T.Vector4()) }, uPoolN: { value: Array.from({ length: 16 }, () => new T.Vector3()) },
          uPoolC: { value: Array.from({ length: 16 }, () => new T.Vector3()) }, uPoolCount: { value: 0 },
        }, vertexShader: VS, fragmentShader: FS, transparent: true, depthWrite: true,
      });
      this._bindPointer();
      this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(host); this.resize();
      const loop = () => { this.raf = requestAnimationFrame(loop); this.lastFrame = performance.now(); this.frame(); }; loop();
      this.watch = setInterval(() => { if (performance.now() - (this.lastFrame || 0) > 400) this.frame(); }, 250);
    }

    /* ---------- site build ---------- */
    setSite(site) {
      if (this.site && this.site.id === site.id) return;
      if (this.litOn) this._litApply(false);
      this.site = site; this.targets = site.targets; this.targetsKey = null;
      ["gCloud", "gSurf", "gFix", "gDyn", "gBody", "gCams"].forEach((k) => { if (this[k]) { this.scene.remove(this[k]); } this[k] = new T.Group(); this.scene.add(this[k]); });
      this.labels.innerHTML = ""; this.aimAnim = {}; this.drag = null;
      this.surfaces = []; this.walls = []; this.fixtureObjs = {}; this.hitSpheres = []; this.targetHits = [];
      this._buildCloud(site); this._buildSurfaces(site); this._buildFixtures(site); this._buildCameras(site); this._buildLabels(site);
      this.bodyKey = null; this.fit(true);
      this._tryPly(site);
    }
    _bounds() {
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9, h = 2.4;
      this.site.rooms.forEach((rm) => { rm.poly.forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }); h = Math.max(h, rm.h); });
      return { x0, y0, x1, y1, h, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
    }
    _inOpening(x, y, z) {
      return (this.site.openings || []).some((o) => {
        const dx = o.b[0] - o.a[0], dy = o.b[1] - o.a[1], L = Math.hypot(dx, dy) || 1;
        const t = ((x - o.a[0]) * dx + (y - o.a[1]) * dy) / (L * L); const px = o.a[0] + dx * t, py = o.a[1] + dy * t;
        return t >= 0 && t <= 1 && Math.hypot(x - px, y - py) < 0.03 && z >= (o.z0 || 0) && z <= o.z1;
      });
    }
    _buildCloud(site) {
      const D = site.scene.density, rand = rng(site.id === "echo" ? 11 : 29), pos = [], it = [], wn = [], seen = new Set();
      const push = (x, y, z, i, n) => { const k = Math.round(x * 25) + "," + Math.round(y * 25) + "," + Math.round(z * 25); if (seen.has(k)) return; seen.add(k); pos.push(x, y, z); it.push(i); wn.push(n ? n[0] : 0, n ? n[1] : 0, 0); };
      site.rooms.forEach((rm) => {
        const P = rm.poly; let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; P.forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); });
        const nf = Math.round((x1 - x0) * (y1 - y0) * D.floor);
        for (let i = 0; i < nf; i++) push(x0 + rand() * (x1 - x0), y0 + rand() * (y1 - y0), 0.004 * rand(), 0.16 + 0.34 * rand());
        for (let e = 0; e < P.length; e++) {
          const a = P[e], b = P[(e + 1) % P.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]); const n = [(b[1] - a[1]) / L, -(b[0] - a[0]) / L];
          const nw = Math.round(L * rm.h * D.wall);
          for (let i = 0; i < nw; i++) { const t = rand(), z = rand() * rm.h, x = a[0] + (b[0] - a[0]) * t, y = a[1] + (b[1] - a[1]) * t; if (!this._inOpening(x, y, z)) push(x, y, z, 0.35 + 0.5 * rand(), n); }
          for (let s = 0; s < L; s += 0.018) { const x = a[0] + (b[0] - a[0]) * (s / L), y = a[1] + (b[1] - a[1]) * (s / L); if (!this._inOpening(x, y, 0.01)) push(x, y, 0.01, 0.95, n); push(x, y, rm.h - 0.005, 0.7, n); }
        }
      });
      site.furniture.forEach((f) => {
        const [ax, ay, az] = f.min, [bx, by, bz] = f.max, w = bx - ax, d = by - ay, h = bz - az;
        const nt = Math.round(w * d * D.top) + 30;
        for (let i = 0; i < nt; i++) push(ax + rand() * w, ay + rand() * d, bz, f.art ? 0.9 : 0.55 + 0.4 * rand());
        const ns = Math.round(2 * (w + d) * h * D.side);
        for (let i = 0; i < ns; i++) {
          const s = rand() * 2 * (w + d), z = az + rand() * h; let x, y;
          if (s < w) { x = ax + s; y = ay; } else if (s < w + d) { x = bx; y = ay + s - w; } else if (s < 2 * w + d) { x = bx - (s - w - d); y = by; } else { x = ax; y = by - (s - 2 * w - d); }
          push(x, y, z, f.art ? 0.85 : 0.4 + 0.4 * rand());
        }
        const edge = (p, q) => { const L = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]); for (let s = 0; s < L; s += 0.014) { const t = s / L; push(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t, 1); } };
        [[ax, ay], [bx, ay], [bx, by], [ax, by]].forEach((c, i, A) => { const n2 = A[(i + 1) % 4]; edge([c[0], c[1], bz], [n2[0], n2[1], bz]); edge([c[0], c[1], az], [c[0], c[1], bz]); });
      });
      this._setCloud(new Float32Array(pos), new Float32Array(it), new Float32Array(wn), true);
    }
    _setCloud(pos, it, wn, cut) {
      this.gCloud.clear();
      const g = new T.BufferGeometry(); g.setAttribute("position", new T.BufferAttribute(pos, 3));
      g.setAttribute("intensity", new T.BufferAttribute(it, 1)); g.setAttribute("wn", new T.BufferAttribute(wn, 3));
      this.points = new T.Points(g, this.mat); this.gCloud.add(this.points); this.cloudCount = pos.length / 3; this.cloudCut = cut;
    }
    async _tryPly(site) {
      for (const url of site.scene.ply || []) {
        try {
          const res = await fetch(url, { cache: "force-cache" }); if (!res.ok) continue;
          const buf = new Uint8Array(await res.arrayBuffer()); const head = new TextDecoder().decode(buf.slice(0, 600));
          const end = head.indexOf("end_header\n"); if (end < 0 || head.indexOf("homeapt-points v1") < 0) continue;
          const n = parseInt((head.match(/element vertex (\d+)/) || [])[1], 10); const off = end + 11; const dv = new DataView(buf.buffer, off);
          const pos = new Float32Array(n * 3), it = new Float32Array(n), wn = new Float32Array(n * 3);
          for (let i = 0; i < n; i++) { const o = i * 13; pos[i * 3] = dv.getFloat32(o, true); pos[i * 3 + 1] = dv.getFloat32(o + 4, true); pos[i * 3 + 2] = dv.getFloat32(o + 8, true); it[i] = dv.getUint8(o + 12) / 255; }
          if (this.site !== site) return;
          this._setCloud(pos, it, wn, false); this.cb.onScene && this.cb.onScene({ site: site.id, kind: "scan", url, points: n }); return;
        } catch (e) { /* not reachable in this environment */ }
      }
      this.cb.onScene && this.cb.onScene({ site: site.id, kind: site.scene.kind, points: this.cloudCount });
    }
    _surfMat(color, opacity) { return new T.MeshLambertMaterial({ color, transparent: true, opacity, side: T.DoubleSide, depthWrite: opacity > 0.8 }); }
    _addSurface(mesh, info) { mesh.userData = info; this.surfaces.push(mesh); this.gSurf.add(mesh); return mesh; }
    _buildSurfaces(site) {
      const dir = (n) => Math.abs(n[0]) > Math.abs(n[1]) ? (n[0] > 0 ? "east" : "west") : (n[1] > 0 ? "north" : "south");
      site.rooms.forEach((rm) => {
        const P = rm.poly; let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; P.forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); });
        const fl = new T.Mesh(new T.PlaneGeometry(x1 - x0, y1 - y0), this._surfMat(0x111216, 1)); fl.position.set((x0 + x1) / 2, (y0 + y1) / 2, 0);
        this._addSurface(fl, { id: rm.id + "-floor", name: rm.name + " floor", short: "Floor", kind: "floor", room: rm.id });
        for (let e = 0; e < P.length; e++) {
          const a = P[e], b = P[(e + 1) % P.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]); const n = [(b[1] - a[1]) / L, -(b[0] - a[0]) / L];
          const m = new T.Mesh(new T.PlaneGeometry(L, rm.h), this._surfMat(0x2b2e36, 0.62));
          m.position.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, rm.h / 2); m.rotation.order = "ZXY"; m.rotation.z = Math.atan2(b[1] - a[1], b[0] - a[0]); m.rotation.x = Math.PI / 2;
          const d = dir(n), nm = site.rooms.length > 1 ? rm.name + " · " + d + " wall" : d[0].toUpperCase() + d.slice(1) + " wall";
          const w = this._addSurface(m, { id: rm.id + "-wall-" + e, name: nm, short: nm, kind: "wall", room: rm.id, outward: new T.Vector3(n[0], n[1], 0), center: m.position.clone() });
          this.walls.push(w);
        }
      });
      site.furniture.forEach((f) => {
        const s = [f.max[0] - f.min[0], f.max[1] - f.min[1], f.max[2] - f.min[2]];
        const m = new T.Mesh(new T.BoxGeometry(s[0], s[1], s[2]), this._surfMat(f.art ? 0x2a2622 : 0x24272e, 0.96));
        m.position.set((f.min[0] + f.max[0]) / 2, (f.min[1] + f.max[1]) / 2, (f.min[2] + f.max[2]) / 2);
        this._addSurface(m, { id: f.id, name: f.name, short: f.name, kind: f.art ? "art" : "furniture", room: f.room });
        const edges = new T.LineSegments(new T.EdgesGeometry(m.geometry), new T.LineBasicMaterial({ color: 0x4a505c, transparent: true, opacity: 0.8 }));
        edges.position.copy(m.position); edges.userData.edge = true; this.gSurf.add(edges);
      });
      site.targets.forEach((t) => {
        const m = new T.Mesh(new T.PlaneGeometry(t.size[0] || 0.4, t.size[1] || 0.4), new T.MeshBasicMaterial({ visible: false, side: T.DoubleSide }));
        m.position.copy(V(t.pos)).addScaledVector(V(t.normal), 0.01); m.lookAt(m.position.clone().add(V(t.normal))); m.userData = { targetId: t.id };
        this.targetHits.push(m); this.gSurf.add(m);
      });
    }
    _buildFixtures(site) {
      const white = new T.MeshLambertMaterial({ color: 0xe9e9e6, emissive: 0x2a2a2a }); this.tex = this.tex || {};
      if (!this.tex.glow) { const cv = document.createElement("canvas"); cv.width = cv.height = 128; const x = cv.getContext("2d"), gr = x.createRadialGradient(64, 64, 0, 64, 64, 64); gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.16, "rgba(255,255,255,0.6)"); gr.addColorStop(0.45, "rgba(255,255,255,0.14)"); gr.addColorStop(1, "rgba(255,255,255,0)"); x.fillStyle = gr; x.fillRect(0, 0, 128, 128); this.tex.glow = new T.CanvasTexture(cv); }
      site.fixtures.forEach((f) => {
        const g = new T.Group(); g.position.copy(V(f.pos)); const o = { group: g, f, strips: [], parts: {} };
        if (f.kind === "cylinder") {
          const orb = new T.Mesh(new T.SphereGeometry(0.03, 24, 16), new T.MeshBasicMaterial({ color: 0x3d3d3d })); g.add(orb); o.parts.body = orb;
          const halo = new T.Sprite(new T.SpriteMaterial({ map: this.tex.glow, color: 0xffffff, transparent: true, opacity: 0.6, blending: T.AdditiveBlending, depthWrite: false })); halo.scale.set(0.3, 0.3, 1); halo.renderOrder = 5; g.add(halo); o.parts.halo = halo;
        } else if (f.kind === "ceiling") {
          const disc = new T.Mesh(new T.CircleGeometry(0.075, 28), new T.MeshBasicMaterial({ color: 0x444444, side: T.DoubleSide })); disc.position.z = 0.0; g.add(disc); o.parts.disc = disc;
          const rim = new T.Mesh(new T.RingGeometry(0.075, 0.09, 28), new T.MeshBasicMaterial({ color: 0x777777, side: T.DoubleSide })); g.add(rim);
          if (f.engineered) {
            const body = new T.Mesh(new T.CylinderGeometry(0.04, 0.04, 0.1, 24), white.clone()); body.rotation.x = Math.PI / 2; body.position.z = -0.05; g.add(body); o.parts.body = body;
          }
        } else {
          const bulb = new T.Mesh(new T.SphereGeometry(0.07, 18, 12), new T.MeshBasicMaterial({ color: 0x444444 })); g.add(bulb); o.parts.bulb = bulb;
        }
        const ring = new T.Mesh(new T.RingGeometry(0.11, 0.125, 40), new T.MeshBasicMaterial({ color: 0xb8d8ff, side: T.DoubleSide, transparent: true, opacity: 0.95, depthTest: false }));
        ring.visible = false; ring.renderOrder = 5; g.add(ring); o.ring = ring;
        const hit = new T.Mesh(new T.SphereGeometry(0.22, 10, 8), new T.MeshBasicMaterial({ visible: false })); hit.userData.fixtureId = f.id; g.add(hit); this.hitSpheres.push(hit);
        this.gFix.add(g); this.fixtureObjs[f.id] = o;
      });
    }
    _camPose(c) {
      const p = V(c.pos); let look;
      if (c.look) look = V(c.look); else look = p.clone().add(new T.Vector3(Math.cos(c.yaw) * Math.cos(c.pitch), Math.sin(c.yaw) * Math.cos(c.pitch), Math.sin(c.pitch)));
      return { p, look };
    }
    _buildCameras(site) {
      site.cameras.forEach((c) => {
        const { p, look } = this._camPose(c); const cam = new T.PerspectiveCamera(c.vfov, 16 / 9, 0.05, 0.55); cam.position.copy(p); cam.up.set(0, 0, 1); cam.lookAt(look); cam.updateMatrixWorld();
        const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([x, y]) => new T.Vector3(x, y, 1).unproject(cam));
        const pts = []; corners.forEach((q, i) => { pts.push(p, q, q, corners[(i + 1) % 4]); });
        const g = new T.BufferGeometry().setFromPoints(pts);
        const l = new T.LineSegments(g, new T.LineBasicMaterial({ color: 0xb8d8ff, transparent: true, opacity: 0.55 })); l.userData.cameraId = c.id; this.gCams.add(l);
      });
    }
    _buildLabels(site) {
      this.flabels = {}; this.tlabels = {};
      site.fixtures.forEach((f) => {
        const b = document.createElement("button"); b.type = "button"; b.className = "apt-flabel"; b.tabIndex = -1; b.dataset.id = f.id;
        b.innerHTML = `<span class="apt-flabel-dot"></span><span class="apt-flabel-text"></span>`;
        b.addEventListener("click", (e) => { e.stopPropagation(); this.cb.onSelect && this.cb.onSelect({ kind: "fixture", id: f.id }); });
        b.addEventListener("pointerenter", () => this._hover({ kind: "fixture", id: f.id }));
        b.addEventListener("pointerleave", () => this._hover(null));
        this.labels.appendChild(b); this.flabels[f.id] = b;
      });
      site.targets.forEach((t) => { const d = document.createElement("div"); d.className = "apt-tlabel"; d.textContent = t.name; this.labels.appendChild(d); this.tlabels[t.id] = d; });
      this.bodyLabel = document.createElement("div"); this.bodyLabel.className = "apt-blabel"; this.labels.appendChild(this.bodyLabel);
      this.camLabels = {}; site.cameras.forEach((c) => { const d = document.createElement("div"); d.className = "apt-clabel"; d.textContent = c.name; this.labels.appendChild(d); this.camLabels[c.id] = d; });
      const ret = this.reticle = document.createElement("button"); ret.type = "button"; ret.className = "apt-reticle";
      ret.setAttribute("aria-label", "Aim handle. Drag onto a surface, or use the arrow keys. Enter keeps the proposal, Escape cancels.");
      ret.innerHTML = `<span class="apt-reticle-ring"></span><span class="apt-reticle-dot"></span>`; this.labels.appendChild(ret); this._bindReticle(ret);
      this.chip = document.createElement("div"); this.chip.className = "apt-dragchip"; this.labels.appendChild(this.chip);
    }

    /* ---------- state ---------- */
    update(s) {
      const prev = this.state; this.state = s;
      if (s.site && (!this.site || s.site.id !== this.site.id)) this.setSite(s.site); if (this.site && s.targets) this._syncTargets(s.targets); if (!s.placing) this.placeHover = null;
      if (!this.site) return;
      const mesh = s.mode === "mesh" || s.mode === "lit";
      this.points && (this.points.visible = !mesh);
      this.surfaces.forEach((m) => { m.material.visible = mesh && !(m.userData.kind === "floor" && false); });
      this.gSurf.children.forEach((c) => { if (c.userData.edge) c.visible = mesh; });
      this.gCams.visible = !!(s.layers && s.layers.cameras) && !s.showDevices;
      this.site.fixtures.forEach((f) => {
        const a = s.fixtures && s.fixtures[f.id] && s.fixtures[f.id].aim; const old = prev.fixtures && prev.fixtures[f.id] && prev.fixtures[f.id].aim;
        if (a && old && (a.point[0] !== old.point[0] || a.point[1] !== old.point[1] || a.point[2] !== old.point[2]) && !REDUCED && prev.site && prev.site.id === this.site.id)
          this.aimAnim[f.id] = { from: V(old.point), to: V(a.point), t0: performance.now() };
      });
      if (s.cameraView !== prev.cameraView) this._goCameraView(s.cameraView);
      if (s.insets && (s.insets.top !== this.insets.top || s.insets.bottom !== this.insets.bottom)) { this.insets = { ...s.insets }; this.resize(); if (!this.userMoved && !this.camView) this.fit(true); }
      this._bodyBuild(s.body);
      this._styleFixtures(); this._dynamic(); this._syncLit(s); this._syncBodyVolume(s); if (performance.now() - (this.lastFrame || 0) > 400) this.frame();
    }
    _fixState(id) { return (this.state.fixtures && this.state.fixtures[id]) || {}; }
    _displayAim(id) {
      const st = this._fixState(id); if (!st.aim) return null; const an = this.aimAnim[id];
      if (an) { const k = clamp((performance.now() - an.t0) / 650, 0, 1); const e = 1 - Math.pow(1 - k, 3); if (k >= 1) delete this.aimAnim[id]; return an.from.clone().lerp(an.to, e); }
      return V(st.aim.point);
    }
    _styleFixtures() {
      const sel = this.state.selected || {}, hov = this.state.hover || {};
      this.site.fixtures.forEach((f) => {
        const o = this.fixtureObjs[f.id], st = this._fixState(f.id); if (!o) return;
        o.ring.visible = sel.kind === "fixture" && sel.id === f.id; o.ring.material.opacity = 0.95;
        if (!o.ring.visible && hov.kind === "fixture" && hov.id === f.id) { o.ring.visible = true; o.ring.material.opacity = 0.45; }
        if (f.kind === "cylinder") {
          const ch = st.channels || {}, rr = ch.radial || {}, dd = ch.down || {}, sp = ch.spot || {};
          const mix = [[sp.on ? sp.bri / 100 : 0, sp.k], [dd.on ? dd.bri / 100 : 0, dd.k], [rr.levels ? this._zoneLv(f.id).reduce((a, b) => a + b, 0) / 6 : 0, this._zoneK(f.id)]];
          const tot = mix.reduce((a, [w]) => a + w, 0), col = [0, 0, 0]; mix.forEach(([w, k]) => { if (w > 0) { const kc = kelvin(k); col[0] += kc[0] * w; col[1] += kc[1] * w; col[2] += kc[2] * w; } }); if (tot > 0) col.forEach((v, i) => { col[i] = v / tot; });
          const lvl = Math.min(1, tot / 1.4); if (tot > 0) o.parts.body.material.color.setRGB(0.6 + 0.4 * col[0], 0.6 + 0.4 * col[1], 0.6 + 0.4 * col[2]); else o.parts.body.material.color.setRGB(0.24, 0.24, 0.25);
          o.parts.halo.visible = tot > 0; o.parts.halo.material.color.setRGB(col[0], col[1], col[2]); o.parts.halo.material.opacity = 0.4 + 0.5 * lvl; const hs = 0.2 + 0.32 * lvl; o.parts.halo.scale.set(hs, hs, 1);
        } else if (f.kind === "ceiling") {
          const c = kelvin(st.k), l = st.on ? 0.45 + (st.bri || 50) / 160 : 0; o.parts.disc.material.color.setRGB(0.18 + c[0] * l, 0.18 + c[1] * l, 0.18 + c[2] * l);
        } else if (o.parts.bulb) { const c = kelvin(st.k); const l = st.on ? 0.9 : 0; o.parts.bulb.material.color.setRGB(0.2 + c[0] * l, 0.2 + c[1] * l, 0.2 + c[2] * l); }
      });
    }
    _downHit(origin) {
      this.ray2.set(origin, new T.Vector3(0, 0, -1)); this.ray2.far = 4;
      const h = this.ray2.intersectObjects(this.surfaces.filter((m) => m.userData.kind !== "wall"), false)[0];
      return h ? { point: h.point.clone(), normal: new T.Vector3(0, 0, 1) } : { point: new T.Vector3(origin.x, origin.y, 0), normal: new T.Vector3(0, 0, 1) };
    }
    _pools() {
      const out = [];
      this.site.fixtures.forEach((f) => {
        const st = this._fixState(f.id), o = V(f.pos);
        if (f.kind === "cylinder") {
          const ch = st.channels || {};
          if (ch.spot && ch.spot.on && st.aim) { const p = this._displayAim(f.id); const n = V(st.aim.normal); const dist = p.distanceTo(o); out.push({ p, n, r: Math.max(0.18, dist * Math.tan((f.engineered.fwhm / 2) * Math.PI / 180) * 1.25), c: kelvin(ch.spot.k), i: (ch.spot.bri / 100) * 1.5, spot: f.id, dir: p.clone().sub(o).normalize() }); }
          if (ch.down && ch.down.on) { const h = this._downHit(o); out.push({ p: h.point, n: h.normal, r: 0.75, c: kelvin(ch.down.k), i: (ch.down.bri / 100) * 1.1 }); }
          const zl = ch.radial ? this._zoneLv(f.id) : null; if (zl && zl.some((v) => v > 0)) { const lv = zl.reduce((a, b) => a + b, 0) / 6, rc = kelvin(this._zoneK(f.id)), fz = this._zoneFacingCached(f.id); zl.forEach((l, zi) => { const z = fz[zi]; if (l > 0 && z && z.point) out.push({ p: V(z.point), n: V(z.normal), r: 0.45 + 0.3 * Math.min(z.dist, 2.5), c: rc, i: l * 1.1 }); }); if (lv > 0) out.push({ p: o, n: new T.Vector3(), r: 0.9, c: rc, i: lv * 0.6 }); }
        } else if (f.kind === "ceiling") {
          if (st.on) { const h = this._downHit(o); out.push({ p: h.point, n: h.normal, r: 0.8 + (st.bri || 50) / 220, c: kelvin(st.k), i: ((st.bri || 50) / 100) * 0.85 }); }
          const ch = st.channels; if (f.engineered && ch && ch.spot && ch.spot.on && st.aim) { const p = this._displayAim(f.id); const dist = p.distanceTo(o); out.push({ p, n: V(st.aim.normal), r: Math.max(0.18, dist * Math.tan((f.engineered.fwhm / 2) * Math.PI / 180) * 1.25), c: kelvin(ch.spot.k), i: (ch.spot.bri / 100) * 1.2, spot: f.id, sim: true, dir: p.clone().sub(o).normalize() }); }
        } else if (st.on) out.push({ p: o, n: new T.Vector3(), r: 1.25, c: kelvin(st.k), i: 0.42 });
      });
      return out.sort((a, b) => b.i - a.i).slice(0, 16);
    }
    _decal(p, n, dir, radius, tex, color, opacity, additive) {
      const m = new T.Mesh(this.decalGeo, new T.MeshBasicMaterial({ map: tex, color, transparent: true, opacity, depthWrite: false, blending: additive ? T.AdditiveBlending : T.NormalBlending, side: T.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 }));
      const N = n.clone().normalize(); let U = dir ? dir.clone().addScaledVector(N, -dir.dot(N)) : new T.Vector3(1, 0, 0).addScaledVector(N, -N.x);
      if (U.lengthSq() < 1e-4) U = Math.abs(N.z) > 0.9 ? new T.Vector3(1, 0, 0) : new T.Vector3(0, 0, 1); U.normalize(); const W = new T.Vector3().crossVectors(N, U);
      m.quaternion.setFromRotationMatrix(new T.Matrix4().makeBasis(U, W, N)); m.position.copy(p).addScaledVector(N, 0.006);
      const stretch = dir ? 1 / Math.max(0.3, Math.abs(dir.dot(N))) : 1; m.scale.set(radius * 2 * stretch, radius * 2, 1); this.gDyn.add(m); return m;
    }
    _line(a, b, color, opacity, dashed) {
      const g = new T.BufferGeometry().setFromPoints([a, b]);
      const l = new T.Line(g, dashed ? new T.LineDashedMaterial({ color, dashSize: 0.06, gapSize: 0.05, transparent: true, opacity, depthTest: false }) : new T.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false }));
      if (dashed) l.computeLineDistances(); l.renderOrder = 4; this.gDyn.add(l); return l;
    }
    _cone(o, p, radius, color, opacity) {
      const dist = o.distanceTo(p); const g = new T.CylinderGeometry(0.01, radius, dist, 32, 1, true);
      const m = new T.Mesh(g, new T.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false, blending: T.AdditiveBlending, side: T.DoubleSide }));
      m.position.copy(o).lerp(p, 0.5); m.quaternion.setFromUnitVectors(new T.Vector3(0, -1, 0), p.clone().sub(o).normalize()); this.gDyn.add(m);
    }
    _dynamic() {
      const s = this.state; this.gDyn.clear(); if (!this.site) return; this._drawTargets(s); this._drawDevices(s); this._drawZones(s); this._drawShadowHit(s);
      const pools = s.layers && s.layers.pools === false ? [] : this._pools(); const mesh = s.mode === "mesh" || s.mode === "lit"; const U = this.mat.uniforms;
      U.uPoolCount.value = pools.length;
      pools.forEach((pl, i) => { U.uPool.value[i].set(pl.p.x, pl.p.y, pl.p.z, pl.r); U.uPoolN.value[i].copy(pl.n); U.uPoolC.value[i].set(pl.c[0] * pl.i, pl.c[1] * pl.i, pl.c[2] * pl.i); });
      pools.forEach((pl) => {
        if (pl.n.lengthSq() < 0.5 || s.mode === "lit") return; const col = new T.Color(pl.c[0], pl.c[1], pl.c[2]);
        this._decal(pl.p, pl.n, pl.dir, pl.r * 1.05, this.tex.spot, col, (mesh ? 0.5 : 0.26) * clamp(pl.i, 0.2, 1.2), true);
        if (pl.sim) this._decal(pl.p, pl.n, pl.dir, pl.r * 0.82, this.tex.ringDash, col, 0.4, true);
      });
      const sel = s.selected || {}; const beams = (s.layers && s.layers.beams) || "selected";
      this.site.fixtures.forEach((f) => {
        const st = this._fixState(f.id); if (!f.engineered || !st.aim) return;
        const isSel = sel.kind === "fixture" && sel.id === f.id; if (!(beams === "all" || (beams === "selected" && isSel))) return;
        const o = V(f.pos), p = this._displayAim(f.id), spot = st.channels && st.channels.spot; const on = spot && spot.on; const c = kelvin(spot ? spot.k : 2700);
        const col = new T.Color(c[0], c[1], c[2]); const r = Math.max(0.18, o.distanceTo(p) * Math.tan((f.engineered.fwhm / 2) * Math.PI / 180) * 1.25);
        this._line(o, p, on ? col : new T.Color(0x8a8a8a), isSel ? 0.75 : 0.3, !on || f.engineered.status === "simulated");
        if (on && isSel) this._cone(o, p, r, col, 0.045);
        if (!on && isSel) this._decal(p, V(st.aim.normal), p.clone().sub(o).normalize(), r, this.tex.ringDash, new T.Color(0x9a9a9a), 0.5, false);
      });
      const pr = s.proposed; const live = this.drag && this.drag.hit;
      const show = live && live.point ? live : (!live && pr && pr.point ? pr : null);
      if (show && s.aimFixture) {
        const f = this.site.fixtures.find((x) => x.id === s.aimFixture); if (f) {
          const o = V(f.pos), p = V(show.point), n = V(show.normal || [0, 0, 1]); const ok = show.valid !== false;
          const col = new T.Color(ok ? 0x45dfff : 0xff625f); const r = Math.max(0.12, o.distanceTo(p) * Math.tan((f.engineered.fwhm / 2) * Math.PI / 180));
          this._line(o, p, col, 0.95, true); this._decal(p, n, p.clone().sub(o).normalize(), r, ok ? this.tex.ring : this.tex.ringDash, col, ok ? 0.95 : 0.8, false);
          if (show.blockerPoint) { const bp = V(show.blockerPoint); this._decal(bp, n, null, 0.06, this.tex.ring, new T.Color(0xff625f), 1, false); }
        }
      }
    }
    _rect(t, color, opacity, dashed, fill) {
      const N = V(t.normal).normalize(); const horiz = Math.abs(N.z) > 0.8; let U;
      if (horiz) { const a = ((t.rot || 0) * Math.PI) / 180; U = new T.Vector3(Math.cos(a), Math.sin(a), 0); } else U = new T.Vector3(-N.y, N.x, 0).normalize();
      const W = new T.Vector3().crossVectors(N, U).normalize(); const w = (t.size && t.size[0]) || 0.3, h = (t.size && t.size[1]) || 0.3; const c = V(t.pos).addScaledVector(N, 0.012);
      const pts = [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]].map(([i, j]) => c.clone().addScaledVector(U, (i * w) / 2).addScaledVector(W, (j * h) / 2));
      const l = new T.Line(new T.BufferGeometry().setFromPoints(pts), dashed ? new T.LineDashedMaterial({ color, dashSize: 0.05, gapSize: 0.035, transparent: true, opacity, depthTest: false }) : new T.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false }));
      if (dashed) l.computeLineDistances(); l.renderOrder = 4; this.gDyn.add(l);
      if (fill) { const m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ color, transparent: true, opacity: fill, depthWrite: false, side: T.DoubleSide })); m.position.copy(c); m.quaternion.setFromRotationMatrix(new T.Matrix4().makeBasis(U, W, N)); m.renderOrder = 3; this.gDyn.add(m); }
    }
    _drawTargets(s) {
      if (s.showTargetMap) (this.targets || []).forEach((t) => { if (s.targetDraft && s.targetDraft.id === t.id) return; if (t.shape === "point" || !(t.size && t.size[0])) this._decal(V(t.pos), V(t.normal), null, 0.08, this.tex.ring, new T.Color(0xffffff), 0.4, false); else this._rect(t, new T.Color(0xdde6f5), 0.45, false, 0.04); });
      const d = s.targetDraft; if (d && d.pos) { const col = new T.Color(0x45dfff); if (d.shape === "point") this._decal(V(d.pos), V(d.normal), null, 0.09, this.tex.ring, col, 0.95, false); else this._rect(d, col, 0.95, true, 0.1); if (d.mode === "editing" && Math.abs(d.normal[2]) > 0.8) { const p = V(d.pos); this._line(p, p.clone().add(new T.Vector3(0, 0, 0.32)), col, 0.7, false); } if (d.pos[2] > 0.05) this._line(V(d.pos), new T.Vector3(d.pos[0], d.pos[1], 0), col, 0.3, true); }
      const hv = s.placing && this.placeHover; if (hv) { const col = new T.Color(0x45dfff); this._decal(V(hv.point), V(hv.normal), null, 0.07, this.tex.ring, col, 0.9, false); if (hv.fit) this._rect({ pos: hv.fit.center, normal: hv.normal, size: hv.fit.size }, col, 0.55, true, 0.05); }
    }
    surfaceHit(cx, cy) {
      this.ray.setFromCamera(this._ndc(cx, cy), this.camera);
      const h = this._solid(this.ray.intersectObjects(this.surfaces.filter((m) => !m.userData.cut), false))[0]; if (!h) return null;
      const n = h.face.normal.clone().transformDirection(h.object.matrixWorld).normalize(); if (n.dot(this.ray.ray.direction) > 0) n.negate();
      const u = h.object.userData, horiz = Math.abs(n.z) > 0.8; let fit = null; const f = this.site.furniture.find((x) => x.id === u.id);
      if (f) {
        if (horiz && n.z > 0) fit = { center: [(f.min[0] + f.max[0]) / 2, (f.min[1] + f.max[1]) / 2, f.max[2]], size: [f.max[0] - f.min[0], f.max[1] - f.min[1]] };
        else if (!horiz && f.art) { const ax = Math.abs(n.y) > Math.abs(n.x); fit = { center: [ax ? (f.min[0] + f.max[0]) / 2 : (n.x > 0 ? f.max[0] : f.min[0]), ax ? (n.y > 0 ? f.max[1] : f.min[1]) : (f.min[1] + f.max[1]) / 2, (f.min[2] + f.max[2]) / 2], size: [ax ? f.max[0] - f.min[0] : f.max[1] - f.min[1], f.max[2] - f.min[2]] }; }
      }
      const r3 = (v) => Math.round(v * 1000) / 1000;
      return { point: [h.point.x, h.point.y, h.point.z], normal: [r3(n.x), r3(n.y), r3(n.z)], surface: u.short || u.name, kind: u.kind, horizontal: horiz, face: horiz ? (n.z > 0 ? "top" : "underside") : u.kind === "wall" ? "wall" : "front", fit, name: f ? f.name : null };
    }
    _syncTargets(list) {
      const key = list.map((t) => [t.id, t.name, t.pos.join(","), t.normal.join(","), (t.size || []).join(",")].join("|")).join(";"); if (key === this.targetsKey) return; this.targetsKey = key; this.targets = list;
      (this.targetHits || []).forEach((m) => this.gSurf.remove(m)); this.targetHits = []; Object.values(this.tlabels || {}).forEach((el) => el.remove()); this.tlabels = {};
      list.forEach((t) => {
        const m = new T.Mesh(new T.PlaneGeometry(Math.max(0.2, (t.size && t.size[0]) || 0.4), Math.max(0.2, (t.size && t.size[1]) || 0.4)), new T.MeshBasicMaterial({ visible: false, side: T.DoubleSide }));
        m.position.copy(V(t.pos)).addScaledVector(V(t.normal), 0.01); m.lookAt(m.position.clone().add(V(t.normal))); m.userData = { targetId: t.id }; this.targetHits.push(m); this.gSurf.add(m);
        const d = document.createElement("div"); d.className = "apt-tlabel"; d.textContent = t.name; this.labels.insertBefore(d, this.reticle || null); this.tlabels[t.id] = d;
      });
    }
    _devDir(d) { return new T.Vector3(Math.cos(d.yaw) * Math.cos(d.pitch), Math.sin(d.yaw) * Math.cos(d.pitch), Math.sin(d.pitch)); }
    _devBasis(d) { const f = this._devDir(d).normalize(); let r = new T.Vector3().crossVectors(f, new T.Vector3(0, 0, 1)); if (r.lengthSq() < 1e-4) r = new T.Vector3(Math.cos(d.yaw + Math.PI / 2), Math.sin(d.yaw + Math.PI / 2), 0); r.normalize(); return { f, r, u: new T.Vector3().crossVectors(r, f).normalize(), th: Math.tan(((d.hfov || 90) * Math.PI) / 360), tv: Math.tan(((d.vfov || 58) * Math.PI) / 360) }; }
    _drawDevice(d, color, opacity, dashed, fill) {
      const o = V(d.pos), B = this._devBasis(d), L = Math.min(d.range || 4, 6);
      const room = this.surfaces.filter((m) => !m.userData.cut && (m.userData.kind === "wall" || m.userData.kind === "floor"));
      const cs = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([i, j]) => { const dir = B.f.clone().addScaledVector(B.r, i * B.th).addScaledVector(B.u, j * B.tv), len = dir.length(); this.ray2.set(o, dir.clone().divideScalar(len)); this.ray2.far = L * len;
        const h = this.ray2.intersectObjects(room, false)[0]; return o.clone().addScaledVector(dir, h ? h.distance / len : L); });
      const pts = []; cs.forEach((q, i) => { pts.push(o, q, q, cs[(i + 1) % 4]); });
      const l = new T.LineSegments(new T.BufferGeometry().setFromPoints(pts), dashed ? new T.LineDashedMaterial({ color, dashSize: 0.08, gapSize: 0.06, transparent: true, opacity }) : new T.LineBasicMaterial({ color, transparent: true, opacity })); if (dashed) l.computeLineDistances(); this.gDyn.add(l);
      if (fill) this.gDyn.add(new T.Mesh(new T.BufferGeometry().setFromPoints([o, cs[0], cs[1], o, cs[1], cs[2], o, cs[2], cs[3], o, cs[3], cs[0]]), new T.MeshBasicMaterial({ color, transparent: true, opacity: fill, side: T.DoubleSide, depthWrite: false })));
      const body = new T.Mesh(d.kind === "camera" ? new T.BoxGeometry(0.09, 0.09, 0.09) : new T.CylinderGeometry(0.05, 0.05, 0.03, 20), new T.MeshBasicMaterial({ color })); body.position.copy(o); if (d.kind !== "camera") body.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), B.f); this.gDyn.add(body);
    }
    _drawDevices(s) {
      if (s.showDevices) (s.cams || []).forEach((d) => { if (s.devDraft && s.devDraft.id === d.id) return; this._drawDevice(d, new T.Color(0xb8d8ff), d.kind === "camera" ? 0.55 : 0.45, d.kind !== "camera", 0.025); });
      const d = s.devDraft; if (d && d.pos) this._drawDevice(d, new T.Color(0x45dfff), 0.95, d.kind !== "camera", 0.06);
    }
    coverage(d) {
      if (!this.site || !d || !d.pos) return []; const o = V(d.pos), B = this._devBasis(d), L = d.range || 5, out = [];
      this.site.furniture.forEach((x) => { const c = new T.Vector3((x.min[0] + x.max[0]) / 2, (x.min[1] + x.max[1]) / 2, (x.min[2] + x.max[2]) / 2), v = c.clone().sub(o), z = v.dot(B.f); if (z <= 0.05 || v.length() > L) return; if (Math.abs(v.dot(B.r) / z) <= B.th && Math.abs(v.dot(B.u) / z) <= B.tv && !out.includes(x.name)) out.push(x.name); });
      return out;
    }
    zoneDirs(f) { const a0 = ((f.radialAnchor || 0) * Math.PI) / 180; return [0, 1, 2, 3, 4, 5].map((i) => new T.Vector3(Math.cos(a0 + (i * Math.PI) / 3), Math.sin(a0 + (i * Math.PI) / 3), 0)); }
    _openingAt(x, y, z) { return (this.site.openings || []).find((o) => { const dx = o.b[0] - o.a[0], dy = o.b[1] - o.a[1], L = Math.hypot(dx, dy) || 1, t = ((x - o.a[0]) * dx + (y - o.a[1]) * dy) / (L * L); return t >= 0 && t <= 1 && Math.hypot(x - (o.a[0] + dx * t), y - (o.a[1] + dy * t)) < 0.03 && z >= (o.z0 || 0) && z <= o.z1; }); }
    zoneFacing(fid) { const f = this.site.fixtures.find((x) => x.id === fid); if (!f || f.kind !== "cylinder") return []; const o = V(f.pos); this.scene.updateMatrixWorld(true);
      return this.zoneDirs(f).map((d, i) => { const dir = d.clone().setZ(-0.12).normalize(); this.ray2.set(o, dir); this.ray2.far = 14;
        const h = this.ray2.intersectObjects(this.surfaces.filter((m) => m.userData.kind !== "floor"), false)[0]; if (!h) return { i, label: "Open space", dist: null };
        const n = h.face.normal.clone().transformDirection(h.object.matrixWorld).normalize(); if (n.dot(dir) > 0) n.negate(); const u = h.object.userData;
        const op = u.kind === "wall" ? this._openingAt(h.point.x, h.point.y, h.point.z) : null;
        return { i, label: op ? (op.z0 > 0 ? "Window" : "Doorway") : u.short || u.name, kind: op ? "opening" : u.kind, dist: h.distance, point: [h.point.x, h.point.y, h.point.z], normal: [n.x, n.y, n.z] }; }); }
    _zoneFacingCached(fid) { this.zoneCache = this.zoneCache || {}; const k = this.site.id + ":" + fid; if (!this.zoneCache[k]) { const r = this.zoneFacing(fid); if (r.some((z) => z.point)) this.zoneCache[k] = r; return r; } return this.zoneCache[k]; }
    _drawZones(s) { const zf = s.zoneFixture && this.site.fixtures.find((x) => x.id === s.zoneFixture); if (!zf || this.camView) return;
      const st = this._fixState(zf.id), kc = kelvin(this._zoneK(zf.id)), warm = new T.Color(kc[0], kc[1], kc[2]), white = new T.Color(1, 1, 1);
      const R0 = 0.14, R1 = 1.3, a0 = ((zf.radialAnchor || 0) * Math.PI) / 180, cp = this.compose && this.compose.fid === zf.id ? this.compose : null, lv = this._zoneLv(zf.id);
      const g = new T.Group(); g.position.set(zf.pos[0], zf.pos[1], zf.pos[2]); const za = (i) => a0 + (i * Math.PI) / 3;
      const line = (pts, color, opacity, dashed) => { const l = new T.Line(new T.BufferGeometry().setFromPoints(pts), dashed ? new T.LineDashedMaterial({ color, transparent: true, opacity, depthTest: false, dashSize: 0.025, gapSize: 0.035 }) : new T.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false })); if (dashed) l.computeLineDistances(); l.renderOrder = 9; g.add(l); };
      const circle = (r, n) => { const p = []; for (let k = 0; k <= n; k++) { const t = (k / n) * Math.PI * 2; p.push(new T.Vector3(Math.cos(t) * r, Math.sin(t) * r, 0)); } return p; };
      const node = (i, r, size, color, opacity) => { const m = new T.Sprite(new T.SpriteMaterial({ map: this.tex.glow, color, transparent: true, opacity, blending: T.AdditiveBlending, depthTest: false, depthWrite: false })); m.position.set(Math.cos(za(i)) * r, Math.sin(za(i)) * r, 0); m.scale.set(size, size, 1); m.renderOrder = 10; g.add(m); };
      if (lv.some((v) => v > 0)) {
        const N = 240, rAt = (th) => { let u = (th - a0) / (Math.PI / 3); u = ((u % 6) + 6) % 6; const i = Math.floor(u) % 6, t = u - Math.floor(u), w = (1 - Math.cos(Math.PI * t)) / 2; return R0 + (lv[i] * (1 - w) + lv[(i + 1) % 6] * w) * (R1 - R0); };
        const pos = [0, 0, 0], tt = [0], ring = [];
        for (let k = 0; k <= N; k++) { const th = (k / N) * Math.PI * 2, r = rAt(th), x = Math.cos(th) * r, y = Math.sin(th) * r; pos.push(x, y, 0); tt.push(1); ring.push(new T.Vector3(x, y, 0)); }
        const idx = []; for (let k = 1; k <= N; k++) idx.push(0, k, k + 1);
        const geo = new T.BufferGeometry(); geo.setAttribute("position", new T.Float32BufferAttribute(pos, 3)); geo.setAttribute("t", new T.Float32BufferAttribute(tt, 1)); geo.setIndex(idx);
        const fill = new T.Mesh(geo, new T.ShaderMaterial({ uniforms: { uColor: { value: warm }, uOpacity: { value: 0.7 } },
          vertexShader: "attribute float t; varying float vT; void main(){ vT = t; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
          fragmentShader: "uniform vec3 uColor; uniform float uOpacity; varying float vT; void main(){ float a = pow(1.0 - vT, 1.8) * 0.85 + 0.04 + 0.3 * smoothstep(0.8, 1.0, vT); gl_FragColor = vec4(uColor * a * uOpacity, 1.0); }",
          transparent: true, blending: T.AdditiveBlending, depthTest: false, depthWrite: false, side: T.DoubleSide })); fill.renderOrder = 8; g.add(fill);
        line(ring, warm, 0.95);
        lv.forEach((v, i) => { if (v > 0) node(i, R0 + v * (R1 - R0), cp && cp.idx === i ? 0.17 : 0.085, cp && cp.idx === i ? white : warm, 0.95); });
      }
      if (cp) { line(circle(R1, 120), white, 0.24, true); line(circle(R0, 48), white, 0.3);
        [-1, 1].forEach((sg) => { const t = za(cp.idx) + (sg * Math.PI) / 6; line([new T.Vector3(Math.cos(t) * R0, Math.sin(t) * R0, 0), new T.Vector3(Math.cos(t) * R1, Math.sin(t) * R1, 0)], white, 0.14); });
        if (!(lv[cp.idx] > 0)) node(cp.idx, R0, 0.13, white, 0.75); }
      for (let i = 0; i < 6; i++) node(i, R1, cp && cp.idx === i ? 0.075 : 0.045, white, cp ? (cp.idx === i ? 0.95 : 0.35) : 0.3);
      this.gDyn.add(g); }
    _draftBasis(d) { const N = V(d.normal).normalize(), horiz = Math.abs(N.z) > 0.8; let U; if (horiz) { const a = ((d.rot || 0) * Math.PI) / 180; U = new T.Vector3(Math.cos(a), Math.sin(a), 0); } else U = new T.Vector3(-N.y, N.x, 0).normalize(); return { N, U, W: new T.Vector3().crossVectors(N, U).normalize(), horiz }; }
    _draftPlaneHit(cx, cy, d, B, lift) { this.ray.setFromCamera(this._ndc(cx, cy), this.camera); let n = B.N;
      if (lift) { const cd = new T.Vector3(); this.camera.getWorldDirection(cd); n = new T.Vector3(cd.x, cd.y, 0); if (n.lengthSq() < 1e-6) return null; n.normalize(); }
      const out = new T.Vector3(); return this.ray.ray.intersectPlane(new T.Plane().setFromNormalAndCoplanarPoint(n, V(d.pos)), out) ? out : null; }
    _ensureRHandles() { if (this.rh) return; this.rh = {}; this.rhSigns = { c0: [1, 1], c1: [-1, 1], c2: [-1, -1], c3: [1, -1] };
      const mk = (key, cls, title, html) => { const el = document.createElement("div"); el.className = "apt-rhandle " + cls; el.title = title; el.setAttribute("aria-label", title); if (html) el.innerHTML = html; el.style.display = "none"; this.labels.appendChild(el); this.rh[key] = el;
        el.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); const d = this.state.targetDraft; if (!d || !d.pos) return; try { el.setPointerCapture(e.pointerId); } catch (err) { /* */ }
          const d0 = { pos: d.pos.slice(), normal: d.normal.slice(), rot: d.rot || 0, size: (d.size || [0.5, 0.5]).slice() }, B = this._draftBasis(d0); this.rdrag = { key, pid: e.pointerId, d0, B, P0: this._draftPlaneHit(e.clientX, e.clientY, d0, B, key === "lift") }; this.host.classList.add("is-editing-rect"); });
        el.addEventListener("pointermove", (e) => { const rd = this.rdrag; if (!rd || rd.pid !== e.pointerId) return; const patch = this._rectDrag(rd, e.clientX, e.clientY); if (patch) this.cb.onDraftEdit && this.cb.onDraftEdit(patch); });
        const end = (e) => { if (this.rdrag && this.rdrag.pid === e.pointerId) { this.rdrag = null; this.host.classList.remove("is-editing-rect"); } }; el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end); };
      ["c0", "c1", "c2", "c3"].forEach((k) => mk(k, "corner", "Drag to resize")); mk("mid", "mid", "Drag to move");
      mk("lift", "lift", "Drag to raise or lower", '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1.5v9M3.5 4L6 1.5 8.5 4M3.5 8L6 10.5 8.5 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'); }
    _rectDrag(rd, cx, cy) { const d0 = rd.d0, B = rd.B, P = this._draftPlaneHit(cx, cy, d0, B, rd.key === "lift"); if (!P || !rd.P0) return null; const r3 = (v) => Math.round(v * 1000) / 1000, o = V(d0.pos);
      if (rd.key === "lift") { const top = this.site.rooms.reduce((m, r) => Math.max(m, r.h || 0), 2.4); return { pos: [d0.pos[0], d0.pos[1], r3(clamp(d0.pos[2] + (P.z - rd.P0.z), 0, top))] }; }
      if (rd.key === "mid") { const np = o.clone().add(P.clone().sub(rd.P0)); return { pos: [r3(np.x), r3(np.y), r3(np.z)] }; }
      const [su, sw] = this.rhSigns[rd.key], opp = o.clone().addScaledVector(B.U, (-su * d0.size[0]) / 2).addScaledVector(B.W, (-sw * d0.size[1]) / 2), v = P.clone().sub(opp);
      const sg = (x, f) => (Math.abs(x) < 1e-6 ? f : Math.sign(x)); let du = v.dot(B.U), dw = v.dot(B.W); du = sg(du, su) * Math.max(0.05, Math.abs(du)); dw = sg(dw, sw) * Math.max(0.05, Math.abs(dw));
      const c = opp.clone().addScaledVector(B.U, du / 2).addScaledVector(B.W, dw / 2); return { pos: [r3(c.x), r3(c.y), r3(c.z)], size: [Math.round(Math.abs(du) * 100) / 100, Math.round(Math.abs(dw) * 100) / 100] }; }
    _placeRHandles(s) { this._ensureRHandles(); const d = s.targetDraft, on = !!(d && d.pos && d.mode === "editing" && s.showTargetMap && !this.camView);
      if (!on) { Object.values(this.rh).forEach((el) => { el.style.display = "none"; }); return; }
      const B = this._draftBasis(d), o = V(d.pos).addScaledVector(B.N, 0.012), sz = d.size || [0.5, 0.5], surf = d.shape !== "point", at = (el, p, show) => { const q = this._screen(p); el.style.display = show && q.ok ? "" : "none"; el.style.transform = "translate(" + Math.round(q.x) + "px, " + Math.round(q.y) + "px) translate(-50%, -50%)"; };
      Object.entries(this.rhSigns).forEach(([k, [su, sw]]) => at(this.rh[k], o.clone().addScaledVector(B.U, (su * sz[0]) / 2).addScaledVector(B.W, (sw * sz[1]) / 2), surf));
      at(this.rh.mid, o, true); at(this.rh.lift, V(d.pos).add(new T.Vector3(0, 0, 0.32)), B.horiz); }
    _fadeTick() { const now = performance.now(), dt = Math.min(0.1, (now - (this._fadeT || now)) / 1000); this._fadeT = now; if (!this.site) return false;
      const a = 1 - Math.exp(-dt / 0.16); let moving = false; this.shown = this.shown || {};
      this.site.fixtures.forEach((f) => { const r = f.kind === "cylinder" && this._fixState(f.id).channels && this._fixState(f.id).channels.radial; if (!r) return;
        const tg = { rk: r.k }; r.levels.forEach((v, i) => { tg["z" + i] = r.on ? v / 100 : 0; }); const key = this.site.id + ":" + f.id, sh = this.shown[key];
        if (!sh) { this.shown[key] = tg; return; }
        Object.keys(tg).forEach((k) => { const t = tg[k], c = sh[k] == null ? t : sh[k], d = t - c; if (Math.abs(d) > (k === "rk" ? 2 : 0.002)) { sh[k] = c + d * a; moving = true; } else sh[k] = t; }); });
      return moving; }
    _zoneLv(fid) { const sh = this.shown && this.shown[this.site.id + ":" + fid]; if (sh && sh.z0 != null) return [0, 1, 2, 3, 4, 5].map((i) => sh["z" + i]);
      const r = this._fixState(fid).channels && this._fixState(fid).channels.radial; return r ? r.levels.map((v) => (r.on ? v / 100 : 0)) : [0, 0, 0, 0, 0, 0]; }
    _zoneK(fid) { const sh = this.shown && this.shown[this.site.id + ":" + fid]; if (sh && sh.rk != null) return sh.rk; const r = this._fixState(fid).channels && this._fixState(fid).channels.radial; return r ? r.k : 2700; }
    _placeAimUI(s) {
      const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      if (!this.toolPill) {
        const p = document.createElement("div"); p.className = "apt-toolpill"; p.style.display = "none"; p.setAttribute("role", "group"); p.setAttribute("aria-label", "Room control");
        p.innerHTML = '<span class="nm"></span><button type="button" data-t="zones" title="Drag around the light to set its radial zones (Z)">Zones</button><button type="button" data-t="aim" title="Drag the reticle to aim the spot (A)">Spot<i class="pend"></i></button>';
        p.addEventListener("pointerdown", (e) => e.stopPropagation());
        p.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { e.stopPropagation(); this.cb.onRoomTool && this.cb.onRoomTool(b.dataset.t); } });
        this.labels.appendChild(p); this.toolPill = p;
        const c = document.createElement("div"); c.className = "apt-aimbar"; c.style.display = "none"; c.setAttribute("role", "group"); c.setAttribute("aria-label", "Proposed aim");
        c.addEventListener("pointerdown", (e) => e.stopPropagation());
        c.addEventListener("click", (e) => { const b = e.target.closest("button"), sl = this.state.selected; if (b && sl) { e.stopPropagation(); this.cb.onAimConfirm && this.cb.onAimConfirm(sl.id, b.dataset.a); } });
        this.labels.appendChild(c); this.aimBar = c;
      }
      const P = this.toolPill, B = this.aimBar, sel = s.selected || {}, f = s.roomTool && sel.kind === "fixture" && this.site.fixtures.find((x) => x.id === sel.id);
      if (f && !this.camView && !this.compose && !this.drag) { const q = this._screen(f.pos); P.style.display = q.ok ? "" : "none"; P.querySelector(".nm").textContent = f.short || "";
        P.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.t === s.roomTool))); P.classList.toggle("has-pending", !!s.pendingAim && s.roomTool !== "aim");
        P.style.transform = "translate(" + Math.round(q.x) + "px, " + Math.round(q.y - 26) + "px) translate(-50%, -100%)"; } else P.style.display = "none";
      const pr = s.proposed;
      if (pr && pr.point && s.aimFixture && !s.setup && !this.drag && !this.camView && sel.kind === "fixture") { const q = this._screen(pr.point); if (!q.ok) { B.style.display = "none"; return; }
        const ok = pr.valid !== false, name = pr.snapName || pr.surface || "this spot", key = [ok, name, pr.reason, pr.shadow && pr.shadow.part].join("|");
        if (B._k !== key) { B._k = key; B.classList.toggle("bad", !ok);
          B.innerHTML = ok ? '<span class="t">' + esc(name) + (pr.shadow ? "<em>your " + esc(pr.shadow.part) + " shadows it</em>" : "") + '</span><button type="button" class="set" data-a="set">Set aim</button><button type="button" data-a="discard">Discard</button>'
            : '<span class="t">' + (pr.reason === "blocked" ? "Blocked" : "Out of reach") + '</span><button type="button" data-a="discard">Discard</button>'; }
        B.style.display = ""; B.style.transform = "translate(" + Math.round(clamp(q.x, 110, this.size.w - 110)) + "px, " + Math.round(clamp(q.y + 38, 40, this.size.h - 70)) + "px) translate(-50%, 0)";
      } else B.style.display = "none";
    }
    _planeAt(cx, cy, z) { this.ray.setFromCamera(this._ndc(cx, cy), this.camera); const r = this.ray.ray; if (Math.abs(r.direction.z) < 1e-4) return null; const t = (z - r.origin.z) / r.direction.z; return t > 0 ? r.origin.clone().addScaledVector(r.direction, t) : null; }
    _composeAt(cx, cy) { const zf = this.state.zoneFixture && this.site.fixtures.find((x) => x.id === this.state.zoneFixture); if (!zf) return null; const p = this._planeAt(cx, cy, zf.pos[2]); if (!p) return null;
      const dx = p.x - zf.pos[0], dy = p.y - zf.pos[1], r = Math.hypot(dx, dy), a0 = ((zf.radialAnchor || 0) * Math.PI) / 180;
      return { fid: zf.id, idx: ((Math.round((Math.atan2(dy, dx) - a0) / (Math.PI / 3)) % 6) + 6) % 6, r, level: Math.round(clamp((r - 0.14) / (1.3 - 0.14), 0, 1) * 100) }; }
    _petalR(fid, i) { const st = this._fixState(fid), rad = st.channels && st.channels.radial; return 0.14 + (rad && rad.on ? rad.levels[i] / 100 : 0) * (1.3 - 0.14); }
    _composeStartable(e) { const s = this.state, zf = s.zoneFixture && this.site.fixtures.find((x) => x.id === s.zoneFixture); if (!zf || this.camView || s.setup || s.tapAim || s.placing) return null;
      const rect = this.canvas.getBoundingClientRect(), k = rect.width / this.size.w, q = this._screen(zf.pos); if (!q.ok) return null; const h = this._composeAt(e.clientX, e.clientY); if (!h) return null;
      if (Math.hypot(e.clientX - (rect.left + q.x * k), e.clientY - (rect.top + q.y * k)) <= 34) return h;
      return h.r <= Math.min(1.42, this._petalR(zf.id, h.idx) + 0.12) && h.r > 0.14 + 0.02 && this._petalR(zf.id, h.idx) > 0.15 ? h : null; }
    _composeMove(e) { const cp = this.compose, rect = this.canvas.getBoundingClientRect(); cp.screen = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      const h = this._composeAt(e.clientX, e.clientY); if (!h || h.fid !== cp.fid) { this._placeLabels(); return; }
      if (!cp.engaged) { const side = Math.sign(h.r - this._petalR(cp.fid, h.idx));
        if (h.idx === cp.startIdx && (side === 0 || side === cp.side)) { this._placeLabels(); return; }
        cp.engaged = true; cp.idx = h.idx; cp.level = h.level; }
      const patch = {}, steps = ((h.idx - cp.idx + 9) % 6) - 3, n = Math.abs(steps);
      for (let k = 1; k < n; k++) { const j = (((cp.idx + Math.sign(steps) * k) % 6) + 6) % 6; patch[j] = Math.round(cp.level + ((h.level - cp.level) * k) / n); }
      patch[h.idx] = h.level; cp.idx = h.idx; cp.level = h.level; this.cb.onCompose && this.cb.onCompose(cp.fid, patch, "move"); }
    _endCompose() { const cp = this.compose; if (!cp) return; this.compose = null; this.host.classList.remove("is-composing"); this.canvas.style.cursor = ""; try { this.canvas.releasePointerCapture(cp.pid); } catch (err) { /* */ }
      this.cb.onCompose && this.cb.onCompose(cp.fid, null, "end"); this._dynamic(); this._placeLabels(); }
    _bodyCaps() { const b = this.state.body; if (!b || !b.visible || !b.joints) return []; const J = b.joints, inf = new Set(b.inferred || []);
      const head = J.nose ? V(J.nose).add(new T.Vector3(0, -0.06, 0.03)) : null;
      const seg = [["torso", "left_shoulder", "left_hip", 0.13], ["torso", "right_shoulder", "right_hip", 0.13], ["shoulder", "left_shoulder", "right_shoulder", 0.07], ["arm", "left_shoulder", "left_elbow", 0.05], ["forearm", "left_elbow", "left_wrist", 0.045], ["arm", "right_shoulder", "right_elbow", 0.05], ["forearm", "right_elbow", "right_wrist", 0.045], ["thigh", "left_hip", "left_knee", 0.075], ["thigh", "right_hip", "right_knee", 0.075], ["leg", "left_knee", "left_ankle", 0.055], ["leg", "right_knee", "right_ankle", 0.055]];
      const caps = seg.filter(([, a, c]) => J[a] && J[c]).map(([part, a, c, r]) => ({ part, a: V(J[a]), b: V(J[c]), r, inferred: inf.has(a) || inf.has(c) }));
      if (head) caps.push({ part: "head", a: head, b: head.clone(), r: 0.12, inferred: false }); return caps; }
    _segDist(p1, q1, p2, q2) { const d1 = q1.clone().sub(p1), d2 = q2.clone().sub(p2), r = p1.clone().sub(p2), a = d1.dot(d1), e = d2.dot(d2), f = d2.dot(r); let s, t;
      if (e <= 1e-9) { t = 0; s = clamp(-d1.dot(r) / a, 0, 1); } else { const c = d1.dot(r), b = d1.dot(d2), den = a * e - b * b; s = den !== 0 ? clamp((b * f - c * e) / den, 0, 1) : 0; t = (b * s + f) / e; if (t < 0) { t = 0; s = clamp(-c / a, 0, 1); } else if (t > 1) { t = 1; s = clamp((b - c) / a, 0, 1); } }
      const c1 = p1.clone().addScaledVector(d1, s), c2 = p2.clone().addScaledVector(d2, t); return { d: c1.distanceTo(c2), c: c1 }; }
    _bodyBlock(o, p) { let best = null; const end = p.clone().addScaledVector(p.clone().sub(o).normalize(), -0.03);
      this._bodyCaps().forEach((k) => { const r = this._segDist(o, end, k.a, k.b); if (r.d < k.r && (!best || r.d - k.r < best.m)) best = { part: k.part, inferred: k.inferred, point: [r.c.x, r.c.y, r.c.z], m: r.d - k.r }; }); return best; }
    _litApply(on) { const R = this.renderer; R.shadowMap.enabled = on; R.shadowMap.type = T.PCFShadowMap; R.physicallyCorrectLights = on; R.toneMapping = on ? T.ACESFilmicToneMapping : T.NoToneMapping; R.toneMappingExposure = on ? 0.038 : 1.0; R.outputEncoding = on ? T.sRGBEncoding : T.LinearEncoding;
      this.scene.children.forEach((o) => { if (o.isHemisphereLight) o.intensity = on ? 1.2 : 0.9; if (o.isDirectionalLight) o.intensity = on ? 0 : 0.5; });
      (this.surfaces || []).forEach((m) => { if (!m.userData.lambert) m.userData.lambert = m.material; const k = m.userData.kind;
        if (on) { if (!m.userData.std) m.userData.std = new T.MeshStandardMaterial({ color: k === "floor" ? 0x6e5c4a : k === "wall" ? 0xd2cdc4 : k === "art" ? 0x8c5a3c : 0x7d7368, roughness: k === "art" ? 0.55 : 0.88, metalness: 0, side: T.DoubleSide, transparent: k === "wall" }); m.material = m.userData.std; m.castShadow = k !== "floor"; m.receiveShadow = true; }
        else { m.material = m.userData.lambert; m.castShadow = false; m.receiveShadow = false; } });
      this.scene.traverse((o) => { if (o.material && !Array.isArray(o.material)) o.material.needsUpdate = true; }); this.litOn = on;
      if (!on && this.gLit) { this.scene.remove(this.gLit); this.gLit = null; this.litKey = null; } }
    _syncLit(s) { const lit = s.mode === "lit"; if (lit !== !!this.litOn) this._litApply(lit); if (!lit || !this.site) return;
      if (this.litKey !== this.site.id) { if (this.gLit) this.scene.remove(this.gLit); this.gLit = new T.Group(); this.scene.add(this.gLit); this.litKey = this.site.id; this.litMap = {};
        const mk = (angle, pen, shadow) => { const L = new T.SpotLight(0xffffff, 0, 0, angle, pen, 2); L.castShadow = shadow; if (shadow) { L.shadow.mapSize.set(1024, 1024); L.shadow.camera.near = 0.05; L.shadow.camera.far = 9; L.shadow.bias = -0.0004; L.shadow.normalBias = 0.02; } this.gLit.add(L); this.gLit.add(L.target); return L; };
        this.site.fixtures.forEach((f) => { const o = V(f.pos), e = {};
          if (f.engineered) { e.spot = mk((f.engineered.fwhm * 0.8 * Math.PI) / 180, 0.55, true); e.spot.position.copy(o); e.spot.shadow.radius = 2.5; }
          if (f.kind === "cylinder") { e.down = mk(1.0, 1, true); e.down.shadow.radius = 6; e.down.position.copy(o).add(new T.Vector3(0, 0, -0.07)); e.down.target.position.copy(o).add(new T.Vector3(0, 0, -2));
            e.rz = this.zoneDirs(f).map((d) => { const L = mk(0.95, 1, false); L.position.copy(o).addScaledVector(d, 0.05); L.target.position.copy(o).addScaledVector(d, 2).add(new T.Vector3(0, 0, -0.35)); return L; }); }
          else if (f.kind === "ceiling") { e.main = mk(1.05, 1, false); e.main.position.copy(o); e.main.target.position.copy(o).add(new T.Vector3(0, 0, -2)); }
          else { e.main = new T.PointLight(0xffffff, 0, 0, 2); e.main.position.copy(o); this.gLit.add(e.main); }
          this.litMap[f.id] = e; });
        this.vpl = Array.from({ length: 8 }, () => { const L = new T.SpotLight(0xffffff, 0, 4.5, 1.45, 1, 2); this.gLit.add(L); this.gLit.add(L.target); return L; });
        }
      const col = (k) => { const c = kelvin(k); return new T.Color(c[0], c[1], c[2]); }; const LM = { spot: 450, down: 350, zone: 120, ceiling: 800, plug: 400 }, cd = (lm, a) => lm / (2 * Math.PI * (1 - Math.cos(a)));
      this.site.fixtures.forEach((f) => { const e = this.litMap[f.id], st = this._fixState(f.id), ch = st.channels || {}; if (!e) return;
        if (e.spot) { const sp = ch.spot || {}; e.spot.intensity = sp.on ? (sp.bri / 100) * cd(LM.spot, (f.engineered.fwhm * 0.8 * Math.PI) / 180) : 0; e.spot.color.copy(col(sp.k)); e.spot.target.position.copy(st.aim ? this._displayAim(f.id) : V(f.pos).add(new T.Vector3(0, 0, -1))); }
        if (e.down) { const d = ch.down || {}; e.down.intensity = d.on ? (d.bri / 100) * cd(LM.down, 1.0) : 0; e.down.color.copy(col(d.k)); }
        if (e.rz) { const zl = this._zoneLv(f.id), zk = this._zoneK(f.id); e.rz.forEach((L, i) => { L.intensity = zl[i] * cd(LM.zone, 0.95); L.color.copy(col(zk)); }); }
        if (e.main) { e.main.intensity = st.on ? (f.kind === "plug" ? LM.plug / (4 * Math.PI) : ((st.bri || 50) / 100) * cd(LM.ceiling, 1.05)) : 0; e.main.color.copy(col(st.k || 2700)); } });
      if (this.vpl) { const src = [], Pv = (v) => (v && v.isVector3 ? v.clone() : V(v)), add = (p, n, flux, k, alb) => { if (flux > 1 && p && n) src.push({ p: Pv(p), n: Pv(n).normalize(), fr: flux * alb, k: k || 2700 }); };
        this.site.fixtures.forEach((f) => { const st = this._fixState(f.id), ch = st.channels || {}, o = V(f.pos);
          if (f.engineered && ch.spot && ch.spot.on && st.aim) { const n = V(st.aim.normal); add(this._displayAim(f.id), n, ((LM.spot * ch.spot.bri) / 100) * 0.8, ch.spot.k, Math.abs(n.z) > 0.8 ? 0.35 : 0.6); }
          if (f.kind === "cylinder") { if (ch.down && ch.down.on) { const h = this._downHit(o); add(h.point, h.normal, ((LM.down * ch.down.bri) / 100) * 0.8, ch.down.k, 0.35); }
            const zl = this._zoneLv(f.id), zk = this._zoneK(f.id), fz = this._zoneFacingCached(f.id); zl.forEach((l, i) => { const z = fz[i]; if (l > 0 && z && z.point) add(z.point, z.normal, LM.zone * l * 0.7, zk, z.kind === "opening" ? 0.08 : z.kind === "wall" ? 0.65 : 0.4); }); }
          else if (f.kind === "ceiling" && st.on) { const h = this._downHit(o); add(h.point, h.normal, ((LM.ceiling * (st.bri || 50)) / 100) * 0.8, st.k, 0.3); } });
        src.sort((a, b) => b.fr - a.fr); const tint = new T.Color(1, 0.96, 0.9);
        this.vpl.forEach((L, i) => { const v = src[i]; if (!v) { L.intensity = 0; return; } L.position.copy(v.p).addScaledVector(v.n, 0.25); L.target.position.copy(L.position).add(v.n); L.color.copy(col(v.k)).multiply(tint); L.intensity = (v.fr / Math.PI) * 0.9; }); }
    }
    _capsule(r, L, mat) { if (L < 1e-3) return new T.Mesh(new T.SphereGeometry(r, 18, 14), mat); if (T.CapsuleGeometry) return new T.Mesh(new T.CapsuleGeometry(r, L, 6, 14), mat);
      const g = new T.Group(); g.add(new T.Mesh(new T.CylinderGeometry(r, r, L, 14), mat)); [-1, 1].forEach((sg) => { const q = new T.Mesh(new T.SphereGeometry(r, 14, 10), mat); q.position.y = (sg * L) / 2; g.add(q); }); return g; }
    _syncBodyVolume(s) { if (!this.gVol) { this.gVol = new T.Group(); this.scene.add(this.gVol); } const b = s.body, lit = s.mode === "lit";
      const key = this.site ? [this.site.id, s.mode, b && b.visible ? JSON.stringify(b.joints) : "", b && b.dim ? 1 : 0].join("|") : ""; this.gVol.visible = !!(b && b.visible) && !this.camView; if (key === this.volKey) return; this.volKey = key;
      this.gVol.clear(); if (!b || !b.visible) return;
      const mat = lit ? new T.MeshStandardMaterial({ color: 0xaeb4bc, roughness: 0.8, metalness: 0, transparent: !!b.dim, opacity: b.dim ? 0.55 : 1 }) : new T.MeshBasicMaterial({ color: 0xdde6f5, transparent: true, opacity: b.dim ? 0.05 : s.mode === "mesh" ? 0.16 : 0.1, depthWrite: false });
      this._bodyCaps().forEach((k) => { const L = k.a.distanceTo(k.b), m = this._capsule(k.r, L, mat); m.position.copy(k.a).lerp(k.b, 0.5); if (L >= 1e-3) m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), k.b.clone().sub(k.a).normalize());
        m.traverse((o) => { if (o.isMesh) { o.castShadow = lit; o.receiveShadow = lit; o.renderOrder = 2; } }); this.gVol.add(m); }); }
    _drawShadowHit(s) { const p = s.proposed; if (!p || !p.shadow || !p.shadow.point || this.camView) return; const m = new T.Mesh(new T.SphereGeometry(0.045, 16, 12), new T.MeshBasicMaterial({ color: 0xffb45f, transparent: true, opacity: 0.95, depthTest: false })); m.position.copy(V(p.shadow.point)); m.renderOrder = 6; this.gDyn.add(m); }
    _bodyBuild(b) {
      const key = b ? JSON.stringify([b.layers, b.dim, b.visible]) : "none"; if (key === this.bodyKey) return; this.bodyKey = key; this.gBody.clear();
      if (!b || !b.visible) return; const J = b.joints, L = b.layers || {}, inf = new Set(b.inferred || []); const dim = b.dim ? 0.45 : 1;
      const jm = new T.MeshBasicMaterial({ color: 0xeef4ff, transparent: true, opacity: dim }); const ji = new T.MeshBasicMaterial({ color: 0xb8d8ff, transparent: true, opacity: 0.55 * dim, wireframe: true });
      window.APT_DATA.COCO.forEach((k) => {
        const p = J[k]; if (!p) return; const isInf = inf.has(k);
        if (isInf ? L.inferred : L.observed) { const m = new T.Mesh(new T.SphereGeometry(isInf ? 0.026 : 0.02, 12, 8), isInf ? ji : jm); m.position.copy(V(p)); this.gBody.add(m); }
        if (L.uncertainty) { const sg = isInf ? 0.16 : 0.045; const m = new T.Mesh(new T.SphereGeometry(sg, 16, 12), new T.MeshBasicMaterial({ color: 0xb8d8ff, transparent: true, opacity: 0.07 * dim, depthWrite: false })); m.position.copy(V(p)); this.gBody.add(m); }
      });
      window.APT_DATA.BONES.forEach(([a, c]) => {
        if (!J[a] || !J[c]) return; const isInf = inf.has(a) || inf.has(c); if (isInf ? !L.inferred : !L.observed) return;
        const g = new T.BufferGeometry().setFromPoints([V(J[a]), V(J[c])]);
        const l = new T.Line(g, isInf ? new T.LineDashedMaterial({ color: 0xb8d8ff, dashSize: 0.03, gapSize: 0.025, transparent: true, opacity: 0.7 * dim }) : new T.LineBasicMaterial({ color: 0xeef4ff, transparent: true, opacity: 0.9 * dim }));
        if (isInf) l.computeLineDistances(); this.gBody.add(l);
      });
      if (L.volume) {
        const segs = [["left_shoulder", "left_hip", 0.13], ["right_shoulder", "right_hip", 0.13], ["left_shoulder", "left_elbow", 0.05], ["left_elbow", "left_wrist", 0.04], ["right_shoulder", "right_elbow", 0.05], ["right_elbow", "right_wrist", 0.04], ["left_hip", "left_knee", 0.07], ["right_hip", "right_knee", 0.07], ["left_knee", "left_ankle", 0.05], ["right_knee", "right_ankle", 0.05]];
        const vm = new T.MeshBasicMaterial({ color: 0xb8d8ff, transparent: true, opacity: 0.09 * dim, depthWrite: false });
        segs.forEach(([a, c, rad]) => { const A = V(J[a]), C = V(J[c]); const m = new T.Mesh(new T.CylinderGeometry(rad, rad, A.distanceTo(C), 14, 1, false), vm); m.position.copy(A).lerp(C, 0.5); m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), C.clone().sub(A).normalize()); this.gBody.add(m); });
        const head = new T.Mesh(new T.SphereGeometry(0.11, 16, 12), vm); head.position.copy(V(J.nose)).add(new T.Vector3(0, -0.06, 0.02)); this.gBody.add(head);
      }
    }

    /* ---------- camera ---------- */
    resize() {
      const w = this.host.clientWidth || 1, h = this.host.clientHeight || 1; this.renderer.setSize(w, h, false);
      this.canvas.style.width = w + "px"; this.canvas.style.height = h + "px"; this.camera.aspect = w / h;
      const sft = Math.round(((this.insets.bottom || 0) - (this.insets.top || 0)) / 2);
      if (sft !== 0 && !this.camView) this.camera.setViewOffset(w, h + 2 * Math.abs(sft), 0, sft > 0 ? 2 * sft : 0, w, h); else this.camera.clearViewOffset();
      this.camera.updateProjectionMatrix(); this.size = { w, h };
    }
    fit(instant) {
      if (!this.site) return; const b = this._bounds(); const w = b.x1 - b.x0, d = b.y1 - b.y0; const diag = Math.hypot(w, d) * 0.5;
      const vis = (this.size.h - (this.insets.bottom || 0) - (this.insets.top || 0)) / this.size.h; const fovV = (this.camera.fov * Math.PI) / 180;
      const fovH = 2 * Math.atan(Math.tan(fovV / 2) * this.camera.aspect); const r = Math.max((diag * 1.18) / Math.tan(fovV / 2) / Math.max(0.45, vis), (diag * 1.2) / Math.tan(fovH / 2));
      this.camView = null; this.userMoved = false; this._goal({ az: this.site.id === "echo" ? -1.95 : -2.3, el: 0.84, r: clamp(r, 3, 60), t: new T.Vector3(b.cx, b.cy, 0.9) }, instant);
    }
    _goal(g, instant) { if (instant || REDUCED) { Object.assign(this.orbit, { az: g.az, el: g.el, r: g.r }); this.orbit.t.copy(g.t); this.orbit.goal = null; } else this.orbit.goal = { ...g, t: g.t.clone() }; }
    orbitStep(dir) { this.userMoved = true; const k = Math.round(this.orbit.az / (Math.PI / 4)) + dir; this._goal({ az: k * (Math.PI / 4), el: this.orbit.el, r: this.orbit.r, t: this.orbit.t }); }
    tilt(dir) { this._goal({ az: this.orbit.az, el: clamp(this.orbit.el + dir * 0.12, 0.2, 1.45), r: this.orbit.r, t: this.orbit.t }); }
    zoomStep(dir) { this.userMoved = true; this._goal({ az: this.orbit.az, el: this.orbit.el, r: clamp(this.orbit.r * (dir > 0 ? 0.82 : 1.22), 2.2, 70), t: this.orbit.t }); }
    focus(point) { this._goal({ az: this.orbit.az, el: this.orbit.el, r: clamp(this.orbit.r * 0.6, 3, 12), t: V(point).setZ(0.8) }); }
    detent() { return ((Math.round(this.orbit.az / (Math.PI / 4)) % 8) + 8) % 8; }
    _goCameraView(id) {
      const c = id && this.site && this.site.cameras.find((x) => x.id === id);
      if (!c) { this.camView = null; this.camera.fov = 40; this.resize(); this.fit(); return; }
      const { p, look } = this._camPose(c); this.camView = { from: this.camera.position.clone(), fromLook: this.orbit.t.clone(), to: p, look, t0: performance.now(), fov: c.vfov };
      this.camera.clearViewOffset(); this.camera.updateProjectionMatrix();
    }
    frame() {
      if (!this.site) { this.renderer.render(this.scene, this.camera); return; }
      const o = this.orbit, cam = this.camera;
      if (this.camView) {
        const cv = this.camView, k = REDUCED ? 1 : clamp((performance.now() - cv.t0) / 900, 0, 1), e = 1 - Math.pow(1 - k, 3);
        cam.position.copy(cv.from).lerp(cv.to, e); cam.fov = 40 + (cv.fov - 40) * e; cam.updateProjectionMatrix(); cam.lookAt(cv.fromLook.clone().lerp(cv.look, e));
      } else {
        if (o.goal) { const g = o.goal, f = 0.14; o.az += (g.az - o.az) * f; o.el += (g.el - o.el) * f; o.r += (g.r - o.r) * f; o.t.lerp(g.t, f); if (Math.abs(g.az - o.az) + Math.abs(g.el - o.el) + Math.abs(g.r - o.r) < 0.002 && o.t.distanceTo(g.t) < 0.002) o.goal = null; }
        cam.position.set(o.t.x + o.r * Math.cos(o.el) * Math.cos(o.az), o.t.y + o.r * Math.cos(o.el) * Math.sin(o.az), o.t.z + o.r * Math.sin(o.el)); cam.lookAt(o.t);
      }
      cam.updateMatrixWorld(); this.mat.uniforms.uCam.value.copy(cam.position); this.mat.uniforms.uCut.value = this.cloudCut && !this.camView ? 1 : 0;
      this.mat.uniforms.uSize.value = this.state.mode === "mesh" ? 0.6 : 1;
      const lit = this.state.mode === "lit", mesh = this.state.mode === "mesh" || lit;
      this.walls.forEach((w) => { const f = w.userData.outward.dot(cam.position.clone().sub(w.userData.center).normalize()); const cut = !this.camView && f > 0.05; w.userData.cut = cut; w.material.opacity = cut ? 0.03 : lit ? 1 : 0.62; w.material.visible = mesh && !(lit && cut); });
      const fading = this._fadeTick(); if (Object.keys(this.aimAnim).length || fading) { this._dynamic(); if (fading) this._styleFixtures(); if (this.litOn) this._syncLit(this.state); }
      this.renderer.render(this.scene, cam); this._placeLabels();
    }
    _screen(p) { const v = (p.isVector3 ? p.clone() : V(p)).project(this.camera); return { x: (v.x * 0.5 + 0.5) * this.size.w, y: (-v.y * 0.5 + 0.5) * this.size.h, ok: v.z < 1 && v.z > -1 }; }
    _placeLabels() {
      const s = this.state, sel = s.selected || {}, hov = s.hover || {}, few = this.site.fixtures.length <= 6, always = s.layers && s.layers.labels === "always";
      const placed = []; let bodyBox = null;
      const b = s.body; if (b && b.visible && b.joints && b.joints.nose && !this.camView) {
        const q = this._screen([b.joints.nose[0], b.joints.nose[1], b.joints.nose[2] + 0.28]); this.bodyLabel.style.display = q.ok ? "" : "none"; this.bodyLabel.textContent = b.label || ""; this.bodyLabel.title = b.title || "";
        this.bodyLabel.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(q.y)}px) translate(-50%, -100%)`; this.bodyLabel.classList.toggle("is-dim", !!b.dim);
        if (q.ok) { const w = this.bodyLabel.offsetWidth || 150; bodyBox = { x0: q.x - w / 2 - 6, x1: q.x + w / 2 + 6, y0: q.y - 30, y1: q.y + 2 }; }
      } else this.bodyLabel.style.display = "none";
      this.site.fixtures.forEach((f) => {
        const el = this.flabels[f.id]; const q = this._screen([f.pos[0], f.pos[1], f.pos[2] + 0.02]);
        const isSel = sel.kind === "fixture" && sel.id === f.id, isHov = hov.kind === "fixture" && hov.id === f.id;
        const vis = q.ok && !this.camView && (few || always || isSel || isHov) && !(s.roomTool && isSel);
        el.style.display = vis ? "" : "none"; if (!vis) return;
        el.querySelector(".apt-flabel-text").textContent = isSel || isHov || !few ? f.name : f.short;
        el.classList.toggle("is-sel", isSel); el.classList.toggle("is-hov", isHov);
        const st = this._fixState(f.id); el.classList.toggle("is-on", !!(st.on || (st.channels && Object.values(st.channels).some((c) => c && c.on))));
        let y = q.y - 22; placed.forEach((p) => { if (Math.abs(p.x - q.x) < 90 && Math.abs(p.y - y) < 20) y = p.y - 20; });
        const lw = el.offsetWidth || 60; if (bodyBox && q.x + lw / 2 > bodyBox.x0 && q.x - lw / 2 < bodyBox.x1 && y + 12 > bodyBox.y0 && y - 12 < bodyBox.y1) y = bodyBox.y0 - 14;
        placed.push({ x: q.x, y });
        el.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(y)}px) translate(-50%, -50%)`;
      });
      const aiming = !!(this.drag || s.tapAim);
      (this.targets || this.site.targets).forEach((t) => {
        const el = this.tlabels[t.id]; const q = this._screen(t.pos); const isSel = sel.kind === "target" && sel.id === t.id; const isHov = hov.kind === "target" && hov.id === t.id;
        const vis = q.ok && !this.camView && (aiming || isSel || isHov || (s.layers && s.layers.targets === "always")) && !(s.targetDraft && (s.targetDraft.id === t.id || (s.targetDraft.pos && V(s.targetDraft.pos).distanceTo(V(t.pos)) < 0.4)));
        el.style.display = vis ? "" : "none"; if (vis) { el.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(q.y + 16)}px) translate(-50%, 0)`; el.classList.toggle("is-sel", isSel || isHov); }
      });
      this.site.cameras.forEach((c) => { const el = this.camLabels[c.id]; const q = this._screen(c.pos); const vis = q.ok && !this.camView && s.layers && s.layers.cameras && !s.showDevices; el.style.display = vis ? "" : "none"; if (vis) el.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(q.y - 14)}px) translate(-50%, -100%)`; });
      const af = s.aimFixture && this.site.fixtures.find((x) => x.id === s.aimFixture); const ret = this.reticle;
      if (af && af.engineered && !this.camView && !s.tapAim && !s.setup) {
        const live = this.drag && this.drag.hit && this.drag.hit.point ? this.drag.hit : null; const pt = live ? live.point : s.proposed && s.proposed.point ? s.proposed.point : this._fixState(af.id).aim ? this._displayAim(af.id) : [af.pos[0], af.pos[1], 0];
        const q = this._screen(pt); ret.style.display = q.ok ? "" : "none"; ret.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(q.y)}px) translate(-50%, -50%)`;
        ret.classList.toggle("is-proposed", !!(live || (s.proposed && s.proposed.point))); ret.classList.toggle("is-invalid", !!((live && live.valid === false) || (!live && s.proposed && s.proposed.valid === false)));
      } else ret.style.display = "none";
      if (this.drag && this.drag.hit && this.drag.screen) {
        const h = this.drag.hit; this.chip.style.display = ""; this.chip.className = "apt-dragchip" + (h.valid === false ? " is-invalid" : "");
        this.chip.innerHTML = h.valid === false ? `<b>${h.message}</b>` : `<b>${h.snapName ? "Snaps to “" + h.snapName + "”" : h.surface}</b><span>${h.dist.toFixed(2)} m · ${Math.round(h.tilt)}° tilt</span>${h.shadow ? '<span style="color:#ffb45f">Your ' + h.shadow.part + ' shadows it</span>' : ""}`;
        this.chip.style.transform = `translate(${Math.round(this.drag.screen.x + 18)}px, ${Math.round(this.drag.screen.y + 18)}px)`;
      } else this.chip.style.display = "none";
      if (s.placing && this.placeHover && this.placeScreen) { const h = this.placeHover; this.chip.style.display = ""; this.chip.className = "apt-dragchip"; this.chip.innerHTML = `<b>${h.name || h.surface}</b><span>${h.horizontal ? "flat" : "vertical"} · click to place</span>`; this.chip.style.transform = `translate(${Math.round(this.placeScreen.x + 18)}px, ${Math.round(this.placeScreen.y + 18)}px)`; }
      const dl = this.draftLabel || (this.draftLabel = Object.assign(document.createElement("div"), { className: "apt-tlabel is-sel" })); if (!dl.parentNode) this.labels.appendChild(dl);
      const dr = s.targetDraft; if (dr && dr.pos && !this.camView) { const q = this._screen(dr.pos); dl.style.display = q.ok ? "" : "none"; dl.textContent = dr.name || "New named aim"; dl.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(q.y + 16)}px) translate(-50%, 0)`; } else dl.style.display = "none";
      this._placeRHandles(s); this._placeAimUI(s);
      this.devLabels = this.devLabels || {}; const shown = new Set(); const dd = s.devDraft;
      if (s.showDevices && !this.camView) (s.cams || []).concat(dd && dd.pos && !dd.id ? [{ ...dd, id: "__draft" }] : []).forEach((d) => { let el = this.devLabels[d.id]; if (!el || !el.parentNode) { el = document.createElement("div"); el.className = "apt-dlabel"; this.labels.appendChild(el); this.devLabels[d.id] = el; }
        const src = dd && (dd.id === d.id || d.id === "__draft") ? dd : d; if (!src.pos) return; const q = this._screen([src.pos[0], src.pos[1], src.pos[2] + 0.12]); el.style.display = q.ok ? "" : "none"; el.textContent = src.name; el.classList.toggle("is-sel", src === dd); el.style.transform = `translate(${Math.round(q.x)}px, ${Math.round(q.y)}px) translate(-50%, -100%)`; shown.add(d.id); });
      Object.entries(this.devLabels).forEach(([k, el]) => { if (!shown.has(k)) el.style.display = "none"; });
      if (this.compose && this.compose.screen) { const cp = this.compose, st = this._fixState(cp.fid), rad = st.channels && st.channels.radial, lv = rad && rad.on ? rad.levels[cp.idx] : 0;
        this.chip.style.display = ""; this.chip.className = "apt-dragchip"; this.chip.innerHTML = "<b>Zone " + (cp.idx + 1) + "</b><span>" + (lv ? lv + "%" : "Off") + "</span>"; this.chip.style.transform = "translate(" + Math.round(cp.screen.x + 18) + "px, " + Math.round(cp.screen.y + 18) + "px)"; }
    }

    /* ---------- picking ---------- */
    _ndc(cx, cy) { const r = this.canvas.getBoundingClientRect(); return new T.Vector2(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1); }
    _pick(cx, cy) {
      this.ray.setFromCamera(this._ndc(cx, cy), this.camera);
      const fh = this.ray.intersectObjects(this.hitSpheres, false)[0]; if (fh) return { kind: "fixture", id: fh.object.userData.fixtureId };
      const th = this.ray.intersectObjects(this.targetHits, false)[0]; const sh = this._solid(this.ray.intersectObjects(this.surfaces.filter((m) => !m.userData.cut), false))[0];
      if (th && (!sh || th.distance <= sh.distance + 0.05)) return { kind: "target", id: th.object.userData.targetId };
      return null;
    }
    aimHit(fixtureId, cx, cy) {
      const f = this.site.fixtures.find((x) => x.id === fixtureId); if (!f || !f.engineered) return null;
      this.ray.setFromCamera(this._ndc(cx, cy), this.camera);
      const h = this._solid(this.ray.intersectObjects(this.surfaces.filter((m) => !m.userData.cut), false))[0];
      if (!h) return { valid: false, reason: "none", message: "No surface here" };
      const o = V(f.pos), p = h.point.clone(); const n = h.face.normal.clone().transformDirection(h.object.matrixWorld).normalize();
      const dir = p.clone().sub(o); const dist = dir.length(); dir.normalize(); if (n.dot(dir) > 0) n.negate();
      const tilt = (Math.acos(clamp(-dir.z, -1, 1)) * 180) / Math.PI; const pan = (Math.atan2(dir.y, dir.x) * 180) / Math.PI;
      const base = { point: [p.x, p.y, p.z], normal: [n.x, n.y, n.z], surface: h.object.userData.short || h.object.userData.name, surfaceId: h.object.userData.id, kind: h.object.userData.kind, dist, tilt, pan };
      if (tilt > f.engineered.tiltLimit) return { ...base, valid: false, reason: "reach", message: `Out of reach · ${Math.round(tilt)}° from straight down, limit ${f.engineered.tiltLimit}°` };
      this.ray2.set(o, dir); this.ray2.far = dist - 0.04;
      const occ = this._solid(this.ray2.intersectObjects(this.surfaces.filter((m) => m !== h.object && m.userData.kind !== "floor"), false))[0];
      if (occ) return { ...base, valid: false, reason: "blocked", message: `Blocked by ${occ.object.userData.short || occ.object.userData.name}`, blockerPoint: [occ.point.x, occ.point.y, occ.point.z] };
      let snap = null; (this.targets || this.site.targets).forEach((t) => { const tp = V(t.pos), tn = V(t.normal); if (tn.dot(n) < 0.9) return; const dd = tp.distanceTo(p); if (dd < Math.max(0.22, Math.max(t.size[0], t.size[1]) * 0.42) && (!snap || dd < snap.d)) snap = { t, d: dd }; });
      if (snap) { const tp = snap.t.pos; return { ...base, point: tp.slice(), normal: snap.t.normal.slice(), valid: true, snapId: snap.t.id, snapName: snap.t.name, dist: V(tp).distanceTo(o), shadow: this._bodyBlock(o, V(tp)) }; }
      const grazing = Math.acos(clamp(n.dot(dir.clone().negate()), -1, 1)) * 180 / Math.PI > 80;
      return { ...base, valid: true, grazing, shadow: this._bodyBlock(o, p) };
    }
    _solid(hits) { return hits.filter((o) => !(o.object.userData.kind === "wall" && this._inOpening(o.point.x, o.point.y, o.point.z))); }
    checkAim(fixtureId, point) {
      const f = this.site && this.site.fixtures.find((x) => x.id === fixtureId); if (!f || !f.engineered || !point) return { valid: false, reason: "none", message: "No destination" };
      const o = V(f.pos), p = V(point), dir = p.clone().sub(o), dist = dir.length(); dir.normalize();
      const tilt = (Math.acos(clamp(-dir.z, -1, 1)) * 180) / Math.PI, pan = (Math.atan2(dir.y, dir.x) * 180) / Math.PI;
      if (tilt > f.engineered.tiltLimit) return { valid: false, reason: "reach", message: `Out of reach · ${Math.round(tilt)}° from straight down, limit ${f.engineered.tiltLimit}°`, dist, tilt, pan };
      this.ray2.set(o, dir); this.ray2.far = dist - 0.04;
      const occ = this._solid(this.ray2.intersectObjects(this.surfaces.filter((m) => m.userData.kind !== "floor"), false))[0];
      if (occ) return { valid: false, reason: "blocked", message: `Blocked by ${occ.object.userData.short || occ.object.userData.name}`, blockerPoint: [occ.point.x, occ.point.y, occ.point.z], dist, tilt, pan };
      return { valid: true, dist, tilt, pan, shadow: this._bodyBlock(o, p) };
    }
    _hover(h) { const a = this.state.hover || null; if ((a && a.id) === (h && h.id) && (a && a.kind) === (h && h.kind)) return; this.cb.onHover && this.cb.onHover(h); }
    _bindPointer() {
      const c = this.canvas; const ptrs = new Map(); let down = null, pinch = null;
      c.addEventListener("pointerdown", (e) => {
        ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY }); try { c.setPointerCapture(e.pointerId); } catch (err) { /* */ }
        if (this.compose && e.pointerId !== this.compose.pid) this._endCompose();
        if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), r: this.orbit.r }; down = null; return; }
        if (ptrs.size === 1 && (e.button === 0 || e.pointerType !== "mouse") && !e.shiftKey) { const h = this._composeStartable(e); if (h) { const rc = c.getBoundingClientRect();
          this.compose = { fid: h.fid, pid: e.pointerId, idx: h.idx, startIdx: h.idx, level: h.level, engaged: false, side: Math.sign(h.r - this._petalR(h.fid, h.idx)) || -1, screen: { x: e.clientX - rc.left, y: e.clientY - rc.top } };
          down = null; c.style.cursor = "grabbing"; this.host.classList.add("is-composing"); this.cb.onCompose && this.cb.onCompose(h.fid, null, "start"); this._dynamic(); this._placeLabels(); return; } }
        down = { x: e.clientX, y: e.clientY, az: this.orbit.az, el: this.orbit.el, moved: false, pan: e.button === 2 || e.shiftKey };
      });
      c.addEventListener("pointermove", (e) => {
        if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.compose && e.pointerId === this.compose.pid) { this._composeMove(e); return; }
        if (pinch && ptrs.size === 2) { const [a, b] = [...ptrs.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y); this.orbit.goal = null; this.orbit.r = clamp(pinch.r * (pinch.d / Math.max(20, d)), 2.2, 70); return; }
        if (down) {
          const dx = e.clientX - down.x, dy = e.clientY - down.y; if (!down.moved && Math.hypot(dx, dy) > 4) { down.moved = true; this.host.classList.add("is-orbiting"); }
          if (down.moved && !this.camView) {
            this.orbit.goal = null; this.userMoved = true;
            if (down.pan) { const k = this.orbit.r * 0.0016; const fw = new T.Vector3(-Math.cos(this.orbit.az), -Math.sin(this.orbit.az), 0), rt = new T.Vector3(-fw.y, fw.x, 0); this.orbit.t.addScaledVector(rt, -dx * k * 0.25).addScaledVector(fw, dy * k * 0.25); down.x = e.clientX; down.y = e.clientY; }
            else { this.orbit.az = down.az - dx * 0.006; this.orbit.el = clamp(down.el + dy * 0.004, 0.2, 1.45); }
          }
          return;
        }
        if (this.state.placing && e.pointerType !== "touch") { const r = this.canvas.getBoundingClientRect(); this.placeHover = this.surfaceHit(e.clientX, e.clientY); this.placeScreen = { x: e.clientX - r.left, y: e.clientY - r.top }; c.style.cursor = "crosshair"; this._dynamic(); return; }
        if (e.pointerType === "mouse" && this.state.zoneFixture && this._composeStartable(e)) { this._hover(null); c.style.cursor = "grab"; return; }
        if (e.pointerType === "mouse") { const h = this._pick(e.clientX, e.clientY); this._hover(h); c.style.cursor = h ? "pointer" : this.state.tapAim ? "crosshair" : "grab"; }
      });
      const up = (e) => {
        if (this.compose && e.pointerId === this.compose.pid) { ptrs.delete(e.pointerId); this._endCompose(); return; }
        ptrs.delete(e.pointerId); if (pinch) { if (ptrs.size < 2) pinch = null; return; }
        if (!down) return; const wasMove = down.moved; down = null; this.host.classList.remove("is-orbiting"); if (wasMove || this.camView) return;
        if (this.state.placing) { const hit = this.surfaceHit(e.clientX, e.clientY); if (hit) this.cb.onPlace && this.cb.onPlace(hit); return; }
        if (this.state.tapAim && this.state.aimFixture) { const hit = this.aimHit(this.state.aimFixture, e.clientX, e.clientY); this.cb.onAim && this.cb.onAim({ phase: "end", hit, via: "tap" }); return; }
        this.cb.onSelect && this.cb.onSelect(this._pick(e.clientX, e.clientY));
      };
      c.addEventListener("pointerup", up); c.addEventListener("pointercancel", (e) => { if (this.compose) this._endCompose(); ptrs.delete(e.pointerId); down = null; pinch = null; this.host.classList.remove("is-orbiting"); });
      c.addEventListener("pointerleave", () => { if (!down) this._hover(null); if (this.placeHover) { this.placeHover = null; this._dynamic(); } });
      c.addEventListener("contextmenu", (e) => e.preventDefault());
      c.addEventListener("wheel", (e) => { e.preventDefault(); if (this.camView) return; this.orbit.goal = null; this.userMoved = true; this.orbit.r = clamp(this.orbit.r * Math.exp(e.deltaY * 0.0011), 2.2, 70); }, { passive: false });
    }
    _bindReticle(ret) {
      ret.addEventListener("pointerdown", (e) => {
        e.preventDefault(); e.stopPropagation(); const id = this.state.aimFixture; if (!id) return; try { ret.setPointerCapture(e.pointerId); } catch (err) { /* */ }
        this.drag = { id, pointerId: e.pointerId, hit: null, screen: { x: e.clientX - this.canvas.getBoundingClientRect().left, y: e.clientY - this.canvas.getBoundingClientRect().top } };
        this.host.classList.add("is-aiming"); this.cb.onAim && this.cb.onAim({ phase: "start" });
      });
      ret.addEventListener("pointermove", (e) => {
        if (!this.drag || this.drag.pointerId !== e.pointerId) return; const r = this.canvas.getBoundingClientRect();
        this.drag.screen = { x: e.clientX - r.left, y: e.clientY - r.top }; this.drag.hit = this.aimHit(this.drag.id, e.clientX, e.clientY); this._dynamic();
      });
      const end = (e) => {
        if (!this.drag || this.drag.pointerId !== e.pointerId) return; const hit = this.drag.hit; this.drag = null; this.host.classList.remove("is-aiming");
        this._dynamic(); this.cb.onAim && this.cb.onAim({ phase: hit ? "end" : "cancel", hit, via: "drag" });
      };
      ret.addEventListener("pointerup", end); ret.addEventListener("pointercancel", (e) => { if (this.drag) { this.drag = null; this.host.classList.remove("is-aiming"); this._dynamic(); this.cb.onAim && this.cb.onAim({ phase: "cancel" }); } });
      ret.addEventListener("keydown", (e) => {
        const map = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }; const m = map[e.key]; if (!m || !this.state.aimFixture) return;
        e.preventDefault(); const r = this.canvas.getBoundingClientRect(); const rr = ret.getBoundingClientRect(); const step = e.shiftKey ? 40 : 10;
        const hit = this.aimHit(this.state.aimFixture, rr.left + rr.width / 2 + m[0] * step, rr.top + rr.height / 2 + m[1] * step);
        if (hit) this.cb.onAim && this.cb.onAim({ phase: "end", hit, via: "key" }); void r;
      });
    }
    cancelDrag() { if (!this.drag) return false; this.drag = null; this.host.classList.remove("is-aiming"); this._dynamic(); this.cb.onAim && this.cb.onAim({ phase: "cancel" }); return true; }
    isDragging() { return !!this.drag; }

    /* ---------- camera frames ---------- */
    renderFromCamera(id, w, h) {
      const c = this.site.cameras.find((x) => x.id === id); if (!c) return null; const { p, look } = this._camPose(c);
      const cam = new T.PerspectiveCamera(c.vfov, w / h, 0.05, 60); cam.up.set(0, 0, 1); cam.position.copy(p); cam.lookAt(look); cam.updateMatrixWorld();
      const rt = new T.WebGLRenderTarget(w, h); const prevCut = this.mat.uniforms.uCut.value, prevCam = this.mat.uniforms.uCam.value.clone();
      const vis = [this.gBody.visible, this.gCams.visible, this.gDyn.visible]; this.gBody.visible = false; this.gCams.visible = false;
      this.mat.uniforms.uCut.value = 0; this.mat.uniforms.uCam.value.copy(p); this.mat.uniforms.uSize.value = 1.5;
      this.renderer.setRenderTarget(rt); this.renderer.render(this.scene, cam); const buf = new Uint8Array(w * h * 4); this.renderer.readRenderTargetPixels(rt, 0, 0, w, h, buf); this.renderer.setRenderTarget(null);
      this.mat.uniforms.uCut.value = prevCut; this.mat.uniforms.uCam.value.copy(prevCam); [this.gBody.visible, this.gCams.visible, this.gDyn.visible] = vis; rt.dispose();
      const cv = document.createElement("canvas"); cv.width = w; cv.height = h; const g = cv.getContext("2d"); const img = g.createImageData(w, h);
      for (let y = 0; y < h; y++) img.data.set(buf.subarray((h - 1 - y) * w * 4, (h - y) * w * 4), y * w * 4);
      g.putImageData(img, 0, 0); return cv.toDataURL("image/jpeg", 0.86);
    }
    projectFromCamera(id, pts) {
      const c = this.site.cameras.find((x) => x.id === id); if (!c) return []; const { p, look } = this._camPose(c);
      const cam = new T.PerspectiveCamera(c.vfov, c.w / c.h, 0.05, 60); cam.up.set(0, 0, 1); cam.position.copy(p); cam.lookAt(look); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
      return pts.map((q) => { const v = V(q).project(cam); return [(v.x + 1) / 2, (1 - v.y) / 2]; });
    }
    dispose() { cancelAnimationFrame(this.raf); clearInterval(this.watch); this.ro.disconnect(); this.renderer.dispose(); }
  }
  window.AptScene = AptScene; window.aptKelvin = kelvin;
})();
