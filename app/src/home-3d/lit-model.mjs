/* home-3d/lit-model.mjs — the Tier-0 Lit light model, renderer-independent.
 *
 * Pure data and arithmetic with no three.js and no renderer: channel lumens to
 * candela, beam angles, the six radial zone directions, Kelvin display
 * colours and the per-fixture light list. lit-view.mjs turns it into built-in
 * three lights today; a WebGPU renderer can consume the same model unchanged.
 *
 * Every lumen rating here is SPEC's ASSUMPTION (SPEC Lit view), never a
 * measured or profile value, and is disclosed with the view.
 */

// SPEC.md > Lit view: assumed lumen ratings. Not measured; never presented as such.
export const LIT_ASSUMED_LUMENS = Object.freeze({ spot: 450, down: 350, zone: 120 });
export const LIT_ASSUMPTION_SOURCE = 'SPEC Lit view assumed lumen ratings';
export const LIT_DISCLOSURE = 'Simulated · not photometric · assumed output';
export const LIT_DISCLOSURE_DETAIL = 'Assumed output, not measured: spot 450 lm, down 350 lm, each radial zone 120 lm. '
    + 'Fixed night exposure. Materials are assumed from the scan colours. One bounce from the eight strongest lit patches, '
    + 'with assumed albedos.';
/* The fixed night exposure (prototype scene.js _litApply). It scales every
 * simulated light's candela into display intensity, so a narrow spot reads
 * brighter than a wide wash of the same output, as it does in the room. */
export const LIT_EXPOSURE = 0.038;
export const LIT_TARGET_FPS = 30;
// Prototype beam shapes: the spot cone half-angle is 0.8 x its FWHM in
// degrees with a 0.55 penumbra; the Down ring and each radial zone are
// soft-edged lobes of 1.0 and 0.95 rad.
export const SPOT_HALF_ANGLE_PER_FWHM_DEG = 0.8;
export const SPOT_PENUMBRA = 0.55;
export const DOWN_HALF_ANGLE = 1.0;
export const ZONE_HALF_ANGLE = 0.95;
// Shadow softness: Spot crisp (small aperture), Down soft (diffuser).
export const SHADOW_RADIUS = Object.freeze({ crisp: 2.5, soft: 6 });
/* One bounce (APT-V02; SPEC Lit view; prototype scene.js VPL block). TEMPORARY:
 * instant radiosity stands in until a GPU irradiance bake replaces it (WebGPU
 * renderer research, section 5), so it deliberately has no leak fixes. The
 * eight strongest lit patches become hemispherical bounce lights carrying the
 * reflected flux, placed 0.25 m off the patch with a 4.5 m range and no
 * shadow, so bounce can leak up to 4.5 m through a wall; the view says so. */
export const LIT_BOUNCE_LIMIT = 'One bounce · casts no shadows · can leak up to 4.5 m through walls';
export const LIT_BOUNCE_COUNT = 8;
export const LIT_BOUNCE_RANGE_M = 4.5;
export const LIT_BOUNCE_CONE = 1.45;
export const LIT_BOUNCE_OFFSET_M = 0.25;
export const LIT_BOUNCE_TINT = Object.freeze([1, 0.96, 0.9]);
// Prototype VPL albedos (assumed): spot target flat 0.35 or vertical 0.6,
// Down pool 0.35, zone wall 0.65, any other zone patch 0.4. A zone facing an
// opening finds no surface in range and adds nothing; the opening class and
// 'Toward walls' wait on APT-Z04 facing kinds.
export const LIT_BOUNCE_ALBEDO = Object.freeze({ spotFlat: 0.35, spotVertical: 0.6, down: 0.35, zoneWall: 0.65, zoneOther: 0.4 });
// A radial lobe aims outward and slightly down (prototype: d x 2 - 0.35 z).
const ZONE_DROP = -0.35;

/* Candela from a lumen rating spread evenly over a cone of half-angle theta:
 * I = flux / (2 pi (1 - cos theta)). */
export function candelaFromLumens(lumens, halfAngleRad) {
    const solid = 2 * Math.PI * (1 - Math.cos(halfAngleRad));
    return solid > 0 && Number.isFinite(lumens) ? lumens / solid : 0;
}

export function spotHalfAngle(fwhmDeg) {
    return (Math.max(1, Number(fwhmDeg) || 20) * SPOT_HALF_ANGLE_PER_FWHM_DEG) * Math.PI / 180;
}

/* A channel's level: its brightness when on, else 0. Linear in brightness. */
export function channelLevel(channel) {
    return channel && channel.on === true && Number.isFinite(channel.brightness) && channel.brightness > 0
        ? Math.min(1, channel.brightness) : 0;
}

/* The unit direction of a radial zone in the fixture's Z-up frame. */
export function zoneDirection(azimuthDeg) {
    const a = (Number(azimuthDeg) || 0) * Math.PI / 180;
    const v = [Math.cos(a) * 2, Math.sin(a) * 2, ZONE_DROP];
    const n = Math.hypot(...v);
    return v.map(x => x / n);
}

