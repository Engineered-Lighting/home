import { encodeBodyFrame, LIVE_BODY_SCHEMA } from '../../app/src/home-3d/apartment-body-frame.mjs';

export function measuredBodyEvent({ revision = 'authored-scan', runId = 'authored-run', epoch = 1, seq = 1,
    captured = 100, reason, x = 1.2, mode = 'rgb' } = {}) {
    const common = { schema: LIVE_BODY_SCHEMA, home: 'den', run_id: runId, track_epoch: epoch,
        content_revision: revision, transform_id: 'authored-transform', seq, mode };
    if (reason) return { ...common, status: 'clear', reason };
    const positions = [];
    for (let row = 0; row < 100; row++) {
        const z = 0.25 + row * 0.013;
        const width = z > 0.77 ? 0.48 : 0.31;
        for (let column = 0; column < 30; column++) {
            const dx = width * (column / 29 - 0.5);
            if (z < 0.72 && Math.abs(dx) < 0.028) continue;
            positions.push({ key: (row + 110) * 640 + column + 200, xyz_m: [x + dx, 1.5 - 0.07 * Math.cos(dx * 11), z], normal: [0, -1, 0] });
        }
    }
    const { count, key, position_mm, normal, kind } = encodeBodyFrame({ points: positions }).points;
    return { ...common, status: 'ready', track_id: 'authored-track', capture_ts: captured, expires_ts: captured + 1,
        points: { count, key, position_mm, normal, kind }, facing_deg: 90, shoulder_midpoint_m: [x, 1.46, 1.51], pelvis_z_m: 0.78 };
}
