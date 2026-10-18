/* Fixed-anchor, presentation-only fixture aiming. Dependency injection keeps
 * pointer ownership and lifecycle testable without React, WebGL or a network. */
export function createDirectAimGestureController({ element, windowTarget = element?.ownerDocument?.defaultView || globalThis,
    pickFixture, surfaceHit, captureSnapshot, previewTarget, restoreSnapshot, getIdentity,
    onSelect = () => {}, onPreview = () => {}, onCommit = () => {}, isEnabled = () => true,
    requestFrame = callback => globalThis.requestAnimationFrame(callback),
    cancelFrame = id => globalThis.cancelAnimationFrame(id), thresholdPx = 5 } = {}) {
    if (!element?.addEventListener || ![pickFixture, surfaceHit, captureSnapshot, previewTarget, restoreSnapshot, getIdentity].every(fn => typeof fn === 'function')) {
        throw TypeError('direct_aim_dependencies_required');
    }
    if (!Number.isFinite(thresholdPx) || thresholdPx < 1 || thresholdPx > 32) throw TypeError('direct_aim_threshold_invalid');
    let disposed = false, active = null, frame = null;
    const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
    const identity = () => JSON.stringify(getIdentity());
    const consume = event => { event.preventDefault?.(); event.stopImmediatePropagation?.(); };
    const report = (gesture, status, extra = {}) => onPreview({ fixture_id: gesture.fixture_id, status,
        target: clone(gesture.target), truth_role: 'virtual_preview', physical_commands: false, ...extra });
    const unschedule = () => { if (frame != null) cancelFrame(frame); frame = null; };
    const release = gesture => {
        try { if (element.hasPointerCapture?.(gesture.pointerId)) element.releasePointerCapture(gesture.pointerId); } catch (_) { /* detached host */ }
    };
    function cancel(status = 'canceled') {
        if (!active) return;
        const gesture = active; active = null; unschedule();
        if (identity() === gesture.identity) restoreSnapshot(gesture.fixture_id, clone(gesture.before));
        release(gesture); report(gesture, status);
    }
    function current() {
        if (!active) return false;
        if (!isEnabled() || identity() !== active.identity) { cancel('stale_scene'); return false; }
        return true;
    }
    function process() {
        frame = null;
        if (!current() || !active.dragging) return;
        const gesture = active;
        let hit;
        try { hit = surfaceHit(gesture.last.x, gesture.last.y, gesture.fixture_id); }
        catch (error) { report(gesture, 'surface_unavailable', { reason: error.message }); return; }
        if (!hit) { report(gesture, 'surface_unavailable'); return; }
        let result;
        try { result = previewTarget(gesture.fixture_id, hit, clone(gesture.before)); }
        catch (error) { report(gesture, 'preview_unavailable', { reason: error.message }); return; }
        if (result?.ok === false) { report(gesture, 'preview_unavailable', { reason: result.reason || 'target_refused' }); return; }
        gesture.target = clone({ point: hit.point, normal: hit.normal, ...(hit.surface_id ? { surface_id: hit.surface_id } : {}), ...(hit.kind ? { kind: hit.kind } : {}) });
        gesture.after = clone(captureSnapshot(gesture.fixture_id));
        report(gesture, 'preview', { descriptor: clone(result?.descriptor) });
    }
    function down(event) {
        if (disposed || active || !isEnabled() || event.button !== 0 || event.isPrimary === false
            || event.target?.closest?.('button,input,select,textarea,a,[data-aim-ignore]')) return;
        const picked = pickFixture(event.clientX, event.clientY);
        const fixture_id = typeof picked === 'string' ? picked : picked?.fixture_id;
        if (!fixture_id) return;
        const before = captureSnapshot(fixture_id);
        if (!before) return;
        consume(event);
        active = { fixture_id, before: clone(before), identity: identity(), pointerId: event.pointerId,
            start: { x: event.clientX, y: event.clientY }, last: { x: event.clientX, y: event.clientY },
            dragging: false, target: null, after: null };
        try { element.setPointerCapture(event.pointerId); }
        catch (_) { active = null; return; }
        onSelect(fixture_id);
    }
    function move(event) {
        if (!active || active.pointerId !== event.pointerId) return;
        consume(event); if (!current()) return;
        active.last = { x: event.clientX, y: event.clientY };
        if (!active.dragging) active.dragging = Math.hypot(event.clientX - active.start.x, event.clientY - active.start.y) >= thresholdPx;
        if (active.dragging && frame == null) frame = requestFrame(process);
    }
    function up(event) {
        if (!active || active.pointerId !== event.pointerId) return;
        consume(event); if (!current()) return;
        active.last = { x: event.clientX, y: event.clientY };
        unschedule(); process();
        if (!active) return;
        const gesture = active; active = null; release(gesture);
        if (gesture.dragging && gesture.target && gesture.after) {
            report(gesture, 'draft');
            onCommit({ fixture_id: gesture.fixture_id, before: clone(gesture.before), after: clone(gesture.after),
                target: clone(gesture.target), truth_role: 'virtual_preview', physical_commands: false });
        }
    }
    const pointerCancel = event => { if (active?.pointerId === event.pointerId) { consume(event); cancel(); } };
    const key = event => { if (event.key === 'Escape' && active) { consume(event); cancel(); } };
    const blur = () => cancel();
    const listeners = [['pointerdown', down], ['pointermove', move], ['pointerup', up],
        ['pointercancel', pointerCancel], ['lostpointercapture', pointerCancel]];
    for (const [name, callback] of listeners) element.addEventListener(name, callback, true);
    windowTarget.addEventListener?.('keydown', key, true); windowTarget.addEventListener?.('blur', blur, true);
    return { cancel,
        readback: () => ({ active: !!active, dragging: active?.dragging || false, fixture_id: active?.fixture_id || null,
            pending_frame: frame != null, disposed, physical_commands: false }),
        dispose() {
            if (disposed) return;
            cancel(); disposed = true;
            for (const [name, callback] of listeners) element.removeEventListener(name, callback, true);
            windowTarget.removeEventListener?.('keydown', key, true); windowTarget.removeEventListener?.('blur', blur, true);
        },
    };
}
