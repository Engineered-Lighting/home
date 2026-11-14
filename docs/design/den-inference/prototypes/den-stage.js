/* den-stage.js: the den in 3D for the prototypes (three.js r128, a global THREE).
 *
 * An etched point room (Home's Cloud look since PR #431), the measured body as gold points (the owner's "Recording"
 * look), the virtual spot's beam and pool, and a floor ring per tracked person. It draws only what the client view
 * holds: the body from the newest unexpired body frame, the beam from the newest fresh spot snapshot, the rings from
 * the newest people event. Overlays ask project() for screen positions.
 */
(function (root) {
'use strict';
const T = () => root.THREE;
const DEG = Math.PI / 180;
const GOLD = [1.0, 0.72, 0.36];

function rng(seed) { let x = seed >>> 0 || 1; return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0) / 4294967296; }; }

/* ---------- the etched room ---------- */
function roomPoints(ROOM) {
    const r = rng(11), P = [], I = [], Wl = [];
    const [W, D, H] = ROOM.size;
    let wall = 0;
    const add = (x, y, z, i) => { P.push(x, y, z); I.push(i); Wl.push(wall); };
    const plane = (o, u, v, du, dv, density, base, edge) => {
        const n = Math.round(du * dv * density);
        for (let k = 0; k < n; k++) {
            const a = r(), b = r();
            const e = Math.min(a * du, (1 - a) * du, b * dv, (1 - b) * dv);
            const i = base * (0.7 + 0.6 * r()) + (e < 0.02 ? edge : e < 0.05 ? edge * 0.35 : 0);
            add(o[0] + u[0] * a * du + v[0] * b * dv, o[1] + u[1] * a * du + v[1] * b * dv, o[2] + u[2] * a * du + v[2] * b * dv, i);
        }
    };
    const box = (x0, y0, z0, x1, y1, z1, base = 0.22, edge = 0.75, density = 1400, skipBottom = true) => {
        const dx = x1 - x0, dy = y1 - y0, dz = z1 - z0;
        plane([x0, y0, z1], [1, 0, 0], [0, 1, 0], dx, dy, density, base * 1.1, edge);          // top
        plane([x0, y0, z0], [1, 0, 0], [0, 0, 1], dx, dz, density, base, edge);                // front (y0)
        plane([x0, y1, z0], [1, 0, 0], [0, 0, 1], dx, dz, density * 0.6, base * 0.7, edge);    // back
        plane([x0, y0, z0], [0, 1, 0], [0, 0, 1], dy, dz, density * 0.8, base * 0.85, edge);   // left
        plane([x1, y0, z0], [0, 1, 0], [0, 0, 1], dy, dz, density * 0.8, base * 0.85, edge);   // right
        if (!skipBottom) plane([x0, y0, z0], [1, 0, 0], [0, 1, 0], dx, dy, density * 0.3, base * 0.5, 0);
    };
    const line = (a, b, n, i) => { for (let k = 0; k < n; k++) { const u = r(); add(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u, i * (0.8 + 0.4 * r())); } };
    // floor, walls (the camera's wall and the ceiling are cut away)
    plane([0, 0, 0], [1, 0, 0], [0, 1, 0], W, D, 380, 0.07, 0.5);
    wall = 1; plane([0, D, 0], [1, 0, 0], [0, 0, 1], W, H, 230, 0.06, 0.45); line([0, D - 0.02, 0.09], [W, D - 0.02, 0.09], 300, 0.35);
    wall = 2; plane([0, 0, 0], [0, 1, 0], [0, 0, 1], D, H, 200, 0.05, 0.45); line([0.02, 0, 0.09], [0.02, D, 0.09], 260, 0.35);
    wall = 3; plane([W, 0, 0], [0, 1, 0], [0, 0, 1], D, H, 200, 0.05, 0.45); line([W - 0.02, 0, 0.09], [W - 0.02, D, 0.09], 260, 0.35);
    wall = 0;
    // rug
    for (const [a, b] of [[[1.15, 1.55, 0.005], [2.95, 1.55, 0.005]], [[1.15, 2.7, 0.005], [2.95, 2.7, 0.005]], [[1.15, 1.55, 0.005], [1.15, 2.7, 0.005]], [[2.95, 1.55, 0.005], [2.95, 2.7, 0.005]]]) line(a, b, 160, 0.45);
    plane([1.15, 1.55, 0.004], [1, 0, 0], [0, 1, 0], 1.8, 1.15, 500, 0.09, 0);
    // sofa: base, back, arms, cushions
    box(1.0, 2.72, 0.0, 2.95, 3.5, 0.44, 0.2, 0.7);
    box(1.0, 3.28, 0.44, 2.95, 3.52, 0.9, 0.2, 0.7);
    box(0.82, 2.72, 0.0, 1.0, 3.52, 0.62, 0.22, 0.75);
    box(2.95, 2.72, 0.0, 3.13, 3.52, 0.62, 0.22, 0.75);
    line([1.97, 2.72, 0.44], [1.97, 3.28, 0.44], 70, 0.45);
    // reading table beside the sofa, with a lamp
    box(0.38, 2.92, 0.0, 0.76, 3.36, 0.56, 0.24, 0.8);
    box(0.5, 3.05, 0.56, 0.62, 3.17, 0.6, 0.25, 0.5, 900);
    line([0.56, 3.11, 0.6], [0.56, 3.11, 0.98], 60, 0.5);
    // coffee table
    box(1.55, 1.95, 0.0, 2.4, 2.38, 0.38, 0.2, 0.75);
    // desk and chair
    box(3.48, 0.55, 0.72, 4.2, 1.85, 0.76, 0.24, 0.8);
    for (const [x, y] of [[3.52, 0.6], [3.52, 1.8], [4.15, 0.6], [4.15, 1.8]]) line([x, y, 0], [x, y, 0.72], 50, 0.4);
    box(3.62, 1.05, 2.0 - 1.2, 3.68, 1.4, 1.15, 0.3, 0.6, 1200);   // a monitor
    // bookshelf on the left wall
    box(0.0, 0.95, 0.0, 0.34, 2.15, 1.9, 0.14, 0.6, 800);
    for (let z = 0.38; z < 1.9; z += 0.36) line([0.02, 0.97, z], [0.32, 0.97, z], 1, 0) || line([0.32, 0.96, z], [0.32, 2.14, z], 120, 0.55);
    for (let k = 0; k < 46; k++) { const y = 1.0 + (k % 23) * 0.048 + r() * 0.01, z0 = 0.4 + Math.floor(k / 23) * 0.72 + (k % 3) * 0.36; line([0.31, y, z0], [0.31, y, z0 + 0.2 + r() * 0.1], 14, 0.3); }
    wall = 1;
    // window on the back wall
    for (const [a, b] of [[[3.0, D - 0.01, 1.0], [3.9, D - 0.01, 1.0]], [[3.0, D - 0.01, 2.05], [3.9, D - 0.01, 2.05]], [[3.0, D - 0.01, 1.0], [3.0, D - 0.01, 2.05]], [[3.9, D - 0.01, 1.0], [3.9, D - 0.01, 2.05]], [[3.45, D - 0.01, 1.0], [3.45, D - 0.01, 2.05]]]) line(a, b, 180, 0.55);
    // a picture over the sofa
    for (const [a, b] of [[[1.55, D - 0.01, 1.35], [2.4, D - 0.01, 1.35]], [[1.55, D - 0.01, 1.85], [2.4, D - 0.01, 1.85]], [[1.55, D - 0.01, 1.35], [1.55, D - 0.01, 1.85]], [[2.4, D - 0.01, 1.35], [2.4, D - 0.01, 1.85]]]) line(a, b, 120, 0.5);
    wall = 0;
    return { positions: new Float32Array(P), intensity: new Float32Array(I), wall: new Float32Array(Wl) };
}

