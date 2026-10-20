// L4 lit splats and points: the layer load rule, the basis blend's CPU twin
// (what the GLSL and WGSL passes compute), per-light dimming, the live spots'
// receiver columns through L5's BVH, and lit-view's receivers mode. Authored
// placeholder data only (fixtures/apartment-lit-layer-fixtures.mjs); no
// household pack, renderer or browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { installPack } from './fixtures/apartment-home-shell-fixtures.mjs';
import { authoredLightingLayer, authoredReceivers, AUTHORED_SOCKETS, packBinding } from './fixtures/apartment-lit-layer-fixtures.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceFile = name => path.join(ROOT, 'app/src/home-3d', name);
const Blend = await import(pathToFileURL(sourceFile('lit-basis-blend.mjs')).href);
const Loader = await import(pathToFileURL(sourceFile('lit-layer-loader.mjs')).href);
const Model = await import(pathToFileURL(sourceFile('lit-model.mjs')).href);
const Exposure = await import(pathToFileURL(sourceFile('lit-exposure.mjs')).href);
const Bake = await import(pathToFileURL(sourceFile('lit-bvh-bake.mjs')).href);
const { twoRoomScene, roomALights } = await import('./fixtures/lit-bake-scenes.mjs');
const Core = (await import('../app/src/home-equipment-registration.js')).default;

const packs = fs.mkdtempSync(path.join(os.tmpdir(), 'lit-l4-'));
const victoria = installPack(packs, 'victoria');
const pack = { ...victoria.manifest };
const served = (files, base = 'https://home.test/content-packs/victoria/' + victoria.revision + '.lighting/') => ({
    baseUrl: base,
    fetchImpl: async url => {
        const name = decodeURIComponent(new URL(url).pathname.split('/').pop()), body = new URL(url).href.startsWith(base) ? files[name] : null;
        return body ? { ok: true, arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) } : { ok: false };
    },
});
const load = (files, set = 'splat', packManifest = pack) => Loader.loadLightingLayer({ ...served(files), pack: packManifest, digest: Core.digest, set });
const refusal = async (promise, reason) => {
    await assert.rejects(promise, error => { assert.ok(Loader.isLayerRefusal(error), String(error)); assert.match(error.reason, reason); return true; });
};

// The authored room's lights at given levels, as lit-view eases them (world frame is irrelevant to the weights).
function lights({ down = 1, zone = 1, spot = 0, kelvin = 3000 } = {}) {
    return AUTHORED_SOCKETS.flatMap(socket => [
        { kind: 'down', number: null, fixtureId: socket.fixtureId, level: down, kelvin },
        ...[1, 2, 3, 4, 5, 6].map(number => ({ kind: 'zone', number, fixtureId: socket.fixtureId, level: zone, kelvin })),
        { kind: 'spot', number: null, fixtureId: socket.fixtureId, level: spot, kelvin, peakCandela: Model.candelaFromLumens(450, Model.spotHalfAngle(20)) },
    ]).map(light => {
        const fluxFraction = Model.litDimmingFraction(light.level);
        return { ...light, fluxFraction, candela: (light.peakCandela || 0) * fluxFraction };
    });
}

