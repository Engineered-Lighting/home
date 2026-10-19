import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const D = require('../app/src/home-apartment-directional.js');
const I = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];
const close = (actual, expected, epsilon = 1e-8) => assert.ok(Math.abs(actual - expected) < epsilon, `${actual} != ${expected}`);

test('fixture-height plane projects and inverts host pixels without changing supplied data', () => {
  const anchor = Object.freeze([0,0,.3]), matrix = Object.freeze([...I]);
  const frame = D.createPlaneFrame({ anchor, clipFromLocal: matrix, width:400, height:200 });
  assert.ok(frame); assert.deepEqual(frame.project([.5,.5]), {x:300,y:50,visible:true});
  const point = frame.unproject(300,50); close(point[0],.5); close(point[1],.5);
  assert.deepEqual(anchor,[0,0,.3]); assert.deepEqual(matrix,I);
});

const THREE = await import('../app/src/vendor/three-0.180.0/three.module.js');
test('oblique perspective round trips the actual nonzero fixture-height plane through a transformed root', () => {
  const camera = new THREE.PerspectiveCamera(48,1.6,.1,100); camera.up.set(0,0,1); camera.position.set(4,-6,5);
  camera.lookAt(.2,.3,2.2); camera.updateMatrixWorld(true);
  const root = new THREE.Object3D(); root.rotation.z=.4; root.scale.setScalar(1.2); root.position.set(.1,.2,0); root.updateMatrixWorld(true);
  const anchor=[.2,.3,2.2], before=camera.matrixWorld.toArray(), rootBefore=root.matrixWorld.toArray();
  const matrix=camera.projectionMatrix.clone().multiply(camera.matrixWorldInverse).multiply(root.matrixWorld).elements;
  const frame=D.createPlaneFrame({anchor,clipFromLocal:matrix,width:960,height:600}); assert.ok(frame);
  for(const point of [[.3,0],[0,.3],[-.2,.1],[.15,-.3]]){
    const expected=new THREE.Vector3(anchor[0]+point[0],anchor[1]+point[1],anchor[2]).applyMatrix4(root.matrixWorld).project(camera);
    const actual=frame.project(point);close(actual.x,(expected.x+1)*480);close(actual.y,(1-expected.y)*300);
    const back=frame.unproject(actual.x,actual.y);close(back[0],point[0]);close(back[1],point[1]);
  }
  assert.deepEqual(camera.matrixWorld.toArray(),before);assert.deepEqual(root.matrixWorld.toArray(),rootBefore);
});
for(const [name,change] of [['nonfinite matrix',m=>{m[3]=NaN;}],['edge-on plane',m=>{m[5]=0;}],['zero transform',m=>m.fill(0)]])
 test('plane refuses '+name,()=>{const matrix=[...I];change(matrix);assert.equal(D.createPlaneFrame({anchor:[0,0,0],clipFromLocal:matrix,width:400,height:200}),null);});
test('plane refuses behind/depth/horizon and nonfinite pointer data',()=>{
  const depth=D.createPlaneFrame({anchor:[0,0,2],clipFromLocal:I,width:400,height:200});assert.equal(depth.project([0,0]),null);assert.equal(depth.unproject(200,100),null);
  const matrix=[...I];matrix[0]=0;matrix[12]=1;matrix[3]=1;matrix[15]=0;
  const horizon=D.createPlaneFrame({anchor:[0,0,0],clipFromLocal:matrix,width:400,height:200});assert.ok(horizon);
  assert.equal(horizon.project([0,0]),null);assert.equal(horizon.project([-1,0]),null);assert.equal(horizon.unproject(200,100),null);
  assert.equal(horizon.unproject(NaN,100),null);assert.equal(horizon.unproject(-1,100),null);
});
const radials=()=>Array.from({length:6},(_,i)=>({number:i+1,azimuth_deg:i*60,on:true,brightness:.5,color_temp_kelvin:3000}));
const plan={project:([x,y])=>({x:200+x*100,y:200-y*100,visible:true})};
test('radial drawing uses each admitted azimuth with lit nodes and fixed outer ticks',()=>{
 const input=radials();input[1].on=false;input[0].azimuth_deg=360;
 const drawing=D.radialSectorPaths({radials:input,frame:plan});assert.ok(drawing);assert.equal(drawing.zones.length,6);
 assert.deepEqual(drawing.hub,{x:200,y:200,visible:true});close(drawing.zones[0].tick.x,330);close(drawing.zones[0].tick.y,200);
 close(drawing.zones[0].node.x,272);assert.equal(drawing.zones[1].node,null);assert.equal(drawing.zones[1].level,0);
 assert.match(drawing.contour,/^M/);assert.match(drawing.contour,/Z$/);assert.ok(drawing.zones.every(zone=>/^M/.test(zone.path)));
});

