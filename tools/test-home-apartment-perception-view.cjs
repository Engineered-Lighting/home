'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const View = require('../app/src/home-apartment-perception-view.js');

const scope = () => ({ siteId: 'victoria', contentRevision: 'pack-a', frameDigest: 'frame-a', registrationVersion: 1,
  cameraId: 'den', sessionId: 'session-a', generation: 7 });
const clock = () => ({ now_s: 100, uncertainty_s: .1, domain: 'unix' });
const frame = (extra = {}) => ({ frame_id: 'frame-1', capture_id: 'capture-1', sequence_id: 1,
  captured_at: 99, valid_until: 102, width: 640, height: 480, media_id: 'media-1', generation: 7, site_id: 'victoria', ...extra });
const pose = (extra = {}) => ({ frame_id: 'frame-1', capture_id: 'capture-1', observed_at: 99, available_at: 99.5,
  valid_until: 102, model_id: 'pose-model', source_id: 'specialist-a', status: 'candidate',
  keypoints: [{ id: 'head', x: 100, y: 50 }, { id: 'hip', x: 110, y: 200, confidence: .01 },
    { id: 'hidden', x: 90, y: 80, visible: false }], skeleton_edges: [['head', 'hip'], ['head', 'hidden'], ['head', 'missing']], ...extra });
const room = (extra = {}) => ({ status: 'admitted', qualification: 'qualified_viewing', frame_id: 'frame-1',
  observed_at: 99, available_at: 99.5, valid_until: 102, scope: scope(),
  registration: { status: 'applicable', camera_id: 'den', camera_to_room_digest: 'registration-a' },
  joints: [{ id: 'head', position_m: [1, 2, 1.7] }, { id: 'hip', position_m: [1, 2, .9], confidence: 0 },
    { id: 'hidden', position_m: [1, 2, 1], visible: false }], edges: [['head', 'hip'], ['head', 'hidden'], ['hip', 'missing']],
  anchor_joint_id: 'hip', ...extra });
const envelope = (extra = {}) => ({ schema: 'home-apartment-perception-1', scope: scope(), source_epoch: null,
  session_state: 'recording', frame: frame(), pose2d: pose(), models: [], room_pose: room(), ...extra });
const project = (env = envelope(), extra = {}) => View.project({ envelope: env, scope: scope(), clock: clock(), ...extra });
const displayed = (descriptor = frame(), pin = scope()) => ({ descriptor, scope: pin, key: View.frameKey(descriptor, pin) });

test('bounded model projection retains actual pose/JEV and metric refusal after a large source catalog',()=>{
  const catalog=Array.from({length:40},(_,i)=>({family:'catalog_'+i,source_id:'source_'+i,model_id:null,status:'not_provisioned',
    observed_at:null,available_at:null,valid_until:null,result:{summary:'Not provisioned'}}));
  const jev={family:'jev',source_id:'reasoning_b',model_id:'authored-jev-v1',status:'completed',
    observed_at:null,available_at:99.5,valid_until:101,result:{activity:'reading'}};
  const poseRow={family:'pose',source_id:'specialist_b',model_id:'pose-one',status:'fresh',observed_at:99,available_at:99.2,valid_until:101,result:{frame:'frame-1'}};
  const metric={family:'metric_input',source_id:'apartment_viewing_metric',model_id:null,status:'unavailable',reason:'source_missing',result:null};
  const input=envelope({models:[...catalog,poseRow,jev,metric]}),original=JSON.stringify(input);
  const out=project(input);
  assert.equal(out.models.length,32);assert.equal(out.modelsTruncated,true);
  assert.equal(out.models.find(row=>row.family==='jev')?.model_id,'authored-jev-v1');
  assert.equal(out.models.find(row=>row.family==='jev')?.observed_at,null,'missing original source time stays missing');
  assert.equal(out.models.find(row=>row.family==='pose')?.valid_until,101);
  assert.equal(out.models.find(row=>row.family==='metric_input')?.reason,'source_missing');
  assert.equal(JSON.stringify(input),original);
});

function personDetails(frameId = 'frame-1') {
  return {schema:'home-lab-frame-persons-1',version:1,frame_id:frameId,coordinate_space:'source_pixels',
    persons:['desk-instance','sofa-instance'].map((instance_id,index)=>({instance_id,
      box:{x:20+index*300,y:20,w:180,h:350},pose_status:'available',pose_reason:null,layout:'coco17',
      score_semantics:'raw model response; not a calibrated probability',
      joints:Array.from({length:17},(_,joint_id)=>({joint_id,name:'joint-'+joint_id,
        pixel:joint_id===0?[100+index*300,50]:null,score:joint_id===0?.8:-.1,
        status:joint_id===0?'supported':'uncertain',reason:joint_id===0?null:'below_threshold'}))}))};
}