test('the layer loads only when bound to the active pack, and every file read matches its SHA-256', async () => {
    const layer = authoredLightingLayer(victoria);
    const splat = await load(layer.files, 'splat');
    assert.equal(splat.count, authoredReceivers().length);
    assert.equal(splat.columns, 29);
    assert.equal(splat.files, 6);
    assert.equal(splat.bytes / splat.count, 40, '40 B per receiver');
    assert.deepEqual({ ...splat.binding }, packBinding(victoria.manifest));
    assert.equal(splat.provenance.measured, false);
    assert.equal((await load(layer.files, 'points')).source, 'points.ply');
    await refusal(load(layer.files, 'splat-mobile'), /lighting_layer_set_absent/);

    // Any other pack generation: refused on its binding.
    await refusal(load(layer.files, 'splat', { ...pack, revision: 'authored-victoria-r2' }), /lighting_layer_binding_mismatch/);
    await refusal(load(layer.files, 'splat', { ...pack, registrationVersion: 2 }), /lighting_layer_binding_mismatch/);
    // A pack asset whose bytes changed.
    const assets = structuredClone(pack.assets); assets['points.ply'].sha256 = 'f'.repeat(64);
    await refusal(load(layer.files, 'points', { ...pack, assets }), /lighting_layer_binding_mismatch|lighting_layer_pack_asset_mismatch/);
    await refusal(load(authoredLightingLayer(victoria, { tamper: m => { m.pack_assets['mesh.glb'].sha256 = '0'.repeat(64); } }).files),
        /lighting_layer_pack_asset_mismatch:mesh.glb/);
    // One flipped byte in a layer file.
    const flipped = { ...layer.files, 'splat-basis-u8.bin': Buffer.from(layer.files['splat-basis-u8.bin']) };
    flipped['splat-basis-u8.bin'][7] ^= 1;
    await refusal(load(flipped), /lighting_layer_file_mismatch:splat-basis-u8.bin/);
    // Provenance must be modeled, never measured; schema pinned.
    await refusal(load(authoredLightingLayer(victoria, { tamper: m => { m.provenance.measured = true; } }).files), /lighting_layer_schema/);
    await refusal(load(authoredLightingLayer(victoria, { tamper: m => { m.schema = 'home-lighting-layer-2'; } }).files), /lighting_layer_schema/);
    // Absent layer.
    await refusal(load({}), /lighting_layer_absent/);
    // The layer lives beside the pack, never inside it.
    assert.equal(Loader.layerBaseUrl('https://home.test/content-packs/victoria/' + victoria.revision + '/', victoria.revision),
        'https://home.test/content-packs/victoria/' + victoria.revision + '.lighting/');
});

test('the blend: dimming each light is monotone and zero intensity removes exactly its contribution', async () => {
    const layer = await load(authoredLightingLayer(victoria).files);
    const set = Blend.packReceiverSet(layer);
    const weightsFor = list => Blend.blendWeights({ lights: list, columns: layer.columnsMeta, columnScale: layer.columnScale }).weights;
    const full = Blend.blendWeights({ lights: lights(), columns: layer.columnsMeta, columnScale: layer.columnScale });
    assert.equal(full.matched.length, 28, 'every down and radial zone of the four sockets has its column');
    // The window column (capture daylight) is off at night.
    assert.deepEqual(Array.from(full.weights.subarray(28 * 4, 29 * 4)), [0, 0, 0, 0]);
    const luminance = (weights, i) => Exposure.lumaOf(Blend.blendReceiver(set, i, weights));
    const all = lights();
    for (const target of [all[0], all[3], all[8 + 6]]) {
        const key = Blend.lightKey(target);
        let previous = null;
        for (const level of [0, 0.05, 0.2, 0.5, 0.8, 1]) {
            const list = all.map(light => (Blend.lightKey(light) === key
                ? { ...light, level, fluxFraction: Model.litDimmingFraction(level) } : light));
            const weights = weightsFor(list);
            const values = Array.from({ length: set.count }, (_, i) => luminance(weights, i));
            if (previous) values.forEach((value, i) => assert.ok(value >= previous[i] - 1e-12, `${key} receiver ${i} level ${level}`));
            previous = values;
        }
        // Zero intensity == the light absent, receiver by receiver, exactly.
        const off = weightsFor(all.map(light => (Blend.lightKey(light) === key ? { ...light, level: 0, fluxFraction: 0 } : light)));
        const absent = weightsFor(all.filter(light => Blend.lightKey(light) !== key));
        for (let i = 0; i < set.count; i++) assert.deepEqual(Blend.blendReceiver(set, i, off), Blend.blendReceiver(set, i, absent));
        // ... and it did contribute somewhere at full.
        assert.ok(Array.from({ length: set.count }, (_, i) => luminance(weightsFor(all), i) - luminance(absent, i)).some(d => d > 1e-6), key);
    }
    // All off: only the disclosed, assumed ambient floor remains.
    const dark = weightsFor(lights({ down: 0, zone: 0 }));
    const ambient = Exposure.LIT_AMBIENT_FLOOR.lux * Model.LIT_EXPOSURE;
    for (let i = 0; i < set.count; i += 97) {
        const rgb = Blend.blendReceiver(set, i, dark), word = set.meta[i * 4];
        const rho = (word & 255) / 255;
        assert.ok(Exposure.lumaOf(rgb) > 0 && Exposure.lumaOf(rgb) <= ambient * 1.01, 'ambient only');
        assert.ok(rho > 0);
    }
});

