/* Presentation only: canonical room anchors over the resident renderer.
 * The engine owns the viewport, scene binding and AbortSignal lifetime. */
export function createApartmentUiOverlayLayer({ THREE, camera, apartmentRoot, overlayRoot, host,
    projectWorld, pickSurface, isCurrent, signal, onDispose = () => {} } = {}) {
    if (!THREE?.Group || !THREE.Vector3 || !THREE.Mesh || !THREE.SphereGeometry || !THREE.MeshBasicMaterial
        || !camera?.matrixWorldInverse || !apartmentRoot?.localToWorld || !overlayRoot?.add
        || !host?.ownerDocument?.createElement || typeof host.appendChild !== 'function'
        || typeof projectWorld !== 'function' || typeof pickSurface !== 'function'
        || typeof isCurrent !== 'function' || typeof signal?.aborted !== 'boolean'
        || typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function'
        || typeof onDispose !== 'function') throw new TypeError('apartment_ui_overlay_dependencies_required');

    const group = new THREE.Group();
    group.name = 'apartment-ui-overlay-layer';
    const element = host.ownerDocument.createElement('div');
    element.className = 'apt-ui-overlay-layer';
    element.setAttribute('aria-hidden', 'true');
    Object.assign(element.style, { position: 'absolute', inset: '0', overflow: 'hidden', pointerEvents: 'none' });
    const anchors = new Set();
    let disposed = false;
    const finitePoint = point => Array.isArray(point) && point.length === 3 && point.every(Number.isFinite);
    const safely = callback => { try { callback(); } catch (_) { /* Other owners still clean up. */ } };
    function detach(node) {
        if (!node) return;
        safely(() => node.remove?.());
        if (node.parentNode) safely(() => node.parentNode.removeChild(node));
    }
    function viewport() {
        if (!host.isConnected || typeof host.getBoundingClientRect !== 'function'
            || host.hidden || host.inert || host.closest?.('[hidden], [inert]')) return null;
        const rect = host.getBoundingClientRect();
        const width = host.clientWidth, height = host.clientHeight;
        if (![rect.left, rect.top, rect.width, rect.height, width, height].every(Number.isFinite)
            || rect.width <= 0 || rect.height <= 0 || width <= 0 || height <= 0
            || host.getClientRects && host.getClientRects().length === 0) return null;
        const style = host.ownerDocument.defaultView?.getComputedStyle?.(host);
        if (style && (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse')) return null;
        return { rect, width, height };
    }
    function disposeAnchor(anchor) {
        if (anchor.disposed) return false;
        anchor.disposed = true;
        anchors.delete(anchor);
        detach(anchor.label);
        safely(() => anchor.dot.removeFromParent());
        safely(() => anchor.geometry.dispose());
        safely(() => anchor.material.dispose());
        return true;
    }
    function dispose() {
        if (disposed) return false;
        disposed = true;
        safely(() => signal.removeEventListener('abort', dispose));
        for (const anchor of [...anchors]) disposeAnchor(anchor);
        detach(element);
        safely(() => group.removeFromParent());
        if (group.parent) safely(() => group.parent.remove(group));
        // Public-group children can be borrowed. Detach them, without walking
        // their geometries/materials/textures or taking resource ownership.
        safely(() => group.clear());
        safely(() => onDispose());
        return true;
    }
    function currentViewport() {
        if (disposed) return null;
        try {
            const bounds = !signal.aborted && viewport();
            if (bounds && isCurrent() === true && !disposed && !signal.aborted) return bounds;
        }
        catch (_) { /* A failed owner predicate retires rather than revives. */ }
        dispose();
        return null;
    }
    function current() { return !!currentViewport(); }
    function updateMatrices() {
        apartmentRoot.updateWorldMatrix?.(true, false);
        camera.updateMatrixWorld?.(true);
    }
    function projectAdmitted(point_m, bounds) {
        if (!finitePoint(point_m)) return null;
        try {
            const world = apartmentRoot.localToWorld(new THREE.Vector3(...point_m));
            const view = world.clone().applyMatrix4(camera.matrixWorldInverse);
            const depth = -view.z;
            if (![world.x, world.y, world.z, depth, camera.near, camera.far].every(Number.isFinite)
                || camera.near < 0 || camera.far <= camera.near || depth <= 0
                || depth < camera.near || depth > camera.far) return null;
            const clip = world.clone().project(camera);
            if (![clip.x, clip.y, clip.z].every(Number.isFinite)
                || Math.abs(clip.x) > 1 || Math.abs(clip.y) > 1 || clip.z < -1 || clip.z > 1) return null;
            const screen = projectWorld(world);
            if (!screen || screen.visible !== true || !Number.isFinite(screen.x) || !Number.isFinite(screen.y)
                || screen.x < 0 || screen.x > bounds.width || screen.y < 0 || screen.y > bounds.height) return null;
            return { x: screen.x, y: screen.y, visible: true };
        } catch (_) { return null; }
    }
    function project(point_m) {
        const bounds = currentViewport();
        if (!bounds) return null;
        let screen;
        try { updateMatrices(); screen = projectAdmitted(point_m, bounds); }
        catch (_) { screen = null; }
        return current() ? screen : null;
    }
    function hide(anchor) {
        anchor.dot.visible = false;
        anchor.label.hidden = true;
    }
    function renderAnchor(anchor, screen) {
        if (anchor.disposed) return false;
        if (!screen || !anchor.validLabel) { hide(anchor); return false; }
        if (anchor.dot.position.x !== anchor.point[0] || anchor.dot.position.y !== anchor.point[1]
            || anchor.dot.position.z !== anchor.point[2]) anchor.dot.position.set(...anchor.point);
        if (anchor.lastX !== screen.x) { anchor.label.style.left = `${screen.x}px`; anchor.lastX = screen.x; }
        if (anchor.lastY !== screen.y) { anchor.label.style.top = `${screen.y}px`; anchor.lastY = screen.y; }
        anchor.dot.visible = true;
        anchor.label.hidden = false;
        return true;
    }
    function addAnchor({ point_m, label = '' } = {}) {
        if (!current() || !finitePoint(point_m) || typeof label !== 'string') return null;
        let anchor, geometry, material, dot, labelElement;
        try {
            geometry = new THREE.SphereGeometry(0.025, 8, 6);
            material = new THREE.MeshBasicMaterial({ color: 0xf4f2ec, depthTest: false, depthWrite: false });
            dot = new THREE.Mesh(geometry, material);
            labelElement = host.ownerDocument.createElement('span');
            labelElement.className = 'apt-ui-overlay-label';
            Object.assign(labelElement.style, { position: 'absolute', transform: 'translate(-50%, -100%)', pointerEvents: 'none' });
            labelElement.textContent = label;
            anchor = { geometry, material, dot, label: labelElement, point: point_m.slice(), validLabel: true, disposed: false };
            dot.position.set(...anchor.point);
            dot.renderOrder = 40;
            anchors.add(anchor);
            group.add(dot);
            element.appendChild(labelElement);
            const handle = Object.freeze({
                update(patch = {}) {
                    if (!current() || anchor.disposed) return false;
                    try {
                        if (Object.prototype.hasOwnProperty.call(patch, 'point_m')) anchor.point = finitePoint(patch.point_m) ? patch.point_m.slice() : null;
                        if (Object.prototype.hasOwnProperty.call(patch, 'label')) {
                            anchor.validLabel = typeof patch.label === 'string';
                            const text = anchor.validLabel ? patch.label : '';
                            if (anchor.label.textContent !== text) anchor.label.textContent = text;
                        }
                        return renderAnchor(anchor, project(anchor.point));
                    } catch (_) { safely(() => hide(anchor)); return false; }
                },
                dispose() { return disposeAnchor(anchor); },
            });
            renderAnchor(anchor, project(anchor.point));
            return current() ? handle : null;
        } catch (_) {
            if (anchor) disposeAnchor(anchor);
            else {
                detach(labelElement);
                safely(() => dot?.removeFromParent());
                safely(() => geometry?.dispose());
                safely(() => material?.dispose());
            }
            return null;
        }
    }
    function pick(clientX, clientY) {
        const bounds = currentViewport();
        if (!bounds || ![clientX, clientY].every(Number.isFinite)) return null;
        try {
            if (clientX < bounds.rect.left || clientX > bounds.rect.left + bounds.rect.width
                || clientY < bounds.rect.top || clientY > bounds.rect.top + bounds.rect.height) return null;
            const hit = pickSurface(clientX, clientY);
            if (!current() || !finitePoint(hit?.point) || !finitePoint(hit?.normal)
                || hit.normal.every(value => value === 0)) return null;
            const name = typeof hit.object?.name === 'string' ? hit.object.name.trim() : '';
            return { point: hit.point.slice(), normal: hit.normal.slice(), surfaceName: name || 'Scan surface' };
        } catch (_) { return null; }
    }
    function update() {
        const bounds = currentViewport();
        if (!bounds) return false;
        if (!anchors.size) return true;
        try { updateMatrices(); }
        catch (_) {
            for (const anchor of anchors) safely(() => hide(anchor));
            return false;
        }
        // Read projection/layout for every anchor before writing any label
        // style, avoiding one forced layout per anchor during an orbit.
        const frames = [...anchors].map(anchor => ({ anchor, screen: projectAdmitted(anchor.point, bounds) }));
        if (!current()) return false;
        let success = true;
        for (const { anchor, screen } of frames) {
            try { if (!renderAnchor(anchor, screen)) success = false; }
            catch (_) { safely(() => hide(anchor)); success = false; }
        }
        return current() && success;
    }
    const layer = Object.freeze({ project, addAnchor, pick, group, update, dispose });
    try { signal.addEventListener('abort', dispose, { once: true }); }
    catch (_) { dispose(); }
    if (current()) {
        try { overlayRoot.add(group); host.appendChild(element); }
        catch (_) { dispose(); }
        current();
    }
    return layer;
}
