/* Echo fixture glyphs (APT-L04, DIRECTION › Fixtures; prototype scene.js
 * _buildFixtures and _styleFixtures). Echo's ceiling lights are recessed
 * discs with a rim, tinted by colour temperature and brightness when on; a
 * plug-driven lamp is a sphere that glows warm when on; a co-located
 * Engineered profile adds a small white cylinder labelled "simulated".
 *
 * Presentation only. A glyph replaces the drawn marker body; it never changes
 * the marker's pick sphere, position, device record or Home Assistant state,
 * and it is never a hit target. It reads reported state only and keeps no
 * clock. */
import * as THREE from 'three';
import { kelvinColor } from './apartment-light-pools.mjs';

export const ECHO_GLYPH_NAMES = Object.freeze({
    root: 'echo-fixture-glyph', disc: 'echo-glyph-disc', rim: 'echo-glyph-rim', plug: 'echo-glyph-plug',
    engineered: 'echo-glyph-engineered', simulated: 'echo-glyph-simulated-tag',
});
// Prototype sizes: a 7.5 cm disc in a 1.5 cm rim, a 7 cm plug sphere and a
// 4 cm by 10 cm engineered body hanging below the disc.
const DISC_RADIUS = .075, RIM_OUTER = .09, PLUG_RADIUS = .07, BODY_RADIUS = .04, BODY_LENGTH = .1;
const OFF_GREY = .18, PLUG_OFF_GREY = .2, RIM_GREY = 0x777777, BODY_WHITE = 0xe9e9e6;
const SIMULATED_TEXT = 'simulated';

const finite = value => typeof value === 'number' && Number.isFinite(value);

/* Which glyph a device draws, from fields the Apartment model already has:
 * a light on a Home Assistant switch is a plug lamp; any other light at
 * ceiling height (the rule HomeApartmentData.isCeilingLight uses) or with an
 * Engineered profile is a recessed disc. Anything else keeps its marker. */
export function echoGlyphKind(device) {
    if (!device || device.type !== 'light' || !Array.isArray(device.pos) || device.pos.length !== 3 || !device.pos.every(finite)) return null;
    if (typeof device.ha_entity_id === 'string' && device.ha_entity_id.startsWith('switch.')) return 'plug';
    if (device.height_preset === 'ceiling' || device.pos[2] >= 2 || device.fixture_kind === 'engineered_gimbal_v1') return 'ceiling';
    return null;
}

/* The disc or bulb colour for reported Home Assistant state, as the
 * prototype tints it: off is a dim grey; on mixes in the colour temperature's
 * display white by brightness (ceiling) or fully (plug). Unknown or missing
 * state reads as off. */
export function echoGlyphTint(kind, haState) {
    const attrs = haState?.attributes || {}, on = haState?.state === 'on';
    const kelvin = finite(attrs.color_temp_kelvin) ? attrs.color_temp_kelvin
        : finite(attrs.color_temp) && attrs.color_temp > 0 ? 1e6 / attrs.color_temp : 2700;
    const white = kelvinColor(kelvin);
    if (kind === 'plug') { const level = on ? .9 : 0; return white.map(value => Math.min(1, PLUG_OFF_GREY + value * level)); }
    const percent = finite(attrs.brightness) ? Math.max(0, Math.min(100, attrs.brightness / 2.55)) : 50;
    const level = on ? .45 + percent / 160 : 0;
    return white.map(value => Math.min(1, OFF_GREY + value * level));
}

function simulatedTag() {
    const canvas = document.createElement('canvas');
    canvas.width = 192; canvas.height = 64;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgba(8,9,11,0.82)'; ctx.fillRect(6, 10, 180, 44);
    ctx.setLineDash([8, 6]); ctx.strokeStyle = '#a1a1a1'; ctx.lineWidth = 3; ctx.strokeRect(6, 10, 180, 44);
    ctx.fillStyle = '#d4d4d4'; ctx.font = '500 26px "Geist", sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(SIMULATED_TEXT, 96, 33);
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false, toneMapped: false,
        sizeAttenuation: false }));
    // A fixed on-screen size (about 64 × 21 px on a 768 px view) so the word
    // stays readable from every detent.
    sprite.name = ECHO_GLYPH_NAMES.simulated; sprite.scale.set(.084, .028, 1); sprite.renderOrder = 34;
    sprite.raycast = () => {};
    sprite.userData = { text: SIMULATED_TEXT, truth_role: 'simulated_profile' };
    return sprite;
}

/* Builds the glyph for one device, or null when it keeps its marker. */
export function createEchoGlyph(device) {
    const kind = echoGlyphKind(device);
    if (!kind) return null;
    const root = new THREE.Group();
    root.name = ECHO_GLYPH_NAMES.root;
    root.userData = { glyph_kind: kind, presentation_only: true, physical_commands: false };
    const basic = color => new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, toneMapped: false });
    const add = (name, geometry, material) => {
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = name; mesh.renderOrder = 12; mesh.raycast = () => {}; root.add(mesh); return mesh;
    };
    let body;
    if (kind === 'ceiling') {
        body = add(ECHO_GLYPH_NAMES.disc, new THREE.CircleGeometry(DISC_RADIUS, 28), basic(0x444444));
        add(ECHO_GLYPH_NAMES.rim, new THREE.RingGeometry(DISC_RADIUS, RIM_OUTER, 28), basic(RIM_GREY));
        if (device.fixture_kind === 'engineered_gimbal_v1') {
            const cylinder = add(ECHO_GLYPH_NAMES.engineered, new THREE.CylinderGeometry(BODY_RADIUS, BODY_RADIUS, BODY_LENGTH, 24),
                new THREE.MeshBasicMaterial({ color: BODY_WHITE, toneMapped: false }));
            cylinder.rotation.x = Math.PI / 2; cylinder.position.z = -BODY_LENGTH / 2;
            const tag = simulatedTag(); tag.position.z = -BODY_LENGTH - .07; root.add(tag);
            root.userData.engineered = 'simulated';
        }
    } else {
        body = add(ECHO_GLYPH_NAMES.plug, new THREE.SphereGeometry(PLUG_RADIUS, 18, 12), basic(0x444444));
    }
    function setState(haState) {
        const tint = echoGlyphTint(kind, haState);
        body.material.color.setRGB(tint[0], tint[1], tint[2], THREE.SRGBColorSpace);
        root.userData.reported_on = haState?.state === 'on';
    }
    function dispose() {
        root.traverse(node => {
            node.geometry?.dispose();
            for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) {
                material.map?.dispose(); material.dispose();
            }
        });
        root.removeFromParent();
    }
    setState(null);
    return Object.freeze({ kind, root, setState, dispose });
}
