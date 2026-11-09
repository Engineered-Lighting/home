// The den simulation's click side, without a browser: the data root, the top line on fixtures,
// the hop table and the searches of tests 2 and 19. Each check has a control that must make it fail.
//
//   node --test tools\den-sim\test-den-sim.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const scratch = prefix => fs.mkdtempSync(path.join(os.tmpdir(), prefix));

test('the data root is DEN_SIM_HOME, or else the home folder, and never %LOCALAPPDATA%', t => {
  const tmp = scratch('den-sim-root-');
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const home = path.join(tmp, 'home'), lad = path.join(tmp, 'lad');
  const plant = (root, name) => {
    fs.mkdirSync(path.join(root, 'prepared', name), { recursive: true });
    fs.writeFileSync(path.join(root, 'prepared', name, 'set.json'), JSON.stringify({ schema: 'den-sim-prepared-set-1', refusal: null, ended_unix: 1 }));
  };
  plant(path.join(lad, 'den-sim'), 'aaaaaaaaaaaa');   // the control: a set where the old root was
  const env = { ...process.env, USERPROFILE: home, HOME: home, LOCALAPPDATA: lad };
  delete env.DEN_SIM_HOME;
  const ask = () => JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e',
    `const m = await import(${JSON.stringify(pathToFileURL(path.join(HERE, 'data-root.mjs')).href)}); const root = m.dataRoot();`
    + 'console.log(JSON.stringify({ root, set: m.newestPreparedSet(root) }));'], { env, encoding: 'utf8' }));
  let got = ask();
  assert.equal(got.root, path.join(home, 'den-sim'));
  assert.equal(got.set, null, 'the planted %LOCALAPPDATA% set is not read');
  plant(path.join(home, 'den-sim'), 'bbbbbbbbbbbb');
  got = ask();
  assert.equal(got.set, path.join(home, 'den-sim', 'prepared', 'bbbbbbbbbbbb'), 'a set under the home folder is found');
  const custom = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e',
    `const m = await import(${JSON.stringify(pathToFileURL(path.join(HERE, 'data-root.mjs')).href)}); console.log(JSON.stringify(m.dataRoot()));`],
  { env: { ...env, DEN_SIM_HOME: path.join(tmp, 'elsewhere') }, encoding: 'utf8' }));
  assert.equal(custom, path.join(tmp, 'elsewhere'));
});
