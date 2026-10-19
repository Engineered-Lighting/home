#!/usr/bin/env node
// Local operator records only. This tool has no network or camera interface.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const MAX_BYTES = 1024 * 1024;
const MAX_STATE_FILES = 4096;
const MAX_STATE_BYTES = 32 * 1024 * 1024;
const PARTS = ['task', 'body', 'head', 'hands', 'protection'];
const DIGEST = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$/;
const PIN_NAMES = ['site_id', 'room_id', 'registration_id', 'geometry_revision', 'camera_id',
  'clock_mapping_id'];
const PIN_DIGESTS = ['registration_digest', 'geometry_digest', 'mount_digest',
  'calibration_digest', 'clock_mapping_digest', 'producer_digest', 'method_digest',
  'model_digest', 'capture_config_digest', 'applicability_digest', 'coverage_digest',
  'optical_policy_digest', 'protection_policy_digest'];
const GEO_NUMBERS = ['max_work_center_error_m', 'max_geometry_age_s',
  'min_task_containment_fraction', 'min_body_containment_fraction',
  'min_head_containment_fraction', 'min_hand_containment_fraction',
  'min_protection_containment_fraction', 'max_undercoverage_fraction',
  'min_reference_eligible_s'];
const SERVICE_NUMBERS = ['min_successful_positive_episodes', 'min_sustained_current_delivery_s',
  'min_moved_position_successes', 'max_response_s', 'max_false_activations'];
const RUNTIME_NUMBERS = ['max_session_s', 'max_capture_hz', 'max_detector_hz', 'max_pose_hz',
  'max_paired_depth_hz', 'max_publication_hz', 'max_publications', 'max_root_identities',
  'max_journal_bytes', 'max_checkpoint_bytes', 'max_commit_bytes', 'max_backlog_bytes',
  'projected_publications', 'projected_root_identities', 'projected_journal_bytes',
  'projected_checkpoint_bytes', 'projected_commit_bytes', 'projected_backlog_bytes'];
const BOUND_NUMBERS = ['depth_error_m', 'association_error_m', 'registration_error_m',
  'estimator_error_m', 'motion_m_per_s', 'facing_error_deg'];
