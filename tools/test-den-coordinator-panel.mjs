// The lighting coordinator's section of the live den view (app/src/home-den-coordinator-panel.mjs): its words for
// den-live's coordinator state, and the settings it sends. The section itself, through the real gateway, is in
// tools/test-den-live-look-only-browser.mjs.
//   node --test tools/test-den-coordinator-panel.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    SETTINGS, changedSettings, learnedLines, lightName, percent, planLines, reasonWords, situationWords, summaryWords, whyLines,
} from '../app/src/home-den-coordinator-panel.mjs';

// The shapes den-live's coordinator_live.py serves, as tools/den-core's Coordinator makes them.
const DECISION = {
    decision_id: 'spot-1-1', version: 'decision/v3', actuation: 'virtual',
    situation: { activity: 'task', key: 'task/evening', people: 1 },
    spots: { F3: { job: { kind: 'task', who: 'e1-trk-1' }, on: true, level: 0.64 }, F2: { job: { kind: 'pre_aim', who: 'e1-trk-2' }, on: false, level: 0 },
        F4: { job: null, on: false, level: 0 }, F1: { job: { kind: 'accent', who: 'blue_print' }, on: true, level: 0.4 } },
    notes: [{ who: 'e1-trk-1', fixture: 'F1', reason: 'in_front_of_reader' }, { who: 'e1-trk-1', fixture: 'F4', reason: 'in_front_of_reader' },
        { who: 'e1-trk-2', fixture: 'F4', reason: 'in_front_of_reader' }, { who: 'e1-trk-1', fixture: 'F2', reason: 'pan_tilt_infeasible:tilt' },
        { who: 'shelf', fixture: null, reason: 'no_free_fixture' }],
    why: 'task/evening: F3 task for e1-trk-1, level 0.64; refused: F1 in_front_of_reader',
};

test('the situation reads in words', () => {
    assert.equal(situationWords(DECISION.situation), 'A close-up task, evening');
    assert.equal(situationWords('vacant/night'), 'Nobody is here, night');
    assert.equal(situationWords({ key: 'sitting/day', people: 2 }), 'Sitting, day · 2 people');
    assert.equal(situationWords('new_thing/day'), 'new thing, day', 'an activity Home has no words for still shows');
    assert.equal(situationWords(null), 'No decision yet');
});

test('"Why" names each spot\'s job, then each refusal, once', () => {
    const lines = whyLines(DECISION);
    assert.deepEqual(lines, [
        'Moving the virtual spots in Home',
        'F1 lights blue print, at 40%',
        'F2 points at you, dark, ready',
        'F3 lights the close-up task, at 64%',
        'F4 rests',
        "F1 not used: in front of you",
        "F2 not used: can't turn that far",
        'F4 not used: in front of you',
        'No spot: no free fixture',
    ]);
    assert.deepEqual(whyLines(null), ['No decision yet']);
    assert.deepEqual(whyLines({ actuation: 'shadow', spots: {}, notes: [] }),
        ['Watching only: nothing moves while it is off or a Stop holds', 'No change to the spots']);
    assert.equal(reasonWords('something_new'), 'something new');
});

test('the bulb plan and what it learned', () => {
    const plan = { service: 'den_lighting.coordinator_plan', data: { plan_id: 'plan-3', generation: 3, situation: 'task/evening',
        scenes: { 'light.reading_lamp': { on: true, brightness: 128, kelvin: 2700 }, 'light.ceiling': { on: false, brightness: null, kelvin: null } } } };
    assert.deepEqual(planLines(plan), ['ceiling: off', 'reading lamp: 50% · 2700 K']);
    assert.deepEqual(planLines({ service: 'den_lighting.coordinator_plan', data: { scenes: {} } }), ["None: the bulbs follow den_lighting's own rules"]);
    assert.deepEqual(planLines(null), ['No plan sent yet']);
    const learned = { 'task/evening': { 'light.reading_lamp': { on: true, brightness: 150, kelvin: 2900, n: 2, pending: null } },
        'gaming/night': { 'light.ceiling': { on: false, brightness: null, kelvin: null, n: 1 } } };
    assert.deepEqual(learnedLines(learned), ['Gaming, night · ceiling: off (1 change)', 'A close-up task, evening · reading lamp: 59% · 2900 K (2 changes)']);
    assert.deepEqual(learnedLines({}), ['Nothing yet']);
    assert.equal(lightName('light.den_desk_lamp'), 'den desk lamp');
    assert.equal(percent(255), '100%'); assert.equal(percent(null), '');
});

test('the summary line', () => {
    assert.equal(summaryWords({ on: true, stopped: false, situation: DECISION.situation }), 'Lighting coordinator · On · A close-up task, evening');
    assert.equal(summaryWords({ on: false, stopped: true, situation: null }), 'Lighting coordinator · Off · Stop holds · No decision yet');
    assert.equal(summaryWords(null), 'Lighting coordinator');
});

test('Save sends only the settings that changed, and only ones den-live has', () => {
    const current = { move_style: 'lit', move_dim_level: 0.3, pre_aim: true, shift_m: 0.1 };
    assert.deepEqual(changedSettings(current, { move_style: 'dim', move_dim_level: 0.3, pre_aim: false, shift_m: 0.1, invented: 1 }),
        { move_style: 'dim', pre_aim: false });
    assert.deepEqual(changedSettings(current, { ...current }), {});
});

test('every setting the coordinator has today has words', () => {
    // tools/den-core/den_coordinator.py SETTINGS, as of the coordinator's step 3.
    const core = ['move_style', 'move_dim_level', 'fade_in_s', 'fade_out_s', 'shift_m', 'shift_hold_s', 'phone_patience_s', 'pre_aim',
        'accent_level', 'follow_walkers'];
    assert.deepEqual(SETTINGS.map(row => row.key).sort(), [...core].sort());
    assert.deepEqual(SETTINGS.find(row => row.key === 'move_style').options.map(([value]) => value), ['lit', 'dim', 'dark']);
});
