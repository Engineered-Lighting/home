import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ANSWER_S, IDENTITY_STRONG, answerOf, captionFor, detailFor, houseOf, identityOf, qwenStatus } from '../app/src/home-den-inference-model.mjs';

const T0 = 1794600000;
const track = (extra = {}) => ({ track_id: 'r7-e1-trk-3', state: 'confirmed', xy_m: [1.9, 3], sigma_m: .07, heading_deg: 196,
    near: 'on the sofa, beside the reading table', service_run_id: 'r7', camera_epoch: 1, association_revision: 2, ...extra });
const event = (tracks, extra = {}) => ({ schema: 'den-live-people-1', emitted_ts: T0 + 20, tracks,
    health: { qwen: { state: 'asking', why: null, last_reply_at: T0 + 17, reply_s_median: 1.6, asks_per_min: 28 } }, ...extra });
const reading = (at = T0 + 17, extra = {}) => ({ word: 'reading', close_up: true, at, ...extra });

test('an answer shows with its age and life, and goes the moment it expires', () => {
    const t = track({ activity: reading() }), e = event([t]);
    const a = answerOf(t, e, 0);
    assert.equal(a.words, 'Reading');
    assert.equal(a.age_s, 3);
    assert.equal(a.left_s, ANSWER_S - 3);
    assert.ok(Math.abs(a.life - 0.7) < 1e-9);
    assert.ok(answerOf(t, e, 6.9));
    assert.equal(answerOf(t, e, 7), null, 'at 10 s after the answer it is gone, with no debounce');
    assert.equal(answerOf(t, e, 30), null);
});

test("den-live's own valid_for_s can only shorten the answer, never lengthen it", () => {
    const short = track({ activity: reading(T0 + 17, { valid_for_s: 2 }) });
    assert.ok(answerOf(short, event([short]), 1.9));
    assert.equal(answerOf(short, event([short]), 2), null);
    const long = track({ activity: reading(T0 + 17, { valid_for_s: 60 }) });
    assert.equal(answerOf(long, event([long]), 7), null, 'never later than 10 s after the answer');
    for (const bad of [-1, NaN, Infinity, '5']) {
        const t = track({ activity: reading(T0 + 17, { valid_for_s: bad }) });
        assert.ok(answerOf(t, event([t]), 6), `ignores ${bad}`);
    }
});

test('the facts and basis appear only when den-live sends them, and an unknown word reads Unclear', () => {
    const t = track({ activity: reading(T0 + 17, { facts: { held_object: 'book', book_state: 'open' }, basis: ['book_in_hands', 'book_pages_open', 'head_down'] }) });
    const a = answerOf(t, event([t]));
    assert.equal(a.object, 'Book');
    assert.deepEqual(a.basis, ['book in hands', 'pages open', 'head down']);
    const today = answerOf(track({ activity: reading() }), event([]));
    assert.equal(today.object, null);
    assert.deepEqual(today.basis, []);
    const odd = track({ activity: { word: 'juggling', close_up: false, at: T0 + 19 } });
    assert.equal(answerOf(odd, event([odd])).words, 'Unclear');
});

test('identity: a strong household match names them, a weak one asks, a guest and no face never do', () => {
    const e = event([]);
    assert.deepEqual(identityOf(track(), e), { name: 'Someone', kind: 'unknown', strength: 'none', score: null, age_s: null });
    assert.equal(identityOf(track({ identity: { kind: 'household', label: 'Sam', score: IDENTITY_STRONG, at: T0 + 10 } }), e).name, 'Sam');
    const weak = identityOf(track({ identity: { kind: 'household', label: 'Sam', score: IDENTITY_STRONG - 0.01, at: T0 + 10 } }), e);
    assert.equal(weak.name, 'Sam?'); assert.equal(weak.strength, 'weak');
    assert.equal(identityOf(track({ identity: { kind: 'guest', score: .3, at: T0 + 15 } }), e).name, 'Guest');
    assert.equal(identityOf(track({ identity: { kind: 'household', label: 'Sam', score: null } }), e).name, 'Someone', 'no score, no name');
    assert.equal(identityOf(track({ identity: { kind: 'household', label: 'Sam', score: .95, at: T0 + 10 } }), e, 2).age_s, 12);
});

