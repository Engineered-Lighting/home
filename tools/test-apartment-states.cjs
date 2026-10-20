// APT-F06 / APT-S02: the shared data-state module against Den's proposed UI
// contracts. The fixtures are synthetic test copies of Den's files
// (tools/fixtures/den-contracts/README.md cites each source and digest).
// No client, socket, clock-based age or dispatch is involved.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment-states.js'), 'utf8');
const S = require(path.join(ROOT, 'app/src/home-apartment-states.js'));
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/den-contracts', name), 'utf8'));

const VOCAB_DOC = fixture('reason-vocabulary.json');
const V = S.createVocabulary(VOCAB_DOC);
const CAPS = fixture('home-den-capabilities-victoria.json');
const clone = value => JSON.parse(JSON.stringify(value));
const rowsByKey = rows => Object.fromEntries(rows.map(row => [row.key, row]));
const text = rows => rows.map(row => [row.title, row.detail, ...(row.details || []).map(item => `${item.shown} ${item.code}`)].join(' ')).join('\n');
const echoShell = patch => ({ siteId: 'echo', phase: 'ready', sourceLabel: 'Echo Park scan', saveState: 'saved', physicalControls: true,
  haConnection: 'online', haLastReportedAt: null, hasPerception: false, ...patch });
const victoriaShell = patch => ({ siteId: 'victoria', phase: 'ready', sourceLabel: 'Victoria Den', saveState: 'saved', physicalControls: false,
  haConnection: 'online', haLastReportedAt: null, hasPerception: true, ...patch });

test('vocabulary: every example row in Den\'s reason-rows fixture renders exactly as Den specifies', () => {
  const rows = fixture('reason-rows.json').rows;
  assert.equal(rows.length, 15);
  for (const row of rows) {
    const found = V.describe(row.group, row.code);
    assert.equal(found.shown, row.shown, `${row.group} ${row.code}`);
    assert.equal(found.severity, row.severity || null, `${row.group} ${row.code} severity`);
    assert.equal(found.legacy, row.legacy === true, `${row.group} ${row.code} legacy`);
    assert.equal(found.known, row.unknown !== true, `${row.group} ${row.code} known`);
    assert.equal(found.detail, row.code, 'the raw code is always kept for Details');
  }
});

test('vocabulary: exact code, then pattern, then Unavailable with the raw code', () => {
  // Exact beats a matching pattern in the same group.
  assert.equal(V.describe('activity_loop.health', 'jev_resuming').shown, 'JEV released · resuming');
  assert.equal(V.describe('activity_loop.health', 'jev_waiting').shown, 'JEV not ready');
  assert.equal(V.describe('activity_loop.health', 'deep_stack_no_recent_output').severity, 'warn');
  assert.equal(V.describe('activity_loop.health', 'deep_stack_interrupted').shown, 'Deep sensing not recording');
  // {n} is an integer only.
  assert.equal(V.describe('brain.status', 'state_http_503').shown, 'Brain answered with an error');
  assert.equal(V.describe('brain.status', 'state_http_abc').known, false);
  assert.equal(V.describe('fixture.pause', 'override_http_409').severity, 'warn');
  assert.equal(V.describe('brain.status', 'brain_degraded').shown, 'Brain reports it is not online');
  // The planning alias resolves to the emitted code's text.
  assert.equal(V.describe('activity_loop.health', 'jev_released_resuming').shown, 'JEV released · resuming');
  // The same code means different things in different groups.
  assert.equal(V.describe('brain.mode', 'degraded_ambient').shown, 'Ambient only (sensing degraded)');
  assert.equal(V.describe('brain.rule', 'degraded_ambient').shown, 'Sensing degraded · ambient only');
  // Legacy desk-preview codes are shown with the suffix, patterns included.
  assert.equal(V.describe('legacy.desk_preview', 'desk_preview_paused').shown, 'Desk preview unavailable (retired preview)');
  // Never dropped silently: an unknown code, or an unknown group.
  for (const [group, code] of [['brain.status', 'some_future_code'], ['no.such.group', 'online'], ['brain.mode', 'Reading']]) {
    const found = V.describe(group, code);
    assert.deepEqual([found.known, found.shown, found.detail], [false, 'Unavailable', code]);
  }
  assert.equal(V.describe('brain.status', null), null, 'a missing reason stays missing');
  // Without a vocabulary every code is Unavailable, never invented.
  assert.deepEqual([S.createVocabulary(null).describe('brain.status', 'online').shown, S.createVocabulary(null).describe('brain.status', 'online').detail], ['Unavailable', 'online']);
});

