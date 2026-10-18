/* Owner-selected registered scan geometry only. No DOM creation, persistence,
 * camera, clock, hardware, network, or independent accuracy qualification. */
const SCOPE = Object.freeze({ site_id: 'victoria', map_id: 'victoria-den', room_id: 'den' });
const BINDING_KEYS = ['content_revision', 'registration_version', 'manifest_digest', 'frame_digest', 'canonical_collision_digest'];
const clone = value => JSON.parse(JSON.stringify(value));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const vector = value => Array.isArray(value) && value.length === 3 && value.every(finite);
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const need = (condition, reason) => { if (!condition) throw TypeError(reason); };
const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const dot = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);
const sub = (a, b) => a.map((value, i) => value - b[i]);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];

function pins(binding, equipment_ref) {
    need(exact(binding, BINDING_KEYS) && typeof binding.content_revision === 'string'
        && binding.content_revision.length > 0 && binding.content_revision.length <= 160
        && Number.isSafeInteger(binding.registration_version) && binding.registration_version >= 0
        && BINDING_KEYS.slice(2).every(key => sha(binding[key])), 'registered_desk_binding_required');
    need(exact(equipment_ref, ['revision', 'digest']) && Number.isSafeInteger(equipment_ref.revision)
        && equipment_ref.revision >= 0 && sha(equipment_ref.digest), 'registered_desk_equipment_ref_required');
}

function corner(value) {
    need(exact(value, ['point_m', 'normal_unit']) && vector(value.point_m)
        && value.point_m.every(number => Math.abs(number) <= 100) && vector(value.normal_unit)
        && Math.abs(Math.hypot(...value.normal_unit)-1) <= 1e-6 && value.normal_unit[2] > 0,
    'registered_desk_surface_corner_required');
    return clone(value);
}

function geometry(values) {
    need(Array.isArray(values) && values.length === 4, 'four_registered_desk_corners_required');
    const corners = values.map(corner), points = corners.map(value => value.point_m);
    let span = 0;
    for (let i = 0; i < 4; i++) for (let j = i+1; j < 4; j++) {
        const distance = Math.hypot(...sub(points[i], points[j]));
        need(distance > 0, 'distinct_registered_desk_corners_required');
        span = Math.max(span, distance);
        need(dot(corners[i].normal_unit, corners[j].normal_unit) > 0, 'registered_desk_normals_disagree');
    }
    need(span <= 20, 'registered_desk_span_exceeded');
    const summed = [0,1,2].map(axis => corners.reduce((sum, value) => sum+value.normal_unit[axis], 0));
    const length = Math.hypot(...summed), normal = summed.map(value => value/length);
    // Projection is used ONLY to validate cyclic convex ordering. The retained
    // vertices remain the exact raw 3D picks, including any nonplanarity.
    const turns = points.map((point, i) => dot(cross(sub(points[(i+1)%4], point),
        sub(points[(i+2)%4], points[(i+1)%4])), normal));
    need(turns.every(value => value > 0) || turns.every(value => value < 0),
        'convex_cyclic_registered_desk_corners_required');
    const centre = [0,1,2].map(axis => points.reduce((sum, point) => sum+point[axis], 0)/4);
    const maxDistance = Math.max(...points.map(point => Math.abs(dot(sub(point, centre), normal))));
    return { corners, target: { kind: 'polygon', vertices_m: clone(points), centre_m: centre,
        normal_unit: normal, planarity: { max_distance_m: maxDistance, qualified: false } } };
}

function equal(a, b) {
    if (a === b) return true;
    if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && equal(a[key], b[key]));
}

export function sealRegisteredDeskTarget({ binding, equipment_ref, corners, confirmed } = {}, digestFn) {
    pins(binding, equipment_ref);
    need(confirmed === true, 'explicit_whole_desktop_confirmation_required');
    need(typeof digestFn === 'function', 'registered_desk_digest_required');
    const checked = geometry(corners);
    const body = { schema: 'home-registered-desk-target-1', version: 1, scope: { ...SCOPE },
        frame: 'z_up_metric_floor0', unit: 'm', binding: clone(binding), equipment_ref: clone(equipment_ref),
        target: checked.target, selection: { method: 'owner_selected_collision_surface', raw_corners: checked.corners,
            asset_sha256: binding.canonical_collision_digest }, confirmed: true,
        truth_role: 'owner_selected_scan_target', qualification: null, physical_commands: false };
    const digest = digestFn(clone(body));
    need(sha(digest), 'registered_desk_digest_required');
    return { ...body, digest };
}

export function validateRegisteredDeskTarget(record, { binding, equipment_ref } = {}, digestFn) {
    need(exact(record, ['schema','version','scope','frame','unit','binding','equipment_ref','target','selection',
        'confirmed','truth_role','qualification','physical_commands','digest']), 'closed_registered_desk_target_required');
    pins(binding, equipment_ref);
    need(equal(record.binding, binding) && equal(record.equipment_ref, equipment_ref), 'registered_desk_target_binding_changed');
    need(exact(record.selection, ['method','raw_corners','asset_sha256']), 'closed_registered_desk_selection_required');
    const expected = sealRegisteredDeskTarget({ binding, equipment_ref, corners: record.selection.raw_corners,
        confirmed: record.confirmed }, digestFn);
    need(equal(record, expected), 'registered_desk_target_changed');
    return clone(record);
}