test('matching analyzed image retains both person boxes and every original missing joint slot',()=>{
  const input=envelope({pose2d:pose({persons2d:personDetails()})});
  const original=JSON.stringify(input),result=project(input,{displayedFrame:displayed()});
  assert.equal(result.pose2d.renderable,true);
  assert.equal(result.pose2d.boxes?.length,2,'both detector boxes must reach the displayed image');
  assert.deepEqual(result.pose2d.persons.map(p=>p.instance_id),['desk-instance','sofa-instance']);
  assert.equal(result.pose2d.persons[1].joints.length,17);
  assert.equal(result.pose2d.persons[1].joints[1].pixel,null);
  assert.equal(result.pose2d.persons[1].joints[1].reason,'below_threshold');
  assert.equal(result.pose2d.persons[1].joints[1].score,-.1,'raw response must not become probability');
  assert.equal(JSON.stringify(input),original);
});

test('person details refuse wrong original, duplicate instances and out-of-image boxes without inventing bodies',()=>{
  for(const mutate of [p=>p.frame_id='other-frame',p=>p.persons[1].instance_id=p.persons[0].instance_id,
    p=>p.persons[1].box.x=639,p=>p.persons[0].joints[0].pixel=[Infinity,50],
    p=>p.persons[0].joints.pop(),p=>p.persons[0].joints[0].score=null,
    p=>p.persons[0].joints[0].reason='low_response']){
    const details=personDetails();mutate(details);
    const result=project(envelope({pose2d:pose({persons2d:details})}),{displayedFrame:displayed()});
    assert.deepEqual(result.pose2d.persons,[]);
    assert.deepEqual(result.pose2d.boxes,[]);
    assert.equal(result.pose2d.personsReason,'invalid_persons2d');
    assert.equal(result.roomPose.noLightingAuthority,true);
  }
});

test('separate reviewed diagnostic packet can display only its actual partial joints without admitting Follow', () => {
  const packet = JSON.parse(readFileSync(require('node:path').join(__dirname, 'fixtures/apartment-diagnostic-room-pose.json'), 'utf8')).diagnostic_room_pose;
  assert.equal(typeof View.diagnosticEventKey, 'function', 'diagnostic observations need their own immutable receipt identity');
  const pin = packet.diagnostic_pose.scope, frameValue = packet.frame;
  const env = { schema: 'home-apartment-perception-1', scope: pin, session_state: 'recording', source_epoch: packet.frame.source_epoch,
    frame: frameValue, pose2d: null, models: [], room_pose: null, diagnostic_room_pose: packet };
  const result = View.project({ envelope: env, scope: pin, clock: null,
    displayedFrame: { descriptor: frameValue, scope: pin, key: View.frameKey(frameValue, pin) },
    diagnosticReceipt: { eventKey: View.diagnosticEventKey(packet), remainingHoldS: 1.5 } });
  assert.equal(result.diagnosticRoomPose.renderable, true);
  assert.equal(result.diagnosticRoomPose.status, 'timing_unverified');
  assert.deepEqual(result.diagnosticRoomPose.joints.map(j => j.position_m), packet.diagnostic_pose.joints.map(j => j.position_m));
  assert.equal(result.roomPose.admitted, false); assert.equal(result.follow.allowed, false);
  assert.equal(result.diagnosticRoomPose.noLightingAuthority, true);
});

test('UMD exposes the same pure browser and CommonJS API', () => {
  const context = vm.createContext({});
  vm.runInContext(readFileSync(require.resolve('../app/src/home-apartment-perception-view.js'), 'utf8'), context);
  assert.equal(typeof context.HomeApartmentPerceptionView.project, 'function');
  assert.equal(JSON.stringify(context.HomeApartmentPerceptionView.SCOPE_FIELDS), JSON.stringify(View.SCOPE_FIELDS));
  assert.equal(context.HomeApartmentPerceptionView.frameKey(frame(), scope()), View.frameKey(frame(), scope()));
});

test('createScope copies exactly seven real pins and never invents missing identity', () => {
  const identity = { ...scope(), cameraId: 'wrong', sessionId: 'wrong', generation: 99, extra: 'ignored' };
  const built = View.createScope(identity, { id: 'session-a', camera_id: 'den', generation: 7 });
  assert.deepEqual(built, scope()); assert.deepEqual(Object.keys(built), View.SCOPE_FIELDS);
  assert.equal(View.scopeMatches(built, scope()), true);
  assert.equal(View.scopeMatches({ ...built, extra: 1 }, built), false);
  const missing = View.createScope({}, {});
  assert.equal(missing.registrationVersion, null);
  assert.equal(View.scopeMatches(missing, missing), false);
});