test('the blend matches the layer contract decode and L2\'s display chain', async () => {
    const layer = await load(authoredLightingLayer(victoria).files);
    const set = Blend.packReceiverSet(layer);
    const { weights } = Blend.blendWeights({ lights: lights({ down: 0.6, zone: 0.3, kelvin: 2700 }), columns: layer.columnsMeta, columnScale: layer.columnScale, gain: 4 });
    const srgb = c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const half = h => { const e = (h >>> 10) & 31, m = h & 1023; return e === 0 ? m * 2 ** -24 : (1 + m / 1024) * 2 ** (e - 15); };
    for (const i of [0, 17, 440, 600, 900]) {
        // README: b_k = (u8/255)^2 * scale * column_scale_k; E = tint/lum(tint) * sum w_k c_k b_k; L = albedo/pi * E.
        const t = [0, 1, 2].map(c => layer.tint[i * 3 + c] / 255), ty = 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2];
        const E = [0, 0, 0];
        for (let k = 0; k < layer.columns; k++) {
            const b = (layer.basis[i * layer.columns + k] / 255) ** 2 * half(layer.scale[i]) * layer.columnScale[k];
            for (let c = 0; c < 3; c++) E[c] += b * weights[k * 4 + c] / layer.columnScale[k] * t[c] / ty;
        }
        const ambient = Blend.blendWeights({ lights: [], columns: [] }).weights.subarray(36 * 4, 36 * 4 + 3);
        const expected = [0, 1, 2].map(c => srgb(layer.albedo[i * 3 + c] / 255) / Math.PI * (E[c] + ambient[c]));
        Blend.blendReceiver(set, i, weights).forEach((value, c) => assert.ok(Math.abs(value - expected[c]) <= 1e-9 + 1e-6 * expected[c], `${i}:${c}`));
        const display = Blend.blendReceiverDisplay(set, i, weights);
        const chain = Exposure.litDisplayPixel(expected, { preExposure: Model.LIT_EXPOSURE, gain: 4 }).map(Exposure.srgbEncode);
        display.forEach((value, c) => assert.ok(Math.abs(value - chain[c]) < 1e-6));
    }
    // The GLSL pass stores the scene-referred radiance (half float): no tone,
    // gain or encode per receiver; the look runs once per pixel in the frame.
    const glsl = Blend.litBlendFragmentGlsl({ windowSlot: 28 });
    assert.match(glsl, /gl_FragColor = vec4\(c, 1\.0\);/);
    assert.doesNotMatch(glsl, /agx|srgbEncode|mesopic/i, 'no display chain in the blend');
});