test('paint changes only the nearest admitted zone and clamps radius without changing stored state',()=>{
 const input=radials(), before=structuredClone(input); input.forEach(zone=>{zone.azimuth_deg+=30;});
 const rotated=structuredClone(input),result=D.paintPatch({point:[Math.cos(Math.PI/6)*.72,Math.sin(Math.PI/6)*.72],radials:input});
 assert.equal(result.zone,1);close(result.level,.5);assert.deepEqual(result.patch,{radials:[{number:1,on:true,brightness:.5}]});
 assert.deepEqual(input,rotated);assert.deepEqual(before[0],{number:1,azimuth_deg:0,on:true,brightness:.5,color_temp_kelvin:3000});
 assert.deepEqual(D.paintPatch({azimuth:30,radius:100,radials:input}).patch.radials,[{number:1,on:true,brightness:1}]);
 assert.deepEqual(D.paintPatch({azimuth:30,radius:0,radials:input}).patch.radials,[{number:1,on:false,brightness:0}]);
});

function snapshot(){return {fixture_id:'fixture-1',mount_id:'mount-1',generation:7,equipment_revision:3,equipment_digest:'a'.repeat(64),
 binding:{content_revision:'authored-r1',registration_version:1,manifest_digest:'b'.repeat(64),frame_digest:'c'.repeat(64),canonical_collision_digest:'d'.repeat(64)},
 display_anchor_m:[.2,.3,2.2],state:{radials:radials(),pan_deg:20,tilt_deg:30}};}
test('identity pins complete fixture lifetime and radial directions while unrelated spot state may change',()=>{
 const source=snapshot(),key=D.identityKey(source);assert.equal(typeof key,'string');
 source.state.pan_deg=120;source.state.radials[0].brightness=.1;assert.equal(D.identityKey(source),key);
 source.binding.frame_digest='e'.repeat(64);assert.notEqual(D.identityKey(source),key);
 assert.equal(D.identityKey({...snapshot(),generation:NaN}),null);
});
test('gesture pickup changes nothing until crossing the current edge and returns cumulative unflushed paint',()=>{
 const source=snapshot(),before=structuredClone(source);
 const gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});assert.ok(gesture);
 assert.equal(gesture.readback().engaged,false);
 assert.deepEqual(gesture.move({snapshot:source,pointerId:1,point:[.3,0]}).patch,{radials:[]});
 const first=gesture.move({snapshot:source,pointerId:1,point:[1.3,0]});assert.equal(first.engaged,true);assert.equal(first.level,1);
 const second=gesture.move({snapshot:source,pointerId:1,point:[.36,.36*Math.sqrt(3)]});
 assert.equal(second.zone,2);assert.deepEqual(second.patch.radials,[{number:1,on:true,brightness:1},{number:2,on:true,brightness:.5}]);
 assert.deepEqual(source,before);assert.equal(gesture.end({snapshot:source,pointerId:1}).patch.radials.length,2);assert.equal(gesture.readback().active,false);
});

