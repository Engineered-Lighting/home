'use strict';
// Fresh G2 test configuration and Worker2 requests. No service/Native admission.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../../..');
const C = require(path.join(root, 'app/src/home-adaptive-feedback-geometry-hold-2-contract.js'));
const W = require(path.join(root, 'tools/home-adaptive-feedback-geometry-hold-2-shadow-worker.cjs'));
const B = require(path.join(root, 'tools/build-home-adaptive-feedback-geometry-hold-2-schemas.cjs'));
const mathFixture = require(path.join(root, 'tools/fixtures/home-adaptive-experience-synthetic.cjs'));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function createFixture() {
  const scene = mathFixture.makeFixture({variant_id: 'P-A-D-adequate-protected-yes', duration_s: 3});
  const workerId = 'g2-fixture-worker', workerEpoch = 'g2-fixture-worker-1';
  const readerId = 'g2-fixture-reader', readerEpoch = 'g2-fixture-reader-1';
  const producer_epochs = [{principal_id: readerId, producer_id: readerId, producer_epoch: readerEpoch},
    {principal_id: workerId, producer_id: workerId, producer_epoch: workerEpoch}];
  const actor_bindings = [], write_authorities = [];
  const classifier_policy = B.make(C.schemas['home-experience-classifier-policy-1']);
  Object.assign(classifier_policy, scene.identity, {clock_profile_digest: scene.clock_profile.digest,
    max_feedback_age_s: 3, max_exposure_age_s: 3, max_exposure_span_s: 1,
    illumination_lux_bounds: {min: 0, max: 1000}});
  C.assert(classifier_policy);
  const feedback = {producer_epochs, actor_bindings, classifier_policy, write_authorities};
  // Construct the eight-field G2 recipe directly; never mutate an old recipe.
  const config = {registry: C.clone(scene.registry), profile: C.clone(scene.profile),
    clock_profile_digest: scene.clock_profile.digest, interface_digest: C.interfaceDigest,
    registered_catalogue_digest: C.digest(scene.registered_surfaces), materials_digest: C.digest(scene.materials),
    profile_mode: C.profileMode, feedback_config_digest: C.digest(feedback)};
  const configuration = {identity: C.clone(scene.identity), clock_profile: C.clone(scene.clock_profile),
    materials: C.clone(scene.materials), registered_surfaces: C.clone(scene.registered_surfaces),
    config, config_digest: C.digest(config), ...feedback};
  const options = {...C.clone(configuration), registry: C.clone(scene.registry), profile: C.clone(scene.profile),
    worker_producer_id: workerId, worker_producer_epoch: workerEpoch,
    runtime_epoch: 'g2-helper-fixture-runtime-1', duration_s: 3,
    subjectBindings: [], revision_authorizations: [], source_origins: {}};
  const names = [...C.publicationNames].sort();
  const registration_bindings = Object.fromEntries([...scene.materials, ...scene.registered_surfaces].map(value =>
    [value.registration_ref, {source_id: value.source_id, geometry_revision: 'synthetic-geometry',
      registry_digest: value.registry_digest, coverage_refs: ['synthetic-pages']}]));
  const source_ids = [...new Set(['scripted-camera', 'scripted-daylight',
    ...Object.values(registration_bindings).map(value => value.source_id)])].sort();
  const grants = [{principal_id: workerId, producer_id: workerId, producer_epoch: workerEpoch,
    expected_consumer_id: workerId, expected_worker_id: workerId,
    token_sha256: sha(Buffer.from('g2-helper-fixture-worker-token')),
    scopes: [{site_id: scene.identity.site_id, room_id: scene.identity.room_id}],
    operations: ['read', 'worker_commit'], payload_types: names,
    schema_pins: Object.fromEntries(names.map(name => [name, sha(fs.readFileSync(path.join(root,
      'contracts/home-adaptive-feedback-geometry-hold-2/schemas', name + '.schema.json')))])),
    revisions: ['1.0', '2.0', '3.0', '4.0'], source_ids, registration_bindings,
    adaptive_subjects: [], expected_actor_ref: null}];
  grants.push({...C.clone(grants[0]), principal_id: readerId, producer_id: readerId,
    producer_epoch: readerEpoch, expected_consumer_id: readerId, expected_worker_id: null,
    token_sha256: sha(Buffer.from('g2-helper-fixture-reader-token')), operations: ['read']});
  return {configuration, grants, options, original_input_bodies: [],
    provenance: {fresh_G2_configuration: true, old_checkpoint_or_configuration_relabeling: false,
      unchanged_mathematical_scene_helpers_reused: true, simulated_only: true, service_authenticated: false,
      native_commissioned: false, actuated: false, acceptance_eligible: false,
      configured_operations: ['read', 'worker_commit'], issuer_operations_granted: []}};
}