function identitySnapshot(getIdentity) {
    const value = getIdentity();
    let raw;
    try { raw = JSON.stringify(value); } catch (_) { throw TypeError('registered_desk_identity_required'); }
    need(typeof raw === 'string' && raw.length > 0 && raw.length <= 8192, 'registered_desk_identity_required');
    const snapshot = JSON.parse(raw);
    need(snapshot !== null, 'registered_desk_identity_required');
    return snapshot;
}

export function createDeskTargetCapture({ element, windowTarget, surfaceHit, getIdentity, isEnabled,
    onChange = () => {}, onComplete = () => {} } = {}) {
    need(element?.addEventListener && element?.removeEventListener && windowTarget?.addEventListener
        && windowTarget?.removeEventListener && [surfaceHit,getIdentity,isEnabled,onChange,onComplete]
            .every(value => typeof value === 'function'), 'registered_desk_capture_dependencies_required');
    const identity = identitySnapshot(getIdentity);
    let status = 'selecting', reason = null, corners = [], active = null, disposed = false;
    const snapshot = () => ({ status, reason, corners: clone(corners), identity: clone(identity),
        active_pointer: active !== null, disposed, physical_commands: false });
    const notify = () => onChange(snapshot());
    const consume = event => { event.preventDefault?.(); event.stopImmediatePropagation?.(); };
    const release = pointer => {
        if (!pointer) return;
        try { element.releasePointerCapture(pointer.id); } catch (_) { /* detached or already released */ }
    };
    function cancel(why = 'canceled') {
        if (status === 'retired') return snapshot();
        const pointer = active; active = null;
        status = 'retired'; reason = String(why).slice(0,160); corners = [];
        try { release(pointer); } finally { notify(); }
        return snapshot();
    }
    function current() {
        if (disposed || status === 'retired') return false;
        let same = false;
        try { same = isEnabled() === true && equal(identitySnapshot(getIdentity), identity); } catch (_) { /* retire below */ }
        if (!same) { cancel('identity_or_mode_changed'); return false; }
        return true;
    }
    function down(event) {
        if (!current() || status !== 'selecting' || active || event.button !== 0 || event.isPrimary === false
            || event.target?.closest?.('button,input,select,textarea,a,[data-aim-ignore]')) return;
        consume(event);
        active = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
        try { element.setPointerCapture(event.pointerId); }
        catch (_) { active = null; cancel('pointer_capture_unavailable'); }
    }
    function move(event) {
        if (!active || active.id !== event.pointerId) return;
        consume(event);
        if (!current()) return;
        if (Math.hypot(event.clientX-active.x, event.clientY-active.y) > 5) active.moved = true;
    }
    function up(event) {
        if (!active || active.id !== event.pointerId) return;
        consume(event);
        const pointer = active; active = null;
        let complete = null;
        try {
            if (!current()) return;
            if (pointer.moved || Math.hypot(event.clientX-pointer.x,event.clientY-pointer.y) > 5) {
                reason = 'drag_is_not_a_corner'; notify(); return;
            }
            const hit = surfaceHit(event.clientX, event.clientY);
            if (!current()) return;
            if (!hit) { reason = 'registered_surface_unavailable'; notify(); return; }
            const next = corner({ point_m: hit.point, normal_unit: hit.normal });
            const proposed = [...corners,next];
            if (proposed.length === 4) geometry(proposed);
            corners = proposed; reason = null;
            if (corners.length === 4) { status = 'complete'; complete = clone(corners); }
            notify();
        } catch (error) {
            if (status !== 'retired') { reason = error.message || 'registered_surface_unavailable'; notify(); }
        } finally { release(pointer); }
        if (complete && current()) onComplete(complete);
    }
    const pointerCancel = event => {
        if (active?.id !== event.pointerId) return;
        consume(event); cancel('pointer_canceled');
    };
    const key = event => { if (event.key === 'Escape' && current()) { consume(event); cancel('escape'); } };
    const blur = event => { if (event.target === windowTarget && status === 'selecting') cancel('blur'); };
    const listeners = [['pointerdown',down],['pointermove',move],['pointerup',up],
        ['pointercancel',pointerCancel],['lostpointercapture',pointerCancel]];
    for (const [name, handler] of listeners) element.addEventListener(name,handler,true);
    windowTarget.addEventListener('keydown',key,true); windowTarget.addEventListener('blur',blur,true);
    return { cancel, readback() { current(); return snapshot(); }, dispose() {
        if (disposed) return;
        try { cancel('disposed'); } finally {
            disposed = true;
            for (const [name,handler] of listeners) element.removeEventListener(name,handler,true);
            windowTarget.removeEventListener('keydown',key,true); windowTarget.removeEventListener('blur',blur,true);
        }
    } };
}