test('numeric headings are admitted without coercing strings or filling missing zones',()=>{
 const input=radials();input[0].azimuth_deg='0';assert.equal(D.radialSectorPaths({radials:input,frame:plan}),null);
 assert.equal(D.paintPatch({point:[1,0],radials:input}),null);
 assert.equal(D.radialSectorPaths({radials:radials().slice(1),frame:plan}),null);
});
test('gesture readback retains its captured fixture ID even if an input object is later changed',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 source.fixture_id='later-fixture';assert.equal(gesture.readback().fixture_id,'fixture-1');
 assert.equal(gesture.move({snapshot:source,pointerId:1,point:[1.3,0]}),null);assert.equal(gesture.readback().status,'retired');
});
const polar=(degrees,radius)=>[Math.cos(degrees*Math.PI/180)*radius,Math.sin(degrees*Math.PI/180)*radius];
test('a fast two-sector sweep fills the skipped canonical zone and retains unflushed earlier edits',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 gesture.move({snapshot:source,pointerId:1,point:[1.3,0]});
 const result=gesture.move({snapshot:source,pointerId:1,point:polar(120,.43)});
 assert.deepEqual(result.patch.radials,[{number:1,on:true,brightness:1},{number:2,on:true,brightness:.63},{number:3,on:true,brightness:.25}]);
});
for(const [name,retire]of[
 ['fixture',s=>{s.fixture_id='another';}],['mount',s=>{s.mount_id='another';}],['generation',s=>{s.generation++;}],
 ['equipment revision',s=>{s.equipment_revision++;}],['equipment digest',s=>{s.equipment_digest='e'.repeat(64);}],
 ['content revision',s=>{s.binding.content_revision='authored-r2';}],['registration',s=>{s.binding.registration_version++;}],
 ['manifest',s=>{s.binding.manifest_digest='e'.repeat(64);}],['frame',s=>{s.binding.frame_digest='e'.repeat(64);}],
 ['collision',s=>{s.binding.canonical_collision_digest='e'.repeat(64);}],['anchor',s=>{s.display_anchor_m[2]+=.1;}],
 ['zone direction',s=>{s.state.radials[0].azimuth_deg=1;}],
])test('gesture retires before returning more paint after changed '+name,()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 gesture.move({snapshot:source,pointerId:1,point:[1.3,0]});retire(source);
 assert.equal(gesture.move({snapshot:source,pointerId:1,point:[.72,0]}),null);
 assert.equal(gesture.readback().active,false);assert.equal(gesture.readback().status,'retired');assert.deepEqual(gesture.readback().patch,{radials:[]});
});
test('second-pointer cancellation drops local queued paint and late events cannot restart it',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 gesture.move({snapshot:source,pointerId:1,point:[1.3,0]});gesture.cancel('second_pointer');
 assert.equal(gesture.move({snapshot:source,pointerId:1,point:[.72,0]}),null);assert.equal(gesture.end({snapshot:source,pointerId:1}),null);
 assert.equal(gesture.readback().status,'second_pointer');assert.deepEqual(gesture.readback().patch,{radials:[]});
});