test('vocabulary: every listed code and pattern in den-reason-vocabulary-1 resolves in its own group', () => {
  assert.equal(VOCAB_DOC.schema, 'den-reason-vocabulary-1');
  let codes = 0, patterns = 0;
  for (const group of VOCAB_DOC.groups) {
    for (const entry of group.codes) {
      if (entry.code) {
        codes += 1;
        const found = V.describe(group.id, entry.code);
        assert.equal(found.text, entry.text, `${group.id} ${entry.code}`);
        assert.equal(found.legacy, group.legacy === true || entry.legacy === true);
      } else {
        patterns += 1;
        const sample = entry.pattern.replace('{n}', '418').replace('{state}', (entry.states || ['waiting'])[0]);
        assert.equal(V.describe(group.id, sample).text, entry.text, `${group.id} ${entry.pattern} → ${sample}`);
      }
    }
  }
  assert.ok(codes > 100 && patterns >= 6, `${codes} codes, ${patterns} patterns`);
});

test('HANDOFF "HA offline (Echo)": Home Assistant named, "Last reported 20:58", controls disabled, nothing queued', () => {
  const at = new Date(2026, 9, 19, 20, 58, 12).toISOString();
  const states = { 'light.front_left': { state: 'on', last_reported: at },
    'light.front_right': { state: 'off', last_reported: new Date(2026, 9, 19, 20, 41).toISOString() },
    'light.elsewhere': { state: 'on', last_reported: new Date(2026, 9, 19, 23, 0).toISOString() } };
  const reported = S.latestReport(states, ['light.front_left', 'light.front_right']);
  assert.equal(reported, at, 'the newest time this home\'s lights reported, never another entity\'s');
  const rows = rowsByKey(S.statusRows(echoShell({ haConnection: 'offline', physicalControls: false, haLastReportedAt: reported }), { report: null, vocabulary: V }));
  assert.deepEqual([rows.lights.state, rows.lights.title, rows.lights.detail],
    ['disconnected', 'Home Assistant not connected', 'Light controls are disabled and nothing is queued. Last reported 20:58']);
  assert.equal(rows['home-assistant'], undefined, 'Echo names its connection in the lights row, once');
  // The shared state that the Echo inspector's badge also names.
  assert.deepEqual([S.haState({ siteId: 'echo', connection: 'offline', lastReportedAt: reported }).title, S.haState({ siteId: 'echo', connection: 'offline', lastReportedAt: reported }).detail],
    ['Home Assistant offline', 'Last reported 20:58']);
  // No time reported: said so, never invented.
  const noTime = rowsByKey(S.statusRows(echoShell({ haConnection: 'disconnected', haLastReportedAt: null }), { report: null, vocabulary: V }));
  assert.equal(noTime.lights.detail, 'Light controls are disabled and nothing is queued. Last reported time unavailable');
  assert.match(rowsByKey(S.statusRows(echoShell({ haConnection: 'connecting' }), { report: null, vocabulary: V })).lights.detail, /^Connecting\. Light controls are disabled/);
  assert.equal(S.latestReport({ 'light.a': { last_reported: 'not a time' } }, ['light.a']), null);
  // Connected always names the system.
  const online = rowsByKey(S.statusRows(echoShell(), { report: null, vocabulary: V }));
  assert.equal(online.lights.title, 'Home Assistant connected');
  assert.equal(online.lights.detail, 'Output guards apply.');
  // Producer seconds format the same way as Home Assistant ISO times.
  assert.equal(S.reportedLine(new Date(2026, 9, 19, 20, 58).getTime() / 1000), 'Last reported 20:58');
});