/* ---------- the body the depth camera would see ---------- */
function bodyPoints(pose, cam, seed) {
    const r = rng(seed * 2654435761);
    const h = pose.heading * DEG, f = [Math.sin(h), Math.cos(h)], rt = [Math.cos(h), -Math.sin(h)];
    const W = (l) => [pose.x + l[0] * rt[0] + l[1] * f[0], pose.y + l[0] * rt[1] + l[1] * f[1], l[2]];
    const s = pose.sitting, mix = (a, b) => a.map((v, i) => v + (b[i] - v) * s);
    const ph = pose.walking ? (pose.phase || 0) : 0, sw = pose.walking ? Math.sin(ph) : 0;
    const J = {};
    const stand = {
        pelvis: [0, 0, 0.95], chest: [0, 0, 1.3], neck: [0, 0.0, 1.49], head: [0, 0.01, 1.63],
        lsh: [-0.19, 0, 1.42], rsh: [0.19, 0, 1.42], lel: [-0.23, -0.02 - 0.08 * sw, 1.14], rel: [0.23, -0.02 + 0.08 * sw, 1.14],
        lwr: [-0.24, 0.02 - 0.16 * sw, 0.88], rwr: [0.24, 0.02 + 0.16 * sw, 0.88],
        lhip: [-0.095, 0, 0.92], rhip: [0.095, 0, 0.92], lkn: [-0.1, 0.02 + 0.13 * sw, 0.5], rkn: [0.1, 0.02 - 0.13 * sw, 0.5],
        lan: [-0.1, -0.01 + 0.24 * sw, 0.08], ran: [0.1, -0.01 - 0.24 * sw, 0.08] };
    const sit = {
        pelvis: [0, -0.06, 0.55], chest: [0, -0.1, 0.88], neck: [0, -0.08, 1.05], head: [0, -0.05, 1.18],
        lsh: [-0.19, -0.09, 0.98], rsh: [0.19, -0.09, 0.98], lel: [-0.24, -0.03, 0.72], rel: [0.24, -0.03, 0.72],
        lwr: [-0.17, 0.2, 0.6], rwr: [0.17, 0.2, 0.6],
        lhip: [-0.095, -0.03, 0.52], rhip: [0.095, -0.03, 0.52], lkn: [-0.11, 0.4, 0.54], rkn: [0.11, 0.4, 0.54],
        lan: [-0.11, 0.47, 0.09], ran: [0.11, 0.47, 0.09] };
    if (pose.hands === 'book') Object.assign(sit, { lel: [-0.22, 0.03, 0.74], rel: [0.22, 0.03, 0.74], lwr: [-0.11, 0.27, 0.8], rwr: [0.11, 0.27, 0.8], head: [0, 0.0, 1.15] });
    if (pose.hands === 'phone') Object.assign(sit, { rel: [0.22, 0.05, 0.74], rwr: [0.07, 0.24, 0.94], head: [0, -0.01, 1.16] });
    for (const k of Object.keys(stand)) J[k] = W(mix(stand[k], sit[k]));
    const P = [], N = [];
    const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    const add3 = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
    const norm = a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const right3 = [rt[0], rt[1], 0];
    const tube = (a, b, r0, r1, flat = 1, density = 5200) => {
        const ax = sub(b, a), len = Math.hypot(...ax), d = norm(ax);
        let u = sub(right3, d.map(x => x * (right3[0] * d[0] + right3[1] * d[1]))); u = norm(Math.hypot(...u) < 1e-3 ? cross(d, [0, 0, 1]) : u);
        const v = norm(cross(d, u));
        const area = 2 * Math.PI * (r0 + r1) / 2 * len + 2 * Math.PI * Math.max(r0, r1) ** 2, n = Math.round(area * density);
        for (let k = 0; k < n; k++) {
            const th = r() * Math.PI * 2;
            let t = r() * (len + r0 + r1) - r0;
            let rr = r0 + (r1 - r0) * Math.max(0, Math.min(1, t / len)), c, nrm;
            const cu = Math.cos(th) * flat, cv = Math.sin(th);
            if (t < 0 || t > len) {   // caps
                const capR = t < 0 ? r0 : r1, base = t < 0 ? a : b, dir = t < 0 ? -1 : 1;
                const phi = Math.acos(1 - r());
                const off = add3(add3(d.map(x => x * dir * Math.cos(phi) * capR), u, cu * Math.sin(phi) * capR), v, cv * Math.sin(phi) * capR);
                c = add3(base, off); nrm = norm(off);
            } else {
                c = add3(add3(add3(a, d, t), u, cu * rr), v, cv * rr);
                nrm = norm(add3(u.map(x => x * cu), v, cv));
            }
            P.push(c); N.push(nrm);
        }
    };
    const blob = (c, rx, ry, rz, density = 5200) => {
        const area = 4 * Math.PI * ((rx * ry) ** 1.6 / 3 + (rx * rz) ** 1.6 / 3 + (ry * rz) ** 1.6 / 3) ** (1 / 1.6), n = Math.round(area * density);
        for (let k = 0; k < n; k++) {
            const z = 2 * r() - 1, th = r() * Math.PI * 2, q = Math.sqrt(1 - z * z);
            const l = [q * Math.cos(th) * rx, q * Math.sin(th) * ry, z * rz];
            const w = add3(add3(add3(c, right3, l[0]), [f[0], f[1], 0], l[1]), [0, 0, 1], l[2]);
            const nl = norm([l[0] / (rx * rx), l[1] / (ry * ry), l[2] / (rz * rz)]);
            P.push(w); N.push(norm(add3(add3(right3.map(x => x * nl[0]), [f[0], f[1], 0], nl[1]), [0, 0, 1], nl[2])));
        }
    };
    const slab = (c, w, hgt, tilt, density = 9000) => {   // a held object, facing up and toward the face
        const n = Math.round(w * hgt * density), ct = Math.cos(tilt), st = Math.sin(tilt);
        for (let k = 0; k < n; k++) {
            const a = (r() - 0.5) * w, b = (r() - 0.5) * hgt;
            P.push(add3(add3(c, right3, a), [f[0] * ct, f[1] * ct, st], b)); N.push(norm([-f[0] * st, -f[1] * st, ct]));
        }
    };
    const mid = (a, b, k = 0.5) => a.map((v, i) => v + (b[i] - v) * k);
    blob(J.head, 0.085, 0.1, 0.115);
    tube(J.neck, mid(J.neck, J.head, 0.4), 0.05, 0.05);
    tube(J.pelvis, J.chest, 0.155, 0.17, 1.0, 5200); blob(mid(J.pelvis, J.chest, 0.65), 0.18, 0.11, 0.2);
    tube(J.chest, J.neck, 0.16, 0.07);
    for (const sd of ['l', 'r']) {
        tube(J[sd + 'sh'], J[sd + 'el'], 0.052, 0.045); tube(J[sd + 'el'], J[sd + 'wr'], 0.045, 0.036); blob(J[sd + 'wr'], 0.04, 0.05, 0.04);
        tube(J[sd + 'hip'], J[sd + 'kn'], 0.08, 0.06); tube(J[sd + 'kn'], J[sd + 'an'], 0.055, 0.042);
        tube(J[sd + 'an'], add3(J[sd + 'an'], [f[0] * 0.15, f[1] * 0.15, -0.04]), 0.04, 0.035);
    }
    if (pose.hands === 'book' && s > 0.5) slab(mid(J.lwr, J.rwr), 0.24, 0.17, 0.95);
    if (pose.hands === 'phone' && s > 0.5) slab(add3(J.rwr, [f[0] * 0.04, f[1] * 0.04, 0.04]), 0.075, 0.15, 1.15);
    // keep what faces the camera, then a coarse depth test so the far side and hidden limbs drop out
    const keep = [], bins = new Map(), BIN = 0.0065;
    for (let k = 0; k < P.length; k++) {
        const p = P[k], d = sub(cam, p), dist = Math.hypot(...d);
        if ((N[k][0] * d[0] + N[k][1] * d[1] + N[k][2] * d[2]) / dist < 0.05) continue;
        const key = Math.round(Math.atan2(-d[1], -d[0]) / BIN) * 4096 + Math.round(Math.atan2(-d[2], Math.hypot(d[0], d[1])) / BIN);
        const best = bins.get(key);
        if (best === undefined || dist < best) bins.set(key, dist);
        keep.push([k, key, dist]);
    }
    const pos = [], shade = [];
    for (const [k, key, dist] of keep) {
        if (dist > bins.get(key) + 0.035) continue;
        const p = P[k], d = norm(sub(cam, p)), facing = N[k][0] * d[0] + N[k][1] * d[1] + N[k][2] * d[2];
        pos.push(p[0] + (r() - 0.5) * 0.008, p[1] + (r() - 0.5) * 0.008, p[2] + (r() - 0.5) * 0.008);
        shade.push(0.45 + 0.55 * facing);
    }
    return { positions: new Float32Array(pos), shade: new Float32Array(shade), head: J.head };
}

