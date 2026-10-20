import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const cli = fileURLToPath(new URL('./commission.mjs', import.meta.url));
const H = 'a'.repeat(64);
function environment(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'home-den-kit-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const at = name => path.join(dir, name);
  const run = (...args) => {
    const p = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 5000 });
    assert.equal(p.error, undefined); assert.equal(p.stderr, '');
    return { code: p.status, body: JSON.parse(p.stdout) };
  };
  const put = (name, value) => { fs.writeFileSync(at(name), JSON.stringify(value)); return at(name); };
  const blank = () => { assert.equal(run('template', '--output', at('blank.json')).code, 0); return JSON.parse(fs.readFileSync(at('blank.json'))); };
  const complete = () => {
    const r = blank(); r.record_id = 'synthetic:record'; r.story_id = 'synthetic:reading';
    r.requirements.independent_of_predictions = true; r.requirements.defined_at = '2026-10-14T00:00:00Z';
    r.requirements.geometry.task_extent_m = [0.4, 0.3, 0.1];
    for (const k of Object.keys(r.requirements.geometry)) if (k !== 'task_extent_m') {
      r.requirements.geometry[k] = k === 'facing_required' ? true
        : k.includes('fraction') ? (k.startsWith('max') ? 0.1 : 0.9) : 1;
    }
    for (const k of Object.keys(r.requirements.protection)) r.requirements.protection[k] = 0.1;
    for (const k of Object.keys(r.requirements.service)) r.requirements.service[k] = k.endsWith('digest') ? H : 1;
    for (const k of Object.keys(r.requirements.runtime)) r.requirements.runtime[k] = k.startsWith('projected') ? 1 : 100;
    for (const k of Object.keys(r.survey.pins)) r.survey.pins[k] = k.endsWith('digest') ? H : k.endsWith('revision') && k !== 'geometry_revision' ? 1 : `synthetic:${k}`;
    for (const k of Object.keys(r.survey.geometry_bounds)) r.survey.geometry_bounds[k] = k.endsWith('digest') ? H : k === 'front_back_resolved' ? true : 0.01;
    r.survey.positions.forEach((p, i) => {
      p.position_id = `synthetic:position${i}`;
      for (const ref of Object.values(p.references)) Object.assign(ref,
        { status: 'available', uncertainty_m: 0.01, reference_digest: H, applicability_digest: H });
    });
    r.survey.fixtures.forEach((f, i) => Object.assign(f,
      { fixture_id: `synthetic:fixture${i}`, survey_digest: H, optical_envelope_digest: H }));
    Object.assign(r.survey.nominal_profile, { profile_id: 'synthetic:profile', example_id: 'synthetic:example', brightness_fraction: 0.5, cct_kelvin: 3500 });
    Object.assign(r.evaluation, { development_partition_id: 'synthetic:dev', held_out_partition_id: 'synthetic:held',
      development_episode_ids: ['synthetic:dev1'], held_out_episode_ids: ['synthetic:held1'],
      confuser_ids: ['synthetic:unused-material'], independent_annotation_protocol_digest: H,
      episode_attempts: [{ episode_id: 'synthetic:dev1', attempt_ids: ['synthetic:attempt-dev'] },
        { episode_id: 'synthetic:held1', attempt_ids: ['synthetic:attempt-held'] }] });
    return r;
  };
  const freeze = r => run('freeze', '--record', put('record.json', r), '--state-dir', at('state'), '--output', at('freeze.json'));
  const attempt = freezeDigest => ({ schema: 'den-commissioning-attempt-1', freeze_digest: freezeDigest,
    episode_id: 'synthetic:held1', attempt_id: 'synthetic:attempt-held', partition: 'held_out',
    started_at: 1791936001, finished_at: 1791936002, outcome: 'failed',
    input_manifest_digest: H, aggregate_result_digest: H });
  return { at, run, put, blank, complete, freeze, attempt };
}