test('HANDOFF "No session": an empty state, no body layer, Start and Stop kept in the dock', () => {
  const caps = clone(CAPS);
  caps.perception.analysis = 'idle'; caps.perception.frame = null;
  for (const analysis of [null, 'idle', 'stopped']) {
    const state = S.perceptionState({ ...caps.perception, analysis }, V);
    assert.deepEqual([state.state, state.badge, state.title, state.bodyLayer], ['no-session', 'no-session', 'No session', false]);
    assert.match(state.detail, /Start and Stop are in Camera · JEV\./);
    assert.doesNotMatch(state.detail, /only|Perception Lab/, 'never says sessions start only in Lab');
  }
  const rows = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: caps }, vocabulary: V }));
  assert.deepEqual([rows.camera.state, rows.camera.title], ['no-session', 'No session']);
  assert.doesNotMatch(text(Object.values(rows)), /vacant|empty room|nobody/i, 'no session never implies an empty room');
  // A camera reason rides along, from the vocabulary.
  const waiting = S.perceptionState({ supported: true, analysis: null, reason: 'camera_no_viewing_source' }, V);
  assert.match(waiting.detail, /^Den camera not connected · /);
  // A recording session shows the frame's own age state, never a number here.
  const recording = S.perceptionState(CAPS.perception, V);
  assert.deepEqual([recording.title, recording.bodyLayer, recording.frame.seconds], ['Analysis recording', true, null]);
  assert.match(recording.detail, /Frame: Age unavailable · clock not qualified/);
});

test('frame timing: producer age only; a number needs a qualified clock; expiry is never extended', () => {
  const unqualified = S.frameAge(CAPS.perception.frame, V);
  assert.deepEqual([unqualified.status, unqualified.badge, unqualified.title, unqualified.seconds],
    ['unavailable', 'unavailable', 'Age unavailable · clock not qualified', null]);
  assert.deepEqual(unqualified.times, { captured_at: 1799999998.6, observed_at: 1799999998.6, available_at: 1799999999.1, valid_until: 1800000001 });
  // Even if a producer sent seconds, an unqualified clock shows no number.
  const sneaky = S.frameAge({ ...CAPS.perception.frame, age: { status: 'current', seconds: 4, uncertainty_s: null, reason: null } }, V);
  assert.deepEqual([sneaky.title, sneaky.seconds], ['Current', null]);
  const current = S.frameAge({ ...CAPS.perception.frame, age: { status: 'current', seconds: 2.4, uncertainty_s: 0.05, reason: null } }, V);
  assert.deepEqual([current.title, current.seconds], ['Current · 2 s old', 2.4]);
  const expired = S.frameAge({ ...CAPS.perception.frame, age: { status: 'stale', seconds: 400, uncertainty_s: 0.05, reason: 'expired' } }, V);
  assert.deepEqual([expired.badge, expired.title, expired.seconds], ['stale', 'Expired', null]);
  // Old by prototype bands but not expired by the producer: still the producer's state.
  const old = S.frameAge({ ...CAPS.perception.frame, age: { status: 'current', seconds: 600, uncertainty_s: 0.05, reason: null } }, V);
  assert.equal(old.status, 'current', 'no <=10 s / <=2 min band replaces authoritative expiry');
  assert.equal(S.frameAge({ captured_at: 1 }, V).title, 'Age unavailable', 'a missing age block is unavailable, not current');
  assert.equal(S.frameAge({ age: { status: 'invalid', reason: 'time_order_invalid' } }, V).title, 'Times out of order');
  assert.equal(S.frameAge({ age: { status: 'future', reason: 'novel_reason' } }, V).detail, 'novel_reason');
  assert.equal(S.frameAge({ captured_at: 'yesterday' }, V).times.captured_at, null, 'an invalid time stays missing');
  // The module never reads a clock.
  assert.doesNotMatch(SOURCE, /Date\.now|performance\.now|new Date\(\)/);
});