function kelvin(k) {   // rough black-body colour, 0..1
    const t = k / 100;
    const r = t <= 66 ? 255 : 329.7 * Math.pow(t - 60, -0.1332);
    const g = t <= 66 ? 99.47 * Math.log(t) - 161.12 : 288.12 * Math.pow(t - 60, -0.0755);
    const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04;
    return [r, g, b].map(v => Math.max(0, Math.min(255, v)) / 255);
}

/* ---------- the stage ---------- */
function create(host, sim, { reducedMotion = () => false } = {}) {
    const THREE = T();
    const ROOM = sim.ROOM;
    const canvas = document.createElement('canvas');
    canvas.className = 'stage-canvas';
    host.appendChild(canvas);
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance' });
    renderer.setClearColor(0x000000, 1);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 60);
    camera.up.set(0, 0, 1);

    const pointVert = `attribute float aI; attribute vec3 aC; uniform float uSize; uniform float uPx; varying float vI; varying vec3 vC;
        void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); gl_Position = projectionMatrix * mv; vI = aI; vC = aC;
        gl_PointSize = clamp(uSize * uPx / -mv.z, 1.0, 6.0); }`;
    const pointFrag = `uniform float uGain; varying float vI; varying vec3 vC;
        void main(){ vec2 d = gl_PointCoord - 0.5; float r = dot(d,d); if (r > 0.25) discard; float a = exp(-r*14.0);
        gl_FragColor = vec4(vC * vI * uGain * a, 1.0); }`;

    // room
    const rp = roomPoints(ROOM);
    const roomGeo = new THREE.BufferGeometry();
    roomGeo.setAttribute('position', new THREE.BufferAttribute(rp.positions, 3));
    roomGeo.setAttribute('aI', new THREE.BufferAttribute(rp.intensity, 1));
    const roomCol = new Float32Array(rp.intensity.length * 3);
    for (let i = 0; i < rp.intensity.length; i++) { roomCol[i * 3] = 0.78; roomCol[i * 3 + 1] = 0.85; roomCol[i * 3 + 2] = 0.95; }
    roomGeo.setAttribute('aC', new THREE.BufferAttribute(roomCol, 3));
    roomGeo.setAttribute('aW', new THREE.BufferAttribute(rp.wall, 1));
    const roomVert = pointVert.replace('attribute float aI;', 'attribute float aI; attribute float aW; uniform vec3 uCam; uniform vec3 uRoom;')
        .replace('vI = aI;', 'float cut = (aW == 1.0 && uCam.y > uRoom.y) || (aW == 2.0 && uCam.x < 0.0) || (aW == 3.0 && uCam.x > uRoom.x) ? 0.0 : 1.0; vI = aI * cut;');
    const roomMat = new THREE.ShaderMaterial({ vertexShader: roomVert, fragmentShader: pointFrag, transparent: true, depthWrite: false,
        blending: THREE.AdditiveBlending, uniforms: { uSize: { value: 0.022 }, uPx: { value: 800 }, uGain: { value: 1.25 },
            uCam: { value: new THREE.Vector3() }, uRoom: { value: new THREE.Vector3(...ROOM.size) } } });
    scene.add(new THREE.Points(roomGeo, roomMat));

    // fixtures: a small can under the ceiling, a faint drop rod
    const fixtureGroup = new THREE.Group(); scene.add(fixtureGroup);
    const fixtureMat = new THREE.MeshBasicMaterial({ color: 0x9aa4b0 });
    for (const f of ROOM.fixtures) {
        const can = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.05, 0.09, 16), fixtureMat);
        can.rotation.x = Math.PI / 2; can.position.set(...f.at); fixtureGroup.add(can);
        const rod = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(f.at[0], f.at[1], f.at[2] + 0.04), new THREE.Vector3(f.at[0], f.at[1], ROOM.size[2])]),
            new THREE.LineBasicMaterial({ color: 0x3a4250, transparent: true, opacity: 0.6 }));
        fixtureGroup.add(rod);
    }

    // the beam: a soft cone and a pool where it lands, both additive, in the light's colour
    const beamMat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
        uniforms: { uColor: { value: new THREE.Color(1, 0.8, 0.6) }, uLevel: { value: 0 } },
        vertexShader: `varying vec2 vUv; varying vec3 vN; varying vec3 vV; void main(){ vUv = uv; vec4 mv = modelViewMatrix*vec4(position,1.0);
            vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }`,
        fragmentShader: `uniform vec3 uColor; uniform float uLevel; varying vec2 vUv; varying vec3 vN; varying vec3 vV;
            void main(){ float edge = pow(abs(dot(vN, vV)), 1.6); float along = mix(0.35, 1.0, vUv.y);
            gl_FragColor = vec4(uColor * uLevel * 0.16 * edge * along, 1.0); }` });
    const beam = new THREE.Mesh(new THREE.ConeGeometry(1, 1, 40, 1, true), beamMat);
    beam.visible = false; scene.add(beam);
    const poolMat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: { uColor: { value: new THREE.Color(1, 0.8, 0.6) }, uLevel: { value: 0 } },
        vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
        fragmentShader: `uniform vec3 uColor; uniform float uLevel; varying vec2 vUv; void main(){ float r = length(vUv-0.5)*2.0;
            float a = exp(-r*r*3.2) * smoothstep(1.0, 0.85, r); gl_FragColor = vec4(uColor * uLevel * 0.85 * a, 1.0); }` });
    const pool = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), poolMat);
    pool.visible = false; scene.add(pool);
    // a dark aim: a dashed axis and a hollow ring
    const axisGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, 1)]);
    const axis = new THREE.Line(axisGeo, new THREE.LineDashedMaterial({ color: 0xc9b9a3, dashSize: 0.06, gapSize: 0.05, transparent: true, opacity: 0.55 }));
    axis.visible = false; scene.add(axis);
    const aimRing = new THREE.Mesh(new THREE.RingGeometry(0.11, 0.125, 48), new THREE.MeshBasicMaterial({ color: 0xc9b9a3, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthWrite: false }));
    aimRing.visible = false; scene.add(aimRing);

    // the body
    const MAX_BODY = 9000;
    const bodyGeo = new THREE.BufferGeometry();
    const bodyPos = new Float32Array(MAX_BODY * 3), bodyI = new Float32Array(MAX_BODY), bodyC = new Float32Array(MAX_BODY * 3);
    bodyGeo.setAttribute('position', new THREE.BufferAttribute(bodyPos, 3));
    bodyGeo.setAttribute('aI', new THREE.BufferAttribute(bodyI, 1));
    bodyGeo.setAttribute('aC', new THREE.BufferAttribute(bodyC, 3));
    bodyGeo.setDrawRange(0, 0);
    const bodyMat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: { uSize: { value: 0.03 }, uPx: { value: 800 }, uGain: { value: 1 }, uTime: { value: 0 }, uBand: { value: -10 },
            uPool: { value: new THREE.Vector3(0, 0, -10) }, uPoolLevel: { value: 0 }, uPoolColor: { value: new THREE.Color(1, 0.85, 0.7) } },
        vertexShader: `attribute float aI; attribute vec3 aC; uniform float uSize; uniform float uPx; uniform float uTime; uniform float uBand;
            uniform vec3 uPool; uniform float uPoolLevel; varying float vI; varying vec3 vC;
            void main(){ vec4 mv = modelViewMatrix*vec4(position,1.0); gl_Position = projectionMatrix*mv;
            float flick = 0.86 + 0.14*sin(uTime*7.0 + position.x*53.0 + position.z*37.0);
            float band = exp(-pow((position.z - uBand)*9.0, 2.0));
            float lit = uPoolLevel * exp(-pow(distance(position, uPool)*4.2, 2.0));
            vI = aI * flick * (1.0 + 0.9*band) ; vC = mix(aC, vec3(1.0, 0.93, 0.82), clamp(lit*1.2,0.0,1.0)) * (1.0 + lit*1.6);
            gl_PointSize = clamp(uSize * uPx / -mv.z, 1.2, 7.0); }`,
        fragmentShader: pointFrag.replace('uniform float uGain;', 'uniform float uGain;') });
    const body = new THREE.Points(bodyGeo, bodyMat); scene.add(body);
    let bodyKey = null, bodyFade = 0, bodyHead = null, bandStart = -10;

    // per-track floor rings, a sigma disc and a facing pointer
    const rings = new Map();
    const ringMat = () => new THREE.ShaderMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide,
        uniforms: { uColor: { value: new THREE.Color(0.72, 0.85, 1.0) }, uOpacity: { value: 0.0 }, uDash: { value: 0 }, uFacing: { value: 0 }, uHasFacing: { value: 0 } },
        vertexShader: `varying vec2 vP; void main(){ vP = position.xy; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
        fragmentShader: `uniform vec3 uColor; uniform float uOpacity; uniform float uDash; uniform float uFacing; uniform float uHasFacing; varying vec2 vP;
            void main(){ float r = length(vP); float a = atan(vP.x, vP.y);
            float ring = smoothstep(0.012, 0.0, abs(r - 1.0) - 0.006);
            float dash = uDash > 0.5 ? step(0.5, fract(a * 9.55)) : 1.0;
            float d = abs(mod(a - uFacing + 3.14159, 6.28318) - 3.14159);
            float arc = uHasFacing * smoothstep(0.62, 0.5, d) * smoothstep(0.03, 0.0, abs(r - 1.0) - 0.012);
            float tip = uHasFacing * smoothstep(0.09, 0.0, d) * smoothstep(0.16, 0.0, abs(r - 1.12) - 0.03) ;
            float alpha = (ring * 0.35 * dash + arc * 0.9 + tip) * uOpacity; if (alpha < 0.003) discard;
            gl_FragColor = vec4(uColor, alpha); }` });
    const sigmaMat = () => new THREE.ShaderMaterial({ transparent: true, depthWrite: false,
        uniforms: { uColor: { value: new THREE.Color(0.72, 0.85, 1.0) }, uOpacity: { value: 0.0 } },
        vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
        fragmentShader: `uniform vec3 uColor; uniform float uOpacity; varying vec2 vUv; void main(){ float r = length(vUv-0.5)*2.0;
            float a = exp(-r*r*2.0) * smoothstep(1.0, 0.7, r) * 0.22 * uOpacity; if (a < 0.003) discard; gl_FragColor = vec4(uColor, a); }` });

    // camera rig
    const views = {
        overview: { dist: 8.4, el: 58, focus: [2.1, 1.85, 0.4] },
        room: { dist: 5.9, el: 40, focus: [2.0, 2.15, 0.55] },
        close: { dist: 2.9, el: 24, focus: null },
    };
    const rig = { az: -16, el: 40, dist: 5.9, focus: [2.0, 2.15, 0.55], goal: null, view: 'room' };
    rig.goal = { ...views.room, az: rig.az };
    let personFocus = [ROOM.seat[0], ROOM.seat[1], 0.85];
    function setView(name) { rig.view = name; const v = views[name]; rig.goal = { dist: v.dist, el: v.el, focus: v.focus, az: rig.az }; }
    let dragging = null;
    canvas.addEventListener('pointerdown', e => { dragging = { x: e.clientX, y: e.clientY, az: rig.az, el: rig.el }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => {
        if (!dragging) return;
        rig.az = dragging.az - (e.clientX - dragging.x) * 0.25; rig.el = Math.max(12, Math.min(78, dragging.el + (e.clientY - dragging.y) * 0.2));
        rig.goal.az = rig.az; rig.goal.el = rig.el;
    });
    const endDrag = e => { if (dragging && Math.hypot(e.clientX - dragging.x, e.clientY - dragging.y) < 5) host.dispatchEvent(new CustomEvent('stagetap', { detail: { x: e.clientX, y: e.clientY } })); dragging = null; };
    canvas.addEventListener('pointerup', endDrag); canvas.addEventListener('pointercancel', () => { dragging = null; });
    canvas.addEventListener('wheel', e => { e.preventDefault(); rig.goal.dist = Math.max(2.2, Math.min(10, rig.goal.dist * Math.exp(e.deltaY * 0.001))); }, { passive: false });

    let width = 1, height = 1;
    function resize() {
        const rect = host.getBoundingClientRect();
        width = Math.max(1, rect.width); height = Math.max(1, rect.height);
        renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
        renderer.setSize(width, height, false);
        camera.aspect = width / height; camera.updateProjectionMatrix();
        const px = height * renderer.getPixelRatio() / (2 * Math.tan(camera.fov * DEG / 2));
        roomMat.uniforms.uPx.value = px; bodyMat.uniforms.uPx.value = px;
    }
    new ResizeObserver(resize).observe(host); resize();

    const tmp = new THREE.Vector3();
    function project(p) {
        tmp.set(p[0], p[1], p[2]).project(camera);
        return { x: (tmp.x + 1) / 2 * width, y: (1 - tmp.y) / 2 * height, behind: tmp.z > 1 };
    }

    let lastT = null;
    const smoothed = new Map();
    let beamState = { target: null, level: 0 };
    function frame(view, now, realDt) {
        const dt = Math.min(0.1, Math.max(0, realDt));
        const reduce = reducedMotion();
        // track rings follow each event with a short time constant, so 3 events a second still glide
        const seen = new Set();
        for (const t of view.tracks) {
            seen.add(t.id);
            let m = rings.get(t.id);
            if (!m) {
                const ring = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 2.4), ringMat());
                const sigma = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), sigmaMat());
                scene.add(ring); scene.add(sigma);
                m = { ring, sigma, x: t.xy[0], y: t.xy[1], opacity: 0, facing: t.heading, z: 0.02 };
                rings.set(t.id, m);
            }
            const k = reduce ? 1 : 1 - Math.exp(-dt / 0.28);
            m.x += (t.xy[0] - m.x) * k; m.y += (t.xy[1] - m.y) * k;
            const seated = t.sigma < 0.1 && t.near && t.near.includes('sofa');
            const zGoal = seated ? 0.47 : 0.015;
            m.z += (zGoal - m.z) * (reduce ? 1 : 1 - Math.exp(-dt / 0.5));
            m.opacity = Math.min(1, m.opacity + dt * 3);
            if (t.heading != null) {
                const goal = t.heading * DEG;
                if (m.facing == null) m.facing = goal;
                let d = goal - m.facing; d = Math.atan2(Math.sin(d), Math.cos(d));
                m.facing += d * (reduce ? 1 : 1 - Math.exp(-dt / (Math.abs(d) > 0.35 ? 0.12 : 0.6)));
            }
            const R = seated ? 0.42 : 0.45;
            m.ring.position.set(m.x, m.y, m.z); m.ring.scale.setScalar(R / 1.0 * 1.0); m.ring.geometry.boundingSphere = null;
            m.ring.scale.set(R, R, 1);
            const u = m.ring.material.uniforms;
            u.uOpacity.value = m.opacity * (t.state === 'coasting' ? 0.55 : 1); u.uDash.value = t.state === 'coasting' ? 1 : 0;
            u.uHasFacing.value = t.heading != null ? 1 : 0; u.uFacing.value = m.facing ?? 0;
            const s2 = Math.max(0.12, t.sigma * 4);
            m.sigma.position.set(m.x, m.y, 0.006); m.sigma.scale.set(s2, s2, 1);
            m.sigma.material.uniforms.uOpacity.value = m.opacity;
            m.screen = project([m.x, m.y, m.z]);
            m.track = t;
        }
        for (const [id, m] of rings) if (!seen.has(id)) {
            m.opacity -= dt * 3;
            m.ring.material.uniforms.uOpacity.value = Math.max(0, m.opacity); m.sigma.material.uniforms.uOpacity.value = Math.max(0, m.opacity);
            if (m.opacity <= 0) { scene.remove(m.ring); scene.remove(m.sigma); rings.delete(id); }
        }

        // the body: regenerate on each new frame; fade out when the frame's lease runs out
        if (view.body) {
            const key = view.body.capture;
            if (key !== bodyKey) {
                bodyKey = key;
                const pose = { ...view.body.pose, phase: key * 5.2 };
                const pts = bodyPoints(pose, ROOM.camera, Math.round(key * 4));
                const n = Math.min(MAX_BODY, pts.positions.length / 3);
                bodyPos.set(pts.positions.subarray(0, n * 3));
                for (let i = 0; i < n; i++) { bodyI[i] = pts.shade[i] * 0.95; bodyC[i * 3] = GOLD[0]; bodyC[i * 3 + 1] = GOLD[1]; bodyC[i * 3 + 2] = GOLD[2]; }
                bodyGeo.setDrawRange(0, n);
                bodyGeo.attributes.position.needsUpdate = true; bodyGeo.attributes.aI.needsUpdate = true; bodyGeo.attributes.aC.needsUpdate = true;
                bodyHead = pts.head; bandStart = now;
            }
            bodyFade = Math.min(1, bodyFade + dt * 4);
        } else bodyFade = Math.max(0, bodyFade - dt * 3);
        bodyMat.uniforms.uGain.value = 1.15 * bodyFade;
        bodyMat.uniforms.uTime.value = reduce ? 0 : now;
        bodyMat.uniforms.uBand.value = reduce ? -10 : 0.1 + (now - bandStart) * 4.5;
        body.visible = bodyFade > 0.01;
        if (bodyHead) personFocus = [bodyHead[0], bodyHead[1], bodyHead[2] - 0.35];

        // the beam: one lit fixture at most in these scenes; a dark aim gets the dashed axis
        const light = view.light;
        const lit = light && light.fresh ? light.lit : null;
        const aimed = light && light.fresh ? light.aimed : null;
        if (aimed && aimed.target) {
            const k = reduce ? 1 : 1 - Math.exp(-dt / 0.12);
            beamState.target = beamState.target ? beamState.target.map((v, i) => v + (aimed.target[i] - v) * k) : aimed.target.slice();
        } else if (!lit) beamState.target = null;
        const wantLevel = lit ? lit.level : 0;
        beamState.level += (wantLevel - beamState.level) * (reduce ? 1 : 1 - Math.exp(-dt / 0.08));
        const fixture = aimed || lit;
        if (fixture && beamState.target) {
            const from = new THREE.Vector3(...fixture.at), to = new THREE.Vector3(...beamState.target);
            const dir = to.clone().sub(from), len = dir.length();
            const color = kelvin(fixture.cct || 2700);
            if (beamState.level > 0.01) {
                beam.visible = true; pool.visible = true;
                const rad = len * Math.tan(12 * DEG);
                beam.scale.set(rad, len, rad);
                beam.position.copy(from.clone().add(dir.clone().multiplyScalar(0.5)));
                beam.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), dir.clone().normalize());
                beamMat.uniforms.uLevel.value = beamState.level; beamMat.uniforms.uColor.value.setRGB(...color);
                pool.position.copy(to); pool.scale.set(rad * 3.2, rad * 3.2, 1);
                poolMat.uniforms.uLevel.value = beamState.level * 0.6; poolMat.uniforms.uColor.value.setRGB(...color);
                bodyMat.uniforms.uPool.value.copy(to); bodyMat.uniforms.uPoolLevel.value = beamState.level;
            } else { beam.visible = false; pool.visible = false; bodyMat.uniforms.uPoolLevel.value = 0; }
            const dark = beamState.level <= 0.01;
            axis.visible = dark; aimRing.visible = dark;
            if (dark) {
                axisGeo.setFromPoints([from, to]); axis.computeLineDistances();
                aimRing.position.copy(to); aimRing.lookAt(from);
            }
        } else { beam.visible = pool.visible = axis.visible = aimRing.visible = false; bodyMat.uniforms.uPoolLevel.value = 0; }

        // the camera
        const g = rig.goal, ke = reduce ? 1 : 1 - Math.exp(-dt / 0.35);
        rig.dist += (g.dist - rig.dist) * ke; rig.el += (g.el - rig.el) * ke; rig.az += (g.az - rig.az) * ke;
        const focus = g.focus || personFocus;
        rig.focus = rig.focus.map((v, i) => v + (focus[i] - v) * ke);
        const el = rig.el * DEG, az = rig.az * DEG;
        const dist = rig.dist * Math.pow(Math.max(1, 1.25 / camera.aspect), 0.75);   // a tall phone view backs off to keep the room in
        camera.position.set(rig.focus[0] + Math.sin(az) * Math.cos(el) * dist * -1, rig.focus[1] - Math.cos(az) * Math.cos(el) * dist, rig.focus[2] + Math.sin(el) * dist);
        camera.lookAt(rig.focus[0], rig.focus[1], rig.focus[2]);
        camera.updateMatrixWorld();
        roomMat.uniforms.uCam.value.copy(camera.position);
        // overlays read these after the camera moves
        for (const m of rings.values()) m.screen = project([m.x, m.y, m.z]);
        renderer.render(scene, camera);
        lastT = now;
    }

    function anchors() {
        const out = new Map();
        for (const [id, m] of rings) {
            const seated = m.z > 0.2;
            const headZ = seated ? 1.32 : 1.86;
            const isBody = view => bodyHead && Math.hypot(bodyHead[0] - m.x, bodyHead[1] - m.y) < 0.6;
            out.set(id, { ring: m.screen, head: project([m.x, m.y, headZ]), hand: project([m.x, m.y, seated ? 0.8 : 1.0]),
                floor: project([m.x, m.y, 0.0]), x: m.x, y: m.y, z: m.z, opacity: m.opacity, hasBody: isBody() });
        }
        return out;
    }

    return { frame, project, anchors, setView, get view() { return rig.view; }, canvas, get size() { return { width, height }; },
        beamTarget: () => beamState.target, beamLevel: () => beamState.level, bodyHead: () => bodyHead, bodyVisible: () => bodyFade > 0.05 };
}

root.DenStage = Object.freeze({ create, bodyPoints, roomPoints, kelvin });
})(typeof window !== 'undefined' ? window : globalThis);