test('Qwen asks about the seated person: a standing guest gets their posture, never the answer line', () => {
    const sam = track({ seated: true, activity: reading() });
    const guest = track({ track_id: 'r7-e1-trk-4', seated: false, posture: 'walking', state: 'confirmed' });
    const e = event([sam, guest]);
    assert.equal(captionFor({ event: e, track: sam }).line.words, 'Reading');
    const g = captionFor({ event: e, track: guest });
    assert.equal(g.asked, false);
    assert.equal(g.line.words, 'Walking');
    // Today, with no seated flag: the only track is the one asked about.
    const only = track({});
    assert.equal(captionFor({ event: event([only]), track: only }).asked, true);
});

test("with no answer the line says what Qwen is doing, including why an answer wasn't theirs", () => {
    const e = event([track()]);
    assert.equal(qwenStatus(e.health.qwen, e).words, 'Waiting for Qwen');
    assert.equal(qwenStatus(e.health.qwen, e).detail, 'replied 3 s ago');
    assert.equal(qwenStatus({ ...e.health.qwen, inflight_since: T0 + 19.5 }, e).words, 'Asking Qwen…');
    assert.equal(qwenStatus({ ...e.health.qwen, last_outcome: { kind: 'unbound_two_people', at: T0 + 19 } }, e).words,
        "Can't tell whose answer: two people in view");
    assert.equal(qwenStatus({ ...e.health.qwen, last_outcome: { kind: 'unbound_two_people', at: T0 + 5 } }, e).words, 'Waiting for Qwen',
        'an old outcome stops explaining after 10 s');
    assert.equal(qwenStatus({ state: 'off', why: 'no one seated' }, e).words, 'Qwen off · no one seated');
    assert.equal(qwenStatus({ state: 'unavailable', why: "Qwen isn't answering" }, e).words, "Qwen unavailable · Qwen isn't answering");
});

test("the light line belongs to the spot's subject only, in the same epoch and revision", () => {
    const t = track();
    const spot = { stopped: false, subject: { track_id: t.track_id, camera_epoch: 1, association_revision: 2, request_id: 'ask-9' },
        fixtures: [{ fixture_id: 'F1', on: false, level: 0, reason: 'in_front_of_reader', target_m: null },
            { fixture_id: 'F3', on: true, level: .7, cct_k: 2700, reason: 'task', motion_phase: 'settled', target_m: [2, 2.7, .8] }] };
    assert.deepEqual(houseOf(t, spot, { F3: 'Spot 3' }), { words: 'Spot 3 · reading light', dark: false, level: .7, cct_k: 2700,
        fixture_id: 'F3', request_id: 'ask-9', moving: false });
    assert.equal(houseOf(track({ track_id: 'other' }), spot), null);
    assert.equal(houseOf(track({ camera_epoch: 2 }), spot), null, 'a new camera epoch is another person until bound again');
    assert.equal(houseOf(track({ association_revision: 3 }), spot), null);
    const dark = { ...spot, fixtures: [{ fixture_id: 'F3', on: false, level: 0, reason: 'pre_aim', target_m: [2, 2.7, .8] }] };
    assert.equal(houseOf(t, dark, { F3: 'Spot 3' }).words, 'Spot 3 aimed, dark');
    assert.equal(houseOf(t, { ...spot, stopped: true }).words, 'Stop is holding the spots');
    assert.equal(houseOf(t, null), null);
});

test('detail steps follow the zoom', () => {
    assert.equal(detailFor(0), 2); assert.equal(detailFor(.5), 1); assert.equal(detailFor(1), 0);
});