test('Holding stays producer-reported: passed through, never timed here', () => {
  assert.deepEqual(S.holding({ state: 'holding', last_seen_s: 2.6 }), { badge: 'holding', title: 'Holding · last seen 3s ago' });
  assert.deepEqual(S.holding({ state: 'holding' }), { badge: 'holding', title: 'Holding' });
  assert.equal(S.holding({ state: 'current', last_seen_s: 1 }), null);
  assert.equal(S.holding(null), null);
  assert.doesNotMatch(SOURCE, /setTimeout|setInterval/);
});

test('local draft, loading and failed read from the one save and room vocabulary', () => {
  const draft = S.saveState('offline', 'Kept on this device');
  assert.deepEqual([draft.badge, draft.title, draft.detail, draft.unsaved], ['draft', 'Local draft kept', 'Kept on this device', true]);
  assert.deepEqual([S.saveState('saving').badge, S.saveState('failed').title, S.saveState('saved').unsaved], ['loading', 'Save failed', false]);
  assert.deepEqual([S.saveState(undefined).title, S.saveState('mystery').title, S.saveState('mystery').detail], ['No changes', 'Unavailable', 'mystery']);
  for (const state of S.UNSAVED_SAVE_STATES) assert.equal(S.isUnsaved(state), true, state);
  // The top bar keeps its own copy for before the Apartment loads; it must match.
  const app = fs.readFileSync(path.join(ROOT, 'app/src/home-app.jsx'), 'utf8');
  assert.deepEqual(JSON.parse(app.match(/const APARTMENT_UNSAVED_STATES = (\[[^\]]*\]);/)[1]), [...S.UNSAVED_SAVE_STATES]);
  assert.ok(app.includes('function apartmentStatusRows(state) {\n  return window.HomeApartmentStates?.statusRows?.(state) || [];\n}'), 'the top bar computes no rows of its own');
  assert.equal(S.isUnsaved('dirty'), false, 'the Apartment never emits dirty');
  assert.deepEqual([S.siteState('loading').title, S.siteState('error').title, S.siteState(undefined).badge, S.siteState('ready').badge],
    ['Loading room', 'Room failed to load', 'loading', 'current']);
});

test('capabilities adapter: Victoria engineered reads modeled with physical output off; permission is never read', () => {
  const caps = S.readCapabilities(CAPS, { siteId: 'victoria' });
  assert.equal(caps.applicable, true);
  assert.deepEqual([caps.sections.engineered.value, caps.sections.engineered.physical_output, caps.sections.engineered.invalid], ['modeled', false, false]);
  assert.ok(caps.sections.engineered.fixtures.every(item => item.physical_output === false));
  assert.equal(caps.sections.bench.supported, false);
  const rows = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: CAPS }, vocabulary: V }));
  assert.deepEqual([rows.lights.state, rows.lights.title, rows.lights.detail],
    ['modeled', 'No light entities for Den fixtures', '4 modeled fixtures · nothing is sent to hardware.']);
  assert.deepEqual([rows['capability-engineered'].state, rows['capability-engineered'].title, rows['capability-engineered'].detail],
    ['modeled', 'Modeled · Supported · available', '4 fixtures · physical output off']);
  assert.equal(rows['capability-bench'].title, 'Not supported here');
  assert.match(rows['capability-bench'].detail, /^Unavailable$/, 'an unlisted reason reads Unavailable');
  assert.deepEqual(rows['capability-bench'].details.map(item => item.code), ['not_supplied'], 'with the raw code in Details');
  assert.equal(rows['capability-body'].detail, 'Approximate room placement · Body profile not supplied');
  // A permitted field in a report is dropped, and no row implies permission.
  const permissive = clone(CAPS); permissive.engineered.permitted = true; permissive.perception.permitted = true;
  const read = S.readCapabilities(permissive, { siteId: 'victoria' });
  assert.equal('permitted' in read.sections.engineered, false);
  const all = text(S.statusRows(victoriaShell(), { report: { capabilities: permissive }, vocabulary: V }));
  assert.doesNotMatch(all, /permit|allowed|you can|authori[sz]ed/i);
  // A fixture claiming physical output breaks the contract: invalid, not commissioned.
  const claiming = clone(CAPS); claiming.engineered.fixtures[1].physical_output = true;
  const invalid = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: claiming }, vocabulary: V }));
  assert.deepEqual([invalid['capability-engineered'].state, invalid.lights.state], ['invalid', 'invalid']);
  assert.deepEqual(S.readCapabilities({ ...CAPS, schema: 'home-den-capabilities-2' }, { siteId: 'victoria' }), { applicable: false, reason: 'schema_unsupported', siteId: 'victoria' });
});