test('every renderer/session/generation pin refuses another scope before exposing data', () => {
  for (const [key, value] of [['siteId', 'echo'], ['contentRevision', 'pack-b'], ['frameDigest', 'frame-b'],
    ['registrationVersion', 2], ['cameraId', 'another'], ['sessionId', 'session-b'], ['generation', 8]]) {
    const changed = { ...scope(), [key]: value };
    assert.equal(View.scopeMatches(scope(), changed), false, key);
    const result = project(envelope({ scope: changed }));
    assert.equal(result.status, 'refused', key); assert.equal(result.reason, 'scope_mismatch', key);
    assert.equal(result.frame, null); assert.equal(result.pose2d.renderable, false);
    assert.deepEqual(result.models, []); assert.equal(result.roomPose.admitted, false); assert.equal(result.follow.allowed, false);
  }
});

test('frame keys pin image, capture, media and all scope fields without synthesizing IDs', () => {
  const key = View.frameKey(frame(), scope()); assert.equal(typeof key, 'string');
  for (const extra of [{ frame_id: 'frame-2' }, { capture_id: 'capture-2' }, { media_id: 'media-2' }, { sequence_id: 2 }]) {
    assert.notEqual(View.frameKey(frame(extra), scope()), key);
  }
  assert.notEqual(View.frameKey(frame(), { ...scope(), generation: 8 }), key);
  assert.equal(View.frameKey(frame({ media_id: null }), scope()), null);
  assert.equal(View.frameKey(frame({ sequence_id: Infinity }), scope()), null);
  assert.equal(View.frameKey(frame()), null);
});

test('supplied joints and edges are preserved without confidence inference or hidden/missing completion', () => {
  const input = envelope(); const before = JSON.stringify(input);
  const result = project(input);
  assert.equal(result.roomPose.admitted, true); assert.equal(result.roomPose.status, 'current');
  assert.deepEqual(result.roomPose.joints.map(j => j.id), ['head', 'hip']);
  assert.deepEqual(result.roomPose.edges, [['head', 'hip']]);
  assert.equal(result.roomPose.joints[1].confidence, 0);
  assert.deepEqual(result.roomPose.anchor.position_m, [1, 2, .9]);
  assert.equal(result.follow.allowed, true); assert.equal(result.roomPose.noLightingAuthority, true);
  assert.equal(result.pose2d.renderable, true); assert.deepEqual(result.pose2d.edges, [['head', 'hip']]);
  assert.equal(result.pose2d.keypoints[1].confidence, .01);
  assert.equal(JSON.stringify(input), before, 'projection cannot mutate original source evidence');
  result.roomPose.joints[0].position_m[0] = 100;
  assert.equal(input.room_pose.joints[0].position_m[0], 1, 'renderer arrays are copied');
});

test('current diagnostic alignment is visibly distinct and supplies no lighting authority', () => {
  const result = project(envelope({ room_pose: room({ qualification: 'diagnostic_alignment' }) }));
  assert.equal(result.roomPose.admitted, true); assert.equal(result.roomPose.diagnostic, true);
  assert.equal(result.roomPose.noLightingAuthority, true); assert.equal(result.follow.allowed, true);
  assert.equal(project(envelope({ room_pose: room({ qualification: 'candidate' }) })).roomPose.admitted, false);
});

test('room timing obeys qualified uncertainty intervals and exact expiry boundaries', () => {
  const cases = [
    ['current', {}, clock(), true, 'current'],
    ['observed boundary', { observed_at: 99.9, available_at: 99.9 }, clock(), true, 'current'],
    ['expiry boundary', { valid_until: 100.1 }, clock(), false, 'stale'],
    ['expiry after boundary', { valid_until: 100.10001 }, clock(), true, 'current'],
    ['observation future', { observed_at: 100.2, available_at: 100.3 }, clock(), false, 'future'],
    ['availability uncertain future', { available_at: 99.95 }, clock(), false, 'future'],
    ['missing observed', { observed_at: null }, clock(), false, 'unavailable'],
    ['missing available', { available_at: null }, clock(), false, 'unavailable'],
    ['missing expiry', { valid_until: null }, clock(), false, 'unavailable'],
    ['invalid order', { available_at: 98 }, clock(), false, 'invalid'],
    ['NaN original time', { observed_at: NaN }, clock(), false, 'invalid'],
    ['missing clock', {}, null, false, 'unavailable'],
    ['unknown uncertainty', {}, { now_s: 100, uncertainty_s: null, domain: 'unix' }, false, 'unavailable'],
    ['wrong clock domain', {}, { now_s: 100, uncertainty_s: 0, domain: 'monotonic' }, false, 'unavailable'],
    ['nonfinite clock', {}, { now_s: Infinity, uncertainty_s: .1, domain: 'unix' }, false, 'unavailable'],
  ];
  for (const [label, extra, sourceClock, admitted, status] of cases) {
    const result = project(envelope({ room_pose: room(extra) }), { clock: sourceClock });
    assert.equal(result.roomPose.admitted, admitted, label);
    assert.equal(result.roomPose.age.status, status, label);
    assert.equal(result.follow.allowed, admitted, label);
    if (!admitted) assert.deepEqual(result.roomPose.joints, [], label);
  }
});

