// Every GLSL material site has a TSL twin on the WebGPU path (?renderer=webgpu)
// that was written from one exact GLSL revision. This builds each site's real
// material with the shipped modules (three r180, as the WebGL path runs) and
// checks its fingerprint against the one the port records. A GLSL edit fails
// here until webgpu-material-ports.mjs follows. No GPU, browser or household
// data. Set HOME_WEBGPU_PORTS_PRINT=1 to print the current fingerprints.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { register, createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'app/src');
const fileUrl = relative => pathToFileURL(path.join(SRC, relative)).href;
const threeURL = fileUrl('vendor/three-0.180.0/three.module.js');
// The browser's importmap, for Node: bare 'three' is the vendored r180 build.
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(specifier, context, next) {
  if (specifier === 'three') return { url: ${JSON.stringify(threeURL)}, shortCircuit: true };
  return next(specifier, context);
}`));
const THREE = await import(threeURL);
const Prints = await import(fileUrl('home-3d/webgpu-port-fingerprints.mjs'));
const { PORT_FINGERPRINTS, ON_BEFORE_COMPILE_PORTS, shaderMaterialFingerprint, probeOnBeforeCompile } = Prints;

const actual = {};
function record(id, fingerprint) {
  assert.ok(!(id in actual) || actual[id] === fingerprint, `${id}: one GLSL revision per port`);
  actual[id] = fingerprint;
}
function recordOnBeforeCompile(material) {
  const id = ON_BEFORE_COMPILE_PORTS[material.customProgramCacheKey()];
  assert.ok(id, 'ported program key ' + material.customProgramCacheKey());
  const probe = probeOnBeforeCompile(material);
  record(id, probe.fingerprint);
  return probe;
}

// pointcloud.js: the shaders are exported as built.
{
  globalThis.window ??= { devicePixelRatio: 1 };
  const pointcloud = await import(fileUrl('home-3d/pointcloud.js'));
  record('pointcloud', shaderMaterialFingerprint(pointcloud));
}

// modes.js: the real display-mesh path, with an authored in-memory GLB scene.
async function scanDisplayMaterial(photoFloorSupport) {
  const { createModes } = await import(fileUrl('home-3d/modes.js'));
  const group = new THREE.Group();
  const texture = new THREE.Texture();
  group.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ map: texture })));
  const frame = { frame: 'z_up_metric_floor0', extents_m: [4, 4, 2.5], splat_baked: true, site: 'victoria' };
  const modes = createModes({ apartmentRoot: new THREE.Group(), pointsMaterial: { uniforms: { uOpacity: { value: 1 } } },
    sim: false, assetCandidates: name => ['memory://' + name], fetchFrame: async () => frame, getPoints: () => null,
    scene: null, renderer: null, camera: null, performanceController: null, siteId: 'victoria', contentRevision: 'authored-r1',
    requireRegistration: true, dollhouseCutaway: true, photoFloorSupport,
    loadAdapters: { loadGlb: async () => ({ scene: group }) } });
  const result = await modes.preload(['mesh']);
  assert.equal(result.ok, true, JSON.stringify(result));
  let material = null;
  modes.getMesh().traverse(node => { if (node.isMesh) material = node.material; });
  return { material, modes };
}
for (const photoFloor of [false, true]) {
  const { material, modes } = await scanDisplayMaterial(photoFloor);
  const probe = recordOnBeforeCompile(material);
  for (const name of ['uDollhouseWorldToLocal', 'uDollhouseConfig', 'uMeshNearCut', ...(photoFloor ? ['uPhotoFloorOnly'] : [])]) {
    assert.ok(probe.uniforms[name] && 'value' in probe.uniforms[name], 'scan display hands back ' + name);
  }
  modes.dispose();
}

// equipment-virtual-fixtures.mjs: the real authored fixtures, as the radial
// visual proof builds them.
{
  const { createEquipmentVirtualFixtures } = await import(fileUrl('home-3d/equipment-virtual-fixtures.mjs'));
  const binding = { content_revision: 'authored-victoria-radial-visual-r1', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  const parent = new THREE.Group(), api = createEquipmentVirtualFixtures(parent, { binding });
  assert.equal(api.setDocument(authoredDocument(binding, Core)).status, 'ready');
  const shaders = new Set(), patched = new Set();
  parent.traverse(node => {
    for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) {
      if (material.isShaderMaterial) shaders.add(material);
      else if (Object.hasOwn(material, 'onBeforeCompile')) patched.add(material);
    }
  });
  const beams = [...shaders].filter(material => material.uniforms.uRoomPlanes);
  const glows = [...shaders].filter(material => !material.uniforms.uRoomPlanes);
  assert.ok(beams.length > 0 && glows.length > 0, 'fixtures build beams and glows');
  for (const material of beams) record('equipment-beam', shaderMaterialFingerprint(material));
  for (const material of glows) record('equipment-glow', shaderMaterialFingerprint(material));
  const keys = new Set();
  for (const material of patched) {
    keys.add(material.customProgramCacheKey());
    const probe = recordOnBeforeCompile(material);
    if (material.customProgramCacheKey() === 'equipment-visible-receiver-1') {
      for (const name of ['uReceiverSceneDepth', 'uReceiverViewport', 'uReceiverProjectionInverse', 'uReceiverDepthValid']) {
        assert.ok(probe.uniforms[name] && 'value' in probe.uniforms[name], 'receiver hands back ' + name);
      }
    }
  }
  assert.deepEqual([...keys].sort(), ['equipment-orb-halo-1', 'equipment-visible-receiver-1']);
  api.dispose();
}

// splat-cutaway.js: Spark's Photo cutaway graph (shared GLSL and its dyno
// statements); splat-cutaway-tsl.mjs is its twin on the WebGPU splat pipeline.
{
  const { DOLLHOUSE_CUTAWAY_GLSL } = await import(fileUrl('home-3d/dollhouse-cutaway.mjs'));
  const { SPLAT_EXTERIOR_CLEANUP_GLSL } = await import(fileUrl('home-3d/splat-exterior-cleanup.mjs'));
  const statementsSource = Prints.splatCutawayStatements(fs.readFileSync(path.join(SRC, 'home-3d/splat-cutaway.js'), 'utf8'));
  assert.ok(statementsSource, 'splat-cutaway.js keeps one dyno statements block');
  record('splat-cutaway', Prints.splatCutawayFingerprint({ dollhouseGlsl: DOLLHOUSE_CUTAWAY_GLSL,
    exteriorGlsl: SPLAT_EXTERIOR_CLEANUP_GLSL, statementsSource }));
}

// apartment-directional-presentation.mjs needs a DOM canvas to build; its fill
// shader is two literals in one ShaderMaterial, read from the source as is.
{
  const source = fs.readFileSync(path.join(SRC, 'home-3d/apartment-directional-presentation.mjs'), 'utf8');
  const literals = [...source.matchAll(/new THREE\.ShaderMaterial\(\{\s*vertexShader: ('(?:[^'\\]|\\.)*'),\s*fragmentShader: ('(?:[^'\\]|\\.)*'),/g)];
  assert.equal(literals.length, 1, 'one directional ShaderMaterial');
  const [vertexShader, fragmentShader] = literals[0].slice(1).map(literal => vm.runInNewContext(literal));
  record('directional-fill', shaderMaterialFingerprint({ vertexShader, fragmentShader }));
}

if (process.env.HOME_WEBGPU_PORTS_PRINT) console.log(JSON.stringify(actual, null, 2));

test('every GLSL site has a WebGPU port written from its current GLSL', () => {
  assert.deepEqual(Object.keys(actual).sort(), Object.keys(PORT_FINGERPRINTS).sort(), 'every port is exercised');
  for (const [id, fingerprint] of Object.entries(actual)) {
    assert.equal(fingerprint, PORT_FINGERPRINTS[id],
      `${id}: the GLSL changed since its TSL port. Update webgpu-material-ports.mjs, then PORT_FINGERPRINTS.`);
  }
});

test('no other module in home-3d builds a GLSL material', () => {
  // A new ShaderMaterial or onBeforeCompile site needs a port before it ships.
  const known = new Set(['pointcloud.js', 'modes.js', 'equipment-spotlight-visual.mjs', 'equipment-virtual-fixtures.mjs',
    'apartment-directional-presentation.mjs', 'dollhouse-cutaway-material.js', 'photo-floor-support.js',
    'webgpu-port-fingerprints.mjs', 'webgpu-material-ports.mjs', 'webgpu-renderer.mjs', 'splat-cutaway.js',
    // L4: its ShaderMaterial is the WebGL GPGPU blend; the WebGPU twin is the TSL compute in lit-basis-blend.mjs.
    'lit-receivers.mjs']);
  for (const name of fs.readdirSync(path.join(SRC, 'home-3d'))) {
    if (!/\.(?:m?js)$/.test(name) || known.has(name)) continue;
    const text = fs.readFileSync(path.join(SRC, 'home-3d', name), 'utf8');
    assert.ok(!/ShaderMaterial|onBeforeCompile/.test(text), `${name} builds a GLSL material without a WebGPU port`);
  }
});

test('the probe never changes the real material', async () => {
  const { material } = await scanDisplayMaterial(true);
  const before = material.customProgramCacheKey();
  const first = probeOnBeforeCompile(material), second = probeOnBeforeCompile(material);
  assert.equal(first.fingerprint, second.fingerprint);
  assert.equal(material.customProgramCacheKey(), before);
});
