import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { createSpotPlayback } from '../app/src/home-den-spot-playback.mjs';

// Exercise the actual Python routes with Home's playback. All geometry, poses and
// inference answers come from the existing synthetic fixture. No actuator exists.
const producer = String.raw`
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tools' / 'den-live'))
from test_preview_manual import PreviewManualTest

PreviewManualTest.setUpClass()
case = PreviewManualTest('runTest')
case.setUp()
case.preview.site = 'victoria'
case.light()
try:
    for raw in sys.stdin:
        request = json.loads(raw)
        try:
            operation = request['operation']
            if operation == 'restart':
                case.preview.close()
                case.preview = case.make_preview()
                case.preview.site = 'victoria'
                case.preview.tick()
            if operation in ('state', 'restart'):
                status, answer = case.preview.state()
            else:
                route = case.preview.routes()[('POST', '/coordinator/virtual/' + operation)]
                status, answer = route(request['body'])
            answer['wire_encoded_count'] = len(case.encoded)
            print(json.dumps({'id': request['id'], 'status': status, 'answer': answer}), flush=True)
        except Exception as error:
            print(json.dumps({'id': request['id'], 'error': str(error)}), flush=True)
finally:
    case.preview.close()
    case.doCleanups()
    PreviewManualTest.doClassCleanups()
`;

test('producer ownership regressions pass with synthetic state and no actuator', () => {
    const result = spawnSync(process.env.PYTHON || 'python', ['-B', '-m', 'unittest', 'discover',
        '-s', 'tools/den-live', '-p', 'test_manual_authority.py'], {
        cwd: fileURLToPath(new URL('..', import.meta.url)), encoding: 'utf8', timeout: 30000,
    });
    assert.equal(result.status, 0, `${result.error || ''}\n${result.stdout}\n${result.stderr}`);
});

function backend(t) {
    const child = spawn(process.env.PYTHON || 'python', ['-B', '-c', producer], {
        cwd: fileURLToPath(new URL('..', import.meta.url)), stdio: ['pipe', 'pipe', 'pipe'],
    });
    let serial = 0, stderr = '';
    const requests = new Map();
    const output = createInterface({ input: child.stdout });
    const completion = new Promise(resolve => child.once('exit', resolve));
    child.stderr.on('data', data => { stderr += data.toString(); });
    function fail(error) {
        for (const pending of requests.values()) { clearTimeout(pending.timer); pending.reject(error); }
        requests.clear();
    }
    child.on('error', fail);
    child.on('exit', code => { if (requests.size) fail(Error(`producer exited ${code}\n${stderr}`)); });
    output.on('line', line => {
        const reply = JSON.parse(line), pending = requests.get(reply.id);
        if (!pending) return;
        requests.delete(reply.id); clearTimeout(pending.timer);
        if (reply.error) pending.reject(Error(reply.error));
        else pending.resolve(reply);
    });
    t.after(async () => {
        child.stdin.end();
        const timer = setTimeout(() => child.kill(), 3000);
        await completion;
        clearTimeout(timer); output.close();
    });
    return (operation, body) => new Promise((resolve, reject) => {
        const id = ++serial;
        const timer = setTimeout(() => {
            requests.delete(id); reject(Error(`producer request ${operation} timed out\n${stderr}`));
        }, 15000);
        requests.set(id, { resolve, reject, timer });
        child.stdin.write(JSON.stringify({ id, operation, body }) + '\n');
    });
}