test('home isolation: Victoria never inherits Echo\'s status, and Echo never reads the Den\'s report', () => {
  const echoOffline = { haConnection: 'offline', haLastReportedAt: new Date(2026, 9, 19, 20, 58).toISOString() };
  // Victoria with no report of its own: nothing of Echo's shows.
  const bare = rowsByKey(S.statusRows(victoriaShell(echoOffline), { report: null, vocabulary: V }));
  assert.equal(bare['home-assistant'], undefined, 'no Home Assistant row without a report from this home');
  assert.doesNotMatch(text(Object.values(bare)), /20:58|Home Assistant|Output guards/);
  assert.equal(S.haState({ siteId: 'victoria', connection: 'online' }).title, 'Home Assistant not reported for this home');
  // Victoria with its own report: its own HA connection and time, not Echo's.
  const own = rowsByKey(S.statusRows(victoriaShell(echoOffline), { report: { capabilities: CAPS }, vocabulary: V }));
  assert.equal(own['home-assistant'].title, 'Home Assistant connected');
  assert.doesNotMatch(text(Object.values(own)), /20:58/);
  // Echo handed Victoria's report: not applicable, never a fallback.
  const echo = S.statusRows(echoShell(), { report: { capabilities: CAPS }, vocabulary: V });
  assert.doesNotMatch(text(echo), /Den|Brain|modeled/i);
  assert.equal(rowsByKey(echo).capabilities.title, 'Not reported');
  assert.deepEqual(S.readCapabilities(CAPS, { siteId: 'echo' }), { applicable: false, reason: 'other_home', siteId: 'echo' });
  // The store keeps a report only under the home it names.
  const store = S.createReportStore();
  assert.equal(store.set('echo', { capabilities: CAPS }), false);
  assert.equal(store.get('echo'), null);
  assert.equal(store.set('victoria', { capabilities: CAPS }), true);
  assert.equal(store.get('victoria').capabilities.site_id, 'victoria');
  let heard = 0; const off = store.subscribe(() => { heard += 1; });
  store.setVocabulary(VOCAB_DOC); off(); store.clear();
  assert.deepEqual([heard, store.get('victoria'), store.vocabulary().size], [1, null, 12]);
  // Nothing in Home fills the shared store yet: no authority is activated.
  assert.equal(S.reports.get('victoria'), null);
});

