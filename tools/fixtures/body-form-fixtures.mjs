// Authored metres, Z up. No human/camera/registration data.
export const BODY_FRAME_KEY='d'.repeat(64);
export function bodyPerson({seated=false,offset=[0,0,0],id='authored-detection'}={}) {
  const xyz=[
    [0,-.06,1.65],[.035,-.05,1.68],[-.035,-.05,1.68],[.085,0,1.66],[-.085,0,1.66],
    [.21,0,1.42],[-.21,0,1.42],[.27,0,1.14],[-.27,0,1.14],[.25,-.03,.89],[-.25,-.03,.89],
    [.15,0,.92],[-.15,0,.92],[.15,0,.50],[-.15,0,.50],[.15,0,.08],[-.15,0,.08]
  ];
  if(seated){
    for(let n=0;n<13;n++)xyz[n][2]-=.38;
    xyz[7]=[.27,-.15,.84];xyz[8]=[-.27,-.15,.84];xyz[9]=[.25,-.37,.75];xyz[10]=[-.25,-.37,.75];
    xyz[13]=[.15,-.42,.52];xyz[14]=[-.15,-.42,.52];xyz[15]=[.15,-.44,.10];xyz[16]=[-.15,-.44,.10];
  }
  return {instance_id:id,joints:xyz.map((p,id)=>({id,position_m:p.map((n,i)=>n+offset[i]),visible:true}))};
}
export function estimatedLowerBody(person) {
  const observed=person.joints.filter(j=>j.id<11);
  return {...person,joints:observed,body_pose:{schema:'home-body-pose-1',frame_key:BODY_FRAME_KEY,frame:'apartment_z_up_m',
    joints:person.joints.map(j=>({id:j.id,position_m:[...j.position_m],status:j.id<11?'observed':'estimated',
      ...(j.id>=11?{method:'authored-fixture-estimate'}:{})}))}};
}