test('missing/applicability-mismatched registration withdraws room and follow while exact 2D remains', () => {
  for (const extra of [{ registration: null }, { registration: { status: 'candidate', camera_id: 'den', camera_to_room_digest: 'a' } },
    { registration: { status: 'applicable', camera_id: 'other', camera_to_room_digest: 'a' } },
    { registration: { status: 'applicable', camera_id: 'den', camera_to_room_digest: null } },
    { scope: { ...scope(), generation: 8 } }, { status: 'candidate' }]) {
    const result = project(envelope({ room_pose: room(extra) }));
    assert.equal(result.roomPose.admitted, false); assert.equal(result.follow.allowed, false);
    assert.equal(result.pose2d.renderable, true); assert.deepEqual(result.roomPose.joints, []);
  }
  const pin = { ...scope(), registrationVersion: null };
  const result = View.project({ envelope: envelope({ scope: pin, room_pose: room({ scope: pin }) }), scope: pin, clock: clock() });
  assert.equal(result.pose2d.renderable, true); assert.equal(result.roomPose.admitted, false);
});

test('exact raw 2D diagnostics render with unknown age; source/frame/capture mismatches do not', () => {
  const unknown = project(envelope({ pose2d: pose({ observed_at: null, available_at: null, valid_until: null }), room_pose: null }), { clock: null });
  assert.equal(unknown.pose2d.renderable, true); assert.equal(unknown.pose2d.age.seconds, null);
  assert.equal(unknown.pose2d.age.status, 'unavailable'); assert.equal(unknown.follow.allowed, false);
  for (const extra of [{ frame_id: 'frame-other' }, { capture_id: 'capture-other' }, { generation: 8 }, { scope: { ...scope(), sessionId: 'other' } }]) {
    assert.equal(project(envelope({ pose2d: pose(extra) })).pose2d.renderable, false);
  }
  for (const extra of [{ generation: 8 }, { site_id: 'echo' }, { width: Infinity }, { media_id: null }]) {
    const result = project(envelope({ frame: frame(extra) }));
    assert.equal(result.frame, null); assert.equal(result.pose2d.renderable, false); assert.equal(result.follow.allowed, false);
  }
});

test('held A image receives only its own pose while B decodes; latest model rows retain different-frame association', () => {
  const a = frame(), poseA = pose();
  const b = frame({ frame_id: 'frame-2', capture_id: 'capture-2', media_id: 'media-2', sequence_id: 2 });
  const poseB = pose({ frame_id: 'frame-2', capture_id: 'capture-2' });
  const model = { family: 'pose', model_id: 'b-model', source_id: 'specialist-b', status: 'ok', observed_at: 99.5,
    available_at: 99.6, valid_until: 103, frame_id: 'frame-2', capture_id: 'capture-2', result: { people: 1 } };
  const pending = project(envelope({ frame: b, pose2d: poseA, models: [model] }), { displayedFrame: displayed(a) });
  assert.equal(pending.frame.descriptor.media_id, 'media-2', 'new descriptor remains available to the decoder');
  assert.equal(pending.pose2d.renderable, true); assert.equal(pending.models[0].association, 'different-frame');
  assert.equal(pending.roomPose.admitted, false, 'no current room pose is inferred for a pending image');
  const oldPoseOnB = project(envelope({ frame: b, pose2d: poseA }), { displayedFrame: displayed(b) });
  assert.equal(oldPoseOnB.pose2d.renderable, false);
  const current = project(envelope({ frame: b, pose2d: poseB, models: [model] }), { displayedFrame: displayed(b) });
  assert.equal(current.pose2d.renderable, true); assert.equal(current.models[0].association, 'same-frame');
  assert.equal(project(envelope(), { displayedFrame: null }).pose2d.renderable, false);
  assert.equal(project(envelope(), { displayedFrame: displayed(a, { ...scope(), generation: 8 }) }).pose2d.renderable, false);
});

