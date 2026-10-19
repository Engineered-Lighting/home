/* Selected-fixture drawing, borrowing the admitted UI layer's canonical group.
 * No clock, input dispatch, modeled setter, asset transform or saved inventory. */
import * as THREE from 'three';

const R0 = .14, R1 = 1.3, N = 240, TAU = Math.PI * 2;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
// The supplied prototype's qualitative color cue; not measured photometry.
export function radialDisplayColor(kelvin) {
    const t = clamp(kelvin || 2700, 1000, 12000) / 100;
    const r = t <= 66 ? 255 : 329.7 * Math.pow(t - 60, -.1332);
    const g = t <= 66 ? 99.47 * Math.log(t) - 161.12 : 288.12 * Math.pow(t - 60, -.0755);
    const b = t <= 19 ? 0 : t <= 66 ? 138.52 * Math.log(t - 10) - 305.04 : 255;
    return new THREE.Color(clamp(r, 0, 255) / 255, clamp(g, 0, 255) / 255, clamp(b, 0, 255) / 255);
}

export function createDirectionalPresentation({ group, host, api, initial, getProjection, getMode,
    publishPlane, isCurrent, onRetire = () => {}, reducedMotion = () => false } = {}) {
    const identity = api?.identityKey(initial?.descriptor), display = api?.createRadialDisplay(initial?.descriptor?.state?.radials);
    if (!group?.isGroup || !host?.ownerDocument || !identity || !display || typeof getProjection !== 'function'
        || typeof getMode !== 'function' || typeof publishPlane !== 'function' || typeof isCurrent !== 'function') throw TypeError('directional_presentation_dependencies');
    let disposed = false, elements = null, plan = null, lastRadials = '', lastRender = '', drawn = null;
    let snapshot = Object.freeze({ descriptor: initial.descriptor, mode: getMode(), active: false, readout: null });
    const listeners = new Set(), resources = new Set();
    const own = resource => { resources.add(resource); return resource; };
    let scene;
    try {
    scene = new THREE.Group(); scene.name = 'apartment-directional-composer';
    scene.userData.presentation_only = true; group.add(scene);
    const geometry = own(new THREE.BufferGeometry());
    const positions = new Float32Array((N + 2) * 3), tint = new Float32Array((N + 2) * 3), t = new Float32Array(N + 2);
    t.fill(1); t[0] = 0;
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('zoneTint', new THREE.BufferAttribute(tint, 3));
    geometry.setAttribute('t', new THREE.BufferAttribute(t, 1));
    geometry.setIndex(Array.from({ length: N }, (_, i) => [0, i + 1, i + 2]).flat());
    const fill = new THREE.Mesh(geometry, own(new THREE.ShaderMaterial({
        vertexShader: 'attribute float t; attribute vec3 zoneTint; varying float vT; varying vec3 vTint; void main(){vT=t;vTint=zoneTint;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
        fragmentShader: 'varying float vT; varying vec3 vTint; void main(){float a=pow(1.0-vT,1.8)*0.85+0.04+0.3*smoothstep(0.8,1.0,vT);gl_FragColor=vec4(vTint*a*0.7,1.0);}',
        transparent: true, blending: THREE.AdditiveBlending, depthTest: false, depthWrite: false, side: THREE.DoubleSide,
    })));
    fill.name = 'directional-polar-fill'; fill.renderOrder = 8; fill.frustumCulled = false; scene.add(fill);
    const edgeGeometry = own(new THREE.BufferGeometry());
    const edgePositions = new Float32Array((N + 1) * 3), edgeColors = new Float32Array((N + 1) * 3);
    edgeGeometry.setAttribute('position', new THREE.BufferAttribute(edgePositions, 3));
    edgeGeometry.setAttribute('color', new THREE.BufferAttribute(edgeColors, 3));
    const edge = new THREE.Line(edgeGeometry, own(new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .95, depthTest: false, depthWrite: false })));
    edge.name = 'directional-polar-edge'; edge.renderOrder = 9; edge.frustumCulled = false; scene.add(edge);
    function circle(radius, count, dashed, opacity) {
        const points = Array.from({ length: count + 1 }, (_, i) => new THREE.Vector3(Math.cos(i / count * TAU) * radius, Math.sin(i / count * TAU) * radius, 0));
        const material = own(dashed ? new THREE.LineDashedMaterial({ color: 0xffffff, transparent: true, opacity, depthTest: false, depthWrite: false, dashSize: .025, gapSize: .035 })
            : new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity, depthTest: false, depthWrite: false }));
        const line = new THREE.Line(own(new THREE.BufferGeometry().setFromPoints(points)), material);
        if (dashed) line.computeLineDistances(); line.renderOrder = 9; line.frustumCulled = false; scene.add(line); return line;
    }
    const outer = circle(R1, 120, true, .24), inner = circle(R0, 48, false, .3);
    const boundariesGeometry = own(new THREE.BufferGeometry()); boundariesGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(12), 3));
    const boundaries = new THREE.LineSegments(boundariesGeometry, own(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: .14, depthTest: false, depthWrite: false })));
    boundaries.renderOrder = 9; boundaries.frustumCulled = false; scene.add(boundaries);
    const canvas = host.ownerDocument.createElement('canvas'); canvas.width = canvas.height = 128;
    const context = canvas.getContext('2d'); if (!context) throw Error('directional_glow_canvas_unavailable');
    const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
    for (const [at, alpha] of [[0, 1], [.16, .6], [.45, .14], [1, 0]]) gradient.addColorStop(at, `rgba(255,255,255,${alpha})`);
    context.fillStyle = gradient; context.fillRect(0, 0, 128, 128);
    const texture = own(new THREE.CanvasTexture(canvas));
    const sprite = name => {
        const node = new THREE.Sprite(own(new THREE.SpriteMaterial({ map: texture, color: 0xffffff, transparent: true, blending: THREE.AdditiveBlending, depthTest: false, depthWrite: false })));
        node.name = name; node.renderOrder = 10; scene.add(node); return node;
    };
    const nodes = Array.from({ length: 6 }, (_, i) => sprite('directional-zone-node-' + (i + 1)));
    const ticks = Array.from({ length: 6 }, (_, i) => sprite('directional-zone-tick-' + (i + 1)));
    function notify(next) { snapshot = Object.freeze(next); for (const listener of [...listeners]) { try { listener(); } catch {} } }
    function current() { try { return !disposed && isCurrent() === true; } catch { return false; } }
    function setAttribute(node, key, value) { if (node && node.getAttribute(key) !== String(value)) node.setAttribute(key, String(value)); }
    function writeReadout() {
        if (!elements?.root.isConnected) return;
        const value = snapshot.readout, active = snapshot.active && snapshot.mode === 'zones';
        elements.pill.hidden = snapshot.active;
        elements.readout.hidden = !active || !value;
        if (active && value) {
            elements.readout.textContent = `Zone ${value.zone} · ${Math.round(value.level * 100)}%`;
            elements.readout.style.transform = `translate3d(${value.x + 14}px,${value.y - 36}px,0)`;
        }
    }
    function withdraw() {
        drawn = null; scene.visible = false;
        if (elements) { elements.root.hidden = true; elements.readout.hidden = true; }
        try { publishPlane(null); } catch {} // A retiring consumer cannot prevent owned cleanup.
    }
    function syncDescriptor(descriptor) {
        if (api.identityKey(descriptor) !== identity || !display.set(descriptor.state.radials)) return false;
        descriptor = { ...descriptor, label: descriptor.label ?? snapshot.descriptor.label };
        const nextMode = getMode(), signature = JSON.stringify([descriptor.state.radials, descriptor.label, nextMode]);
        if (signature !== lastRadials) { lastRadials = signature; notify({ ...snapshot, descriptor, mode: nextMode }); }
        return true;
    }
    function drawShape(radials) {
        const ordered = [...radials].sort((a, b) => a.azimuth_deg - b.azimuth_deg);
        const colors = ordered.map(zone => radialDisplayColor(zone.color_temp_kelvin));
        const centerColor = colors.reduce((sum, color) => sum.add(color), new THREE.Color(0, 0, 0)).multiplyScalar(1 / 6);
        tint.set(centerColor.toArray(), 0);
        for (let k = 0; k <= N; k++) {
            const degrees = k / N * 360;
            let index = ordered.findIndex((zone, i) => { const a = degrees < zone.azimuth_deg ? degrees + 360 : degrees; return a >= zone.azimuth_deg && a <= ordered[(i + 1) % 6].azimuth_deg + (i === 5 ? 360 : 0); });
            if (index < 0) index = 5;
            const a = ordered[index], b = ordered[(index + 1) % 6], start = a.azimuth_deg, end = b.azimuth_deg + (index === 5 ? 360 : 0);
            const at = degrees < start ? degrees + 360 : degrees, blend = (1 - Math.cos(Math.PI * (at - start) / (end - start))) / 2;
            const level = (a.on ? a.brightness : 0) * (1 - blend) + (b.on ? b.brightness : 0) * blend;
            const radius = R0 + level * (R1 - R0), theta = degrees / 180 * Math.PI;
            const point = [Math.cos(theta) * radius, Math.sin(theta) * radius, 0], color = colors[index].clone().lerp(colors[(index + 1) % 6], blend);
            positions.set(point, (k + 1) * 3); tint.set(color.toArray(), (k + 1) * 3);
            edgePositions.set(point, k * 3); edgeColors.set(color.toArray(), k * 3);
        }
        for (const attribute of [geometry.attributes.position, geometry.attributes.zoneTint, edgeGeometry.attributes.position, edgeGeometry.attributes.color]) attribute.needsUpdate = true;
        fill.visible = edge.visible = radials.some(zone => zone.on && zone.brightness > 0);
        for (const zone of radials) {
            const index = zone.number - 1, theta = zone.azimuth_deg / 180 * Math.PI, active = snapshot.active && snapshot.readout?.zone === zone.number;
            const level = zone.on ? zone.brightness : 0, radius = level > 0 ? R0 + level * (R1 - R0) : R0;
            const node = nodes[index], tick = ticks[index];
            node.position.set(Math.cos(theta) * radius, Math.sin(theta) * radius, 0); node.visible = level > 0 || active;
            node.material.color.copy(active ? new THREE.Color(1, 1, 1) : radialDisplayColor(zone.color_temp_kelvin));
            node.material.opacity = level > 0 ? .95 : .75; node.scale.setScalar(active ? .17 : .085);
            tick.position.set(Math.cos(theta) * R1, Math.sin(theta) * R1, 0); tick.scale.setScalar(active ? .075 : .045);
            tick.material.opacity = snapshot.active ? active ? .95 : .35 : .3;
            if (active) {
                const orderedIndex = ordered.findIndex(item => item.number === zone.number), previous = ordered[(orderedIndex + 5) % 6].azimuth_deg - (orderedIndex === 0 ? 360 : 0), next = ordered[(orderedIndex + 1) % 6].azimuth_deg + (orderedIndex === 5 ? 360 : 0);
                const values = [];
                for (const angle of [(previous + zone.azimuth_deg) / 2, (next + zone.azimuth_deg) / 2]) for (const r of [R0, R1]) values.push(Math.cos(angle / 180 * Math.PI) * r, Math.sin(angle / 180 * Math.PI) * r, 0);
                boundariesGeometry.attributes.position.array.set(values); boundariesGeometry.attributes.position.needsUpdate = true;
            }
        }
        outer.visible = inner.visible = boundaries.visible = snapshot.active;
    }
    function frame({ nowMs } = {}) {
        if (!current()) { dispose(); return false; }
        const projection = getProjection();
        if (!syncDescriptor(projection?.descriptor || snapshot.descriptor)) { dispose(); return false; }
        const shown = display.advance(nowMs, { reducedMotion: reducedMotion() });
        if (shown && plan?.isConnected) for (const path of plan.querySelectorAll('[data-apt-radial-plan-zone]')) {
            const zone = shown.radials.find(item => item.number === +path.getAttribute('data-apt-radial-plan-zone'));
            const color = radialDisplayColor(zone.color_temp_kelvin);
            setAttribute(path, 'fill', `rgb(${Math.round(color.r * 255)},${Math.round(color.g * 255)},${Math.round(color.b * 255)})`);
            setAttribute(path, 'fill-opacity', zone.display_opacity);
        }
        if (!projection || !shown || !elements?.root.isConnected) { withdraw(); return false; }
        const radials = shown.radials, geometry = api.radialSectorPaths({ radials, frame: projection.frame });
        if (!geometry?.hub?.visible) { withdraw(); return false; }
        scene.position.fromArray(projection.descriptor.display_anchor_m); scene.visible = snapshot.mode === 'zones';
        const renderKey = JSON.stringify([radials, snapshot.mode, snapshot.active, snapshot.readout?.zone]);
        if (renderKey !== lastRender) { lastRender = renderKey; drawShape(radials); }
        elements.root.hidden = false;
        for (const path of elements.root.querySelectorAll('[data-apt-radial-number]')) {
            const number = +path.getAttribute('data-apt-radial-number'), zone = geometry.zones.find(item => item.number === number);
            setAttribute(path, 'd', zone.path);
            const target = snapshot.descriptor.state.radials.find(item => item.number === number);
            if (snapshot.mode === 'zones' && target.on && target.brightness > 0) setAttribute(path, 'data-apt-radial-start', 'zone:' + number);
            else path.removeAttribute('data-apt-radial-start');
        }
        for (const circle of elements.root.querySelectorAll('[data-apt-radial-hub]')) { setAttribute(circle, 'cx', geometry.hub.x); setAttribute(circle, 'cy', geometry.hub.y); }
        const hubTarget = elements.root.querySelector('[data-apt-radial-hub="hit"]');
        if (snapshot.mode === 'zones') setAttribute(hubTarget, 'data-apt-radial-start', 'hub'); else hubTarget?.removeAttribute('data-apt-radial-start');
        elements.pill.style.transform = `translate3d(${Math.round(geometry.hub.x)}px,${Math.round(geometry.hub.y - 26)}px,0) translate(-50%,-100%)`;
        writeReadout();
        if (!current()) { dispose(); return false; }
        drawn = { ...projection, geometry }; publishPlane(drawn); return true;
    }
    function dispose() {
        if (disposed) return; disposed = true; withdraw();
        scene.removeFromParent(); for (const resource of resources) { try { resource.dispose(); } catch {} }
        resources.clear(); listeners.clear(); elements = null; plan = null;
        try { onRetire(); } catch {} // Observer failures do not escape the owner's retirement.
    }
    return Object.freeze({ frame, dispose, withdraw,
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }, getSnapshot: () => snapshot,
        attach(next) { if (!disposed) { elements = next; elements.root.hidden = true; } return () => { if (elements === next) { withdraw(); elements = null; } }; },
        attachPlan(element) { plan = element; return () => { if (plan === element) plan = null; }; },
        sync(descriptor) { return current() && syncDescriptor(descriptor); },
        pointer(readout, active) { if (!current()) { dispose(); return false; } notify({ ...snapshot, active, readout }); writeReadout(); return true; },
        readback: () => ({ disposed, identity, drawn: !!drawn, target: snapshot.descriptor.state.radials, active: snapshot.active }),
    });
    } catch (error) {
        scene?.removeFromParent();
        for (const resource of resources) { try { resource.dispose(); } catch {} }
        resources.clear(); try { publishPlane(null); } catch {}
        throw error;
    }
}