test('wraparound sweep interpolates through zone one without repainting the long way around',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:4,point:polar(300,.2),startable:true});
 gesture.move({snapshot:source,pointerId:4,point:polar(300,1.3)});
 gesture.move({snapshot:source,pointerId:4,point:polar(300,.14)});
 const result=gesture.move({snapshot:source,pointerId:4,point:polar(60,1.3)});
 assert.deepEqual(result.patch.radials,[{number:1,on:true,brightness:.5},{number:2,on:true,brightness:1},{number:6,on:false,brightness:0}]);
});
test('off zones keep their saved level until a deliberate pickup engages and only touched fields are emitted',()=>{
 const source=snapshot();source.state.radials[0].on=false;source.state.radials[0].brightness=.8;
 const gesture=D.createRadialGesture({snapshot:source,pointerId:2,point:[0,0],startable:true});
 assert.deepEqual(gesture.move({snapshot:source,pointerId:2,point:[.1,0]}).patch,{radials:[]});
 const result=gesture.move({snapshot:source,pointerId:2,point:[.72,0]});
 assert.deepEqual(result.patch,{radials:[{number:1,on:true,brightness:.5}]});
 assert.equal(source.state.radials[0].brightness,.8);assert.equal(source.state.radials[0].on,false);
 assert.equal(source.state.radials[0].color_temp_kelvin,3000);assert.equal(source.state.pan_deg,20);
});
test('a different pointer cannot paint or end the captured gesture',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:2,point:[.2,0],startable:true});
 assert.equal(gesture.move({snapshot:source,pointerId:3,point:[1.3,0]}),null);assert.equal(gesture.end({snapshot:source,pointerId:3}),null);
 assert.equal(gesture.readback().active,true);assert.deepEqual(gesture.readback().patch,{radials:[]});
});
test('queued cumulative patches are owned copies and a normal producer readback does not retire the gesture',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:2,point:[.2,0],startable:true});
 const result=gesture.move({snapshot:source,pointerId:2,point:[1.3,0]});result.patch.radials[0].brightness=0;
 assert.equal(gesture.readback().patch.radials[0].brightness,1);
 const updated=structuredClone(source);updated.state.radials[0].brightness=1;
 assert.ok(gesture.move({snapshot:updated,pointerId:2,point:polar(60,.72)}));assert.equal(gesture.readback().active,true);
});
for(const [name,change]of[['missing direction',x=>{delete x[0].azimuth_deg;}],['duplicate direction',x=>{x[1].azimuth_deg=0;}],['duplicate number',x=>{x[1].number=1;}],['unknown power',x=>{delete x[1].on;}],['out of range level',x=>{x[1].brightness=1.1;}]])
 test('radial data refuses '+name+' without fabricating a usable heading',()=>{const input=radials();change(input);assert.equal(D.radialSectorPaths({radials:input,frame:plan}),null);assert.equal(D.paintPatch({point:[1,0],radials:input}),null);});
test('all six canonical directions remain keyed by number when the input order and spot pose differ',()=>{
 const source=snapshot();source.state.radials.forEach(zone=>{zone.azimuth_deg+=23;});const before=structuredClone(source);
 const normal=D.radialSectorPaths({radials:source.state.radials,frame:plan});source.state.pan_deg=-175;source.state.tilt_deg=88;source.state.radials.reverse();
 assert.deepEqual(D.radialSectorPaths({radials:source.state.radials,frame:plan}),normal);
 for(const zone of before.state.radials)assert.equal(D.paintPatch({point:polar(zone.azimuth_deg,.72),radials:source.state.radials}).zone,zone.number);
});
test('native plan drawing fails closed if any required projected sample is unavailable',()=>{
 assert.equal(D.radialSectorPaths({radials:radials(),frame:{project:()=>null}}),null);
 assert.equal(D.radialSectorPaths({radials:radials(),frame:{project:()=>{throw Error('retired');}}}),null);
 assert.equal(D.createRadialGesture({snapshot:snapshot(),pointerId:1,point:[0,0],startable:false}),null);
});
test('strict near and far boundaries remain distinct from viewport clipping',()=>{
 for(const z of [-1,1])assert.ok(D.createPlaneFrame({anchor:[0,0,z],clipFromLocal:I,width:400,height:200}).project([0,0]));
 for(const z of [-1.00001,1.00001])assert.equal(D.createPlaneFrame({anchor:[0,0,z],clipFromLocal:I,width:400,height:200}).project([0,0]),null);
 const frame=D.createPlaneFrame({anchor:[0,0,0],clipFromLocal:I,width:400,height:200});
 assert.deepEqual(frame.project([2,0]),{x:600,y:100,visible:false});assert.equal(frame.unproject(600,100),null);
});
test('orthographic projection with the actual Z-up root conversion is applied exactly once',()=>{
 const root=new THREE.Object3D();root.rotation.x=-Math.PI/2;root.updateMatrixWorld(true);
 const camera=new THREE.OrthographicCamera(-3,3,2,-2,.1,100);camera.position.set(4,5,6);camera.lookAt(0,2,0);camera.updateMatrixWorld(true);
 const anchor=[.2,.3,2.2],matrix=camera.projectionMatrix.clone().multiply(camera.matrixWorldInverse).multiply(root.matrixWorld).elements;
 const frame=D.createPlaneFrame({anchor,clipFromLocal:matrix,width:900,height:600}),point=[.4,-.2];
 const expected=new THREE.Vector3(anchor[0]+point[0],anchor[1]+point[1],anchor[2]).applyMatrix4(root.matrixWorld).project(camera),screen=frame.project(point);
 close(screen.x,(expected.x+1)*450);close(screen.y,(1-expected.y)*300);
 const back=frame.unproject(screen.x,screen.y);close(back[0],point[0]);close(back[1],point[1]);
});