test('a live spot\'s receiver column: shadowed direct light plus its bounce, linear in candela, zero when off', async () => {
    const scene = twoRoomScene();
    const receivers = Bake.buildReceivers(scene.triangles), bvh = Bake.buildBvh(scene.triangles, scene.albedo);
    // A spot in room A aimed through the shared wall at room B's floor: the
    // wall facing it is lit, room B's floor inside the cone is shadowed, and a
    // floor point outside the cone gets nothing.
    const spot = { ...roomALights({ aim: [6, 2, 0] })[0], candela: 1, color: [1, 1, 1] };
    const probes = [[[3.99, 2.15, 1.25], [-1, 0, 0], 'lit'], [[6, 2, 0.001], [0, 0, 1], 'shadowed'], [[0.4, 3.6, 0.001], [0, 0, 1], 'outside']];
    const positions = Float32Array.from(probes.flatMap(probe => probe[0])), normals = Float32Array.from(probes.flatMap(probe => probe[1]));
    const extra = { positions, normals, count: probes.length };
    // This scene is meshed at 0.25 m; the real scan's vertices are about 3 cm apart.
    extra.nearest = Bake.nearestReceivers(receivers.receivers, receivers.receiverCount, positions, normals, extra.count, { radius: 0.3 });
    assert.ok(extra.nearest.every(r => r !== 0xffffffff), 'each probe sits on the scan');
    const column = Bake.receiverSpotColumn(bvh, Bake.packLights([spot]).data, extra, null);
    const half = Bake.halfTableF32(), lum = i => 0.2126 * half[column[i * 3]] + 0.7152 * half[column[i * 3 + 1]] + 0.0722 * half[column[i * 3 + 2]];
    probes.forEach(([, , expectation], i) => {
        if (expectation === 'lit') assert.ok(lum(i) > 0, `probe ${i} lit`);
        else assert.equal(lum(i), 0, `probe ${i} ${expectation}`);
    });
    // The bounce is the spot's layer at the nearest scan receiver.
    const layerHalves = new Uint16Array(receivers.receiverCount * 3).fill(Bake.toHalf(0.25));
    const withBounce = Bake.receiverSpotColumn(bvh, Bake.packLights([spot]).data, extra, layerHalves);
    assert.equal(half[withBounce[3]], 0.25, 'a shadowed receiver still gets the spot\'s bounce');

    // Spot weights scale linearly with candela; zero candela removes it.
    const layer = await load(authoredLightingLayer(victoria).files), set = Blend.packReceiverSet(layer);
    const spots = new Uint32Array(set.count * Blend.LIT_SPOT_WORDS);
    Blend.writeSpotColumn(spots, set.count, 1, new Uint16Array(set.count * 3).fill(Bake.toHalf(0.5)));
    const key = Blend.lightKey(lights()[7]);
    const dark = lights({ down: 0, zone: 0 });
    const at = level => {
        const list = dark.map(item => (Blend.lightKey(item) === key ? { ...item, level, fluxFraction: Model.litDimmingFraction(level),
            candela: item.peakCandela * Model.litDimmingFraction(level) } : item));
        const { weights, unbaked } = Blend.blendWeights({ lights: list, columns: layer.columnsMeta, columnScale: layer.columnScale, spotSlots: new Map([[key, 1]]) });
        assert.equal(unbaked.length, 3, 'the other three spots have no column yet');
        return Exposure.lumaOf(Blend.blendReceiver(set, 5, weights, spots));
    };
    const off = at(0), mid = at(0.5), full = at(1);
    const ambientOnly = Exposure.lumaOf(Blend.blendReceiver(set, 5, Blend.blendWeights({ lights: dark.filter(item => item.kind !== 'spot'),
        columns: layer.columnsMeta, columnScale: layer.columnScale }).weights));
    assert.equal(off, ambientOnly, 'a spot at zero adds nothing');
    assert.ok(off < mid && mid < full);
    assert.ok(Math.abs((full - off) / (mid - off) - 1 / Model.litDimmingFraction(0.5)) < 1e-3, 'linear in candela');
});

