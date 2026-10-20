// Focused contract for this workflow's fixed jobs/steps, not a YAML parser.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = fs.readFileSync(new URL('../.github/workflows/llm-response-qa.yml', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const jobSection = source.split('\njobs:\n');
assert.equal(jobSection.length, 2); assert.doesNotMatch(source, /\t|^\s*continue-on-error:/m);
const headers = [...jobSection[1].matchAll(/^  (\S+):\n/gm)];
assert.equal(headers.length, [...jobSection[1].matchAll(/^  \S.*$/gm)].length);
assert.deepEqual(headers.map(match => match[1]), ['shell_contracts', 'shell_browser_parts', 'touched_contracts', 'deterministic']);
const jobs = Object.fromEntries(headers.map((match, index) => [match[1],
  jobSection[1].slice(match.index + match[0].length, headers[index + 1]?.index)]));
function field(block, name, indent = 4) {
  const values = [...block.matchAll(new RegExp('^ {' + indent + '}' + name + ': (.+)$', 'gm'))];
  assert.equal(values.length, 1, 'one literal field: ' + name); return values[0][1];
}
function steps(job) {
  const parts = job.split('    steps:\n'); assert.equal(parts.length, 2);
  const entries = [...parts[1].matchAll(/^      - (?:name|uses): .+\n/gm)];
  assert.equal(entries.length, [...parts[1].matchAll(/^      - /gm)].length);
  assert.equal(entries[0]?.index, 0);
  return entries.map((entry, index) => parts[1].slice(entry.index, entries[index + 1]?.index));
}
const shell = steps(jobs.shell_contracts), shellParts = steps(jobs.shell_browser_parts);
const touched = steps(jobs.touched_contracts), aggregate = steps(jobs.deterministic);
const originalCommands = ['node tools/run-chat-dedupe-tests.js', 'python3 tools/test-workflow-scenarios.py',
  'node tools/run-chat-ui-regression.js', 'node tools/qa-browser-smoke.js',
  'node --test tools/test-apartment-home-shell-browser.mjs',
  'node tools/run-touched-harnesses.mjs --base "origin/${{ github.base_ref }}"'];
const setupCommands = ['npm ci --no-audit --no-fund', 'npx playwright install --with-deps chromium'];
// The authored shell proof is three files: part 1 stays in the shell job, parts 2-3 get their own worker.
const shellPartCommands = ['node --test tools/test-apartment-home-shell-browser-part2.mjs',
  'node --test tools/test-apartment-home-shell-browser-part3.mjs'];