test('Den systems: lighting brain, den-ops level and activity-loop health render through the vocabulary', () => {
  const report = { capabilities: CAPS, activityHealth: fixture('activity-health-events.json').events };
  const rows = rowsByKey(S.statusRows(victoriaShell(), { report, vocabulary: V }));
  assert.deepEqual([rows['lighting-brain'].state, rows['lighting-brain'].title, rows['lighting-brain'].detail],
    ['connected', 'Brain online', 'Reading light · Reading at sofa · spotlight on']);
  assert.deepEqual([rows['den-ops'].state, rows['den-ops'].title], ['current', 'Monitoring normal']);
  // Newest per component: JEV recovered, deep sensing warns, the camera waits.
  assert.deepEqual([rows['activity-loop'].state, rows['activity-loop'].title], ['stale', 'Deep sensing has no recent output']);
  assert.equal(rows['activity-loop'].detail, 'JEV: OK · Deep sensing: Deep sensing has no recent output · Camera: Waiting for the first camera frame');
  // Another home's events are ignored.
  const foreign = report.activityHealth.map(event => ({ ...event, home: 'echo' }));
  assert.equal(rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: CAPS, activityHealth: foreign }, vocabulary: V }))['activity-loop'].title, 'Activity loop healthy');
  // The monitor's banners keep their own text; silent has no level.
  const silent = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: CAPS, denOps: fixture('den-ops-banner-silent.json') }, vocabulary: V }))['den-ops'];
  assert.deepEqual([silent.state, silent.title, silent.details.map(item => item.code)], ['stale', 'Ops monitor silent on victoria-jetson', ['monitor_silent', 'normal']]);
  const alert = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: CAPS, denOps: fixture('den-ops-banner-alert.json') }, vocabulary: V }))['den-ops'];
  assert.deepEqual([alert.state, alert.title, alert.detail], ['blocked', 'Deep sensing paused: GPU hot', 'Deep sensing paused · ambient only']);
  // Brain offline with its reason; a refusal is Details, not an outage.
  const offline = clone(CAPS); offline.lighting_brain.status = 'offline'; offline.lighting_brain.reason = 'heartbeat_missed';
  const brain = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: offline }, vocabulary: V }))['lighting-brain'];
  assert.deepEqual([brain.state, brain.title, brain.details.map(item => item.shown)],
    ['disconnected', 'Brain offline · holding the last state', ['Brain offline · holding the last state', 'No update from the brain for 15 s', 'Reading light']]);
  const refused = clone(CAPS); refused.lighting_brain.reason = 'state_scope_mismatch';
  const kept = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: refused }, vocabulary: V }))['lighting-brain'];
  assert.deepEqual([kept.title, kept.details[1].shown], ['Brain online', 'Brain state is for another room']);
  const future = clone(CAPS); future.lighting_brain.status = 'brain_rebooting_v9';
  assert.equal(rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: future }, vocabulary: V }))['lighting-brain'].title, 'Brain reports it is not online');
  const unknown = clone(CAPS); unknown.lighting_brain.status = 'Online!';
  const unknownRow = rowsByKey(S.statusRows(victoriaShell(), { report: { capabilities: unknown }, vocabulary: V }))['lighting-brain'];
  assert.deepEqual([unknownRow.title, unknownRow.details[0].code], ['Unavailable', 'Online!']);
});

test('Quick reads Home Assistant states from this module, so no surface computes its own', () => {
  const Q = require(path.join(ROOT, 'app/src/home-apartment-quick.js'));
  for (const name of ['haStatus', 'lastReportedAt', 'formatClock', 'lastReportedLine']) assert.equal(Q[name], S[name], name);
  const quick = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment-quick.js'), 'utf8');
  assert.doesNotMatch(quick, /function haStatus|function lastReportedAt|function formatClock/);
  // Both loaders list the module before its readers.
  for (const file of ['app/src/index.html', 'app/src/home-feature-loader.js']) {
    const source = fs.readFileSync(path.join(ROOT, file), 'utf8');
    const at = name => source.indexOf(name);
    assert.ok(at('home-apartment-states.js') > 0, `${file} lists the state module`);
    assert.ok(at('home-apartment-states.js') < at('home-apartment-quick.js'), `${file}: states before quick`);
    assert.ok(at('home-apartment-states.js') < at('home-apartment-status.jsx'), `${file}: states before the status rows`);
  }
});
