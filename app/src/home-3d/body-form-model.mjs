// A presentation model, in the apartment's existing Z-up metres. No floor
// snapping, identity association, persistence, interpolation or actuation.
const finite = n => typeof n === 'number' && Number.isFinite(n);
const position = p => Array.isArray(p) && p.length === 3 && p.every(n => finite(n) && Math.abs(n) < 1000);
const add = (a,b) => a.map((n,i)=>n+b[i]);
const sub = (a,b) => a.map((n,i)=>n-b[i]);
const mul = (a,n) => a.map(v=>v*n);
const length = a => Math.hypot(...a);
const unit = a => mul(a,1/length(a));
const mid = (a,b) => mul(add(a,b),.5);
const cross = (a,b) => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const distance = (a,b) => length(sub(a,b));
const between = (n,lo,hi) => n >= lo && n <= hi;
const missingReasons = new Set(['outside_frame','low_2d_response','depth_unavailable','depth_rejected','occluded','not_provided']);
const unavailable = reason => ({status:'unavailable',reason,parts:[],observedJoints:0,estimatedJoints:0,missingJoints:17,
  frameScoped:true,measuredStature:false,physicalOcclusion:false});

function evidence(person, frameKey) {
  const joints=new Map();
  if (!Array.isArray(person?.joints) || person.joints.length>17) return null;
  for (const j of person.joints) {
    if(!Number.isInteger(j.id)||j.id<0||j.id>16||joints.has(j.id)||j.visible!==true||!position(j.position_m))return null;
    joints.set(j.id,{point:[...j.position_m],status:'observed'});
  }
  const pose=person.body_pose;
  if (!pose) return joints;
  if(pose.schema!=='home-body-pose-1'||!/^[a-f0-9]{64}$/.test(frameKey)||pose.frame_key!==frameKey||pose.frame!=='apartment_z_up_m'
    ||!Array.isArray(pose.joints)||pose.joints.length!==17)return null;
  const seen=new Set();
  for(const j of pose.joints){
    if(!Number.isInteger(j.id)||j.id<0||j.id>16||seen.has(j.id))return null;
    seen.add(j.id);
    if(j.status==='missing'){
      if(joints.has(j.id)||j.position_m!=null||!missingReasons.has(j.reason))return null;
    }else if(j.status==='observed'){
      const source=joints.get(j.id);
      if(!source||!position(j.position_m)||distance(source.point,j.position_m)>1e-6)return null;
    }else if(j.status==='estimated'){
      if(joints.has(j.id)||!position(j.position_m)||typeof j.method!=='string'||!j.method.trim()||j.method.length>64)return null;
      joints.set(j.id,{point:[...j.position_m],status:'estimated'});
    }else return null;
  }
  // Every observed joint must be explicitly represented as observed.
  if(pose.joints.some(j=>joints.get(j.id)?.status==='observed'&&j.status!=='observed'))return null;
  return joints;
}

export function buildBodyForm(person,{frameKey}={}) {
  const joints=evidence(person,frameKey);
  if(!joints)return unavailable('body_pose_contract');
  const get=id=>joints.get(id)?.point, has=(...ids)=>ids.every(id=>joints.has(id));
  if(!has(5,6))return unavailable('shoulder_pair_missing');
  const width=distance(get(5),get(6));
  if(!between(width,.12,.75))return unavailable('shoulder_span_invalid');
  const shoulders=mid(get(5),get(6));
  const headIds=has(3,4)?[3,4]:has(1,2)?[1,2]:has(0)?[0]:[];
  const face=headIds.length?mul(headIds.map(get).reduce(add,[0,0,0]),1/headIds.length):null;
  const headSupported=!!face&&between(distance(face,shoulders),width*.18,width*1.15);
  const hasHips=has(11,12)&&between(distance(get(11),get(12)),.10,.70)
    &&between(distance(shoulders,mid(get(11),get(12))),.16,.95);
  if(!headSupported&&!hasHips)return unavailable('coherent_body_anchors_missing');
  const hips=hasHips?mid(get(11),get(12)):null;
  const up=hips?unit(sub(shoulders,hips)):[0,0,1];
  const lateral=unit(sub(get(6),get(5))), forwardRaw=cross(up,lateral);
  if(length(forwardRaw)<.4)return unavailable('torso_axes_invalid');
  const forward=unit(forwardRaw),right=unit(cross(forward,up));
  const basis=[right,forward,up],parts=[],rejected=[];
  const estimated=ids=>ids.some(id=>joints.get(id)?.status!=='observed');
  const ellipsoid=(key,center,radii,ids,assumed=false)=>parts.push({key,kind:'ellipsoid',center,radii,basis,
    estimated:assumed||estimated(ids),jointIds:ids});
  const segment=(key,a,b,radius,lo=.04,hi=.85)=>{
    if(!has(a,b))return false;
    const span=distance(get(a),get(b));
    if(!between(span,lo,hi)){rejected.push(key);return false;}
    parts.push({key,kind:'segment',start:get(a),end:get(b),radius,estimated:estimated([a,b]),jointIds:[a,b]});return true;
  };
  // A connected neutral volume below observed shoulders is disclosed as
  // estimated shape. It invents no pelvis, lower-body joints or foot contact.
  if(hips){
    ellipsoid('torso',mid(shoulders,hips),[width*.55,width*.31,distance(shoulders,hips)*.66],[5,6,11,12]);
    ellipsoid('pelvis',hips,[distance(get(11),get(12))*.66,width*.31,width*.31],[11,12]);
  }else ellipsoid('upper-chest',add(shoulders,mul(up,-width*.34)),[width*.53,width*.27,width*.44],[5,6],true);
  segment('shoulders',5,6,width*.16,.12,.75);
  if(headIds.length){
    if(headSupported){
      const center=headIds.length===1?add(face,mul(forward,-width*.13)):face;
      ellipsoid('head',center,[width*.25,width*.24,width*.34],headIds);
      parts.push({key:'neck',kind:'segment',start:shoulders,end:add(center,mul(up,-width*.22)),radius:width*.13,
        estimated:estimated([5,6,...headIds]),jointIds:[5,6,...headIds]});
    }else rejected.push('head');
  }
  for(const [side,s,e,w] of [['left',5,7,9],['right',6,8,10]]){
    if(segment(side+'-upper-arm',s,e,width*.155,.07,.65)){
      if(segment(side+'-forearm',e,w,width*.12,.06,.65))ellipsoid(side+'-hand',get(w),[width*.115,width*.08,width*.17],[w],true);
    }
  }
  if(hips)for(const [side,h,k,a] of [['left',11,13,15],['right',12,14,16]]){
    if(segment(side+'-thigh',h,k,width*.23,.12,.85)){
      if(segment(side+'-calf',k,a,width*.16,.12,.85)){
        ellipsoid(side+'-foot',add(get(a),add(mul(forward,width*.14),mul(up,-width*.05))),[width*.15,width*.30,width*.13],[a],true);
      }
    }
  }
  const complete=['torso','head','left-upper-arm','left-forearm','right-upper-arm','right-forearm',
    'left-thigh','left-calf','right-thigh','right-calf'].every(key=>parts.some(p=>p.key===key));
  const estimatedJoints=[...joints.values()].filter(j=>j.status==='estimated').length;
  return {status:complete?(estimatedJoints?'estimated':'complete'):'partial',reason:null,parts,rejected,
    observedJoints:joints.size-estimatedJoints,estimatedJoints,missingJoints:17-joints.size,
    frameScoped:true,measuredStature:false,physicalOcclusion:false,volume:'approximate'};
}
