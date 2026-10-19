'use strict';
// Proposal: actual Guide script, authored DOM/file/fetch ports only. No browser/host.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const html=fs.readFileSync(path.join(__dirname,'desk-geometry-guide.html'),'utf8');
class Element{
  constructor(tag){Object.assign(this,{tag,children:[],value:'',files:[],checked:false,disabled:false,hidden:false,textContent:''})}
  append(...nodes){const empty=this.children.length===0;for(const node of nodes){this.children.push(node);if(node&&typeof node==='object')node.parentElement=this}
    if(this.tag==='select'&&empty&&nodes[0]?.tag==='option')this.value=nodes[0].value}
  replaceChildren(...nodes){this.children=[];this.value='';this.append(...nodes)}
  setAttribute(name,value){this[name]=value}
  get rows(){return this.children.filter(n=>n.tag==='tr')}
  querySelectorAll(tag){return this.children.flatMap(n=>n&&typeof n==='object'?[...(n.tag===tag?[n]:[]),...n.querySelectorAll(tag)]:[])}
  querySelector(tag){return this.querySelectorAll(tag)[0]||null}
  set selectedIndex(index){this.value=this.children[index]?.value??''}
}
const names=['work','body','head','hand_left','hand_right','protection'];
const equipment={schema:'home-equipment-registration-1',version:1,scope:{site_id:'victoria'},revision:2,digest:'a'.repeat(64),
  binding:{content_revision:'authored-reference-only',registration_version:1,manifest_digest:'b'.repeat(64),frame_digest:'c'.repeat(64),canonical_collision_digest:'d'.repeat(64)},
  references:{frame:'z_up_metric_floor0',units:'m',floor:{id:'floor',normal:[0,0,1],offset_m:0},walls:[
    {id:'left',label:'Left',normal_xy:[1,0],offset_m:0},{id:'front',label:'Front',normal_xy:[0,1],offset_m:0}]}};
function file(value){const bytes=Buffer.from(JSON.stringify(value));return{size:bytes.length,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)}}
function form({loseFreeze=0,loseStart=0}={}){
  const root=new Element('main'),nodes={},requests=[];let freezeLoss=loseFreeze,startLoss=loseStart;
  for(const [,tag,attributes,id]of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const node=new Element(tag);node.id=id;node.disabled=/\bdisabled\b/.test(attributes);node.hidden=/\bhidden\b/.test(attributes);nodes[id]=node;root.append(node)}
  const document={getElementById:id=>nodes[id]||['tbody','input','select','button'].flatMap(tag=>root.querySelectorAll(tag)).find(n=>n.id===id),createElement:tag=>new Element(tag),createTextNode:s=>s};
  nodes.surveyVersion.value='1';nodes.method.value='tape';nodes.heldActivity.value='unknown';
  const context={document,crypto:webcrypto,TextDecoder,Uint8Array,AbortSignal,
    fetch:async(route,request)=>{const body=JSON.parse(request.body);requests.push({route,body,headers:request.headers});
      if(route==='/reference/freeze'&&freezeLoss-->0)throw Error('authored lost freeze ACK');
      if(route==='/reference/start'&&startLoss-->0)throw Error('authored lost start ACK');
      const result=route==='/reference/freeze'?{status:'frozen'}:route==='/reference/start'?{status:'started',window_id:body.window_id}:
        route==='/reference/end'?{status:'ended'}:route==='/reference/results'?{status:'incomplete',open_windows:1,qualified:false,
          authority:{geometry:false,association:false,engagement:false,beam:false,physical:false},operational_qualification:{decision:'block'}}:{status:'stopped'};
      return{ok:true,json:async()=>result}}};
  const script=html.match(/<script>([\s\S]*)<\/script>/)[1].replaceAll('__REFERENCE_ONLY_MODE__','enabled')
    .replaceAll('__ASSESSMENT_MODE__','offline').replaceAll('__EVALUATION_MODE__','offline')
    .replaceAll('__CAMERA_TARGETS__','offline').replaceAll('__ASSESSMENT_REFERENCES__','null').replaceAll('__NONCE__','AUTHORED_NONCE');
  vm.runInNewContext(script,context,{timeout:1000});
  return{nodes,document,requests};
}
async function ready(options){const f=form(options);f.nodes.equipment.files=[file(equipment)];await f.nodes.equipment.onchange();
  f.nodes.referenceError.value='.001';f.nodes.heldConfigurations.value='seated-desk';f.nodes.heldMaxWindows.value='2';
  f.document.getElementById('referenceAnchorName').value='Independently measured nose';return f}
const bodies=(f,route)=>f.requests.filter(row=>row.route===route).map(row=>row.body);