test('L5\'s bake host posts each moved spot\'s column for a registered receiver set', async () => {
    const scene = twoRoomScene();
    const endpoint = Bake.createLocalBakeEndpoint({ gpu: null });
    const messages = [];
    const client = Bake.createBakeClient({ endpoint, onReceiverLayer: message => messages.push(message), onScene: () => {}, onLightmap: () => {} });
    client.setScene(new Float32Array(scene.triangles), new Float32Array(scene.albedo));
    const probes = [[1.2, 1.4, 0.001], [1.3, 1.5, 0.001], [3.5, 0.5, 0.001]];
    client.setReceivers('splat', Float32Array.from(probes.flat()), Float32Array.from(probes.flatMap(() => [0, 0, 1])));
    const { candela, color, ...spot } = roomALights()[0];
    client.update({ lights: [spot], weights: { [spot.key]: [1, 1, 1] }, exposure: 1, quality: 'cpu', aims: [] });
    for (let i = 0; i < 1000 && !messages.length; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(messages.length, 1);
    assert.equal(messages[0].set, 'splat'); assert.equal(messages[0].key, spot.key);
    assert.equal(messages[0].data.length, probes.length * 3);
    assert.ok(Bake.halfTableF32()[messages[0].data[0]] > 0, 'the pool is lit');
    // Moving the spot re-bakes its column with a newer version.
    client.update({ lights: [{ ...spot, direction: [0.1, 0, -1] }], weights: { [spot.key]: [1, 1, 1] }, exposure: 1, quality: 'cpu', aims: [] });
    for (let i = 0; i < 1000 && messages.length < 2; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(messages.length, 2);
    assert.ok(messages[1].version > messages[0].version);
    client.dispose();
});

test('the camera look: white balance toward the fixtures keeps exposure, highlights land at display 0.8', () => {
    const warm = Model.cctToLinearRgb(3000), m = Exposure.whiteBalanceMatrix(warm);
    const out = m.map(row => row[0] * warm[0] + row[1] * warm[1] + row[2] * warm[2]);
    assert.ok(Math.abs(Exposure.lumaOf(out) - Exposure.lumaOf(warm)) < 1e-9, 'a white balance moves colour, not exposure');
    const spread = rgb => Math.max(...rgb) / Math.min(...rgb);
    assert.ok(spread(out) < spread(warm) && spread(out) > 1.02, 'D = 0.75: mostly neutral, a little warmth left');
    const identity = Exposure.whiteBalanceMatrix(warm, 0);
    // The published sRGB-XYZ matrices are inverses to about 1e-7.
    identity.forEach((row, r) => row.forEach((v, c) => assert.ok(Math.abs(v - (r === c ? 1 : 0)) < 1e-5)));
    // Highlights metered at L cd/m² move 60 % of the way (in EV) toward display
    // 0.8 (L2's approved partial adaptation), so a dimmer room still reads dimmer.
    const look = Exposure.LIT_CAMERA_LOOK, toTarget = L => Math.log2(Exposure.cameraHighlightLinear() / (L * Model.LIT_EXPOSURE));
    for (const L of [0.05, 1, 9.3, 60]) {
        const { adaptEv } = Exposure.cameraAdaptationFromLuminance(L, Model.LIT_EXPOSURE);
        assert.ok(Math.abs(adaptEv - Math.min(look.maxEv, Math.max(look.minEv, look.fraction * toTarget(L)))) < 1e-12, String(L));
        const full = toTarget(L), x = L * Model.LIT_EXPOSURE * 2 ** full;
        assert.ok(Math.abs(Exposure.srgbEncode(Exposure.litCameraPixel([x, x, x])[1]) - look.highlightDisplay) < 1e-3, 'the target is display 0.8');
    }
    const shown = L => L * 2 ** Exposure.cameraAdaptationFromLuminance(L, Model.LIT_EXPOSURE).adaptEv;
    assert.ok(shown(1) < shown(9.3) && shown(0.2) < shown(1), 'dimming still reads as dimmer');
    // No toe lift, no Purkinje: black stays black and the chain is monotone in luminance.
    assert.deepEqual(Exposure.litCameraPixel([0, 0, 0]).map(v => +v.toFixed(4)), [0, 0, 0]);
    let previous = -1;
    for (const x of [1e-4, 1e-3, 1e-2, 0.1, 1, 4, 16]) { const y = Exposure.lumaOf(Exposure.litCameraPixel([x, x, x])); assert.ok(y >= previous); previous = y; }
});

test('the night ambient follows the window column; exterior splats get the assumed night exterior', async () => {
    const layer = await load(authoredLightingLayer(victoria).files), set = Blend.packReceiverSet(layer);
    const window = layer.columnsMeta.findIndex(column => column.kind === 'window');
    const median = 0.5, dark = lights({ down: 0, zone: 0 });
    const { weights } = Blend.blendWeights({ lights: dark, columns: layer.columnsMeta, columnScale: layer.columnScale, look: 'camera', windowMedian: median });
    const floor = Model.cctToLinearRgb(Exposure.LIT_AMBIENT_FLOOR.kelvin);
    const lux = Exposure.LIT_AMBIENT_FLOOR.lux, share = Blend.LIT_WINDOW_AMBIENT.share;
    for (let c = 0; c < 3; c++) {
        assert.ok(Math.abs(weights[36 * 4 + c] - (1 - share) * lux * floor[c] * Model.LIT_EXPOSURE) < 1e-9, 'a uniform remainder');
        assert.ok(Math.abs(weights[42 * 4 + c] - layer.columnScale[window] * share * lux / median * floor[c] * Model.LIT_EXPOSURE) < 1e-9,
            'the rest through the window column, scaled to its median');
        assert.equal(weights[window * 4 + c], 0, 'the window column is blended on its own, capped');
    }
    assert.ok(Math.abs(weights[42 * 4 + 3] - Blend.LIT_WINDOW_AMBIENT.capRatio * median / layer.columnScale[window]) < 1e-9, 'capped at 4x the median');
    // The capped window term, receiver by receiver: never above 4x the median share.
    const withWindow = { ...set, windowSlot: window };
    const capped = Blend.blendWeights({ lights: dark, columns: layer.columnsMeta, columnScale: layer.columnScale, look: 'camera', windowMedian: 1e-6 }).weights;
    const limit = (share * 4 + (1 - share)) * lux;
    for (let i = 0; i < set.count; i += 37) {
        const rgb = Blend.blendReceiver(withWindow, i, capped), word = set.meta[i * 4];
        const rho = Exposure.lumaOf([word & 255, (word >>> 8) & 255, (word >>> 16) & 255].map(v => (v / 255 <= 0.04045 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4)));
        if (rho > 0.05) assert.ok(Exposure.lumaOf(rgb) / Model.LIT_EXPOSURE / (rho / Math.PI) <= limit * 1.6, 'window ambient is capped near the glass');
    }
    // Without a window column the whole floor stays uniform (L4's behaviour).
    const flat = Blend.blendWeights({ lights: dark, columns: layer.columnsMeta, columnScale: layer.columnScale }).weights;
    assert.deepEqual(Array.from(flat.subarray(window * 4, window * 4 + 3)), [0, 0, 0]);
    // An exterior splat (flag 4) shows the assumed night exterior, whatever the fixtures do.
    const exterior = { ...set, meta: Uint32Array.from(set.meta) };
    exterior.meta[0] = (exterior.meta[0] | (Blend.LIT_RECEIVER_FLAGS.emitter << 24)) >>> 0;
    const on = Blend.blendWeights({ lights: lights(), columns: layer.columnsMeta, columnScale: layer.columnScale, look: 'camera' }).weights;
    const allOff = Blend.blendWeights({ lights: dark, columns: layer.columnsMeta, columnScale: layer.columnScale, look: 'camera' }).weights;
    assert.deepEqual(Blend.blendReceiver(exterior, 0, on), Blend.blendReceiver(exterior, 0, allOff));
    const ext = Blend.blendReceiver(exterior, 0, on), cd = Exposure.lumaOf(ext) / Model.LIT_EXPOSURE;
    assert.ok(cd > Blend.LIT_NIGHT_EXTERIOR.cdM2 * 0.5 && cd < Blend.LIT_NIGHT_EXTERIOR.cdM2 * 1.5, 'about the assumed exterior luminance: ' + cd);
    assert.ok(ext[0] > ext[2], 'warm');
    // The camera's CPU twin and the eye's differ only in presentation.
    const camera = Blend.blendReceiverDisplay(set, 5, on, null, { look: 'camera' }), eye = Blend.blendReceiverDisplay(set, 5, on, null, { look: 'eye' });
    assert.notDeepEqual(camera, eye);
    assert.deepEqual(Blend.blendReceiverDisplay(set, 5, on, null, { look: 'camera', operator: 'linear' }), Blend.blendReceiverDisplay(set, 5, on, null, { operator: 'linear' }),
        'the linear comparison view carries no look');
});

test('the point cloud stays monochrome: grey surfaces, the fixtures\' own colour, black where they do not reach', async () => {
    const layer = await load(authoredLightingLayer(victoria).files, 'points');
    const grey = Blend.greyAlbedo(layer.albedo);
    for (let i = 0; i < grey.length; i += 3) assert.ok(grey[i] === grey[i + 1] && grey[i + 1] === grey[i + 2], 'no capture colour');
    const set = Blend.packReceiverSet({ ...layer, albedo: grey });
    const at = (level, kelvin) => Blend.blendWeights({ lights: lights({ down: level, zone: level, kelvin }), columns: layer.columnsMeta,
        columnScale: layer.columnScale, look: 'camera', windowMedian: 0.5, monochrome: true }).weights;
    // All off: black (no ambient, no exterior).
    const off = at(0, 3000);
    for (let i = 0; i < set.count; i += 41) assert.deepEqual(Blend.blendReceiver(set, i, off), [0, 0, 0]);
    // A warm light reads warm and a cool one cooler: no white balance on the cloud.
    const warm = Blend.blendReceiverDisplay(set, 5, at(0.75, 2700), null, { look: 'camera' });
    const cool = Blend.blendReceiverDisplay(set, 5, at(0.75, 5000), null, { look: 'camera' });
    assert.ok(warm[0] / warm[2] > cool[0] / cool[2] && warm[0] > warm[2], 'the colour temperature shows');
});

test('all-off monochrome lighting retains finite white-balance metadata', () => {
    const result = Blend.blendWeights({ lights: [], look: 'camera', monochrome: true });
    assert.ok(Number.isFinite(result.whiteKelvin));
    assert.ok(result.weights.every(Number.isFinite));
});
