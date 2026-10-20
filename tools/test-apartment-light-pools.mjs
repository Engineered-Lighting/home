// APT-L02: beam and pool rules from authored descriptors, plus the drawer on a
// borrowed group. No renderer, equipment state, receiver or output.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';

const vendor = new URL('../app/src/vendor/three-0.180.0/three.module.js', import.meta.url).href;
const source = fs.readFileSync(new URL('../app/src/home-3d/apartment-light-pools.mjs', import.meta.url), 'utf8');
const imports = /import \* as THREE from 'three';/g;
assert.equal([...source.matchAll(imports)].length, 1, 'one exact bare Three resolution seam');
const Pools = await import('data:text/javascript;base64,' + Buffer.from(source.replace(imports,
  `import * as THREE from ${JSON.stringify(vendor)};`)).toString('base64'));

const channel = (on, brightness, kelvin = 3000) => ({ on, brightness, color_temp_kelvin: kelvin });
const fixture = (id, { spot = channel(true, .8), down = channel(true, .5, 2700), x = 1 } = {}) => ({
  fixture_id: id, label: 'Authored ' + id, anchor_m: [x, 2, 2.4],
  state: { spotlight: spot, downward: down, radials: [], pan_deg: 0, tilt_deg: 45 },
  beam: { origin_m: [x, 2, 2.26], endpoint_m: [x + 1, 2, 0], reach_m: Math.hypot(1, 2.26) },
});
const floor = { normal: [0, 0, 1], offset_m: 0 };
const fixtures = [fixture('a'), fixture('b', { x: 3 }), fixture('c', { spot: channel(false, .8), down: channel(false, .5), x: 5 })];

test('only the selected light draws its axis and faint cone by default; all and none switch it', () => {
  const selected = Pools.lightPoolsPlan({ fixtures, selectedId: 'a', floor });
  assert.equal(selected.beams, 'selected'); assert.deepEqual(selected.axes.map(axis => axis.fixture_id), ['a']);
  const axis = selected.axes[0];
  assert.deepEqual([axis.from, axis.to], [fixtures[0].beam.origin_m, fixtures[0].beam.endpoint_m]);
  assert.ok(Math.abs(axis.radius - fixtures[0].beam.reach_m * Math.tan(10 * Math.PI / 180)) < 1e-12, 'cone from the 20 degree spot FWHM');
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: 'a', beams: 'all', floor }).axes.map(axis => axis.fixture_id), ['a', 'b']);
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: 'a', beams: 'none', floor }).axes, []);
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: null, floor }).axes, [], 'nothing selected draws no beam');
  assert.equal(Pools.lightPoolsPlan({ fixtures, beams: 'sideways' }).beams, 'selected');
});

test('a selected light whose spot is off shows a grey dashed ring at its last aim', () => {
  const plan = Pools.lightPoolsPlan({ fixtures, selectedId: 'c', floor });
  assert.deepEqual(plan.axes, []); assert.deepEqual(plan.rings.map(ring => ring.fixture_id), ['c']);
  assert.deepEqual(plan.rings[0].center, fixtures[2].beam.endpoint_m);
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: 'c', beams: 'none', floor }).rings, []);
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: 'a', floor }).rings, [], 'a lit spot has no last-aim ring');
});

test('each lit Down channel pools on the floor below its socket; labels say modeled', () => {
  const plan = Pools.lightPoolsPlan({ fixtures, selectedId: 'a', floor });
  assert.deepEqual(plan.downPools.map(pool => pool.fixture_id), ['a', 'b'], 'an off Down channel draws no pool');
  assert.deepEqual(plan.downPools[0].center, [1, 2, .008]);
  assert.ok(Math.abs(plan.downPools[0].radius - 2.4 * Math.tan(30 * Math.PI / 180)) < 1e-12);
  assert.deepEqual(plan.labels.map(label => label.text), ['Spot · modeled', 'Down · modeled'], 'only the selected light is labelled');
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: 'a', pools: false, floor }).downPools, []);
  assert.deepEqual(Pools.lightPoolsPlan({ fixtures, selectedId: 'a', floor: null }).downPools, [], 'no floor reference, no invented pool');
  const tilted = { normal: [0, Math.sin(.1), Math.cos(.1)], offset_m: .2 };
  assert.ok(Math.abs(Pools.floorHeightAt(tilted, 1, 2) - (.2 - Math.sin(.1) * 2) / Math.cos(.1)) < 1e-12);
  assert.equal(plan.truth_role, 'virtual_preview'); assert.equal(plan.physical_commands, false);
});

test('the drawer owns only its group, reuses anchors and disposes cleanly', () => {
  const group = new THREE.Group(), anchors = [];
  const layer = { group, addAnchor: ({ point_m, label }) => {
    const handle = { point_m, label, disposed: false, update(patch) { Object.assign(handle, patch); return true; }, dispose() { handle.disposed = true; return true; } };
    anchors.push(handle); return handle;
  } };
  const view = Pools.createLightPools({ layer });
  let readback = view.update(Pools.lightPoolsPlan({ fixtures, selectedId: 'a', floor }));
  assert.deepEqual(readback.axes, ['a']); assert.deepEqual(readback.down_pools, ['a', 'b']);
  assert.deepEqual(readback.objects, ['light-pools-axis:a', 'light-pools-cone:a', 'light-pools-cone-rim:a', 'light-pools-down:a', 'light-pools-down:b']);
  assert.deepEqual(anchors.map(anchor => anchor.label), ['Spot · modeled', 'Down · modeled']);
  readback = view.update(Pools.lightPoolsPlan({ fixtures, selectedId: 'c', floor }));
  assert.deepEqual(readback.objects, ['light-pools-last-aim:c', 'light-pools-down:a', 'light-pools-down:b']);
  assert.ok(anchors.every(anchor => anchor.disposed), 'labels of a deselected light are removed');
  assert.equal(group.children.length, 1); assert.equal(group.children[0].name, 'apartment-light-pools');
  view.dispose(); assert.equal(group.children.length, 0); assert.equal(view.readback().disposed, true);
  assert.throws(() => Pools.createLightPools({}), /light_pools_layer_required/);
});
