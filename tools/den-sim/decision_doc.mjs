// Hop 18: one spot Decision into Home's document (plan, hop 18, steps 1 to 5). A pure function: it reads no clock, file
// or environment. In: a Decision line in canon's spot shape (hop 17 step 7), the four-row channel table (F1 to F4 to
// Home's fixture ids, read at run time from the geometry, never written down), asks.jsonl's rows and the person states
// the click wrote, where the Decision's PersonState is found. Out: activity-lighting-state-1, the state a feed.play row
// carries. Home checks the evidence's shape and not its truth, so bound: true is this writer's word, and it is written
// only on the whole join. offDocument() is the empty room, and stands in for any click that writes no Decision.
import Client from '../../app/src/home-lighting-brain-client.js';
import { LANDMARK_ID } from './couch_to_room.mjs';

export const DOMAIN = 'virtual-den-4', ACTUATION = 'synthetic', VERSION = 'den-sim-decision-doc-1';
// As the older brain's reader binding did: the feed re-times a document once a second, so evidence never lapses while shown.
export const EVIDENCE_S = 2.8;
const SITE = Client.BASE.split('/').at(-1), ROOM = 'den', OFF_CCT_K = 2700, LABEL = 'SIMULATION · ';
const vec = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
const text = (value, max) => String(value ?? '').slice(0, max);
const offRow = fixture_id => ({ fixture_id, ambient: { radial_level: 0, down_level: 0, cct_k: OFF_CCT_K, fade_s: 0 },
  spot: { on: false, level: 0, cct_k: OFF_CCT_K, target_m: null, fade_s: 0 }, paused_until: null, pause_reason: null });
const UNBOUND = { bound: false, activity: null, pose: null, activity_valid_until: null, pose_valid_until: null };

function table(channels) {
  if (!Array.isArray(channels) || channels.length !== 4 || channels.some((row, i) => row?.channel !== `F${i + 1}.spot` || typeof row.fixture_id !== 'string')
    || new Set(channels.map(row => row.fixture_id)).size !== 4) throw Error('channel_table_invalid');
  return channels;
}

// Step 4: the contract fixture's whole field set, every spot off, the evidence unbound, occupancy and activity empty.
export function offDocument(channels, at = 1, why = 'no Decision: all four spots off') {
  return { schema: Client.SCHEMA, synthetic: true, home: SITE, room: ROOM, generated_at: at, state_revision: 0, decision_id: null, reading_evidence: { ...UNBOUND },
    brain: 'online', mode: 'off', why: text(LABEL + why, 240), occupancy: {}, activity: {}, fixtures: table(channels).map(row => offRow(row.fixture_id)),
    decisions: [], cost: {}, inputs: {}, settings_version: VERSION };
}

// Step 3's join: the reader's PersonState, as the Decision names it, through asks.jsonl to its request, bundle, track and
// reply. { state, ask } on the whole join, else { why }.
export function readingJoin(decision, asks, personStates) {
  const tracks = Object.keys(decision.targets || {});
  if (tracks.length !== 1) return { why: `${tracks.length} readers with a target, not one` };
  const named = (decision.inputs?.person_state || []).filter(id => typeof id === 'string' && id.startsWith(`${tracks[0]}@`));
  const state = named.length === 1 ? (personStates || []).find(item => item.state_id === named[0] && item.track_id === tracks[0]) : null;
  if (!state) return { why: `the Decision names no person state of ${tracks[0]} that the click wrote` };
  const refs = (list, origin) => (list || []).filter(item => item?.origin === origin).map(item => item.ref);
  const reply = refs(state.sources?.focused_engagement, 'qwen'), bundle = refs(state.sources?.working_region, 'rules').filter(ref => ref !== LANDMARK_ID);
  const rows = reply.length === 1 && bundle.length === 1 ? (asks || []).filter(row => row.body_sha256 === reply[0] && row.bundle_id === bundle[0] && row.track_id === tracks[0]
    && typeof row.request_id === 'string' && row.root && row.facts && Array.isArray(row.stills) && row.stills.length) : [];
  if (rows.length !== 1) return { why: `asks.jsonl holds ${rows.length} rows joining ${state.state_id} to one request, bundle, track and reply` };
  return { state, ask: rows[0] };
}