const canonical = value => value === null || typeof value !== 'object'
  ? JSON.stringify(value)
  : Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
    : `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const keyed = (names, value = null) => Object.fromEntries(names.map(k => [k, value]));
const fail = code => { throw new Error(code); };

export function template() {
  const reference = () => ({ status: 'unknown', uncertainty_m: null,
    reference_digest: null, applicability_digest: null });
  const position = role => ({ position_id: null, role,
    references: Object.fromEntries(PARTS.map(part => [part, reference()])) });
  return {
    schema: 'den-commissioning-record-1', record_id: null, revision: 1, story_id: null,
    requirements: {
      author_role: 'owner', independent_of_predictions: null, defined_at: null,
      geometry: { task_extent_m: [null, null, null], facing_required: null, ...keyed(GEO_NUMBERS) },
      protection: keyed(['head_clearance_m', 'body_clearance_m', 'hand_clearance_m']),
      service: { ...keyed(SERVICE_NUMBERS), counting_rule_digest: null },
      runtime: keyed(RUNTIME_NUMBERS),
    },
    survey: {
      pins: { ...keyed(PIN_NAMES), ...keyed(PIN_DIGESTS),
        registration_revision: null, clock_mapping_revision: null },
      geometry_bounds: { ...keyed(BOUND_NUMBERS), front_back_resolved: null,
        bound_method_digest: null },
      positions: [position('intended'), position('moved')],
      fixtures: [0, 1].map(() => ({ fixture_id: null, survey_digest: null,
        optical_envelope_digest: null })),
      nominal_profile: { profile_id: null, example_id: null,
        brightness_fraction: null, cct_kelvin: null },
    },
    evaluation: { development_partition_id: null, held_out_partition_id: null,
      development_episode_ids: [], held_out_episode_ids: [], confuser_ids: [],
      episode_attempts: [], independent_annotation_protocol_digest: null },
  };
}

function validateRecord(record) {
  const errors = [], blockers = [];
  const problem = (p, why) => errors.push({ field: p, reason: why });
  const missing = p => blockers.push({ field: p, reason: 'required_independent_input_unavailable' });
  const object = (v, keys, p) => {
    if (!v || typeof v !== 'object' || Array.isArray(v)) {
      problem(p, 'object_required'); return {};
    }
    for (const k of Object.keys(v)) if (!keys.includes(k)) problem(`${p}.*`, 'unknown_field');
    for (const k of keys) if (!(k in v)) missing(`${p}.${k}`);
    return v;
  };
  const value = (v, p, predicate, reason) => {
    if (v === null || v === undefined) missing(p);
    else if (!predicate(v)) problem(p, reason);
  };
  const id = (v, p) => value(v, p, n => typeof n === 'string' && ID.test(n), 'opaque_id_required');
  const hash = (v, p) => value(v, p, n => typeof n === 'string' && DIGEST.test(n), 'sha256_required');
  const number = (v, p, min = 0, max = 1e12, integer = false) => value(v, p,
    n => typeof n === 'number' && Number.isFinite(n) && n >= min && n <= max
      && (!integer || Number.isInteger(n)), 'number_out_of_range');
  const array = (v, p, min = 1, max = 512) => {
    if (!Array.isArray(v) || v.length > max) { problem(p, 'bounded_array_required'); return []; }
    if (v.length < min) missing(p);
    return v;
  };
  const ids = (v, p) => {
    const a = array(v, p); a.forEach((n, i) => id(n, `${p}[${i}]`));
    if (new Set(a).size !== a.length) problem(p, 'duplicate_id');
    return a;
  };
  const root = object(record, Object.keys(template()), 'record');
  value(root.schema, 'schema', n => n === 'den-commissioning-record-1', 'schema_mismatch');
  id(root.record_id, 'record_id'); id(root.story_id, 'story_id'); number(root.revision, 'revision', 1, 1e6, true);
  const req = object(root.requirements, Object.keys(template().requirements), 'requirements');
  value(req.author_role, 'requirements.author_role', n => n === 'owner', 'owner_requirements_required');
  value(req.independent_of_predictions, 'requirements.independent_of_predictions',
    n => n === true, 'requirements_must_precede_prediction_review');
  value(req.defined_at, 'requirements.defined_at', n => typeof n === 'string'
    && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(n)
    && Number.isFinite(Date.parse(n)), 'utc_timestamp_required');
  const geo = object(req.geometry, ['task_extent_m', 'facing_required', ...GEO_NUMBERS], 'requirements.geometry');
  value(geo.facing_required, 'requirements.geometry.facing_required', n => typeof n === 'boolean', 'boolean_required');
  const extents = array(geo.task_extent_m, 'requirements.geometry.task_extent_m', 3, 3);
  extents.forEach((n, i) => number(n, `requirements.geometry.task_extent_m[${i}]`, Number.MIN_VALUE, 100));
  for (const k of GEO_NUMBERS) number(geo[k], `requirements.geometry.${k}`,
    k === 'max_undercoverage_fraction' ? 0 : Number.MIN_VALUE, k.includes('fraction') ? 1 : 1e6);
  const protection = object(req.protection, Object.keys(template().requirements.protection), 'requirements.protection');
  for (const k of Object.keys(template().requirements.protection)) number(protection[k], `requirements.protection.${k}`, 0, 100);
  const service = object(req.service, [...SERVICE_NUMBERS, 'counting_rule_digest'], 'requirements.service');
  for (const k of SERVICE_NUMBERS) number(service[k], `requirements.service.${k}`,
    k === 'max_false_activations' ? 0 : Number.MIN_VALUE, 1e6,
    k.includes('episodes') || k.includes('successes') || k === 'max_false_activations');
  hash(service.counting_rule_digest, 'requirements.service.counting_rule_digest');
  const runtime = object(req.runtime, RUNTIME_NUMBERS, 'requirements.runtime');
  for (const k of RUNTIME_NUMBERS) number(runtime[k], `requirements.runtime.${k}`,
    k.startsWith('projected_') ? 0 : Number.MIN_VALUE, 1e12,
    k.includes('bytes') || k.includes('identities') || k.includes('publications'));
  for (const part of ['publications', 'root_identities', 'journal_bytes', 'checkpoint_bytes', 'commit_bytes', 'backlog_bytes']) {
    if (typeof runtime[`projected_${part}`] === 'number' && typeof runtime[`max_${part}`] === 'number'
      && runtime[`projected_${part}`] > runtime[`max_${part}`]) problem(`requirements.runtime.projected_${part}`, 'projected_capacity_exceeds_limit');
  }
  const survey = object(root.survey, Object.keys(template().survey), 'survey');
  const pins = object(survey.pins, Object.keys(template().survey.pins), 'survey.pins');
  for (const k of PIN_NAMES) id(pins[k], `survey.pins.${k}`);
  for (const k of PIN_DIGESTS) hash(pins[k], `survey.pins.${k}`);
  for (const k of ['registration_revision', 'clock_mapping_revision']) number(pins[k], `survey.pins.${k}`, 1, 1e9, true);
  const bounds = object(survey.geometry_bounds, Object.keys(template().survey.geometry_bounds), 'survey.geometry_bounds');
  for (const k of BOUND_NUMBERS) {
    if (k === 'facing_error_deg' && geo.facing_required === false && bounds[k] === null) continue;
    number(bounds[k], `survey.geometry_bounds.${k}`, 0, k === 'facing_error_deg' ? 180 : 1000);
  }
  if (!(geo.facing_required === false && bounds.front_back_resolved === null)) {
    value(bounds.front_back_resolved, 'survey.geometry_bounds.front_back_resolved', n => typeof n === 'boolean', 'boolean_required');
  }
  if (geo.facing_required === true && bounds.front_back_resolved === false) missing('survey.geometry_bounds.front_back_resolved');
  hash(bounds.bound_method_digest, 'survey.geometry_bounds.bound_method_digest');
  const positions = array(survey.positions, 'survey.positions', 2, 32);
  const positionIds = [];
  positions.forEach((position, i) => {
    const p = `survey.positions[${i}]`;
    const pos = object(position, ['position_id', 'role', 'references'], p);
    id(pos.position_id, `${p}.position_id`); positionIds.push(pos.position_id);
    value(pos.role, `${p}.role`, n => ['intended', 'moved'].includes(n), 'position_role_required');
    const refs = object(pos.references, PARTS, `${p}.references`);
    for (const part of PARTS) {
      const rp = `${p}.references.${part}`;
      const ref = object(refs[part], ['status', 'uncertainty_m', 'reference_digest', 'applicability_digest'], rp);
      value(ref.status, `${rp}.status`, n => ['available', 'unavailable', 'unknown'].includes(n), 'reference_status_required');
      if (ref.status !== 'available') missing(rp);
      number(ref.uncertainty_m, `${rp}.uncertainty_m`, 0, 100);
      hash(ref.reference_digest, `${rp}.reference_digest`); hash(ref.applicability_digest, `${rp}.applicability_digest`);
    }
  });
  if (!positions.some(p => p?.role === 'intended') || !positions.some(p => p?.role === 'moved')) missing('survey.positions.intended_and_moved');
  const suppliedPositions = positionIds.filter(n => n !== null && n !== undefined);
  if (new Set(suppliedPositions).size !== suppliedPositions.length) problem('survey.positions', 'duplicate_position');
  const fixtures = array(survey.fixtures, 'survey.fixtures', 2, 16);
  const fixtureIds = [];
  fixtures.forEach((fixture, i) => {
    const p = `survey.fixtures[${i}]`;
    const f = object(fixture, ['fixture_id', 'survey_digest', 'optical_envelope_digest'], p);
    id(f.fixture_id, `${p}.fixture_id`); fixtureIds.push(f.fixture_id);
    hash(f.survey_digest, `${p}.survey_digest`); hash(f.optical_envelope_digest, `${p}.optical_envelope_digest`);
  });
  const suppliedFixtures = fixtureIds.filter(n => n !== null && n !== undefined);
  if (new Set(suppliedFixtures).size !== suppliedFixtures.length) problem('survey.fixtures', 'duplicate_fixture');
  const profile = object(survey.nominal_profile, Object.keys(template().survey.nominal_profile), 'survey.nominal_profile');
  id(profile.profile_id, 'survey.nominal_profile.profile_id'); id(profile.example_id, 'survey.nominal_profile.example_id');
  number(profile.brightness_fraction, 'survey.nominal_profile.brightness_fraction', 0, 1);
  number(profile.cct_kelvin, 'survey.nominal_profile.cct_kelvin', 1000, 20000);
  const ev = object(root.evaluation, Object.keys(template().evaluation), 'evaluation');
  id(ev.development_partition_id, 'evaluation.development_partition_id');
  id(ev.held_out_partition_id, 'evaluation.held_out_partition_id');
  if (ev.development_partition_id && ev.development_partition_id === ev.held_out_partition_id) problem('evaluation', 'partitions_must_differ');
  const development = ids(ev.development_episode_ids, 'evaluation.development_episode_ids');
  const held = ids(ev.held_out_episode_ids, 'evaluation.held_out_episode_ids');
  ids(ev.confuser_ids, 'evaluation.confuser_ids');
  if (development.some(n => held.includes(n))) problem('evaluation', 'development_held_out_overlap');
  hash(ev.independent_annotation_protocol_digest, 'evaluation.independent_annotation_protocol_digest');
  const episodes = [...development, ...held], attempts = [], assigned = [];
  for (const [i, item] of array(ev.episode_attempts, 'evaluation.episode_attempts').entries()) {
    const p = `evaluation.episode_attempts[${i}]`;
    const a = object(item, ['episode_id', 'attempt_ids'], p);
    id(a.episode_id, `${p}.episode_id`); assigned.push(a.episode_id);
    if (!episodes.includes(a.episode_id)) problem(`${p}.episode_id`, 'episode_outside_partition');
    attempts.push(...ids(a.attempt_ids, `${p}.attempt_ids`));
  }
  if (new Set(assigned).size !== assigned.length || new Set(attempts).size !== attempts.length) problem('evaluation.episode_attempts', 'duplicate_episode_or_attempt');
  if (episodes.some(n => !assigned.includes(n))) missing('evaluation.episode_attempts.membership');
  if (Number.isFinite(service.min_successful_positive_episodes)
    && service.min_successful_positive_episodes > held.length) problem('requirements.service.min_successful_positive_episodes', 'floor_exceeds_entire_held_out_set');
  return { errors, blockers, counts: { positions: positions.length, fixtures: fixtures.length,
    development_episodes: development.length, held_out_episodes: held.length, planned_attempts: attempts.length } };
}

function readJSON(file) {
  let stat;
  try { stat = fs.lstatSync(file); } catch { fail('input_unavailable'); }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_BYTES) fail('input_not_bounded_regular_json');
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { fail('invalid_json'); }
}
function writeNew(file, value) {
  const body = `${JSON.stringify(value, null, 2)}\n`;
  if (Buffer.byteLength(body) > MAX_BYTES) fail('output_limit_exceeded');
  let fd;
  try {
    fd = fs.openSync(file, 'wx', 0o600); fs.writeFileSync(fd, body); fs.fsyncSync(fd);
  } catch (e) { fail(e.code === 'EEXIST' ? 'immutable_output_already_exists' : 'output_unavailable'); }
  finally { if (fd !== undefined) fs.closeSync(fd); }
}
function report(result, extra = {}) {
  const groups = [
    ['requirements', 'Define task, protection, service and bounded-runtime requirements independently.'],
    ['survey.pins', 'Record the surveyed room, camera, registration, clock and method pins.'],
    ['survey.geometry_bounds', 'Commission applicable geometry, motion and facing bounds.'],
    ['survey.positions', 'Obtain independent task, body, head, hand and protection references.'],
    ['survey.fixtures', 'Survey two virtual fixture candidates and their optical envelopes.'],
    ['survey.nominal_profile', 'Record the owner-approved nominal example settings.'],
    ['evaluation', 'Seal development and held-out episodes, attempts and annotation rules.'],
  ];
  return { schema: 'den-commissioning-report-1', status: result.errors.length ? 'invalid'
    : result.blockers.length ? 'incomplete' : 'complete', errors: result.errors,
    blockers: result.blockers, counts: result.counts,
    next_steps: groups.filter(([prefix]) => result.blockers.some(b => b.field.startsWith(prefix)))
      .map(([, instruction]) => instruction),
    runtime_qualification: 'not_assessed', physical_authority: false, ...extra };
}
function partitionRefs(record) {
  return ['development', 'held_out'].map(role => ({
    partition_id: record.evaluation[`${role}_partition_id`], role,
    membership_digest: digest(record.evaluation[`${role}_episode_ids`].slice().sort()),
  }));
}
function readFreeze(file) {
  const frozen = readJSON(file);
  if (frozen.schema !== 'den-commissioning-freeze-1'
    || frozen.freeze_digest !== digest(frozen.record)
    || validateRecord(frozen.record).errors.length || validateRecord(frozen.record).blockers.length
    || canonical(frozen.assessment_partition_refs) !== canonical(partitionRefs(frozen.record))) fail('freeze_integrity_failed');
  return frozen;
}
function withState(dir, fn) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (fs.lstatSync(dir).isSymbolicLink()) fail('state_directory_symlink');
  const lock = path.join(dir, '.commissioning-lock');
  let fd;
  try { fd = fs.openSync(lock, 'wx', 0o600); } catch { fail('state_locked'); }
  try {
    const entries = fs.readdirSync(dir);
    if (entries.length > MAX_STATE_FILES) fail('state_capacity_exceeded');
    const files = entries.filter(n => n.endsWith('.json'));
    const bytes = files.reduce((sum, n) => sum + fs.lstatSync(path.join(dir, n)).size, 0);
    if (bytes > MAX_STATE_BYTES) fail('state_capacity_exceeded');
    return fn(files.map(n => readJSON(path.join(dir, n))));
  } finally { fs.closeSync(fd); fs.unlinkSync(lock); }
}
function checkHeldOut(record, events) {
  const held = record.evaluation.held_out_episode_ids;
  for (const event of events) {
    if (event.schema === 'den-commissioning-retirement-1'
      && (event.partition_id === record.evaluation.held_out_partition_id
        || held.some(n => event.episode_ids.includes(n)))) fail('retired_held_out_membership');
    if (event.schema === 'den-commissioning-freeze-1'
      && event.freeze_digest !== digest(record)
      && held.some(n => event.record.evaluation.development_episode_ids.includes(n))) fail('development_episode_cannot_be_held_out');
    if (event.schema === 'den-commissioning-freeze-1'
      && event.freeze_digest !== digest(record)
      && held.some(n => event.record.evaluation.held_out_episode_ids.includes(n))
      && events.some(a => a.schema === 'den-commissioning-attempt-1'
        && a.partition === 'held_out' && a.freeze_digest === event.freeze_digest)) fail('assessed_membership_cannot_be_refrozen');
  }
}

// Validate an owner-created aggregate, never individual observations or truth rows.
function aggregate(summary, frozen) {
  const missing = [];
  const keys = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(k => k in value);
  const nonnegative = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
  const stats = value => keys(value, ['samples', 'eligible_s', 'p50', 'p95', 'max'])
    && Number.isInteger(value.samples) && value.samples >= 0 && nonnegative(value.eligible_s)
    && (value.samples === 0 ? [value.p50, value.p95, value.max].every(n => n === null)
      : [value.p50, value.p95, value.max].every(nonnegative)
        && value.p50 <= value.p95 && value.p95 <= value.max && value.eligible_s > 0);
  if (!keys(summary, ['schema', 'freeze_digest', 'assessment_digest', 'assessment_partition_ref',
    'units', 'duration_s', 'source_validity', 'work_center_error_m', 'reference_uncertainty_m', 'containment', 'latency_s'])
    || summary.schema !== 'den-commissioning-measurement-summary-1'
    || summary.freeze_digest !== frozen.freeze_digest || !DIGEST.test(summary.assessment_digest)
    || !frozen.assessment_partition_refs.some(ref => canonical(ref) === canonical(summary.assessment_partition_ref))
    || !keys(summary.units, ['distance', 'time']) || summary.units.distance !== 'm' || summary.units.time !== 's'
    || !nonnegative(summary.duration_s)
    || !keys(summary.reference_uncertainty_m, PARTS)
    || !keys(summary.containment, PARTS)
    || !keys(summary.latency_s, ['observation_to_available', 'publication_to_decision'])) fail('aggregate_summary_invalid');
  if (summary.source_validity === null) missing.push('source_validity');
  else if (!keys(summary.source_validity, ['total_samples', 'valid_samples', 'invalid_samples', 'unknown_samples'])
    || !Object.values(summary.source_validity).every(n => Number.isInteger(n) && n >= 0)
    || summary.source_validity.valid_samples + summary.source_validity.invalid_samples
      + summary.source_validity.unknown_samples !== summary.source_validity.total_samples) fail('aggregate_summary_invalid');
  if (summary.work_center_error_m === null) missing.push('work_center_error_m');
  else if (!stats(summary.work_center_error_m) || summary.work_center_error_m.eligible_s > summary.duration_s) fail('aggregate_summary_invalid');
  for (const part of PARTS) {
    const uncertainty = summary.reference_uncertainty_m[part];
    if (uncertainty === null) missing.push(`reference_uncertainty_m.${part}`);
    else if (!stats(uncertainty) || uncertainty.eligible_s > summary.duration_s) fail('aggregate_summary_invalid');
    const n = summary.containment[part];
    if (n === null) { missing.push(`containment.${part}`); continue; }
    if (!keys(n, ['eligible_s', 'contained_s', 'undercovered_s', 'unavailable_reference_s', 'missing_prediction_s'])
      || !Object.values(n).every(nonnegative) || n.eligible_s + n.unavailable_reference_s > summary.duration_s + 1e-9
      || Math.abs(n.contained_s + n.undercovered_s - n.eligible_s) > 1e-9
      || n.missing_prediction_s > n.undercovered_s + 1e-9) fail('aggregate_summary_invalid');
  }
  for (const [name, n] of Object.entries(summary.latency_s)) {
    if (n === null) missing.push(`latency_s.${name}`);
    else if (!stats(n) || n.eligible_s > summary.duration_s) fail('aggregate_summary_invalid');
  }
  if (missing.length) process.exitCode = 3;
  return { ...summary, missing_groups: missing, status: missing.length
    ? 'incomplete_owner_aggregate' : 'structurally_valid_owner_aggregate',
    origin: 'owner_supplied_not_recomputed', runtime_qualification: 'not_assessed', physical_authority: false };
}

function main() {
  const [command, ...args] = process.argv.slice(2), options = {};
  const allowed = { template: ['output'], validate: ['record'], freeze: ['record', 'state-dir', 'output'],
    'record-attempt': ['freeze', 'manifest', 'state-dir'], retire: ['freeze', 'reason', 'state-dir'],
    inspect: ['freeze', 'state-dir'], aggregate: ['freeze', 'summary'] };
  if (!allowed[command]) fail('usage_template_validate_freeze_record-attempt_retire_inspect_aggregate');
  if (args.length % 2) fail('explicit_option_value_required');
  for (let i = 0; i < args.length; i += 2) {
    const key = args[i].replace(/^--/, '');
    if (!args[i].startsWith('--') || !allowed[command].includes(key) || options[key] || !args[i + 1]) fail('unknown_or_duplicate_option');
    options[key] = args[i + 1];
  }
  if (allowed[command].some(k => !options[k])) fail('required_option_missing');
  if (command === 'template') {
    writeNew(options.output, template()); return { status: 'template_created', physical_authority: false };
  }
  if (command === 'validate' || command === 'freeze') {
    const record = readJSON(options.record), result = validateRecord(record);
    if (command === 'validate' || result.errors.length || result.blockers.length) {
      process.exitCode = result.errors.length ? 2 : result.blockers.length ? 3 : 0;
      return report(result);
    }
    return withState(options['state-dir'], events => {
      checkHeldOut(record, events);
      const hash = digest(record), prior = events.find(e => e.schema === 'den-commissioning-freeze-1'
        && e.record.record_id === record.record_id && e.record.revision === record.revision);
      if (prior && prior.freeze_digest !== hash) fail('frozen_revision_changed');
      const frozen = { schema: 'den-commissioning-freeze-1', freeze_digest: hash,
        record, assessment_partition_refs: partitionRefs(record) };
      const eventPath = path.join(options['state-dir'], `freeze-${hash}.json`);
      if (!prior) writeNew(eventPath, frozen);
      writeNew(options.output, frozen);
      return report(result, { status: 'frozen', freeze_digest: hash });
    });
  }
  const frozen = readFreeze(options.freeze), record = frozen.record;
  if (command === 'aggregate') return aggregate(readJSON(options.summary), frozen);
  return withState(options['state-dir'], events => {
    const registered = events.some(e => e.schema === 'den-commissioning-freeze-1'
      && e.freeze_digest === frozen.freeze_digest);
    if (!registered) fail('freeze_not_registered_in_state');
    const retired = events.filter(e => e.schema === 'den-commissioning-retirement-1'
      && (e.partition_id === record.evaluation.held_out_partition_id
        || e.episode_ids.some(n => record.evaluation.held_out_episode_ids.includes(n))));
    if (command === 'retire') {
      const reasons = ['code_informed', 'method_informed', 'threshold_informed', 'scope_informed', 'conditions_informed'];
      if (!reasons.includes(options.reason)) fail('retirement_reason_code_required');
      const event = { schema: 'den-commissioning-retirement-1',
        partition_id: record.evaluation.held_out_partition_id,
        episode_ids: record.evaluation.held_out_episode_ids,
        freeze_digest: frozen.freeze_digest, reason: options.reason };
      if (!retired.length) writeNew(path.join(options['state-dir'], `retirement-${digest(event.partition_id)}.json`), event);
      return { status: 'retired_to_development', held_out_episodes: event.episode_ids.length, physical_authority: false };
    }
    if (command === 'record-attempt') {
      const manifest = readJSON(options.manifest);
      const keys = ['schema', 'freeze_digest', 'episode_id', 'attempt_id', 'partition',
        'started_at', 'finished_at', 'outcome', 'input_manifest_digest', 'aggregate_result_digest'];
      if (Object.keys(manifest).length !== keys.length || keys.some(k => !(k in manifest))
        || manifest.schema !== 'den-commissioning-attempt-1' || manifest.freeze_digest !== frozen.freeze_digest
        || !['development', 'held_out'].includes(manifest.partition)
        || !['completed', 'failed', 'aborted', 'excluded'].includes(manifest.outcome)
        || !DIGEST.test(manifest.input_manifest_digest) || !DIGEST.test(manifest.aggregate_result_digest)
        || !Number.isFinite(manifest.started_at) || !Number.isFinite(manifest.finished_at)
        || manifest.finished_at < manifest.started_at
        || manifest.started_at < Date.parse(record.requirements.defined_at) / 1000) fail('attempt_manifest_invalid');
      const episode = record.evaluation.episode_attempts.find(e => e.episode_id === manifest.episode_id);
      if (!episode?.attempt_ids.includes(manifest.attempt_id)
        || !record.evaluation[`${manifest.partition}_episode_ids`].includes(manifest.episode_id)) fail('attempt_not_predeclared');
      if (retired.length && manifest.partition === 'held_out') fail('retired_held_out_membership');
      if (manifest.partition === 'held_out') checkHeldOut(record, events);
      const prior = events.find(e => e.schema === 'den-commissioning-attempt-1' && e.attempt_id === manifest.attempt_id);
      if (prior && digest(prior) !== digest(manifest)) fail('attempt_id_changed');
      if (!prior) writeNew(path.join(options['state-dir'], `attempt-${digest(manifest.attempt_id)}.json`), manifest);
      return { status: prior ? 'exact_retry' : 'attempt_recorded', physical_authority: false };
    }
    const attempts = events.filter(e => e.schema === 'den-commissioning-attempt-1'
      && e.freeze_digest === frozen.freeze_digest);
    if (!retired.length) checkHeldOut(record, events);
    const planned = record.evaluation.episode_attempts.reduce((n, e) => n + e.attempt_ids.length, 0);
    return report(validateRecord(record), { freeze_digest: frozen.freeze_digest,
      held_out_status: retired.length ? 'retired_to_development' : 'sealed_not_assessed',
      attempts: { planned, recorded: attempts.length, missing: planned - attempts.length,
        outcomes: Object.fromEntries(['completed', 'failed', 'aborted', 'excluded']
          .map(outcome => [outcome, attempts.filter(a => a.outcome === outcome).length])) } });
  });
}

try { console.log(JSON.stringify(main(), null, 2)); }
catch (e) {
  console.log(JSON.stringify({ status: 'refused', reason: /^[a-z0-9_-]+$/.test(e.message)
    ? e.message : 'operation_failed', physical_authority: false }));
  process.exitCode = 2;
}
