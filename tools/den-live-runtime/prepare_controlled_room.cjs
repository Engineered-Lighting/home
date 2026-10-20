'use strict';
// Protected preparation: raw equipment/geometry never enter console or source.
const fs=require('node:fs'),path=require('node:path'),{pathToFileURL}=require('node:url');
const L=require('../../app/src/home-lighting-target-contract.js');
const Core=require('../../app/src/home-equipment-registration.js');
const Display=require('../../app/src/home-room-output-view.js');
const Composition=require('../../app/src/home-live-room-composition.js');
const ROOT=path.resolve(__dirname,'../..'),copy=x=>JSON.parse(JSON.stringify(x));
const DEFAULT_SELECTION={schema:'home-live-resident-asset-selection-1',version:1,profile:'explicit',assets:{cloud:'points.ply',photo:'apartment.mobile.ply',mesh:'mesh.mobile.glb'}};
const pin=(id,body)=>({id,digest:L.digest(body??id)});
const ACTUAL_REVISION='local-2026-10-15-den-view-r1-v3';
const ACTUAL_MANIFEST='2603a77906c2f8081eb87b461d5c8f8dce3246ddd3f7940a2cd4a50017327268';
const EQUIPMENT_DIGEST='fa93a675a8d1845f5aaff9c2760832b6deb499a4df7e65a05b469fa605926d33';
function privateJson(filename,max=1024*1024){
  if(typeof filename!=='string'||!path.isAbsolute(filename))throw Error('protected_absolute_file_required');
  const stat=fs.lstatSync(filename);if(!stat.isFile()||stat.isSymbolicLink()||stat.size>max||process.platform!=='win32'&&(stat.mode&0o077))throw Error('protected_file_refused');
  return JSON.parse(fs.readFileSync(filename,'utf8'));
}
function writePrivate(filename,value){
  if(!path.isAbsolute(filename)||fs.existsSync(filename))throw Error('fresh_protected_output_required');
  fs.mkdirSync(path.dirname(filename),{recursive:true,mode:0o700});
  const fd=fs.openSync(filename,'wx',0o600);try{fs.writeFileSync(fd,JSON.stringify(value)+'\n');fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
}
async function prepareControlledRoom({packDirectory,equipmentFile,assetSelection=DEFAULT_SELECTION,baseBinding,sourceBundleDigest,outputFile,roomAssessmentFile=null,authoredFixturePin=null,clarification=false,manual=false}){
  if(typeof clarification!=='boolean'||typeof manual!=='boolean')throw Error('controlled_clarification_profile_required');
  clarification=clarification||manual;
  if(!baseBinding||baseBinding.schema!=='home-den-controlled-base-binding-1'||baseBinding.version!==1
      ||!baseBinding.registration||!baseBinding.capture_registration||!baseBinding.delivery_policy
      ||!/^[a-f0-9]{64}$/.test(sourceBundleDigest||''))throw Error('explicit_controlled_base_binding_required');
  let clarificationPolicy=null;
  if(clarification){
    const methods=baseBinding.source_policy?.method_refs;
    const matched=Array.isArray(methods)?methods.filter(m=>m?.id==='controlled-authored-holding-method'):[];
    if(matched.length!==1)throw Error('controlled_clarification_method_required');
    clarificationPolicy={schema:'home-live-clarification-policy-1',version:1,proposal_ttl_s:30,max_pending:1,max_history:128,
      repeat_policy:'one_per_scope',material_methods:[copy(matched[0])],interaction_methods:[copy(matched[0])]};
    if(!require('../../app/src/home-live-clarification-state.js').validatePolicy(clarificationPolicy).ok)throw Error('controlled_clarification_method_required');
  }
  const {inspectRoom}=await import(pathToFileURL(path.join(ROOT,'tools/den-room/prepare.mjs')).href);
  const inspected=await inspectRoom({packDirectory,equipmentFile,assetSelection});
  const {candidate,equipment,registered_model:derived}=inspected,pack=candidate.pack;
  const actual=pack.revision===ACTUAL_REVISION;
  if(actual&&(pack.manifest_digest!==ACTUAL_MANIFEST||equipment.revision!==2||equipment.digest!==EQUIPMENT_DIGEST))throw Error('actual_victoria_equipment_or_pack_pin_mismatch');
  const authored=!actual&&authoredFixturePin&&Object.keys(authoredFixturePin).sort().join(',')==='equipment_digest,manifest_digest,schema,version'
    &&authoredFixturePin.schema==='home-den-authored-room-1'&&authoredFixturePin.version===1
    &&authoredFixturePin.manifest_digest===pack.manifest_digest&&authoredFixturePin.equipment_digest===equipment.digest;
  if(actual&&authoredFixturePin||!actual&&!authored)throw Error('current_actual_or_explicit_authored_room_required');
  if(!derived)throw Error('explicit_registered_model_selection_required');
  const mounts=equipment.equipment.filter(r=>r.kind==='mount').sort((a,b)=>a.id.localeCompare(b.id));
  if(mounts.length!==4||mounts.some(m=>!Array.isArray(m.position_m)||m.position_status==='missing'))throw Error('four_saved_resolved_mounts_required');
  const registration=copy(baseBinding.registration),until=Date.now()/1000+600;
  const room={schema:'home-live-room-policy-1',version:1,policy_ref:pin('controlled-room-output-policy'),registration,
    inventory_ref:pin('controlled-four-fixture-inventory',{equipment_ref:{revision:equipment.revision,digest:equipment.digest},mounts:mounts.map(m=>m.id)}),
    valid_until:until,ambient_timing:{fade_in_s:1,vacancy_delay_s:1,fade_out_s:1,source_loss_hold_s:0},
    exposure:{assessment_ref:pin('authored-room-exposure-upper-bounds'),reference_ref:pin('authored-normalized-exposure-reference'),
      unit:'normalized_modeled_exposure',max_total:.1,valid_until:until},
    fixtures:mounts.map((m,i)=>({fixture_id:'victoria-den-light-'+String(i+1).padStart(2,'0'),mount_id:m.id,
      profile_ref:pin('modeled-fixture-profile-'+(i+1)),pose_ref:pin('modeled-fixture-pose-'+(i+1),{mount_id:m.id,equipment_digest:equipment.digest,offset_m:[0,0,-.15]}),
      valid_until:until,max_power_w:30,spot_power_w_at_full:10,spot_exposure_upper_at_full:.01,task_policy:null,
      ambient:['radial1','radial2','radial3','radial4','radial5','radial6','downward'].map((domain,j)=>({domain,
        origin_m:[0,0,0],direction_unit:j===6?[0,0,-1]:[Math.cos(j*Math.PI/3),Math.sin(j*Math.PI/3),0],half_angle_rad:j===6?Math.PI/6:Math.PI/6,
        range_m:8,assessment_ref:pin('authored-ambient-bound-'+(i+1)+'-'+domain),valid_until:until,level_scale:j===6?.7:1,
        max_fraction:1,cct_min_k:1500,cct_max_k:6500,power_w_at_full:2,exposure_upper_at_full:.001}))}))};
  const display=Display.buildDisplayMapping({equipment,room_policy:room});
  // Presentation translation only: modeled camera coordinates are not claimed
  // to be the actual OAK extrinsics. Geometry/fixture policies share this frame.
  const anchors=display.fixtures.map(f=>f.display_origin_m),center=anchors.reduce((v,p)=>v.map((n,k)=>n+p[k]/4),[0,0,0]);
  const floor=equipment.references.floor,zFloor=(floor.offset_m-floor.normal[0]*center[0]-floor.normal[1]*center[1])/floor.normal[2];
  const translation=[center[0]-1,center[1]-3,zFloor];
  let assessed=null;
  if(roomAssessmentFile){
    assessed=privateJson(roomAssessmentFile);const {digest,...unsigned}=assessed;
    if(assessed.schema!=='home-den-room-assessment-1'||assessed.version!==1||Core.digest(unsigned)!==digest
      ||assessed.candidate_digest!==candidate.digest||assessed.qualification!=='owner_assessed_virtual_model'
      ||assessed.room?.complete!==true||!(Date.now()/1000<assessed.room.valid_until)
      ||assessed.equipment_ref?.digest!==equipment.digest||L.canonicalJson(assessed.pack)!==L.canonicalJson(pack))throw Error('current_owner_room_assessment_required');
  }
  for(let i=0;i<room.fixtures.length;i++){
    const f=room.fixtures[i],mapping=display.fixtures[i],b=mapping.display_basis;
    const origin=mapping.display_origin_m.map((n,k)=>n-translation[k]-(k===2?.15:0));
    const basis=b.map((n,k)=>k%3===0?n:-n);
    f.ambient.forEach((a,j)=>{a.origin_m=copy(origin);const angle=j*Math.PI/3;
      a.direction_unit=j===6?[0,0,-1]:[basis[0]*Math.cos(angle)+basis[1]*Math.sin(angle),basis[3]*Math.cos(angle)+basis[4]*Math.sin(angle),0];});
    // Actual-room extraction is explicitly unassessed until the owner supplies
    // the existing signed/hashed applicability record. Never assert completeness.
    if(authored||assessed){
      const task=copy(baseBinding.delivery_policy);
      Object.assign(task.fixture,{id:f.fixture_id,fixture_pose_ref:copy(f.pose_ref),origin_m:origin,local_to_room:basis});
      // Engineering fixtures retain the explicitly commissioned baseline optics.
      // Real-room rehearsal uses its separate, labeled modeled narrow fixture.
      if(!authored)task.fixture.optics.envelope_half_angle_deg=12;
      task.policy_ref=pin('controlled-task-policy-'+(i+1));
      if(assessed){task.room={assessment_ref:pin('owner-room-assessment:'+assessed.digest.slice(0,32),assessed),
        collision_ref:{id:'canonical-den-collision',digest:pack.collision_digest},geometry_digest:registration.geometry_digest,complete:true,
        usable_bounds:{...copy(assessed.room.usable_bounds),center_m:assessed.room.usable_bounds.center_m.map((n,k)=>n-translation[k])},
        occluders:assessed.room.occluders.map(o=>({...copy(o),shape:{...copy(o.shape),center_m:o.shape.center_m.map((n,k)=>n-translation[k])}})),valid_until:assessed.room.valid_until};
      }
      // A room/collision assessment cannot expand the independently pinned
      // person-observation footprint. Policy validation refuses emitted domains
      // outside its original qualified bounds.
      f.task_policy=task;
    }
  }
  room.policy_ref=pin('controlled-room-output-policy',{...room,policy_ref:null});
  if(!Composition.validatePolicy(room).ok)throw Error('controlled_room_policy_refused');
  const matrix=[1,0,0,0,0,1,0,0,0,0,1,0,...translation,1];
  const binding={schema:'home-live-resident-binding-'+(manual?10:clarification?9:8),version:manual?10:clarification?9:8,scope:copy(baseBinding.session_ref),manifest_ref:null,live_manifest:null,
    registration:copy(registration),pack:copy(derived.pack),room_to_scene:{matrix,from_geometry_digest:registration.geometry_digest,to_frame_digest:pack.frame_digest,unit:'m'},
    qualification:'controlled_software_test',model_adapter:copy(derived.model_adapter),asset_selection:copy(assetSelection),fixture_display:display,digest:null};
  const names=['frame.json','collision.glb',derived.pack.model_asset,...Object.values(assetSelection.assets)],manifest=JSON.parse(fs.readFileSync(path.join(packDirectory,'content-pack.json'),'utf8'));
  const resident_assets=Object.fromEntries(names.map(n=>['/content-packs/'+pack.site_id+'/'+pack.revision+'/'+manifest.assets[n].path,manifest.assets[n].sha256]));
  // The launcher pins its exact generated index bytes separately after mounting.
  const resident_source_paths=[];
  for(const dir of ['home-3d','vendor','fonts','icons']){
    const walk=rel=>{const source=path.join(ROOT,'app/src',rel);if(!fs.existsSync(source))return;for(const e of fs.readdirSync(source,{withFileTypes:true})){
      if(e.isSymbolicLink())continue;const n=rel+'/'+e.name;if(e.isDirectory())walk(n);else if(/\.(?:js|mjs|json|wasm|css|woff2?|ttf|otf|svg|png|jpe?g|ico|ktx2)$/.test(n))resident_source_paths.push('/'+n);
    }};walk(dir);
  }
  const prepared={schema:'home-den-controlled-room-'+(manual?3:clarification?2:1),version:manual?3:clarification?2:1,pack_directory:packDirectory,asset_selection:copy(assetSelection),resident_binding:binding,
    room_policy:room,source_bundle_digest:sourceBundleDigest,acquisition_binding:{registration:copy(baseBinding.capture_registration),projection:copy(baseBinding.projection),mount_signature:copy(baseBinding.mount_signature)},
    resident_assets,resident_source_paths};
  if(clarification)prepared.clarification_policy=clarificationPolicy;
  if(manual){
    const policy={schema:'home-live-manual-policy-1',version:1,policy_ref:pin('controlled-manual-ambient-policy'),
      room_policy_ref:copy(room.policy_ref),valid_until:until,max_duration_s:120,operating_not_after:Date.now()/1000+120,
      max_active_holds:28,max_pending_per_domain:1,intent_capacity:128,
      resource_limits:{derived_event_bytes:262144,worker_commit_bytes:1048576}};
    policy.policy_ref=pin('controlled-manual-ambient-policy',{...policy,policy_ref:null});prepared.manual_policy=policy;
  }
  if(outputFile)writePrivate(outputFile,prepared);
  return prepared;
}
module.exports={prepareControlledRoom};
