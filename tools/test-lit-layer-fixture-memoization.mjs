// Compare every output byte with the original per-row normalization formula.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { authoredLightingLayer } from './fixtures/apartment-lit-layer-fixtures.mjs';

test('memoizing fixture lobe constants preserves every byte for all receiver kinds', async () => {
  const fixtureUrl = new URL('./fixtures/apartment-lit-layer-fixtures.mjs', import.meta.url);
  const source = fs.readFileSync(fixtureUrl, 'utf8');
  const cached = 'const down = LOBE_PEAKS.down, radial = LOBE_PEAKS.radial, row = [];';
  assert.equal(source.split(cached).length, 2, 'the reference replaces exactly the cached expression');
  const reference = source.replace(cached, 'const down = lobePeakPerLumen(DOWN_HWHM_DEG, 90), radial = lobePeakPerLumen(RADIAL_HWHM_DEG, 180), row = [];')
    .replace("'../../app/src/home-3d/lit-bvh-bake.mjs'", JSON.stringify(new URL('../app/src/home-3d/lit-bvh-bake.mjs', import.meta.url).href));
  const original = await import('data:text/javascript;base64,' + Buffer.from(reference).toString('base64'));
  const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
  const names = ['frame.json', 'collision.glb', 'apartment.ply', 'apartment.mobile.ply', 'points.ply', 'mesh.glb'];
  const pack = { site: 'victoria', revision: 'authored-memoization-r1', manifest: { revision: 'authored-memoization-r1', registrationVersion: 1,
    assets: Object.fromEntries(names.map(name => [name, { sha256: sha256(name), bytes: name.length }])) } };
  const rows = [{ p: [.2, .5, 0], n: [0, 0, 1] }, { p: [2, 4, .8], n: [0, -1, 0] }, { p: [0, 1, 1.2], n: [1, 0, 0] }];
  const options = { sets: ['splat', 'splat-mobile', 'points'], rows };
  const before = original.authoredLightingLayer(pack, options), after = authoredLightingLayer(pack, options);
  assert.deepEqual(Object.keys(after.files), Object.keys(before.files));
  for (const name of Object.keys(before.files)) assert.deepEqual(after.files[name], before.files[name], name);
  assert.deepEqual(after.manifest, before.manifest);
});
