// Contract for tools/run-touched-harnesses.mjs --shard i/n: for the same base,
// the n shard plans are pairwise disjoint, keep the unsharded order, and their
// union is exactly the unsharded --list. Receipt verification accepts only an
// exact, all-green cover of that plan.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const runner = path.join(root, 'tools', 'run-touched-harnesses.mjs');
const run = (argv, timeout = 60000) => {
  const result = spawnSync(process.execPath, [runner, ...argv], { cwd: root, encoding: 'utf8', timeout, windowsHide: true });
  assert.ifError(result.error); assert.equal(result.signal, null);
  return result;
};
function planOf(argv) {
  const result = run([...argv, '--list']);
  assert.equal(result.status, 0, `--list ${argv.join(' ')}\n${result.stdout}\n${result.stderr}`);
  assert.doesNotMatch(result.stdout, /^ {2}(?:RUN|FAIL) /m, 'listing executes nothing');
  return [...result.stdout.matchAll(/^ {2}PLAN (\S+)$/gm)].map(match => match[1]);
}
function assertExactCover(argv, counts) {
  const full = planOf(argv);
  assert.equal(new Set(full).size, full.length, 'the unsharded plan names each item once');
  for (const count of counts) {
    const shards = Array.from({ length: count }, (_, index) => planOf([...argv, '--shard', `${index + 1}/${count}`]));
    const union = shards.flat();
    assert.equal(new Set(union).size, union.length, `shards of ${count} are pairwise disjoint for ${argv.join(' ')}`);
    assert.deepEqual([...union].sort(), [...full].sort(), `union of ${count} shards equals the unsharded plan for ${argv.join(' ')}`);
    for (const shard of shards) assert.deepEqual(shard, full.filter(name => shard.includes(name)), 'each shard keeps the unsharded order');
    assert.deepEqual(planOf([...argv, '--shard', `1/${count}`]), shards[0], 'partition is reproducible');
  }
  return full;
}

