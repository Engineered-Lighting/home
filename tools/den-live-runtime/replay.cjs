'use strict';
// Ordinary causal replay invokes the real worker; archived output is only an oracle.
const fs=require('node:fs');
const Worker=require('../home-live-context-worker.cjs');
const Lighting=require('../../app/src/home-lighting-target-contract.js');
const {createControlWitness}=require('./manual-control-replay.cjs');
const hash=Lighting.digest,canonical=Lighting.canonicalJson;
function check(condition,reason){if(!condition)throw Error(reason);}
function validateTaskPathOriginal(data){
  // Data-only successor. Never execute a resolver or issue a canonical grant.
  const reason='archive_task_path_original_binding_refused';
  const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&canonical(Object.keys(v).sort())===canonical(keys.slice().sort());
  const same=(a,b)=>canonical(a)===canonical(b),pin=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  const token=v=>typeof v==='string'&&v.length>0&&v.length<=160,uint=v=>Number.isSafeInteger(v)&&v>=0;
  const method=v=>exact(v,['id','digest'])&&token(v.id)&&pin(v.digest);
  check(exact(data,['schema','version','fixture_id','assessment','assessment_sha256','profile_sha256','room_commit_ref','path_witness','assessor_ref'])
    &&data.schema==='home-room-task-assessment-original-2'&&data.version===2&&token(data.fixture_id)
    &&pin(data.profile_sha256)&&hash(data.assessment)===data.assessment_sha256
    &&data.assessment?.schema==='home-fixture-retained-angle-request-2'&&data.assessment.version===2
    &&data.assessment.assessment?.profile_sha256===data.profile_sha256
    &&same(data.assessment.retained_aim_ref?.room_commit_ref,data.room_commit_ref)
    &&Buffer.byteLength(canonical(data),'utf8')<=512*1024,reason);
  const r=data.assessment,w=data.path_witness;
  check(exact(w,['ref','original'])&&exact(w.ref,['store_id','component_id','receipt_id','digest'])
    &&token(w.ref.store_id)&&token(w.ref.component_id)&&token(w.ref.receipt_id)&&pin(w.ref.digest),reason);
  const o=w.original;
  check(exact(o,['schema','version','query','checks','minimum_work_range_m','target_uncertainty_m','total_aim_error_bound_rad',
    'observed_at_home_us','available_at_home_us','valid_until_home_us','evidence_source','physical_permission','physical_truth'])
    &&o.schema==='home-retained-task-path-witness-1'&&o.version===1&&hash(o)===w.ref.digest,reason);
  const q=o.query,d=q?.assessor_descriptor,a=r?.assessment;
  check(exact(q,['schema','version','scope','assessor_descriptor','room_commit_ref','input_cut','fixture_id','retained_aim_ref',
    'retained_motion','profile_sha256','provider_pins','method_ref','qualification_ref','lineage','native','original_caps','motion'])
    &&q.schema==='home-retained-task-path-query-1'&&q.version===1,reason);
  check(exact(q.scope,['site_id','room_id','session_id','domain_epoch'])&&Object.values(q.scope).every(token)
    &&same(r.scope,{home_id:q.scope.site_id,room_id:q.scope.room_id,fixture_id:data.fixture_id})
    &&q.input_cut?.cut_digest===r.retained_aim_ref?.input_cut_digest,reason);
  check(exact(d,['schema','version','fixture_profiles','receipt_store_id','receipt_component_id','method_ref','qualification_ref',
    'speed_fixture_deg_s','motion_timeout_us','evidence_source','physical_permission','physical_truth'])
    &&d.schema==='home-retained-task-assessor-capability-1'&&d.version===1
    &&d.fixture_profiles&&typeof d.fixture_profiles==='object'&&!Array.isArray(d.fixture_profiles)
    &&Object.keys(d.fixture_profiles).length>=1&&Object.keys(d.fixture_profiles).length<=4
    &&Object.entries(d.fixture_profiles).every(([k,v])=>token(k)&&pin(v))
    &&token(d.receipt_store_id)&&token(d.receipt_component_id)&&method(d.method_ref)&&method(d.qualification_ref)
    &&Array.isArray(d.speed_fixture_deg_s)&&d.speed_fixture_deg_s.length===2&&d.speed_fixture_deg_s.every(v=>Number.isFinite(v)&&v>0)
    &&uint(d.motion_timeout_us)&&d.motion_timeout_us>0
    &&d.evidence_source==='simulated'&&d.physical_permission===false&&d.physical_truth===false
    &&exact(data.assessor_ref,['id','digest'])&&data.assessor_ref.id===d.schema&&data.assessor_ref.digest===hash(d),reason);
  check(w.ref.store_id===d.receipt_store_id&&w.ref.component_id===d.receipt_component_id
    &&o.evidence_source==='authored-independent'&&o.physical_permission===false&&o.physical_truth===false
    &&exact(o.checks,['work_coverage','all_people_protection','room_collision','dark_motion_path'])
    &&Object.values(o.checks).every(v=>v===true),reason);
  check(q.fixture_id===data.fixture_id&&same(q.room_commit_ref,data.room_commit_ref)&&same(q.retained_aim_ref,r.retained_aim_ref)
    &&q.profile_sha256===data.profile_sha256&&q.profile_sha256===d.fixture_profiles[data.fixture_id]
    &&same(q.method_ref,d.method_ref)&&same(q.qualification_ref,d.qualification_ref)
    &&same(q.original_caps,r.original_caps)&&same(q.lineage,r.lineage)
    &&same(d.speed_fixture_deg_s,r.speed_fixture_deg_s)&&d.motion_timeout_us===r.motion_timeout_us,reason);
  check(exact(q.motion,['kind','motion_id','revision_id','predecessor_id','fixture_angles_deg','speed_fixture_deg_s','motion_timeout_us'])
    &&Object.keys(q.motion).every(k=>same(q.motion[k],r[k])),reason);
  check(exact(q.native,['reference_sha256','snapshot_sha256','capabilities_sha256','start_centideg'])
    &&['reference_sha256','snapshot_sha256','capabilities_sha256'].every(k=>pin(q.native[k]))
    &&a?.assessment_id==='retained-path-'+w.ref.digest&&same(a.checks,o.checks)
    &&a.current_reference_sha256===q.native.reference_sha256&&same(a.start_centideg,q.native.start_centideg),reason);
  check(same(q.retained_motion?.retained_aim_ref,q.retained_aim_ref)
    &&same([q.retained_motion?.original?.to_pan_deg,q.retained_motion?.original?.to_tilt_deg],r.fixture_angles_deg),reason);
  const caps=q.original_caps;
  check(exact(caps,['evidence_not_after_home_us','protection_not_after_home_us','authority_not_after_home_us','clock_qualification_not_after_home_us'])
    &&Object.values(caps).every(uint)&&[o.observed_at_home_us,o.available_at_home_us,o.valid_until_home_us,r.issued_home_us].every(uint)
    &&o.observed_at_home_us<=o.available_at_home_us&&o.available_at_home_us<=r.issued_home_us
    &&r.issued_home_us<o.valid_until_home_us&&o.valid_until_home_us<=Math.min(...Object.values(caps))
    &&a.valid_from_home_us===o.available_at_home_us&&a.valid_until_home_us===o.valid_until_home_us
    &&r.support_expiry_home_us===Math.min(o.valid_until_home_us,...Object.values(caps)),reason);
  check(['minimum_work_range_m','target_uncertainty_m','total_aim_error_bound_rad'].every(k=>Number.isFinite(o[k])&&a[k]===o[k])
    &&o.minimum_work_range_m>0&&o.target_uncertainty_m>=0&&o.total_aim_error_bound_rad>=0&&o.total_aim_error_bound_rad<=Math.PI,reason);
  return data;
}
function readArchive(filename){
  const stat=fs.lstatSync(filename);check(stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=64*1024*1024,'archive_file_bound');
  const text=fs.readFileSync(filename,'utf8');check(text.endsWith('\n'),'archive_incomplete_tail');
  const lines=text.split('\n');lines.pop();check(lines.length>=2&&lines.length<=16384,'archive_record_bound');
  let prior=null;const records=lines.map((line,index)=>{
    const entry=JSON.parse(line);check(entry.schema==='home-live-session-record-1'&&entry.version===1&&entry.seq===index
      &&entry.previous_digest===prior&&/^[a-f0-9]{64}$/.test(entry.digest),'archive_chain_mismatch');
    const {digest,...body}=entry;check(hash(body)===digest,'archive_digest_mismatch');prior=digest;return entry.record;
  });
  check(records[0].kind==='session_manifest'&&records.at(-1).kind==='session_end','archive_missing_manifest_or_end');
  check(records.at(-1).data.status==='complete','archive_session_incomplete');return records;
}
function replay(filename){
  const records=readArchive(filename),manifest=records[0].data.manifest,config=manifest.worker_config;
  const assessed=manifest.schema==='home-live-session-manifest-2'&&manifest.version===2;
  check((assessed?manifest.runtime_profile==='home-live-runtime-config-3':manifest.schema==='home-live-session-manifest-1'&&manifest.version===1)&&config,'archive_manifest_invalid');
  if(assessed)check(canonical(Object.keys(manifest).sort())===canonical(['schema','version','session_ref',
    'configuration_digest','source_bundle_digest','worker_config','worker_description','runtime_profile'].sort()),'archive_manifest_invalid');
  check(!Object.hasOwn(config,'token')&&!Object.hasOwn(config,'token_file'),'archive_credential_refused');
  const description=Worker.describe(config);check(canonical(description)===canonical(manifest.worker_description),'archive_worker_pins_mismatch');
  if(assessed)check(config.room_subset_mode===true&&config.room_policy?.schema==='home-live-room-policy-2'
    &&config.live_manifest?.schema==='home-live-ingress-manifest-13'&&config.live_manifest.version===13
    &&description.room_subset_mode===true
    &&config.live_manifest.room_subset_interface_digest===description.room_subset_interface_digest,
    'archive_assessed_profile_required');
  const worker=Worker.createWorker(config);let prepared=null,tick=null,cuts=0,ticks=0,commits=0;
  const transactions=new Map(),publications=new Map(),originals=new Map(),control=createControlWitness(config);
  const preparedCommits=new Map(),acceptedCheckpoints=new Map();
  const durableRoomCommits=new Map(),acceptedRoomCommits=new Map();let taskOriginals=0;
  let lastAcceptedCheckpoint=null,restoreRecovery=false,restoreCutSeen=false;
  function requireOriginalCheckpoint(checkpoint){
    if(config.manual_policy&&checkpoint)check(acceptedCheckpoints.has(hash(checkpoint))
      &&canonical(acceptedCheckpoints.get(hash(checkpoint)))===canonical(checkpoint),'archive_restore_checkpoint_original_missing');
  }
  function validatePcOriginals(cut){
    if(!config.pc_support_policy)return;
    const expected=new Map();
    for(const item of cut.items){const fact=item.payload;
      if(fact.schema!=='home-live-fact-1'||fact.kind!=='pc_input_interaction'
        ||fact.status!=='supplied'||fact.value!=='attributed_recent')continue;
      const bindings=config.pc_support_policy.bindings.filter(binding=>binding.source_id===fact.source_id
        &&canonical(binding.method_ref)===canonical(fact.method_ref));
      check(bindings.length===1,'archive_pc_input_original_mismatch');
      const refs=(fact.original_receipt_refs||[]).filter(ref=>ref.component_id===bindings[0].proof_component_id);
      check(refs.length===1,'archive_pc_input_original_mismatch');
      const ref=refs[0],original=originals.get(canonical([ref.store_id,ref.receipt_id]));
      check(original&&Object.hasOwn(original,'pc_input_original'),'archive_missing_pc_input_original');
      check(hash(original.pc_input_original)===ref.digest,'archive_pc_input_original_mismatch');
      const key=canonical(ref);
      check(!expected.has(key)||canonical(expected.get(key))===canonical(original),'archive_pc_input_original_mismatch');
      expected.set(key,original);
    }
    const records=[...expected.keys()].sort().map(key=>expected.get(key));
    check(canonical(cut.pc_input_originals)===canonical(records),'archive_pc_input_original_mismatch');
  }
  for(const {kind,data} of records.slice(1)){
    if(kind==='journal_prepare'){
      check(typeof data.tx_id==='string'&&!transactions.has(data.tx_id),'archive_transaction_identity');
      check(Array.isArray(data.events)&&Array.isArray(data.publications)&&Array.isArray(data.receipts)
        &&Array.isArray(data.original_receipts)&&(data.publications.length>0||Array.isArray(data.control_operations)&&data.control_operations.length>0)&&data.events.length===data.publications.length
        &&data.receipts.length===data.publications.length,'archive_transaction_originals_missing');
      for(let i=0;i<data.publications.length;i++){
        const p=data.publications[i],e=data.events[i],r=data.receipts[i];
        check(p.id===e.id&&r.id===e.id&&p.producer_id===e.producer_id&&r.cursor===p.cursor
          &&p.payload_digest===hash(e.payload)&&canonical(p.payload)===canonical(e.payload),'archive_transaction_original_mismatch');
      }
      if(Object.hasOwn(data,'control_operations'))control.prepare(data.tx_id,data.control_operations);
      transactions.set(data.tx_id,{data,status:'prepared'});
    }else if(kind==='journal_commit'||kind==='journal_abort'){
      const tx=transactions.get(data.tx_id);check(tx&&tx.status==='prepared','archive_transaction_result');
      tx.status=kind==='journal_commit'?'committed':'aborted';
      if(Object.hasOwn(tx.data,'control_operations'))control.settle(data.tx_id,tx.status);
      if(tx.status==='committed'){
        if((config.manual_policy||assessed)&&data.result?.schema==='home-context-worker-commit-receipt-1'){
          const original=preparedCommits.get(data.result.id);
          check(original&&data.result.commit_digest===hash(original)
            &&canonical(tx.data.events)===canonical(original.publications),'archive_accepted_checkpoint_original_missing');
          check(!Object.hasOwn(tx.data,'control_operations'),'archive_worker_control_operation_laundering');
          if(config.manual_policy){
            control.validateAcceptedCheckpoint(original.checkpoint);
            acceptedCheckpoints.set(hash(original.checkpoint),original.checkpoint);
            lastAcceptedCheckpoint=original.checkpoint;
          }
          if(assessed)durableRoomCommits.set(data.result.id,original);
        }
        for(const original of tx.data.original_receipts){const key=canonical([original.ref?.store_id,original.ref?.receipt_id]);
          check(original.ref&&(!originals.has(key)||canonical(originals.get(key))===canonical(original)),'archive_original_receipt_conflict');originals.set(key,original);}
        for(const publication of tx.data.publications)publications.set(publication.cursor,publication);
      }
    }else if(kind==='worker_restore'){
      requireOriginalCheckpoint(data.checkpoint);
      worker.restore(data.checkpoint,data.t,{allow_expired:data.allow_expired===true});prepared=null;
      if(config.manual_policy){restoreRecovery=true;restoreCutSeen=false;}
    }else if(kind==='worker_cold_start'){
      requireOriginalCheckpoint(data.checkpoint);
      worker.coldStart(data.checkpoint??null,data.reset_transport===true);prepared=null;
      if(config.manual_policy){restoreRecovery=true;restoreCutSeen=false;}
    }else if(kind==='worker_cut'){
      control.validateCut(data.cut);
      for(const item of data.cut.items){const archived=publications.get(item.cursor);
        check(archived&&canonical(archived)===canonical(item),'archive_missing_original_publication');
        for(const ref of item.payload.original_receipt_refs||[]){const original=originals.get(canonical([ref.store_id,ref.receipt_id]));
          check(original&&canonical(original.ref)===canonical(ref),'archive_missing_original_receipt');}}
      validatePcOriginals(data.cut);
      if(config.room_policy)check(data.receipt_clock_bounds,'archive_missing_consumer_clock_bounds');
      worker.applyCut(data.cut,data.received_at,data.receipt_clock_bounds??null);cuts++;
      if(restoreRecovery)restoreCutSeen=true;
    }else if(kind==='worker_tick'){
      check(!restoreRecovery||restoreCutSeen,'archive_restore_requires_fresh_cut');
      control.validateTick();
      check(prepared===null,'archive_tick_without_settlement');tick=data.t;prepared=worker.tick(tick);ticks++;
    }else if(kind==='worker_prepared'){
      control.validateCheckpoint(data.prepared?.checkpoint);
      check(prepared!==null,'archive_prepared_without_tick');
      check(canonical(prepared)===canonical(data.prepared),'ordinary_replay_output_mismatch');
      if(data.commit){check(canonical(data.commit.checkpoint)===canonical(prepared.checkpoint)
        &&canonical(data.commit.publications)===canonical(prepared.publications)&&data.commit.id===prepared.commit_id,'ordinary_replay_commit_mismatch');}
      if((config.manual_policy||assessed)&&data.commit)preparedCommits.set(data.commit.id,data.commit);
    }else if(kind==='worker_commit_result'){
      check(prepared&&data.receipt?.status==='accepted'&&data.receipt.id===prepared.commit_id,'archive_commit_result_mismatch');
      if(data.checkpoint)check(canonical(data.checkpoint)===canonical(prepared.checkpoint),'ordinary_replay_checkpoint_mismatch');
      if(assessed){const original=preparedCommits.get(data.receipt.id);
        check(original&&hash(original)===data.receipt.commit_digest,'archive_task_commit_original_missing');
        check(durableRoomCommits.has(data.receipt.id)
          &&canonical(durableRoomCommits.get(data.receipt.id))===canonical(original),
          'archive_task_commit_transaction_missing');
        acceptedRoomCommits.set(data.receipt.id,original);}
      prepared=null;tick=null;commits++;
      if(config.manual_policy&&restoreRecovery){check(restoreCutSeen,'archive_restore_requires_fresh_cut');restoreRecovery=false;}
    }else if(kind==='room_task_assessment_original'){
      check(assessed,'archive_record_kind_refused');
      const keys=['fixture_id','assessment','assessment_sha256','profile_sha256','room_commit_ref'];
      const pathOriginal=data?.schema==='home-room-task-assessment-original-2';
      if(pathOriginal)validateTaskPathOriginal(data);
      check(data&&(pathOriginal||canonical(Object.keys(data).sort())===canonical(keys.sort()))
        &&typeof data.fixture_id==='string'&&data.fixture_id.length>0&&data.fixture_id.length<=160
        &&/^[a-f0-9]{64}$/.test(data.profile_sha256)&&hash(data.assessment)===data.assessment_sha256
        &&Buffer.byteLength(canonical(data),'utf8')<=512*1024,'archive_task_original_binding_refused');
      const ref=data.room_commit_ref,request=data.assessment,commit=acceptedRoomCommits.get(ref?.commit_id);
      check(pathOriginal||typeof request?.assessment?.assessment_id!=='string'
        ||!request.assessment.assessment_id.startsWith('retained-path-'),'archive_task_path_original_required');
      const refKeys=['configuration_digest','manifest_digest','worker_version','room_output_schema','commit_id',
        'commit_body_sha256','decision_sha256','room_output_revision_id','room_output_sha256'];
      check(ref&&canonical(Object.keys(ref).sort())===canonical(refKeys.sort())&&commit
        &&ref.configuration_digest===description.config_digest&&ref.manifest_digest===config.manifest_ref?.digest
        &&ref.worker_version==='home-live-context-worker-12.0.0'&&ref.room_output_schema==='home-live-room-output-1'
        &&ref.commit_body_sha256===hash(commit)&&request.schema==='home-fixture-retained-angle-request-2'
        &&request.version===2&&request.assessment?.profile_sha256===data.profile_sha256
        &&canonical(request.retained_aim_ref?.room_commit_ref)===canonical(ref),'archive_task_original_binding_refused');
      const matches=commit.publications.map(p=>p.payload).filter(p=>p.room_output&&hash(p)===ref.decision_sha256);
      check(matches.length===1&&matches[0].room_output.revision_id===ref.room_output_revision_id
        &&hash(matches[0].room_output)===ref.room_output_sha256
        &&request.retained_aim_ref.input_cut_digest===matches[0].input_cut?.cut_digest
        &&matches[0].room_output.fixtures.some(f=>f.fixture_id===data.fixture_id),'archive_task_original_binding_refused');
      // This retains and binds original material. It never creates a grant or
      // substitutes for independent canonical command/native replay.
      taskOriginals++;
    }else if(kind!=='session_end')throw Error('archive_record_kind_refused');
  }
  check([...transactions.values()].every(tx=>tx.status!=='prepared'),'archive_transaction_unconfirmed');
  control.finalize();
  check(!restoreRecovery,'archive_restore_recovery_unconfirmed');
  control.validateTerminalCheckpoint(lastAcceptedCheckpoint);
  check(prepared===null&&ticks>0&&commits>0,'archive_worker_effect_unconfirmed');
  return {schema:'home-live-ordinary-replay-result-'+(assessed?'2':'1'),version:assessed?2:1,status:'reproduced',cuts,ticks,commits,
    ...(assessed?{task_assessment_originals:taskOriginals,canonical_commands_replayed:false}:{}),
    manifest_digest:hash(manifest),config_digest:description.config_digest,physical_authority:false,acceptance_eligible:false};
}
module.exports={readArchive,replay,validateTaskPathOriginal};
if(require.main===module){try{if(process.argv.length!==3)throw Error('usage_replay_archive');process.stdout.write(JSON.stringify(replay(process.argv[2]))+'\n');}
catch(error){process.stdout.write(JSON.stringify({schema:'home-live-ordinary-replay-result-1',version:1,status:'incomplete',reason:error.message,
  physical_authority:false,acceptance_eligible:false})+'\n');process.exitCode=2;}}
