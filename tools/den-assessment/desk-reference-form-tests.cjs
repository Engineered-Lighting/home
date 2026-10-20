'use strict';
// Actual guide script, authored DOM/HTTP only; no browser, storage or camera.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const html=fs.readFileSync(path.join(__dirname,'desk-geometry-guide.html'),'utf8');
class Element{
  constructor(tag){Object.assign(this,{tag,children:[],value:'',files:[],checked:false,disabled:false,hidden:false,textContent:''})}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=[...nodes]}
  setAttribute(name,value){this[name]=value}
  get rows(){return this.children.filter(n=>n.tag==='tr')}
  querySelectorAll(tag){return this.children.flatMap(n=>typeof n==='object'?[...(n.tag===tag?[n]:[]),...n.querySelectorAll(tag)]:[])}
  querySelector(tag){return this.querySelectorAll(tag)[0]||null}
}
const refs={frame:'z_up_metric_floor0',unit:'m',equipment_ref:{revision:2,digest:'a'.repeat(64),file_sha256:'b'.repeat(64)},
  binding:{content_revision:'authored',registration_version:1,manifest_digest:'c'.repeat(64),frame_digest:'d'.repeat(64),canonical_collision_digest:'e'.repeat(64)},
  floor:{id:'floor',normal:[0,0,1],offset_m:0},walls:[{id:'left',label:'Left',normal_xy:[1,0],offset_m:0},
  {id:'front',label:'Front',normal_xy:[0,1],offset_m:0}],reference_uncertainty_m:.001};
const parts=['work','body','head','hand_left','hand_right','protection'];
function form(){
  const root=new Element('main'),nodes={},requests=[];
  for(const [,id]of html.matchAll(/id="([^"]+)"/g)){nodes[id]=new Element('div');nodes[id].id=id;root.append(nodes[id])}
  const document={getElementById:id=>nodes[id]||['tbody','input','select'].flatMap(tag=>root.querySelectorAll(tag)).find(n=>n.id===id),createElement:tag=>new Element(tag),createTextNode:s=>s};
  nodes.surveyVersion.value='1';nodes.method.value='tape';nodes.heldActivity.value='unknown';
  const context={document,crypto:webcrypto,TextDecoder,Uint8Array,AbortSignal,
    fetch:async(route,request)=>{requests.push({route,body:JSON.parse(request.body)});const value=route.endsWith('/freeze')?{status:'frozen'}:
      route.endsWith('/start')?{status:'started',window_id:'held-1'}:route.endsWith('/end')?{status:'ended'}:
      route.endsWith('/report')?{status:'review_report',operational_qualification:{decision:'block'},qualified:false}: {status:'stopped'};
      return{ok:true,json:async()=>value}}};
  const script=html.match(/<script>([\s\S]*)<\/script>/)[1].replaceAll('__ASSESSMENT_MODE__','enabled')
    .replaceAll('__ASSESSMENT_REFERENCES__',JSON.stringify(refs));
  vm.runInNewContext(script,context,{timeout:1000});
  return{nodes,document,requests};
}
async function freeze(f){f.nodes.heldConfigurations.value='upright-desk';f.nodes.heldMaxWindows.value='2';await f.nodes.heldFreeze.onclick()}

test('actual reference UI keeps independent blanks and sends no browser coordinates/times/hashes',async()=>{
  const f=form();assert.equal(f.nodes.heldAssessment.hidden,false);assert.equal(f.nodes.cameraPanel.hidden,true);
  for(const name of parts){assert.equal(f.document.getElementById('held-state-'+name).value,'unknown');
    for(const row of f.document.getElementById(name==='work'?'work':'part-'+name).rows)
      assert.ok(row.querySelectorAll('input').every(input=>input.value===''))}
  assert.equal(f.requests.length,0);await freeze(f);assert.equal(f.requests[0].route,'/assessment/freeze');
  assert.ok(Object.values(f.requests[0].body.requirements).every(v=>v===null));
  assert.equal(f.requests[0].body.protocol.id,undefined);
  f.nodes.heldReferenceError.value='.01';await f.nodes.heldStart.onclick();
  const start=f.requests[1];assert.equal(start.route,'/assessment/start');
  assert.deepEqual(start.body.parts,Object.fromEntries(parts.map(name=>[name,{status:'unknown'}])));
  assert.equal(start.body.references.equipment_ref.revision,2);
  assert.doesNotMatch(JSON.stringify(start.body.parts),/shape|center_m|half_extents|camera_xyz|observed_at|start_at|digest/);
  assert.equal(f.nodes.heldEnd.disabled,false);assert.equal(f.nodes.heldReferenceError.readOnly,true);
  await f.nodes.heldEnd.onclick();assert.deepEqual(f.requests[2],{route:'/assessment/end',body:{window_id:'held-1'}});
  await f.nodes.heldReport.onclick();assert.deepEqual(f.requests[3],{route:'/assessment/report',body:{}});
  assert.match(f.nodes.heldResult.textContent,/"decision": "block"/);assert.equal(f.nodes.heldStart.disabled,true);
});

test('available reference blanks block Start without copying proposed or other part values',async()=>{
  const f=form();await freeze(f);f.nodes.heldReferenceError.value='.01';
  f.document.getElementById('held-state-head').value='available';
  await f.nodes.heldStart.onclick();assert.equal(f.requests.length,1);assert.match(f.nodes.heldState.textContent,/unknown is not zero/);
});

test('Stop never fabricates End and further held actions stay disabled by state',async()=>{
  const f=form();await freeze(f);f.nodes.heldReferenceError.value='.01';await f.nodes.heldStart.onclick();
  await f.nodes.stop.onclick();await f.nodes.heldEnd.onclick();
  assert.deepEqual(f.requests.map(r=>r.route),['/assessment/freeze','/assessment/start','/stop']);
});