const git = argv => execFileSync('git', argv, { cwd: root, encoding: 'utf8' }).trim();
function actualBase() {
  const candidates = [process.env.GITHUB_BASE_REF && `origin/${process.env.GITHUB_BASE_REF}`, 'origin/main'].filter(Boolean);
  for (const ref of candidates) {
    if (spawnSync('git', ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`], { cwd: root, windowsHide: true }).status === 0) return ref;
  }
  assert.fail(`no base ref available among ${candidates.join(', ')}; the workflow checks out full history`);
}

const FILE_SETS = [
  [], ['README.md'], ['app/src/home-app.jsx'], ['app/src/home-tokens.css'],
  ['app/src/home-apartment.jsx', 'app/src/home-3d/engine.js'],
  ['app/src/home-apartment-aim.jsx', 'app/src/home-apartment-cards.jsx', 'app/src/home-3d/equipment-virtual-fixtures.mjs'],
  ['tools/run-touched-harnesses.mjs'], ['web-gateway/server.mjs', 'app/src/index.html'],
];

test('for the actual base, every worker count 1..6 partitions the unsharded --list exactly', () => {
  const base = actualBase();
  const full = assertExactCover(['--base', base], [1, 2, 3, 4, 5, 6]);
  const listed = run(['--base', base, '--list']).stdout.split('\n')[0];
  assert.match(listed, new RegExp(`^\\[touched-harnesses\\] \\d+ changed files → ${full.filter(name => name !== 'check-jsx.js').length} harnesses`));
});

test('fixed file sets and --all partition exactly for 1, 2, 3 and 5 workers', () => {
  for (const files of FILE_SETS) assertExactCover(files.length ? ['--files', files.join(',')] : ['--files', ','], [1, 2, 3, 5]);
  const all = assertExactCover(['--all'], [2, 3]);
  assert.ok(all.length > 40 && all.includes('check-jsx.js'), '--all still lists every run-* harness, mapped suite and check-jsx');
});

test('the partition depends on the plan, not on the order files are given', () => {
  const files = FILE_SETS.flat();
  const forward = [1, 2, 3].map(index => planOf(['--files', files.join(','), '--shard', `${index}/3`]));
  const reverse = [1, 2, 3].map(index => planOf(['--files', [...files].reverse().join(','), '--shard', `${index}/3`]));
  assert.deepEqual(reverse, forward);
});

test('the weight table names only real plan items', () => {
  const source = fs.readFileSync(runner, 'utf8');
  const table = /const SHARD_WEIGHT_S = \{\n([\s\S]*?)\n\};/.exec(source);
  assert.ok(table, 'weight table present');
  const names = [...table[1].matchAll(/^ {2}"([^"]+)": (\d+),$/gm)];
  assert.equal(names.length, table[1].split('\n').length, 'one literal integer weight per line');
  for (const [, name, seconds] of names) {
    assert.ok(fs.existsSync(path.join(root, 'tools', name)), `weighted item exists: ${name}`);
    assert.ok(Number(seconds) >= 1 && Number(seconds) <= 180, `${name} weight within the 180 s per-file deadline`);
  }
});

test('malformed shard and verification arguments are refused before anything runs', () => {
  const readme = ['--files', 'README.md'];
  for (const argv of [[...readme, '--shard', '0/3'], [...readme, '--shard', '4/3'], [...readme, '--shard', '3'],
    [...readme, '--shard', 'a/b'], [...readme, '--shard', '1/0'], [...readme, '--shard', '1/33'], [...readme, '--shard'],
    [...readme, '--shard=1/3'], ['--all', '--base', 'origin/main'], ['--files', 'a', '--base', 'b'],
    [...readme, '--verify-receipts', '.tmp/x'], [...readme, '--shards', '3'],
    [...readme, '--verify-receipts', '.tmp/x', '--shards', '2', '--shard', '1/2'],
    [...readme, '--receipt', '.tmp/x.json', '--list'], [...readme, '--receipt']]) {
    const result = run(argv);
    assert.equal(result.status, 2, `refused: ${argv.join(' ')}\n${result.stdout}${result.stderr}`);
    assert.doesNotMatch(result.stdout, /^ {2}(?:RUN|PLAN) /m);
  }
});

test('receipts from real sharded runs verify, and every inexact or failing cover is rejected', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'touched-shards-'));
  try {
    const files = ['--files', 'tools/test-apartment-travel-guard.cjs,tools/test-apartment-chat-presenter.cjs'];
    const plan = planOf(files);
    assert.deepEqual(plan, ['test-apartment-chat-presenter.cjs', 'test-apartment-travel-guard.cjs']);
    for (const count of [2, 3]) {
      const sub = path.join(dir, String(count)); fs.mkdirSync(sub);
      for (let index = 1; index <= count; index++) {
        const result = run([...files, '--shard', `${index}/${count}`, '--receipt', path.join(sub, `shard-${index}-of-${count}.json`)], 180000);
        assert.equal(result.status, 0, result.stdout + result.stderr);
      }
      const receipts = Array.from({ length: count }, (_, index) => JSON.parse(fs.readFileSync(path.join(sub, `shard-${index + 1}-of-${count}.json`), 'utf8')));
      assert.deepEqual(receipts.flatMap(receipt => receipt.shard_plan).sort(), [...plan].sort());
      for (const receipt of receipts) {
        assert.equal(receipt.schema, 'touched-harness-shard-receipt/v1'); assert.equal(receipt.head, git(['rev-parse', 'HEAD']));
        assert.deepEqual(receipt.plan, plan); assert.deepEqual(receipt.results.map(result => result.name), receipt.shard_plan);
        assert.ok(receipt.results.every(result => result.status === 'ok' && Number.isInteger(result.duration_ms)));
      }
      const verify = run([...files, '--verify-receipts', sub, '--shards', String(count)]);
      assert.equal(verify.status, 0, verify.stdout + verify.stderr);
      assert.match(verify.stdout, new RegExp(`OK {3}${count} receipts, disjoint, union = unsharded plan \\(2 items\\), all green`));
    }

    const good = path.join(dir, '2'), read = name => JSON.parse(fs.readFileSync(path.join(good, name), 'utf8'));
    const tamper = (label, mutate, verifyArgs = [...files, '--shards', '2'], pattern = /receipts REJECTED/) => {
      const sub = fs.mkdtempSync(path.join(dir, 'bad-'));
      const receipts = { 'shard-1-of-2.json': read('shard-1-of-2.json'), 'shard-2-of-2.json': read('shard-2-of-2.json') };
      mutate(receipts);
      for (const [name, receipt] of Object.entries(receipts)) if (receipt !== undefined) {
        fs.writeFileSync(path.join(sub, name), typeof receipt === 'string' ? receipt : JSON.stringify(receipt));
      }
      const result = run([...verifyArgs, '--verify-receipts', sub]);
      assert.equal(result.status, 1, `${label} must be rejected\n${result.stdout}${result.stderr}`);
      assert.match(result.stdout, pattern, label);
    };
    tamper('a missing worker', receipts => { delete receipts['shard-2-of-2.json']; }, undefined, /shard 2\/2: receipt missing/);
    tamper('a failed harness', receipts => { receipts['shard-1-of-2.json'].results[0].status = 'fail'; }, undefined, /test-apartment-chat-presenter\.cjs fail/);
    tamper('a skipped harness', receipts => { receipts['shard-2-of-2.json'].results = []; }, undefined, /results do not match/);
    tamper('a harness run twice', receipts => {
      receipts['shard-2-of-2.json'].results.push(receipts['shard-1-of-2.json'].results[0]);
    }, undefined, /also ran in shard 1/);
    tamper('a narrowed full plan', receipts => { receipts['shard-1-of-2.json'].plan.pop(); }, undefined, /full plan differs/);
    tamper('a moved harness', receipts => {
      const [one, two] = [receipts['shard-1-of-2.json'], receipts['shard-2-of-2.json']];
      two.shard_plan.unshift(...one.shard_plan); two.results.unshift(...one.results); one.shard_plan = []; one.results = [];
    }, undefined, /shard plan differs from the partition/);
    tamper('another head', receipts => { receipts['shard-2-of-2.json'].head = '0'.repeat(40); }, undefined, /head .* differs/);
    tamper('another change set', receipts => { receipts['shard-1-of-2.json'].changed_files = ['README.md']; }, undefined, /changed_files .* differs/);
    tamper('a stray worker', receipts => { receipts['shard-3-of-2.json'] = receipts['shard-2-of-2.json']; }, undefined, /not a receipt for shards 1\.\.2/);
    tamper('an unreadable receipt', receipts => { receipts['shard-1-of-2.json'] = '{'; }, undefined, /unreadable/);
    tamper('receipts for a different worker count', () => {}, [...files, '--shards', '3'], /not a receipt for shards 1\.\.3/);
    tamper('receipts for a different plan', () => {}, ['--files', 'tools/test-apartment-travel-guard.cjs', '--shards', '2'], /full plan differs/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('the two directional browser parts are both planned together and run on different workers', () => {
  const parts = ['test-apartment-directional-browser.mjs', 'test-apartment-directional-browser-part2.mjs'];
  for (const argv of [['--base', actualBase()], ['--files', 'app/src/home-apartment-directional.js'],
    ['--files', 'tools/fixtures/apartment-directional-browser-proof.mjs'], ['--files', 'app/src/home-apartment.jsx,app/src/home-3d/engine.js']]) {
    const full = planOf(argv);
    assert.equal(parts.filter(name => full.includes(name)).length === 0 || parts.every(name => full.includes(name)), true,
      'a change that triggers one directional part triggers both: ' + argv.join(' '));
    if (!full.includes(parts[0])) continue;
    for (const count of [2, 3, 4]) {
      const owners = parts.map(name => [...Array(count).keys()].filter(index => planOf([...argv, '--shard', `${index + 1}/${count}`]).includes(name)));
      assert.ok(owners.every(list => list.length === 1), 'each part has exactly one worker');
      assert.notEqual(owners[0][0], owners[1][0], `the parts run on different workers with ${count} shards: ${argv.join(' ')}`);
    }
  }
});