test('reused frame IDs with changed media do not guess which image owns a pose', () => {
  const changed = frame({ media_id: 'replacement-image' });
  const result = project(envelope({ frame: changed }), { displayedFrame: displayed(frame()) });
  assert.equal(result.pose2d.renderable, false); assert.equal(result.pose2d.reason, 'media_identity_ambiguous');
});

test('stopped, failed and disconnected sessions retain recorded 2D and withdraw room/follow', () => {
  for (const state of ['stopped', 'failed', 'disconnected', 'unknown']) {
    const result = project(envelope({ session_state: state }));
    assert.equal(result.pose2d.renderable, true); assert.equal(result.pose2d.status, 'recorded');
    assert.equal(result.roomPose.admitted, false); assert.equal(result.roomPose.reason, 'session_not_live');
    assert.equal(result.follow.allowed, false);
  }
});

test('JEV keeps its independent original evidence-window time and result without inheriting the pose timestamp', () => {
  const jev = { family: 'jev', model_id: 'jev-phone', model_revision: 'r2', source_id: 'specialist-jev',
    status: 'candidate', reason: 'unscored', observed_at: 80, available_at: 90, valid_until: 95,
    frame_id: 'frame-older', result: { label: 'phone_use', evidence_window: { start_s: 75, end_s: 80 } } };
  const result = project(envelope({ models: [jev] })); const row = result.models[0];
  for (const key of ['family', 'model_id', 'model_revision', 'source_id', 'status', 'reason', 'observed_at', 'available_at', 'valid_until']) assert.equal(row[key], jev[key], key);
  assert.deepEqual(row.result, jev.result); assert.equal(row.association, 'different-frame');
  assert.equal(row.age.status, 'stale'); assert.equal(row.age.seconds, 20);
  assert.equal(result.pose2d.originalTimes.observed_at, 99);
  assert.equal(row.result.evidence_window.end_s, 80);
});

test('invalid visible positions, duplicate IDs and collection limits refuse rather than synthesize a room skeleton', () => {
  for (const joints of [[{ id: 'bad', position_m: [1, NaN, 3] }], [{ id: 'bad', position_m: [1, Infinity, 3] }],
    [{ id: 'a', position_m: [1, 2, 3] }, { id: 'a', position_m: [4, 5, 6] }],
    Array.from({ length: 129 }, (_, id) => ({ id, position_m: [0, 0, 0] }))]) {
    const result = project(envelope({ room_pose: room({ joints }) }));
    assert.equal(result.roomPose.admitted, false); assert.deepEqual(result.roomPose.joints, []); assert.equal(result.follow.allowed, false);
  }
  const hidden = project(envelope({ room_pose: room({ anchor_joint_id: 'hidden' }) }));
  assert.equal(hidden.roomPose.admitted, true); assert.equal(hidden.roomPose.anchor, null); assert.equal(hidden.follow.allowed, false);
  const missing = project(envelope({ room_pose: room({ joints: [{ id: 'head' }, { id: 'hip', visible: false }] }) }));
  assert.deepEqual(missing.roomPose.joints, []); assert.equal(missing.follow.allowed, false);
  const edges = project(envelope({ room_pose: room({ edges: Array.from({ length: 257 }, () => ['head', 'hip']) }) }));
  assert.equal(edges.roomPose.admitted, false);
});

test('model result display is bounded and nonfinite/cyclic results become explicitly unavailable', () => {
  const cyclic = {}; cyclic.self = cyclic;
  const models = Array.from({ length: 40 }, (_, id) => ({ family: 'test', model_id: `m${id}`, source_id: 's', status: 'ok',
    observed_at: null, available_at: null, valid_until: null, result: { text: 'x'.repeat(3000), list: Array.from({ length: 80 }, (_, i) => i) } }));
  models[0].result = { nonfinite: Infinity }; models[1].result = cyclic;
  const result = project(envelope({ models }));
  assert.equal(result.models.length, 32); assert.equal(result.modelsTruncated, true);
  assert.equal(result.models[0].result, null); assert.equal(result.models[0].resultUnavailable, true);
  assert.equal(result.models[1].result, null); assert.equal(result.models[1].resultUnavailable, true);
  assert.equal(result.models[2].result.text.length, 2048); assert.equal(result.models[2].result.list.length, 64);
  assert.equal(result.models[2].resultTruncated, true);
});