test('blank template reports incomplete grouped steps without requiring measurements to begin', t => {
  const e = environment(t), r = e.blank(), p = e.run('validate', '--record', e.put('input.json', r));
  assert.equal(p.code, 3); assert.equal(p.body.status, 'incomplete'); assert.deepEqual(p.body.errors, []);
  assert.ok(p.body.next_steps.length >= 6); assert.ok(p.body.blockers.some(b => b.field.includes('max_geometry_age_s')));
  assert.equal(p.body.runtime_qualification, 'not_assessed'); assert.equal(p.body.physical_authority, false);
});
test('complete synthetic record freezes, preserves digest pins and returns only aggregate inspection', t => {
  const e = environment(t), r = e.complete(), p = e.freeze(r); assert.equal(p.code, 0);
  assert.match(p.body.freeze_digest, /^[a-f0-9]{64}$/);
  const frozen = JSON.parse(fs.readFileSync(e.at('freeze.json')));
  assert.deepEqual(frozen.record, r); assert.equal(frozen.assessment_partition_refs.length, 2);
  const report = e.run('inspect', '--freeze', e.at('freeze.json'), '--state-dir', e.at('state'));
  assert.equal(report.code, 0); assert.equal(report.body.attempts.missing, 2);
  assert.equal(JSON.stringify(report.body).includes('synthetic:position'), false);
  assert.equal(report.body.runtime_qualification, 'not_assessed');
});
test('inconsistent overlap and runtime capacity reject instead of inventing admission', t => {
  const e = environment(t), r = e.complete();
  r.evaluation.held_out_episode_ids = r.evaluation.development_episode_ids;
  r.requirements.runtime.projected_commit_bytes = 101;
  const p = e.freeze(r); assert.equal(p.code, 2); assert.equal(p.body.status, 'invalid');
  assert.ok(p.body.errors.some(x => x.reason === 'development_held_out_overlap'));
  assert.ok(p.body.errors.some(x => x.reason === 'projected_capacity_exceeds_limit'));
  assert.equal(fs.existsSync(e.at('state')), false);
});
test('missing independent reference and nominal unknown limits block a freeze explicitly', t => {
  const e = environment(t), r = e.complete(); r.survey.positions[1].references.hands.status = 'unavailable';
  r.requirements.service.min_sustained_current_delivery_s = null;
  const p = e.freeze(r); assert.equal(p.code, 3);
  assert.ok(p.body.blockers.some(x => x.field.endsWith('references.hands')));
  assert.equal(fs.existsSync(e.at('freeze.json')), false);
});
test('frozen revision cannot be changed retrospectively and digest tampering fails', t => {
  const e = environment(t), r = e.complete(); assert.equal(e.freeze(r).code, 0);
  r.requirements.service.max_response_s = 2;
  const changed = e.run('freeze', '--record', e.put('changed.json', r), '--state-dir', e.at('state'), '--output', e.at('new-freeze.json'));
  assert.equal(changed.body.reason, 'frozen_revision_changed');
  const frozen = JSON.parse(fs.readFileSync(e.at('freeze.json'))); frozen.record.requirements.service.max_response_s = 2;
  const p = e.run('inspect', '--freeze', e.put('tampered.json', frozen), '--state-dir', e.at('state'));
  assert.equal(p.body.reason, 'freeze_integrity_failed');
});
test('failed attempts retained once; exact retry works and changed retry rejects', t => {
  const e = environment(t), r = e.complete(), p = e.freeze(r); assert.equal(p.code, 0);
  const a = e.attempt(p.body.freeze_digest);
  const submit = () => e.run('record-attempt', '--freeze', e.at('freeze.json'), '--manifest', e.put('attempt.json', a), '--state-dir', e.at('state'));
  assert.equal(submit().body.status, 'attempt_recorded'); assert.equal(submit().body.status, 'exact_retry');
  a.outcome = 'completed'; assert.equal(submit().body.reason, 'attempt_id_changed');
  const q = e.run('inspect', '--freeze', e.at('freeze.json'), '--state-dir', e.at('state'));
  assert.equal(q.body.attempts.recorded, 1); assert.equal(q.body.attempts.outcomes.failed, 1);
});
test('held-out retirement cannot be undone by renaming set and incrementing revision', t => {
  const e = environment(t), r = e.complete(); assert.equal(e.freeze(r).code, 0);
  assert.equal(e.run('retire', '--freeze', e.at('freeze.json'), '--state-dir', e.at('state'), '--reason', 'method_informed').code, 0);
  r.revision = 2; r.evaluation.held_out_partition_id = 'synthetic:renamed';
  const p = e.run('freeze', '--record', e.put('new.json', r), '--state-dir', e.at('state'), '--output', e.at('new-freeze.json'));
  assert.equal(p.body.reason, 'retired_held_out_membership');
});
test('assessed held-out episodes cannot be refrozen with changed method pins', t => {
  const e = environment(t), r = e.complete(), p = e.freeze(r); assert.equal(p.code, 0);
  const a = e.attempt(p.body.freeze_digest);
  assert.equal(e.run('record-attempt', '--freeze', e.at('freeze.json'), '--manifest', e.put('attempt.json', a), '--state-dir', e.at('state')).code, 0);
  r.revision = 2; r.survey.pins.method_digest = 'b'.repeat(64);
  const q = e.run('freeze', '--record', e.put('new.json', r), '--state-dir', e.at('state'), '--output', e.at('new-freeze.json'));
  assert.equal(q.body.reason, 'assessed_membership_cannot_be_refrozen');
});
test('undeclared attempts, oversized files and output overwrites refuse with no input leakage', t => {
  const e = environment(t), r = e.complete(), p = e.freeze(r); assert.equal(p.code, 0);
  const a = e.attempt(p.body.freeze_digest); a.attempt_id = 'synthetic:undeclared';
  assert.equal(e.run('record-attempt', '--freeze', e.at('freeze.json'), '--manifest', e.put('attempt.json', a), '--state-dir', e.at('state')).body.reason, 'attempt_not_predeclared');
  fs.writeFileSync(e.at('large.json'), 'PRIVATE HOUSEHOLD DATA'.repeat(60000));
  const large = e.run('validate', '--record', e.at('large.json'));
  assert.equal(large.body.reason, 'input_not_bounded_regular_json');
  assert.equal(JSON.stringify(large).includes('PRIVATE'), false);
  assert.equal(e.run('template', '--output', e.at('record.json')).body.reason, 'immutable_output_already_exists');
  r.requirements['SENSITIVE KEY DO NOT ECHO'] = 'SENSITIVE VALUE DO NOT ECHO';
  const unknown = e.run('validate', '--record', e.put('unknown-field.json', r));
  assert.equal(unknown.code, 2); assert.equal(JSON.stringify(unknown).includes('SENSITIVE'), false);
  assert.ok(unknown.body.errors.some(n => n.reason === 'unknown_field' && n.field === 'requirements.*'));
});
test('unknown facing blocks only stories that independently require facing', t => {
  const e = environment(t), r = e.complete();
  r.survey.geometry_bounds.front_back_resolved = false;
  assert.equal(e.run('validate', '--record', e.put('required.json', r)).code, 3);
  r.requirements.geometry.facing_required = false; r.survey.geometry_bounds.facing_error_deg = null;
  r.survey.geometry_bounds.front_back_resolved = null;
  assert.equal(e.run('validate', '--record', e.put('optional.json', r)).code, 0);
});
test('overlapping freeze sealed before retirement cannot escape retirement with a renamed partition', t => {
  const e = environment(t), r = e.complete(), first = e.freeze(r); assert.equal(first.code, 0);
  const b = structuredClone(r); b.revision = 2; b.evaluation.held_out_partition_id = 'synthetic:alias';
  const second = e.run('freeze', '--record', e.put('record-b.json', b), '--state-dir', e.at('state'), '--output', e.at('freeze-b.json'));
  assert.equal(second.code, 0);
  assert.equal(e.run('retire', '--freeze', e.at('freeze.json'), '--state-dir', e.at('state'), '--reason', 'method_informed').code, 0);
  const a = e.attempt(second.body.freeze_digest);
  const q = e.run('record-attempt', '--freeze', e.at('freeze-b.json'), '--manifest', e.put('attempt.json', a), '--state-dir', e.at('state'));
  assert.equal(q.body.reason, 'retired_held_out_membership');
  assert.equal(e.run('inspect', '--freeze', e.at('freeze-b.json'), '--state-dir', e.at('state')).body.held_out_status, 'retired_to_development');
});
test('overlapping freeze sealed before assessment cannot reuse already assessed episodes', t => {
  const e = environment(t), r = e.complete(), first = e.freeze(r); assert.equal(first.code, 0);
  const b = structuredClone(r); b.revision = 2; b.survey.pins.method_digest = 'b'.repeat(64);
  const second = e.run('freeze', '--record', e.put('record-b.json', b), '--state-dir', e.at('state'), '--output', e.at('freeze-b.json'));
  assert.equal(second.code, 0);
  const a = e.attempt(first.body.freeze_digest);
  assert.equal(e.run('record-attempt', '--freeze', e.at('freeze.json'), '--manifest', e.put('attempt-a.json', a), '--state-dir', e.at('state')).code, 0);
  a.freeze_digest = second.body.freeze_digest;
  const q = e.run('record-attempt', '--freeze', e.at('freeze-b.json'), '--manifest', e.put('attempt-b.json', a), '--state-dir', e.at('state'));
  assert.equal(q.body.reason, 'assessed_membership_cannot_be_refrozen');
});
test('aggregate handoff validates only bounded aggregate statistics, never claiming recomputed truth', t => {
  const e = environment(t), r = e.complete(), p = e.freeze(r); assert.equal(p.code, 0);
  const frozen = JSON.parse(fs.readFileSync(e.at('freeze.json')));
  const s = { schema: 'den-commissioning-measurement-summary-1', freeze_digest: p.body.freeze_digest,
    assessment_digest: H, assessment_partition_ref: frozen.assessment_partition_refs[1],
    units: { distance: 'm', time: 's' },
    source_validity: { total_samples: 100, valid_samples: 80, invalid_samples: 10, unknown_samples: 10 },
    duration_s: 90, work_center_error_m: { samples: 100, eligible_s: 80, p50: 0.01, p95: 0.03, max: 0.04 },
    reference_uncertainty_m: Object.fromEntries(['task', 'body', 'head', 'hands', 'protection'].map(k => [k,
      { samples: 10, eligible_s: 80, p50: 0.01, p95: 0.02, max: 0.03 }])),
    containment: Object.fromEntries(['task', 'body', 'head', 'hands', 'protection'].map(k => [k,
      { eligible_s: 80, contained_s: 70, undercovered_s: 10, unavailable_reference_s: 10, missing_prediction_s: 5 }])),
    latency_s: { observation_to_available: { samples: 100, eligible_s: 80, p50: 0.1, p95: 0.3, max: 0.4 },
      publication_to_decision: { samples: 0, eligible_s: 0, p50: null, p95: null, max: null } } };
  const submit = () => e.run('aggregate', '--freeze', e.at('freeze.json'), '--summary', e.put('summary.json', s));
  const valid = submit(); assert.equal(valid.code, 0); assert.equal(valid.body.origin, 'owner_supplied_not_recomputed');
  assert.equal(valid.body.runtime_qualification, 'not_assessed'); assert.equal(valid.body.physical_authority, false);
  s.units.distance = 'mm'; assert.equal(submit().body.reason, 'aggregate_summary_invalid'); s.units.distance = 'm';
  s.source_validity.unknown_samples = 11; assert.equal(submit().body.reason, 'aggregate_summary_invalid'); s.source_validity.unknown_samples = 10;
  s.assessment_partition_ref = { ...frozen.assessment_partition_refs[1], membership_digest: 'b'.repeat(64) };
  assert.equal(submit().body.reason, 'aggregate_summary_invalid'); s.assessment_partition_ref = frozen.assessment_partition_refs[1];
  s.latency_s.publication_to_decision = null;
  const incomplete = submit(); assert.equal(incomplete.code, 3);
  assert.deepEqual(incomplete.body.missing_groups, ['latency_s.publication_to_decision']);
  assert.equal(incomplete.body.latency_s.publication_to_decision, null);
  s.containment.task.missing_prediction_s = 11; assert.equal(submit().body.reason, 'aggregate_summary_invalid');
  s.containment.task.missing_prediction_s = 5; s.private_image_path = 'DO NOT ECHO';
  assert.equal(JSON.stringify(submit()).includes('DO NOT ECHO'), false);
});
