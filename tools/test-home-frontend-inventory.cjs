'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { inspectFrontendInventory } = require('./lib/home-frontend-inventory.cjs');

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'home-frontend-inventory-'));
  const indexSource = `<script src="./runtime.js"></script><script>var files = [['app.jsx', true], ['home-feature-loader.js', false], ['home-3d/engine.js', false]];</script>`;
  const featureLoaderSource = `const FEATURES = { room: { files: [['lazy.jsx', true]] } };`;
  const runtimeSource = 'const swUrl = `./worker.js?v=${build}`; navigator.serviceWorker.register(swUrl, {scope:"./"});';
  const files = {
    'app.jsx': '', 'home-feature-loader.js': featureLoaderSource, 'runtime.js': runtimeSource, 'worker.js': '', 'lazy.jsx': '',
    'den.js': '',
    'home-3d/engine.js': "export async function createEngine() { const [markers] = await Promise.all([sib('markers.js')]); return { world: () => sib('missing-optional-world.mjs') }; }",
    'home-3d/markers.js': 'const fixture = await import(`./fixture.mjs${new URL(import.meta.url).search}`);',
    'home-3d/fixture.mjs': "import '../den.js';",
  };
  for (const [relative, source] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, relative)), { recursive: true });
    fs.writeFileSync(path.join(root, relative), source);
  }
  return { root, inspect: () => inspectFrontendInventory(root, { indexSource, featureLoaderSource, runtimeSource }),
    dispose: () => fs.rmSync(root, { recursive: true, force: true }) };
}

test('actual boot/lazy declarations and ordinary engine imports account for reachable sources', () => {
  const f = fixture();
  try {
    const inventory = f.inspect();
    assert.deepEqual(inventory.missingDeclared, []); assert.deepEqual(inventory.orphanedFiles, []);
    assert.ok(inventory.lazyFiles.includes('lazy.jsx')); assert.ok(inventory.declaredFiles.includes('den.js'));
    assert.ok(!inventory.declaredFiles.includes('home-3d/missing-optional-world.mjs'));
  } finally { f.dispose(); }
});

test('a newly added orphan is still refused rather than broadly exempted by lazy loading', () => {
  const f = fixture();
  try {
    fs.writeFileSync(path.join(f.root, 'unreachable.js'), 'window.unreachable = true;');
    assert.deepEqual(f.inspect().orphanedFiles, ['unreachable.js']);
  } finally { f.dispose(); }
});

test('missing declared lazy scripts and missing required module imports remain failures', () => {
  const f = fixture();
  try {
    fs.unlinkSync(path.join(f.root, 'lazy.jsx'));
    assert.ok(f.inspect().missingDeclared.includes('lazy.jsx'));
    fs.writeFileSync(path.join(f.root, 'home-3d/fixture.mjs'), "import '../missing-den.js';");
    const mutated = f.inspect();
    assert.ok(mutated.missingDeclared.includes('missing-den.js')); assert.ok(mutated.orphanedFiles.includes('den.js'));
  } finally { f.dispose(); }
});