test('logical paint and readback update immediately while drawing follows the 160 ms time constant',()=>{
 const source=snapshot(),display=D.createRadialDisplay(source.state.radials);display.advance(0);
 const gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 const result=gesture.move({snapshot:source,pointerId:1,point:[1.3,0]});
 assert.equal(result.level,1);assert.equal(gesture.readback().level,1);
 assert.deepEqual(result.patch,{radials:[{number:1,on:true,brightness:1}]});
 const target=radials();target[0].brightness=1;assert.equal(display.set(target),true);
 const first=display.advance(0);assert.equal(first.radials[0].brightness,.5);assert.equal(first.moving,true);
 const eased=display.advance(160);close(eased.radials[0].brightness,.5+.5*(1-Math.exp(-1)));
 assert.equal(gesture.readback().level,1);assert.equal(target[0].brightness,1);assert.equal(source.state.radials[0].brightness,.5);
});

test('drawing uses elapsed wall time through a slow frame instead of adding an artificial 100 ms lag cap',()=>{
 const display=D.createRadialDisplay(radials());display.advance(0);const target=radials();target[0].brightness=1;display.set(target);
 close(display.advance(500).radials[0].brightness,.5+.5*(1-Math.exp(-.5/.16)));
});

test('retargeting drawing begins at its current eased level rather than jumping to either target',()=>{
 const display=D.createRadialDisplay(radials());display.advance(0);const up=radials();up[0].brightness=1;display.set(up);
 const current=display.advance(160).radials[0].brightness,down=radials();down[0].brightness=.2;
 assert.equal(display.set(down),true);close(display.advance(160).radials[0].brightness,current);
 close(display.advance(320).radials[0].brightness,current+(.2-current)*(1-Math.exp(-1)));
});

test('reduced motion snaps only the drawing to exact current targets without waiting for another frame interval',()=>{
 const input=radials(),display=D.createRadialDisplay(input);display.advance(10);const target=radials();
 target[0].on=false;target[0].brightness=.8;target[1].brightness=.9;target[1].color_temp_kelvin=4200;display.set(target);
 const shown=display.advance(10,{reducedMotion:true});assert.equal(shown.radials[0].brightness,0);assert.equal(shown.radials[0].display_opacity,0);
 assert.equal(shown.radials[1].brightness,.9);assert.equal(shown.radials[1].color_temp_kelvin,4200);assert.equal(shown.moving,false);
 assert.equal(target[0].brightness,.8);assert.equal(target[0].on,false);assert.deepEqual(input,radials());
});

test('power Off eases level and opacity to zero while the remembered modeled brightness stays intact',()=>{
 const target=radials(),display=D.createRadialDisplay(target);const initial=display.advance(0).radials[0];
 target[0].on=false;assert.equal(display.set(target),true);const fading=display.advance(160).radials[0];
 close(fading.brightness,.5*Math.exp(-1));close(fading.display_opacity,initial.display_opacity*Math.exp(-1));
 assert.ok(fading.brightness>0&&fading.brightness<.5);assert.equal(target[0].brightness,.5);assert.equal(target[0].on,false);
 display.advance(2000);const settled=display.advance(2001).radials[0];assert.equal(settled.brightness,0);assert.equal(settled.display_opacity,0);assert.equal(settled.on,false);
});