function illustrativeRequests(fixture) {
  const authority = B.make(C.schemas['home-control-input-cut-3'].properties.authority,
    C.schemas['home-control-input-cut-3']);
  authority.producers = fixture.configuration.producer_epochs.map(row => ({producer_id: row.producer_id,
    producer_epoch: row.producer_epoch, revocation_generation: 0, revoked: false}));
  const journalEpoch = 'g2-helper-fixture-journal-1';
  const cut = (t, cursor) => ({as_of_t: t, cut_cursor: cursor, authority: C.clone(authority),
    journal_epoch: journalEpoch, cut_id: 'g2-helper-fixture-cut-' + t, complete: true});
  const worker = W.createWorker(fixture.options);
  function syntheticAck(commit) {
    const cp = commit.checkpoint;
    return {schema: C.names.receipt, version: 5, id: commit.id,
      worker_id: fixture.options.worker_producer_id, producer_epoch: fixture.options.worker_producer_epoch,
      journal_epoch: cp.journal_epoch, input_cursor: cp.input_cursor, commit_seq: cp.commit_seq,
      semantic_seq: cp.semantic_seq, control_seq: cp.control_seq, cursor: cp.input_cursor + commit.outputs.length,
      raw_request_digest: C.digest(commit), first_received_at: cp.last_t,
      receipts: commit.outputs.map((body, index) => ({schema: 'home-context-receipt-2', id: body.id,
        producer_id: fixture.options.worker_producer_id, producer_epoch: fixture.options.worker_producer_epoch,
        stream_epoch: cp.journal_epoch, cursor: cp.input_cursor + index + 1, committed_at: cp.last_t,
        payload_digest: C.digest(body), status: 'accepted'}))};
  }
  const bootstrap_commit = worker.prepareBootstrap({cut: cut(0, 0)});
  worker.acknowledge(bootstrap_commit, syntheticAck(bootstrap_commit));
  const semantic0_commit = worker.prepareBoundary({t: 0, cut: cut(0, 0)});
  const semanticReceipt = syntheticAck(semantic0_commit);
  worker.acknowledge(semantic0_commit, semanticReceipt);
  const publications = semantic0_commit.outputs.map((body, index) => ({schema: C.names.publication,
    id: body.id, producer_id: fixture.options.worker_producer_id, producer_epoch: fixture.options.worker_producer_epoch,
    stream_epoch: journalEpoch, journal_epoch: journalEpoch, cursor: semanticReceipt.receipts[index].cursor,
    context_seq: semanticReceipt.commit_seq, committed_at: 0, payload_type: body.schema,
    payload_version: body.version + '.0', payload_digest: C.digest(body), payload: C.clone(body)}));
  worker.ingestCommittedOutputs(publications, .05);
  const control_point2_commit = worker.prepareBoundary({t: .2, cut: cut(.2, semanticReceipt.cursor)});
  return {bootstrap_commit, semantic0_commit, control_point2_commit,
    provenance: {purpose: 'fresh Worker2 prepared request examples only',
      cut_and_ack_metadata_are_illustrative: true, service_accepted: false,
      genuine_consumer_or_worker_FIRST: false, admission_or_capacity_proof: false}};
}

if (require.main === module) {
  const fixture = createFixture();
  if (process.argv.includes('--rpc')) {
    const readline = require('node:readline');
    let worker = null;
    const methods = new Set(['prepareBootstrap', 'prepareBoundary', 'acknowledge', 'ingest',
      'ingestCommittedOutputs', 'restore', 'snapshot', 'receiptLog', 'admitControlKeyframeRecovery']);
    readline.createInterface({input: process.stdin}).on('line', line => {
      try {
        const request = JSON.parse(line);
        let value;
        if (request.method === 'create') {
          worker = W.createWorker(request.options ?? fixture.options); value = null;
        } else {
          if (!worker || !methods.has(request.method) || !Array.isArray(request.args)) throw Error('fixture_rpc_method');
          value = worker[request.method](...request.args);
        }
        process.stdout.write(JSON.stringify({ok: true, value: value ?? null}) + '\n');
      } catch (error) {
        process.stdout.write(JSON.stringify({ok: false, error: String(error.stack || error)}) + '\n');
      }
    });
  } else if (process.argv.includes('--bootstrap-from-live-cut')) {
    const request = JSON.parse(fs.readFileSync(0, 'utf8'));
    const worker = W.createWorker(request.options ?? fixture.options);
    process.stdout.write(JSON.stringify(worker.prepareBootstrap({cut: request.cut})));
  } else {
    process.stdout.write(JSON.stringify({...fixture, prepared_request_examples: illustrativeRequests(fixture)}));
  }
}
module.exports = {createFixture, illustrativeRequests};