// The evidence, citing the join's ids, on the chain's clock: the feed moves every clock onto today's.
function evidence(decision, { state, ask }) {
  const seen = ask.stills.map(still => still.observed_at), until = decision.ts + EVIDENCE_S;
  const activity = { request_id: ask.request_id, bundle_id: ask.bundle_id, track_id: ask.track_id, reply_body_sha256: ask.body_sha256, root: ask.root,
    interval_start: Math.min(...seen), observed_at: Math.max(...seen), available_at: ask.sent_ts + ask.elapsed_s, valid_until: until };
  const pose = { state_id: state.state_id, track_id: state.track_id, bundle_id: ask.bundle_id, request_id: ask.request_id,
    interval_start: Math.min(...seen), observed_at: Math.max(...seen), available_at: state.emitted_ts, valid_until: until };
  const clocks = [activity, pose].flatMap(source => [source.interval_start, source.observed_at, source.available_at, source.valid_until]);
  return clocks.every(Number.isFinite) ? { bound: true, activity, pose, activity_valid_until: until, pose_valid_until: until } : null;
}

// Steps 1, 2 and 5. { ok, reason, state, lit, unbound }: a refusal names its reason, and unbound says why bound is false.
export function decisionDocument({ decision, channels, asks = [], personStates = [] }) {
  const refused = reason => ({ ok: false, reason, state: null, lit: [], unbound: null });
  if (decision?.actuator_domain !== DOMAIN) return refused('decision_not_virtual_den_4');
  if (decision.actuation !== ACTUATION) return refused('decision_not_synthetic');
  if (!Number.isFinite(decision.ts) || decision.ts <= 0) return refused('decision_ts_invalid');
  const fixtures = [], lit = [];
  for (const row of table(channels)) {
    const applied = decision.applied_scene?.[row.channel], requested = decision.requested_scene?.[row.channel];
    if (!applied || !requested) return refused(`decision_channel_missing: ${row.channel}`);
    // Level and colour as verify judges them, from the read-back; the point from the request; the fade from its own two times.
    const { level, cct_k: cct } = applied.readback || {};
    if (!(Number.isFinite(level) && level > 0)) { fixtures.push(offRow(row.fixture_id)); continue; }
    if (!vec(requested.target_m)) return refused(`lit_row_without_point: ${row.channel}`);
    if (!Number.isFinite(cct)) return refused(`lit_row_without_cct: ${row.channel}`);
    const fade = (applied.steps || []).find(step => step?.kind === 'fade_in'), fadeS = fade ? Math.round((fade.result?.at - fade.sent_at) * 1000) / 1000 : 0;   // to the ms
    if (!Number.isFinite(fadeS) || fadeS < 0) return refused(`fade_in_times_invalid: ${row.channel}`);
    fixtures.push({ ...offRow(row.fixture_id), spot: { on: true, level, cct_k: cct, target_m: requested.target_m.slice(), fade_s: fadeS } });
    lit.push(row.channel);
  }
  const join = readingJoin(decision, asks, personStates), bound = join.ask ? evidence(decision, join) : null;
  const state = { ...offDocument(channels, decision.ts, text(decision.why, 200)), decision_id: text(decision.decision_id, 120) || null,
    reading_evidence: bound || { ...UNBOUND }, mode: lit.length ? 'reading' : 'off', fixtures,
    decisions: [{ at: decision.ts, rule: text(decision.policy || 'decide', 80), why: text(decision.why, 240), change: lit.length ? `lit ${lit.join(', ')}` : 'every spot off' }] };
  return { ok: true, reason: null, state, lit, unbound: bound ? null : join.why || 'the evidence holds a clock that is not a number' };
}