test('candidate-free form starts with explicit unknowns and never needs candidate, policy or camera data',async()=>{
  const f=await ready();assert.equal(f.nodes.heldAssessment.hidden,false);assert.equal(f.nodes.prepare.hidden,true);
  assert.equal(f.nodes.cameraPanel.hidden,true);assert.equal(f.nodes.cameraDetails.hidden,true);
  assert.equal(f.document.getElementById('heldExport'),undefined);
  for(const name of names){assert.equal(f.document.getElementById('held-state-'+name).value,'unknown');
    assert.ok(f.document.getElementById(name==='work'?'work':'part-'+name).querySelectorAll('input').every(n=>n.value===''))}
  await f.nodes.heldStart.onclick();assert.equal(f.requests.length,0,'Start must wait for acknowledged Freeze');
  await f.nodes.heldFreeze.onclick();const frozen=bodies(f,'/reference/freeze')[0];
  assert.equal(frozen.references.equipment_ref.revision,2);assert.equal(frozen.protocol.id,undefined);
  assert.ok(Object.values(frozen.requirements).every(v=>v===null));
  f.nodes.heldReferenceError.value='.01';await f.nodes.heldStart.onclick();const start=bodies(f,'/reference/start')[0];
  assert.equal(start.window_id,'reference-1');
  assert.deepEqual(start.parts,Object.fromEntries(names.map(name=>[name,{status:'unknown',outer_corners:null,thickness_m:null}])));
  assert.deepEqual(start.selected_anchor,{status:'unknown',original_measurement:null});
  assert.doesNotMatch(JSON.stringify(start),/candidate|prediction|camera_xyz|point_m|shape|observed_at|monotonic_ns|resolver|qualified/);
  assert.equal(f.requests.at(-1).headers['X-Desk-Nonce'],'AUTHORED_NONCE');
  await f.nodes.heldReport.onclick();assert.deepEqual(bodies(f,'/reference/results'),[{}]);
  assert.match(f.nodes.heldResult.textContent,/"decision": "block"/);
  await f.nodes.stop.onclick();await f.nodes.heldEnd.onclick();
  assert.equal(bodies(f,'/reference/end').length,0,'Stop cannot manufacture End');
  assert.ok(f.requests.every(row=>row.route.startsWith('/reference/')||row.route==='/stop'));
});

test('lost Freeze and Start ACKs retry exact cached requests and preserve window identity',async()=>{
  const f=await ready({loseFreeze:1,loseStart:1});await f.nodes.heldFreeze.onclick();
  assert.equal(f.nodes.heldStart.disabled,true);assert.equal(f.nodes.equipment.disabled,true);
  // Mutating this authored DOM cannot alter the already retained request bytes.
  f.nodes.heldConfigurations.value='must-not-replace-frozen-request';await f.nodes.heldFreeze.onclick();
  const freezes=bodies(f,'/reference/freeze');assert.equal(freezes.length,2);assert.deepEqual(freezes[1],freezes[0]);
  f.nodes.heldReferenceError.value='.01';await f.nodes.heldStart.onclick();
  assert.equal(f.nodes.heldEnd.disabled,true);assert.equal(f.nodes.heldReferenceError.readOnly,true);
  f.nodes.heldActivity.value='focused';await f.nodes.heldStart.onclick();
  const starts=bodies(f,'/reference/start');assert.equal(starts.length,2);assert.deepEqual(starts[1],starts[0]);assert.equal(starts[0].window_id,'reference-1');
  await f.nodes.heldEnd.onclick();await f.nodes.heldStart.onclick();
  assert.equal(bodies(f,'/reference/start').at(-1).window_id,'reference-2');
});

test('available-part blanks refuse locally while measured anchor sends only original wall measurements',async()=>{
  const f=await ready();await f.nodes.heldFreeze.onclick();f.nodes.heldReferenceError.value='.01';
  f.document.getElementById('held-state-head').value='available';await f.nodes.heldStart.onclick();
  assert.equal(bodies(f,'/reference/start').length,0);assert.match(f.nodes.heldState.textContent,/unknown is not zero/);
  f.document.getElementById('held-state-head').value='unknown';f.document.getElementById('referenceAnchorStatus').value='available';
  const inputs=f.document.getElementById('referenceAnchor').querySelectorAll('input');
  inputs.forEach((node,i)=>node.value=['2','3','1.3','.005'][i]);await f.nodes.heldStart.onclick();
  const start=bodies(f,'/reference/start')[0];assert.deepEqual(start.selected_anchor,{status:'available',original_measurement:{wall_distances_m:[2,3],height_above_floor_m:1.3,uncertainty_m:.005}});
  assert.ok(inputs.every(node=>node.readOnly));assert.equal(start.parts.head.outer_corners,null);
});