test('display targets and frames are owned copies and direction changes cannot replace an existing presentation',()=>{
 const input=radials(),display=D.createRadialDisplay(input),source=snapshot(),identity=D.identityKey(source);display.advance(0);
 const target=radials();target[0].brightness=.9;assert.equal(display.set(target),true);target[0].brightness=.1;
 const frame=display.advance(160);close(frame.radials[0].brightness,.5+.4*(1-Math.exp(-1)));frame.radials[0].brightness=0;
 const reordered=radials().reverse();reordered.find(zone=>zone.number===1).brightness=.9;
 assert.equal(display.set(reordered),true);const drift=radials();drift[0].azimuth_deg+=5;assert.equal(display.set(drift),false);
 close(display.advance(160).radials[0].brightness,.5+.4*(1-Math.exp(-1)));
 source.state.radials[0].brightness=.9;source.state.radials[1].on=false;assert.equal(D.identityKey(source),identity);assert.deepEqual(input,radials());
});

test('display refuses malformed targets without replacing the admitted target or inventing a temperature',()=>{
 assert.equal(D.createRadialDisplay(radials().slice(1)),null);const malformed=radials();delete malformed[0].color_temp_kelvin;
 assert.equal(D.createRadialDisplay(malformed),null);const display=D.createRadialDisplay(radials());display.advance(0);
 assert.equal(display.set(malformed),false);assert.equal(display.advance(NaN),null);assert.deepEqual(display.advance(160).radials.map(zone=>zone.brightness),Array(6).fill(.5));
});

test('first pickup into a different zone adopts only that zone before later sweeps interpolate',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 const first=gesture.move({snapshot:source,pointerId:1,point:polar(120,.43)});
 assert.equal(first.engaged,true);assert.deepEqual(first.patch,{radials:[{number:3,on:true,brightness:.25}]});
 const next=gesture.move({snapshot:source,pointerId:1,point:polar(240,1.01)});
 assert.deepEqual(next.patch,{radials:[{number:3,on:true,brightness:.25},{number:4,on:true,brightness:.5},{number:5,on:true,brightness:.75}]});
});

test('an exact half-turn sweep takes the prototype negative-direction tie without painting the opposite arc',()=>{
 const source=snapshot(),gesture=D.createRadialGesture({snapshot:source,pointerId:1,point:[.2,0],startable:true});
 gesture.move({snapshot:source,pointerId:1,point:[1.3,0]});const result=gesture.move({snapshot:source,pointerId:1,point:polar(180,.14)});
 assert.deepEqual(result.patch,{radials:[{number:1,on:true,brightness:1},{number:4,on:false,brightness:0},{number:5,on:true,brightness:.33},{number:6,on:true,brightness:.67}]});
});
test('a hit margin widens only each zone hit path past its lit edge, in plane metres',()=>{
 const input=radials();input[1].on=false;input[2].brightness=1;
 const base=D.radialSectorPaths({radials:input,frame:plan}),wide=D.radialSectorPaths({radials:input,frame:plan,hitMargin:.12});
 assert.deepEqual(D.radialSectorPaths({radials:input,frame:plan,hitMargin:0}),base,'zero margin is the existing drawing');
 for(const key of ['hub','inner','outer','contour'])assert.deepEqual(wide[key],base[key]);
 const points=path=>path.slice(1,-2).split(' L').map(pair=>pair.split(' ').map(Number));
 wide.zones.forEach((zone,i)=>{
  const was=base.zones[i];
  assert.deepEqual([zone.number,zone.azimuth_deg,zone.level,zone.node,zone.tick],[was.number,was.azimuth_deg,was.level,was.node,was.tick]);
  const a=points(zone.path),b=points(was.path);assert.equal(a.length,26);assert.equal(b.length,26);
  for(let j=0;j<13;j++){
   close(Math.hypot(a[j][0]-200,a[j][1]-200)-Math.hypot(b[j][0]-200,b[j][1]-200),12,1e-9);
   close(Math.atan2(a[j][1]-200,a[j][0]-200),Math.atan2(b[j][1]-200,b[j][0]-200),1e-12);
  }
  assert.deepEqual(a.slice(13),b.slice(13),'the inner arc stays on the hub ring');
 });
 close(Math.max(...points(wide.zones[2].path).slice(0,13).map(([x,y])=>Math.hypot(x-200,y-200))),142,1e-9);
 for(const hitMargin of [-.01,NaN,Infinity,'0.12',null])assert.equal(D.radialSectorPaths({radials:input,frame:plan,hitMargin}),null);
});