const runs = entries => entries.filter(entry => /^        run:/m.test(entry)).map(entry => field(entry, 'run', 8));
// One worker count names every shard, receipt, artifact and the aggregate's union check.
const SHARDS = 6, shardList = Array.from({ length: SHARDS }, (_, index) => index + 1);
const receiptDir = '.tmp/touched-harnesses', receiptPath = receiptDir + '/shard-${{ matrix.shard }}-of-' + SHARDS + '.json';
const shardedCommand = originalCommands[5] + ' --shard "${{ matrix.shard }}/' + SHARDS + '" --receipt "' + receiptPath + '"';
const verifyCommand = originalCommands[5] + ' --verify-receipts ' + receiptDir + ' --shards ' + SHARDS;
const prOnly = "github.event_name == 'pull_request'";
const withoutComments = value => value.replace(/^\s*#.*\n/gm, '').trim();

test('independent workers retain runtime, deadlines, six original commands and unconditional workflow contracts', () => {
  assert.match(source, /^on:\n  pull_request:\n  workflow_dispatch:\n/m);
  for (const [name, entries, keys] of [['shell_contracts', shell, ['name', 'runs-on', 'timeout-minutes']],
    ['shell_browser_parts', shellParts, ['name', 'runs-on', 'timeout-minutes']],
    ['touched_contracts', touched, ['name', 'runs-on', 'timeout-minutes', 'strategy']]]) {
    const prefix = jobs[name].split('    steps:\n')[0];
    assert.deepEqual([...prefix.matchAll(/^    (\S+):/gm)].map(match => match[1]), keys);
    assert.equal(field(prefix, 'runs-on'), 'ubuntu-latest'); assert.equal(field(prefix, 'timeout-minutes'), '10');
    assert.match(entries[0], /^      - uses: actions\/checkout@v4$/m); assert.equal(field(entries[0], 'fetch-depth', 10), '0');
    assert.match(entries[1], /^      - uses: actions\/setup-node@v4$/m); assert.equal(field(entries[1], 'node-version', 10), '20');
    for (const entry of entries) {
      if (/^        uses: actions\/upload-artifact@/m.test(entry)) assert.equal(field(entry, 'if', 8), 'always()');
      else if (entry.includes(originalCommands[5])) assert.equal(field(entry, 'if', 8), prOnly);
      else assert.doesNotMatch(entry, /^        if:/m);
    }
  }
  assert.equal(shell.length, 12); assert.equal(shellParts.length, 7); assert.equal(touched.length, 7);
  assert.deepEqual(shell.slice(0, 4).map(withoutComments), touched.slice(0, 4).map(withoutComments));
  assert.deepEqual(shellParts.slice(0, 4).map(withoutComments), touched.slice(0, 4).map(withoutComments));
  assert.equal(field(jobs.shell_browser_parts.split('    steps:\n')[0], 'name'), 'full-shell browser parts 2 and 3');
  assert.deepEqual(runs(shellParts), [...setupCommands, ...shellPartCommands]);
  assert.deepEqual(runs(shell), [...setupCommands, ...originalCommands.slice(0, 4),
    'node --test tools/test-ui-qa-workflow.mjs', 'node --test tools/test-touched-shards.mjs', originalCommands[4]]);
  assert.deepEqual(runs(touched), [...setupCommands, shardedCommand]);
});

test('every touched shard runs as its own non-fail-fast worker of one plan, with no hosted --all', () => {
  const prefix = jobs.touched_contracts.split('    steps:\n')[0];
  assert.equal(field(prefix, 'name'), 'touched-surface contracts (${{ matrix.shard }}/' + SHARDS + ')');
  const strategy = prefix.split('    strategy:\n');
  assert.equal(strategy.length, 2);
  assert.equal(withoutComments(strategy[1]), 'fail-fast: false\n      matrix:\n        shard: [' + shardList.join(', ') + ']');
  assert.equal(touched[4].split('\n')[0], '      - name: Touched-surface harnesses');
  assert.equal(field(touched[4], 'run', 8), shardedCommand);
  for (const entry of [shell, shellParts, touched, aggregate].flat()) {
    if (/^        run:/m.test(entry)) assert.doesNotMatch(field(entry, 'run', 8), /--all\b/, 'no hosted --all');
  }
});

test('every authored shell proof part on disk runs exactly once, across two separate workers', () => {
  const files = fs.readdirSync(new URL('./', import.meta.url))
    .filter(name => /^test-apartment-home-shell-browser(?:-part\d+)?\.mjs$/.test(name)).sort();
  assert.deepEqual(files, ['test-apartment-home-shell-browser-part2.mjs', 'test-apartment-home-shell-browser-part3.mjs',
    'test-apartment-home-shell-browser.mjs']);
  const commands = [shell, shellParts, touched, aggregate]
    .map(entries => runs(entries).filter(command => command.includes('test-apartment-home-shell-browser')));
  assert.deepEqual(commands, [[originalCommands[4]], shellPartCommands, [], []]);
  assert.deepEqual(commands.flat().map(command => command.replace('node --test tools/', '')).sort(), files);
});

test('the original required check always requires every worker result and the verified shard union', () => {
  assert.equal(field(jobs.deterministic, 'name'), 'deterministic chat and ui contracts');
  assert.equal(field(jobs.deterministic, 'needs'), '[shell_contracts, shell_browser_parts, touched_contracts]');
  assert.equal(field(jobs.deterministic, 'if'), 'always()'); assert.equal(field(jobs.deterministic, 'timeout-minutes'), '1');
  assert.equal(aggregate.length, 5);
  assert.doesNotMatch(aggregate[0], /^        if:/m);
  assert.equal(field(aggregate[0], 'SHELL_RESULT', 10), '${{ needs.shell_contracts.result }}');
  assert.equal(field(aggregate[0], 'SHELL_PARTS_RESULT', 10), '${{ needs.shell_browser_parts.result }}');
  assert.equal(field(aggregate[0], 'TOUCHED_RESULT', 10), '${{ needs.touched_contracts.result }}');
  assert.deepEqual(aggregate.slice(1, 3).map(withoutComments), touched.slice(0, 2).map(withoutComments));
  for (const entry of aggregate.slice(1, 3)) assert.doesNotMatch(entry, /^        if:/m);
  assert.equal(field(aggregate[3], 'if', 8), prOnly);
  assert.equal(field(aggregate[3], 'uses', 8), 'actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131');
  assert.equal(field(aggregate[3], 'pattern', 10), 'touched-harness-shard-*-of-' + SHARDS);
  assert.equal(field(aggregate[3], 'path', 10), receiptDir); assert.equal(field(aggregate[3], 'merge-multiple', 10), 'true');
  assert.equal(field(aggregate[4], 'if', 8), prOnly);
  assert.deepEqual(runs(aggregate).slice(1), [verifyCommand]);
});

test('all original diagnostics and every worker receipt upload exactly once under distinct always-run artifact names', () => {
  const uploads = [shell.at(-1), shellParts.at(-1), touched.at(-2), touched.at(-1)], names = [], paths = [];
  for (const entry of uploads) {
    assert.equal(field(entry, 'if', 8), 'always()');
    assert.equal(field(entry, 'uses', 8), 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a');
    assert.equal(field(entry, 'path', 10), '|'); names.push(field(entry, 'name', 10));
    assert.equal(field(entry, 'if-no-files-found', 10), 'ignore'); assert.equal(field(entry, 'include-hidden-files', 10), 'true');
    assert.equal(field(entry, 'retention-days', 10), '7');
    paths.push(...[...entry.matchAll(/^            (\S.*)$/gm)].map(match => match[1]));
  }
  assert.deepEqual(names, ['apartment-shell-authored-browser', 'apartment-shell-authored-browser-parts-2-3',
    'touched-harness-shard-${{ matrix.shard }}-of-' + SHARDS, 'apartment-touched-authored-browser-${{ matrix.shard }}-of-' + SHARDS]);
  assert.deepEqual([...touched.at(-2).matchAll(/^            (\S.*)$/gm)].map(match => match[1]), [receiptPath]);
  const shellPaths = ['.tmp/apartment-home-shell-browser/run-*/*receipt.json', '.tmp/apartment-home-shell-browser/run-*/*.png'];
  for (const entry of [shell.at(-1), shellParts.at(-1)]) {
    assert.deepEqual([...entry.matchAll(/^            (\S.*)$/gm)].map(match => match[1]), shellPaths);
  }
  assert.deepEqual(paths.sort(), [...shellPaths, ...shellPaths,
    receiptPath, ...['overlay-bridge', 'room-selection', 'room-inspector', 'directional', 'aim-theme', 'aim-wiring', 'aim-library-shell', 'composer-comfort'].map(name => '.tmp/apartment-' + name + '-browser/run-*/receipt.json'),
    '.tmp/apartment-aim-theme-browser/run-*/*.png'].sort());
});

test('the actual aggregate Bash gate rejects every non-success combination and genuinely absent variables', () => {
  assert.equal(field(aggregate[0], 'run', 8), '|');
  const lines = aggregate[0].split('        run: |\n'); assert.equal(lines.length, 2);
  const script = lines[1].trimEnd().split('\n').map(line => {
    assert.match(line, /^          \S/); return line.slice(10);
  }).join('\n');
  assert.equal(script, 'test "$SHELL_RESULT" = success && test "$SHELL_PARTS_RESULT" = success && test "$TOUCHED_RESULT" = success');
  const bash = process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : '/bin/bash';
  assert.ok(fs.existsSync(bash), 'explicit platform Bash is available');
  const states = ['success', 'failure', 'cancelled', 'skipped', '', 'pending', 'unknown'];
  const names = ['SHELL_RESULT', 'SHELL_PARTS_RESULT', 'TOUCHED_RESULT'];
  const combos = states.flatMap(a => states.flatMap(b => states.map(c => [a, b, c])));
  // Every non-empty subset of absent variables, with the rest successful.
  for (let missing = 1; missing < 8; missing++) combos.push(names.map((_, index) => missing & (1 << index) ? undefined : 'success'));
  for (const values of combos) {
    const env = { ...process.env };
    for (const key of Object.keys(env)) if (/^(?:SHELL_RESULT|SHELL_PARTS_RESULT|TOUCHED_RESULT|BASH_ENV)$/i.test(key)) delete env[key];
    names.forEach((name, index) => { if (values[index] !== undefined) env[name] = values[index]; });
    const result = spawnSync(bash, ['--noprofile', '--norc', '-c', script],
      { cwd: root, env, encoding: 'utf8', timeout: 2000, maxBuffer: 65536, windowsHide: true });
    assert.ifError(result.error); assert.equal(result.signal, null);
    assert.equal(result.status === 0, values.every(value => value === 'success'),
      'actual gate: ' + JSON.stringify(values.map(value => value ?? '<MISSING>')));
  }
});