/* Display colour only (Tanner Helland's blackbody fit, as the fixtures use),
 * as sRGB components 0..1. Not a measured spectrum. */
export function kelvinRgb(kelvin) {
    const t = (Number.isFinite(kelvin) ? kelvin : 3000) / 100;
    const red = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
    const green = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
    const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
    const c = v => Math.min(255, Math.max(0, v)) / 255;
    return [c(red), c(green), c(blue)];
}

/* The lights one fixture contributes, in its own Z-up frame.
 *   fixture = { spot: { origin, direction, channel } | null,
 *               down: { origin, channel } | null,
 *               center: [x, y, z] | null,
 *               zones: [{ number, azimuth_deg, channel }] }
 * Each light: { kind, number, origin, direction, halfAngle, penumbra, shadow,
 *   level, kelvin, candela, intensity } where intensity = candela x exposure. */
export function fixtureLights(fixture, { spotFwhmDeg = 20, exposure = LIT_EXPOSURE } = {}) {
    const lights = [];
    const push = light => lights.push({ ...light, intensity: light.candela * exposure });
    if (fixture?.spot && fixture.spot.origin && fixture.spot.direction) {
        const half = spotHalfAngle(spotFwhmDeg), level = channelLevel(fixture.spot.channel);
        push({ kind: 'spot', number: null, origin: fixture.spot.origin.slice(), direction: fixture.spot.direction.slice(),
            halfAngle: half, penumbra: SPOT_PENUMBRA, shadow: 'crisp', level, kelvin: fixture.spot.channel?.color_temp_kelvin,
            candela: candelaFromLumens(LIT_ASSUMED_LUMENS.spot, half) * level });
    }
    if (fixture?.down && fixture.down.origin) {
        const level = channelLevel(fixture.down.channel);
        push({ kind: 'down', number: null, origin: fixture.down.origin.slice(), direction: [0, 0, -1],
            halfAngle: DOWN_HALF_ANGLE, penumbra: 1, shadow: 'soft', level, kelvin: fixture.down.channel?.color_temp_kelvin,
            candela: candelaFromLumens(LIT_ASSUMED_LUMENS.down, DOWN_HALF_ANGLE) * level });
    }
    for (const zone of fixture?.zones || []) {
        if (!fixture.center) break;
        const direction = zoneDirection(zone.azimuth_deg), level = channelLevel(zone.channel);
        push({ kind: 'zone', number: zone.number, origin: fixture.center.map((v, i) => v + direction[i] * 0.05), direction,
            halfAngle: ZONE_HALF_ANGLE, penumbra: 1, shadow: null, level, kelvin: zone.channel?.color_temp_kelvin,
            candela: candelaFromLumens(LIT_ASSUMED_LUMENS.zone, ZONE_HALF_ANGLE) * level });
    }
    return lights;
}

/* The bounce patches, strongest first, at most LIT_BOUNCE_COUNT.
 * `lights` are fixtureLights() entries; `hits[i]` is the first surface along
 * light i's axis as { point, normal } (normal unit, facing the light) or null.
 * `up` is the frame's up axis. Each patch carries the bounce light's pose and
 * intensity: reflected / pi x 0.9 x exposure, as in the prototype. */
export function bouncePatches(lights, hits, { up = [0, 0, 1], exposure = LIT_EXPOSURE } = {}) {
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const patches = [];
    (lights || []).forEach((light, index) => {
        const hit = hits?.[index];
        if (!(light.level > 0) || !hit) return;
        const facing = Math.abs(dot(hit.normal, up));
        let flux, albedo;
        if (light.kind === 'spot') { flux = LIT_ASSUMED_LUMENS.spot * light.level * 0.8; albedo = facing > 0.8 ? LIT_BOUNCE_ALBEDO.spotFlat : LIT_BOUNCE_ALBEDO.spotVertical; }
        else if (light.kind === 'down') { flux = LIT_ASSUMED_LUMENS.down * light.level * 0.8; albedo = LIT_BOUNCE_ALBEDO.down; }
        else { flux = LIT_ASSUMED_LUMENS.zone * light.level * 0.7; albedo = facing <= 0.35 ? LIT_BOUNCE_ALBEDO.zoneWall : LIT_BOUNCE_ALBEDO.zoneOther; }
        const reflected = flux * albedo;
        if (!(reflected > 1)) return;
        patches.push({ kind: light.kind, number: light.number ?? null, fixtureId: light.fixtureId ?? null,
            point: hit.point.slice(), normal: hit.normal.slice(), albedo, reflected, kelvin: light.kelvin,
            position: hit.point.map((v, i) => v + hit.normal[i] * LIT_BOUNCE_OFFSET_M),
            intensity: (reflected / Math.PI) * 0.9 * exposure });
    });
    return patches.sort((a, b) => b.reflected - a.reflected).slice(0, LIT_BOUNCE_COUNT);
}
