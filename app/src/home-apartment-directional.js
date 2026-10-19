/* Presentation-only radial geometry and event-driven painting. Canonical
 * azimuths are atan2(y,x); this module owns no renderer, clock or output. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeApartmentDirectional = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const vector = (value, length) => Array.isArray(value) && value.length === length && value.every(finite);
  const clamp = value => Math.max(0, Math.min(1, value));
  const DEG = Math.PI / 180;
  const MAX_PLANE_CONDITION = 1e10;
  const HORIZON_RELATIVE_EPSILON = 1e-12;
  const SCREEN_REPROJECTION_LIMIT_PX = .01;
  // clipFromLocal is the existing P * cameraInverse * apartmentRootWorld
  // snapshot. Points below are offsets in the canonical fixture-height plane.
  function createPlaneFrame({ anchor, clipFromLocal, width, height } = {}) {
    if (!vector(anchor, 3) || !clipFromLocal || clipFromLocal.length !== 16
        || !Array.from(clipFromLocal).every(finite) || !finite(width) || !finite(height) || width <= 0 || height <= 0) return null;
    const m = Array.from(clipFromLocal), origin = anchor.slice();
    const rows = [0,1,2,3].map(row => [m[row],m[4+row],
      m[row]*origin[0]+m[4+row]*origin[1]+m[8+row]*origin[2]+m[12+row]]);
    const h = [...rows[0], ...rows[1], ...rows[3]], scale = Math.max(...h.map(Math.abs));
    if (!finite(scale) || scale === 0) return null;
    const [a,b,c,d,e,f,g,i,j] = h.map(value => value/scale);
    const adj = [e*j-f*i,c*i-b*j,b*f-c*e,f*g-d*j,a*j-c*g,c*d-a*f,d*i-e*g,b*g-a*i,a*e-b*d];
    const determinant = a*adj[0]+b*adj[3]+c*adj[6];
    if (!finite(determinant) || determinant === 0) return null;
    const inverse = adj.map(value => value/determinant);
    const norm = values => Math.max(...[0,3,6].map(offset => Math.abs(values[offset])+Math.abs(values[offset+1])+Math.abs(values[offset+2])));
    // A nearly edge-on plane is an inspector fallback, not a huge paint jump.
    if (!inverse.every(finite) || norm(h.map(value=>value/scale))*norm(inverse) > MAX_PLANE_CONDITION) return null;
    const clipAt = point => {
      if (!vector(point,2)) return null;
      const p = [point[0],point[1],1], clip = rows.map(row=>row.reduce((sum,value,index)=>sum+value*p[index],0));
      const wMagnitude = rows[3].reduce((sum,value,index)=>sum+Math.abs(value*p[index]),0);
      if (!clip.every(finite) || !(clip[3]>0) || clip[3] <= HORIZON_RELATIVE_EPSILON*wMagnitude
          || clip[2] < -clip[3] || clip[2] > clip[3]) return null;
      return clip;
    };
    function project(point) {
      const clip = clipAt(point); if (!clip) return null;
      const x = (clip[0]/clip[3]+1)*width/2, y = (1-clip[1]/clip[3])*height/2;
      return finite(x)&&finite(y) ? { x,y,visible:x>=0&&x<=width&&y>=0&&y<=height } : null;
    }
    function unproject(x,y) {
      if (!finite(x)||!finite(y)||x<0||x>width||y<0||y>height) return null;
      const ndc=[x*2/width-1,1-y*2/height,1];
      const q=[0,3,6].map(offset=>inverse[offset]*ndc[0]+inverse[offset+1]*ndc[1]+inverse[offset+2]);
      const denominatorMagnitude = Math.abs(inverse[6]*ndc[0])+Math.abs(inverse[7]*ndc[1])+Math.abs(inverse[8]);
      if (!q.every(finite)||q[2]===0||Math.abs(q[2])<=HORIZON_RELATIVE_EPSILON*denominatorMagnitude) return null;
      const point=[q[0]/q[2],q[1]/q[2]], back=project(point);
      return back&&Math.abs(back.x-x)<SCREEN_REPROJECTION_LIMIT_PX&&Math.abs(back.y-y)<SCREEN_REPROJECTION_LIMIT_PX ? point : null;
    }
    return Object.freeze({project,unproject});
  }
  const angle = value => ((value % 360) + 360) % 360;
  function zonesFrom(radials) {
    if (!Array.isArray(radials) || radials.length !== 6) return null;
    const zones = radials.map(zone => zone && finite(zone.azimuth_deg) && ({number:zone.number,azimuth_deg:angle(zone.azimuth_deg),
      brightness:zone.brightness,on:zone.on,level:zone.on ? zone.brightness : 0}));
    if (zones.some(zone=>!zone || !Number.isInteger(zone.number)||zone.number<1||zone.number>6
        || !finite(zone.azimuth_deg)||!finite(zone.brightness)||zone.brightness<0||zone.brightness>1||typeof zone.on!=='boolean')
        || new Set(zones.map(zone=>zone.number)).size!==6 || new Set(zones.map(zone=>zone.azimuth_deg)).size!==6) return null;
    return zones.sort((a,b)=>a.number-b.number);
  }
  // Fixed-size tessellation is shared by the plan panel and selected SVG.
  // A caller clips off-viewport samples; invalid depth retires the drawing.
  function radialSectorPaths({radials,frame,innerRadius=.14,outerRadius=1.3}={}) {
    const zones=zonesFrom(radials);
    if (!zones || typeof frame?.project!=='function' || !finite(innerRadius)||innerRadius<0
        || !finite(outerRadius)||outerRadius<=innerRadius) return null;
    const ordered=[...zones].sort((a,b)=>a.azimuth_deg-b.azimuth_deg), span=outerRadius-innerRadius;
    const radiusAt=azimuth=>{
      const a=angle(azimuth);
      for(let i=0;i<ordered.length;i++){
        const first=ordered[i],next=ordered[(i+1)%6],end=next.azimuth_deg+(i===5?360:0);
        const here=a<first.azimuth_deg?a+360:a;
        if(here>=first.azimuth_deg&&here<=end){const t=(here-first.azimuth_deg)/(end-first.azimuth_deg),blend=(1-Math.cos(Math.PI*t))/2;
          return innerRadius+span*(first.level*(1-blend)+next.level*blend);}
      }
      return NaN;
    };
    let valid=true;
    const at=(radius,azimuth)=>{
      let point;try{point=frame.project([radius*Math.cos(azimuth*DEG),radius*Math.sin(azimuth*DEG)]);}catch(_){point=null;}
      if(!point||!finite(point.x)||!finite(point.y)){valid=false;return null;}return point;
    };
    const path=points=>points.every(Boolean)?'M'+points.map(point=>point.x+' '+point.y).join(' L')+' Z':'';
    const circle=radius=>path(Array.from({length:72},(_,i)=>at(radius,i*5)));
    const hub=at(0,0),inner=circle(innerRadius),outer=circle(outerRadius);
    const contour=path(Array.from({length:72},(_,i)=>at(radiusAt(i*5),i*5)));
    const result=ordered.map((zone,i)=>{
      const previous=ordered[(i+5)%6].azimuth_deg-(i===0?360:0),next=ordered[(i+1)%6].azimuth_deg+(i===5?360:0);
      const start=(previous+zone.azimuth_deg)/2,end=(zone.azimuth_deg+next)/2;
      const edge=Array.from({length:13},(_,j)=>{const a=start+(end-start)*j/12;return at(radiusAt(a),a);});
      const inside=Array.from({length:13},(_,j)=>at(innerRadius,end-(end-start)*j/12));
      return {number:zone.number,azimuth_deg:zone.azimuth_deg,level:zone.level,path:path([...edge,...inside]),
        node:zone.level>0?at(innerRadius+span*zone.level,zone.azimuth_deg):null,tick:at(outerRadius,zone.azimuth_deg)};
    }).sort((a,b)=>a.number-b.number);
    return valid ? {hub,inner,outer,contour,zones:result} : null;
  }
  const signedAngle = value => {const a=angle(value);return a>=180?a-360:a;};
  // Match the authored integer-percent readout. Stored channel values are
  // never quantized until that particular zone is deliberately painted.
  function paintPatch({point,azimuth,radius,radials,innerRadius=.14,outerRadius=1.3}={}) {
    const zones=zonesFrom(radials);
    if(!zones||!finite(innerRadius)||innerRadius<0||!finite(outerRadius)||outerRadius<=innerRadius)return null;
    if(point!==undefined){if(!vector(point,2))return null;radius=Math.hypot(...point);azimuth=angle(Math.atan2(point[1],point[0])/DEG);}
    if(!finite(radius)||radius<0||!finite(azimuth))return null;
    azimuth=angle(azimuth);
    const zone=zones.reduce((best,item)=>Math.abs(signedAngle(azimuth-item.azimuth_deg))<Math.abs(signedAngle(azimuth-best.azimuth_deg))?item:best);
    const level=Math.round(clamp((radius-innerRadius)/(outerRadius-innerRadius))*100)/100;
    return {zone:zone.number,level,radius,azimuth,patch:{radials:[{number:zone.number,on:level>0,brightness:level}]}};
  }
  const bindingKeys=['content_revision','registration_version','manifest_digest','frame_digest','canonical_collision_digest'];
  const identifier=value=>typeof value==='string'&&value.length>0&&value.length<=160;
  const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  // This key never grants authority. The host must first readmit the current
  // descriptor, then use the same key again before applying a queued patch.
  function identityKey(snapshot) {
    const zones=zonesFrom(snapshot?.state?.radials),binding=snapshot?.binding;
    if(!zones||!identifier(snapshot.fixture_id)||!identifier(snapshot.mount_id)
        ||!Number.isSafeInteger(snapshot.generation)||snapshot.generation<0
        ||!Number.isSafeInteger(snapshot.equipment_revision)||snapshot.equipment_revision<0
        ||!(digest(snapshot.equipment_digest)||snapshot.equipment_revision===0&&snapshot.equipment_digest===null)
        ||!vector(snapshot.display_anchor_m,3)||!binding||typeof binding!=='object'||Array.isArray(binding)
        ||Object.keys(binding).length!==bindingKeys.length||!identifier(binding.content_revision)
        ||!Number.isSafeInteger(binding.registration_version)||binding.registration_version<0
        ||!bindingKeys.slice(2).every(key=>digest(binding[key])))return null;
    return JSON.stringify([snapshot.fixture_id,snapshot.mount_id,snapshot.generation,snapshot.equipment_revision,
      snapshot.equipment_digest,bindingKeys.map(key=>binding[key]),snapshot.display_anchor_m,
      zones.map(zone=>[zone.number,zone.azimuth_deg])]);
  }
  // No DOM ownership or output here: the host owns pointer cancellation and
  // flushes the cumulative sparse patch on its existing projection cadence.
  function createRadialGesture({snapshot,pointerId,point,startable,innerRadius=.14,outerRadius=1.3}={}) {
    const identity=identityKey(snapshot),first=paintPatch({point,radials:snapshot?.state?.radials,innerRadius,outerRadius});
    if(!identity||!first||!Number.isInteger(pointerId)||pointerId<0||startable!==true)return null;
    const fixtureId=snapshot.fixture_id,logical=zonesFrom(snapshot.state.radials),touched=new Map();
    const initial=logical.find(zone=>zone.number===first.zone),edge=innerRadius+(outerRadius-innerRadius)*initial.level;
    const initialSide=Math.sign(first.radius-edge);
    let active=true,engaged=false,status='active',last={...first,level:initial.level};
    const patch=()=>({radials:[...touched.values()].sort((a,b)=>a.number-b.number).map(zone=>({...zone}))});
    const report=()=>({zone:last.zone,level:last.level,radius:last.radius,azimuth:last.azimuth,engaged,patch:patch()});
    function cancel(reason='canceled'){active=false;status=String(reason);touched.clear();}
    function admitted(current){if(!active)return false;if(identityKey(current)!==identity){cancel('retired');return false;}return true;}
    function write(number,level){
      level=Math.round(clamp(level)*100)/100;
      const zone=logical.find(item=>item.number===number);zone.on=level>0;zone.brightness=level;zone.level=level;
      touched.set(number,{number,on:zone.on,brightness:level});
    }
    function move({snapshot:current,pointerId:nextId,point:nextPoint}={}) {
      if(!admitted(current)||nextId!==pointerId)return null;
      const hit=paintPatch({point:nextPoint,radials:logical,innerRadius,outerRadius});if(!hit)return null;
      if(!engaged){
        const side=Math.sign(hit.radius-edge);
        if(hit.zone===first.zone&&(side===0||side===initialSide)){
          last={...hit,level:initial.level};return report();
        }
        engaged=true;
        // The first deliberate entry adopts that zone. Interpolation starts
        // with the next sweep, not across zones traversed during soft pickup.
        last=hit;
      }
      const delta=signedAngle(hit.azimuth-last.azimuth),distance=Math.abs(delta);
      if(distance>0){
        for(const zone of logical){
          const offset=delta>0?angle(zone.azimuth_deg-last.azimuth):angle(last.azimuth-zone.azimuth_deg);
          if(zone.number!==last.zone&&zone.number!==hit.zone&&offset>0&&offset<distance)
            write(zone.number,last.level+(hit.level-last.level)*offset/distance);
        }
      }
      write(hit.zone,hit.level);last=hit;return report();
    }
    function end({snapshot:current,pointerId:nextId,point:nextPoint}={}) {
      if(!admitted(current)||nextId!==pointerId)return null;
      const result=nextPoint===undefined?report():move({snapshot:current,pointerId:nextId,point:nextPoint});
      active=false;status='ended';return result;
    }
    return Object.freeze({move,end,cancel,readback:()=>({active,engaged,status,pointerId,fixture_id:fixtureId,identity,...report()})});
  }
  // Logical targets remain exact. This is drawing-only state, advanced by
  // the admitted renderer frame; it owns no clock, output or inventory.
  function createRadialDisplay(radials) {
    const read=next=>{const zones=zonesFrom(next);return zones&&next.every(zone=>finite(zone.color_temp_kelvin)&&zone.color_temp_kelvin>0)
      ?zones.map(zone=>({...zone,color_temp_kelvin:next.find(item=>item.number===zone.number).color_temp_kelvin})):null;};
    const initial=read(radials);if(!initial)return null;
    const opacity=zone=>zone.level>0?.25+zone.level/1.3:0;
    let shown=initial.map(zone=>({...zone,brightness:zone.level,on:zone.level>0,display_opacity:opacity(zone)})),target=initial,lastTime=null;
    function set(next){const zones=read(next);if(!zones||zones.some((zone,i)=>zone.number!==target[i].number||zone.azimuth_deg!==target[i].azimuth_deg))return false;target=zones;return true;}
    function advance(nowMs,{reducedMotion=false}={}){
      if(!finite(nowMs))return null;
      const dt=lastTime===null?0:Math.max(0,(nowMs-lastTime)/1000);lastTime=Math.max(lastTime??nowMs,nowMs);
      const a=reducedMotion?1:1-Math.exp(-dt/.16);let moving=false;
      shown=shown.map((zone,i)=>{
        const goal=target[i],step=(current,wanted,epsilon)=>{const d=wanted-current;if(reducedMotion||Math.abs(d)<=epsilon)return wanted;moving=true;return current+d*a;};
        const brightness=step(zone.brightness,goal.level,.002),kelvin=step(zone.color_temp_kelvin,goal.color_temp_kelvin,2);
        return {...zone,on:brightness>0,brightness,color_temp_kelvin:kelvin,display_opacity:step(zone.display_opacity,opacity(goal),.002)};
      });
      return {radials:shown.map(zone=>({...zone})),moving};
    }
    return Object.freeze({set,advance});
  }
  return Object.freeze({createPlaneFrame,radialSectorPaths,paintPatch,identityKey,createRadialGesture,createRadialDisplay});
});