function home(snapshot, rpc) {
    let listener, sequence = 0;
    const posts = [], playback = [], pending = new Set(), hands = new Map();
    const binding = snapshot.binding;
    const scope = { generation: 1, binding: { content_revision: binding.scan_revision },
        equipment_revision: binding.equipment_revision, equipment_digest: binding.equipment_digest,
        fixture_ids: Object.keys(snapshot.fixtures) };
    const overlay = {
        coordinatorSpotScope: () => scope,
        applyCoordinatorSpots: () => true,
        onVirtualFixtureHand(callback) { listener = callback; return () => { listener = null; }; },
        virtualFixtureHandHolds: () => [...hands.values()],
        restoreVirtualFixtureHand(id, hand) { hands.set(id, { fixture_id: id, hand }); return true; },
        releaseVirtualFixtureHand(id) { hands.delete(id); return [id]; },
        releaseVirtualFixtureHands() { const ids = [...hands.keys()]; hands.clear(); return ids; },
    };
    const player = createSpotPlayback({ home: 'den', site: 'victoria', clock: () => 1000,
        clientId: 'wire-home-client', makeEventId: () => `wire-event-${++sequence}`,
        onPlayback(phase, value) { playback.push({ phase, epoch: value?.epoch, seq: value?.seq }); },
        later: () => 1, cancel() {},
        async post(operation, body) {
            posts.push({ operation, body: structuredClone(body) });
            const request = rpc(operation, body);
            pending.add(request);
            try { return await request; } finally { pending.delete(request); }
        } });
    player.attach(overlay);
    function receive(state, reconnect = false) {
        if (reconnect) {
            player.connect(1000);
            player.anchor({ epoch: state.snapshot.epoch, server_mono: state.server_mono });
            player.tracking(binding.transform_id, binding.scan_revision);
        }
        player.receive(state.snapshot);
    }
    return { player, posts, playback, hands, receive,
        edit(id, before, after) {
            hands.set(id, { fixture_id: id, hand: { state: structuredClone(after) } });
            listener({ phase: 'start', fixture_id: id });
            listener({ phase: 'settled', fixture_id: id, before, after, at_ms: 1794700000000 + sequence });
        },
        async settle() {
            for (let i = 0; i < 10; i++) {
                await new Promise(resolve => setImmediate(resolve));
                await Promise.all([...pending]);
            }
        } };
}

test('Home edits, restart recovery and fenced Resume agree with the actual virtual producer', { timeout: 30000 }, async t => {
    const rpc = backend(t);
    const initial = (await rpc('state')).answer;
    assert.equal(initial.snapshot.manual_protocol, 2);
    const name = Object.keys(initial.snapshot.fixtures)[0];
    const first = home(initial.snapshot, rpc);
    first.receive(initial, true);
    first.edit(name, { spotlight: { brightness: initial.snapshot.fixtures[name].level } },
        { spotlight: { brightness: 0.35 } });
    await first.settle();
    const edited = (await rpc('state')).answer;
    const beforeRestart = edited.snapshot.fixtures[name].hand;
    assert.equal(beforeRestart.state.spotlight.brightness, 0.35);
    assert.ok(beforeRestart.hold_id);
    first.receive(edited);

    const restarted = (await rpc('restart')).answer;
    assert.notEqual(restarted.snapshot.epoch, edited.snapshot.epoch);
    assert.equal(restarted.snapshot.fixtures[name].hand.hold_id, beforeRestart.hold_id);
    assert.equal(restarted.wire_encoded_count, edited.wire_encoded_count, 'recovery issues no fixture command');
    first.receive(restarted, true);
    assert.equal(first.hands.get(name).hand.state.spotlight.brightness, 0.35);
    assert.deepEqual(first.playback.slice(-3), ['decoded', 'scheduled', 'applied'].map(phase => ({
        phase, epoch: restarted.snapshot.epoch, seq: restarted.snapshot.seq,
    })), 'latency evidence retains the recovered producer epoch and snapshot identity');
    const fresh = home(restarted.snapshot, rpc);
    fresh.receive(restarted, true);
    assert.equal(fresh.hands.get(name).hand.state.spotlight.brightness, 0.35);
    assert.equal(fresh.posts.length, 0, 'restoration never creates a correction');

    first.edit(name, { spotlight: { brightness: 0.35 } }, { spotlight: { brightness: 0.45 } });
    await first.settle();
    const newer = (await rpc('state')).answer;
    assert.equal(newer.snapshot.fixtures[name].hand.state.spotlight.brightness, 0.45);
    const stale = await rpc('manual/resume', { fixture_id: name, hold_id: beforeRestart.hold_id,
        expected_revision: beforeRestart.revision, request_id: 'stale-resume-request' });
    assert.equal(stale.status, 409);
    first.receive(newer);
    assert.equal(first.player.handBack(), 1);
    await first.settle();
    assert.equal(first.hands.size, 0);
    const resumed = (await rpc('state')).answer;
    assert.equal(resumed.snapshot.fixtures[name].hand, null);
    assert.equal(resumed.bulbs_on, false);
    assert.equal(resumed.snapshot.devices, undefined);
});
