'use strict';
// Actual opt-in guide script, authored DOM and fixed fake HTTP replies only.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'desk-geometry-guide.html'),'utf8');
class Element{
  constructor(tag){Object.assign(this,{tag,children:[],value:'',checked:false,disabled:false,hidden:false,textContent:''})}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=[...nodes]}
  setAttribute(name,value){this[name]=value}
  get rows(){return this.children.filter(n=>n.tag==='tr')}
  querySelectorAll(tag){return this.children.flatMap(n=>typeof n==='object'?[...(n.tag===tag?[n]:[]),...n.querySelectorAll(tag)]:[])}
  querySelector(tag){return this.querySelectorAll(tag)[0]||null}
  getBoundingClientRect(){return {left:0,top:0,width:640,height:360}}
}
function form(){
  const root=new Element('main'),nodes={};for(const [,id]of html.matchAll(/id="([^"]+)"/g)){nodes[id]=new Element('div');nodes[id].id=id;root.append(nodes[id])}
  const document={getElementById:id=>nodes[id]||root.querySelectorAll('tbody').find(n=>n.id===id)||root.querySelectorAll('input').find(n=>n.id===id),createElement:tag=>new Element(tag),createTextNode:s=>s};
  nodes.surveyVersion.value='1';nodes.method.value='tape';const requests=[];let pointNumber=0;
  const context={document,AbortSignal,fetch:async(route,request)=>{const body=JSON.parse(request.body);requests.push({route,body});return{ok:true,json:async()=>route==='/camera/snapshot'?{
    snapshot_id:'snapshot-'+('a'.repeat(64)),capture_epoch:'authored-epoch',width:640,height:360,preview_mime:'image/png',preview_base64:'AA=='
  }:{status:'candidate',camera_xyz_m:[999,888,2],camera_uncertainty_m:null,depth_spread_m:.9,depth_support:100,
     camera_original_ref:{store_id:'desk-camera-originals',receipt_id:'point-'+String(++pointNumber).padStart(64,'0'),digest:'b'.repeat(64)}}}}};
  vm.runInNewContext(html.match(/<script>([\s\S]*)<\/script>/)[1].replaceAll('__CAMERA_TARGETS__','enabled'),context,{timeout:1000});
  return {nodes,requests,context};
}
test('frozen mode locks independent references before click and hides machine diagnostics',async()=>{
  const f=form();assert.equal(f.nodes.cameraPanel.hidden,false);
  f.nodes.useCapturedCamera.checked=true;f.nodes.useCapturedCamera.onchange();
  assert.equal(f.nodes.surveyVersion.value,'2');assert.equal(f.nodes.fitCamera.checked,true);
  await f.nodes.cameraSnapshot.onclick();assert.equal(f.requests.length,1);
  for(const [index,row]of f.nodes.targets.rows.entries()){
    const fields=row.querySelectorAll('input');assert.ok(fields.slice(0,4).every(n=>n.type==='hidden'));
    fields.slice(4).forEach((n,j)=>n.value=String([1+index,2,1,.01][j]));
    row.querySelector('button').onclick();assert.ok(fields.slice(4).every(n=>n.readOnly));assert.equal(row.querySelector('select').disabled,true);
    await f.nodes.cameraPreview.onclick({clientX:100+index,clientY:120});
    assert.equal(fields[0].value,'999');assert.equal(fields[1].value,'888');
    assert.ok(fields.slice(0,4).every(n=>n.type==='hidden'));
    assert.doesNotMatch(f.nodes.status.textContent,/999|888|\.9|100/);
  }
  const value=f.context.cameraInputs();assert.equal(value.schema,'home-desk-camera-targets-1');assert.equal(value.points.length,6);
  assert.ok(value.points.every(row=>row.camera_uncertainty_m===null&&row.camera_original_ref.store_id==='desk-camera-originals'));
  const first=f.requests.find(row=>row.route==='/camera/point').body;
  assert.equal(first.reference.room_measurement.height_above_floor_m,1);assert.equal(first.reference.role,'fit');
  assert.equal(first.u,100);assert.equal(first.v,120);assert.ok(!('camera_xyz_m' in first));
});
test('opt-in and complete locked reference are required before any point action',async()=>{
  const f=form();await f.nodes.cameraSnapshot.onclick();assert.equal(f.requests.length,0);
  f.nodes.useCapturedCamera.checked=true;f.nodes.useCapturedCamera.onchange();await f.nodes.cameraSnapshot.onclick();
  await f.nodes.cameraPreview.onclick({clientX:100,clientY:120});assert.equal(f.requests.length,1);
  f.nodes.targets.rows[0].querySelector('button').onclick();
  assert.match(f.nodes.status.textContent,/unknown is not zero/);
  await f.nodes.cameraPreview.onclick({clientX:100,clientY:120});assert.equal(f.requests.length,1);
});
