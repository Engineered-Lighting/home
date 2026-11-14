// The live den view's Qwen line (app/src/home-den-live-qwen.mjs): den-live's Qwen ask, in the den-live chip.
//   node --test tools/test-den-live-qwen-line.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { qwenLine, QWEN_ANSWER_S } = await import(pathToFileURL(path.join(ROOT, 'app/src/home-den-live-qwen.mjs')).href);

const event = (qwen, activity = null, emitted = 1000) => ({ schema: 'den-live-people-1', emitted_ts: emitted,
    health: qwen ? { qwen } : {}, tracks: [{ track_id: 'e1-trk-1', state: 'confirmed', xy_m: [1, 1], ...(activity ? { activity } : {}) }] });

test('nothing about Qwen when den-live has no ask', () => {
    assert.equal(qwenLine(event(null)), '');
    assert.equal(qwenLine(null), '');
});

test('the newest answer, in plain words, with its age on den-live\'s clock', () => {
    const line = qwenLine(event({ state: 'asking' }, { word: 'reading', close_up: true, at: 998 }), .4);
    assert.equal(line, 'Qwen: reading (close-up work) · 2 s ago');
    assert.equal(qwenLine(event({ state: 'asking' }, { word: 'phone_use', close_up: false, at: 999 })), 'Qwen: on the phone · 1 s ago');
    assert.equal(qwenLine(event({ state: 'asking' }, { word: 'hobby', close_up: true, at: 1000 })), 'Qwen: a hobby (close-up work) · 0 s ago');
    assert.equal(qwenLine(event({ state: 'asking' }, { word: 'not-a-word', close_up: false, at: 1000 })), 'Qwen: unclear · 0 s ago');
    for (const word of ['constructor', '__proto__', 'toString']) {
        assert.equal(qwenLine(event({ state: 'asking' }, { word, close_up: false, at: 1000 })), 'Qwen: unclear · 0 s ago');
    }
});

test('an old answer gives way to the state', () => {
    const old = { word: 'reading', close_up: true, at: 1000 - QWEN_ANSWER_S - 1 };
    assert.equal(qwenLine(event({ state: 'asking' }, old)), 'Qwen: asking…');
    assert.equal(qwenLine(event({ state: 'off', why: 'nobody seated' }, old)), 'Qwen: off (nobody seated)');
    assert.equal(qwenLine(event({ state: 'unavailable', why: "Qwen isn't answering" })), "Qwen: unavailable (Qwen isn't answering)");
});

test("an answer goes 10 s after it came (contract 3's expiry), not 30", () => {
    assert.equal(QWEN_ANSWER_S, 10);
    const answer = { word: 'reading', close_up: true, at: 990 };
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, at: 991 })), 'Qwen: reading (close-up work) · 9 s ago');
    assert.equal(qwenLine(event({ state: 'asking' }, answer)), 'Qwen: asking…', 'gone at exactly 10 s');
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, at: 995 }), 4.9), 'Qwen: reading (close-up work) · 10 s ago');
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, at: 995 }), 5), 'Qwen: asking…',
        'a stalled stream counts the time since its last event');
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, at: 1000 }), Number.NaN), 'Qwen: asking…');
});

test("the reply's own expiry wins when the event carries it, but never past 10 s", () => {
    const answer = { word: 'reading', close_up: true, at: 998 };
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, valid_for_s: 3 }), 2.9), 'Qwen: reading (close-up work) · 5 s ago');
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, valid_for_s: 3 }), 3), 'Qwen: asking…');
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, valid_for_s: 0 })), 'Qwen: asking…', 'already expired');
    assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, valid_for_s: 60 }), 8), 'Qwen: asking…',
        'a longer expiry is not honoured');
    for (const odd of [-1, '5', null, Number.POSITIVE_INFINITY]) {
        assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, valid_for_s: odd }), 7.9), 'Qwen: reading (close-up work) · 10 s ago');
        assert.equal(qwenLine(event({ state: 'asking' }, { ...answer, valid_for_s: odd }), 8), 'Qwen: asking…');
    }
});

test('a long or odd why is shortened to one line', () => {
    const line = qwenLine(event({ state: 'off', why: 'x\n'.repeat(200) }));
    assert.ok(line.length <= 'Qwen: off ()'.length + 80);
    assert.ok(!line.includes('\n'));
});
