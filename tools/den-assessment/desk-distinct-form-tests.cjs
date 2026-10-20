'use strict';
// Execute the actual guide script with a minimal local DOM and fake HTTP port.
// No browser, server, camera, model, private file or installed service is opened.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const html=fs.readFileSync(path.join(__dirname,'desk-geometry-guide.html'),'utf8');
const script=html.match(/<script>([\s\S]*)<\/script>/)[1];

class Element {
  constructor(tag){this.tag=tag;this.children=[];this.value='';this.files=[];this.disabled=false;this.checked=false;this.hidden=false;this.textContent=''}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=[...nodes]}
  setAttribute(name,value){this[name]=value}
  get rows(){return this.children.filter(node=>node.tag==='tr')}
  querySelectorAll(tag){return this.children.flatMap(node=>typeof node==='object'?[...(node.tag===tag?[node]:[]),...node.querySelectorAll(tag)]:[])}
  querySelector(tag){return this.querySelectorAll(tag)[0]||null}
  set selectedIndex(index){this.value=this.children[index].value}
}
function file(value){const raw=Buffer.from(JSON.stringify(value));return {size:raw.length,arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)}}
async function form(version){
  const root=new Element('main'),nodes={};
  for(const [,id]of html.matchAll(/id="([^"]+)"/g)){const node=new Element('div');node.id=id;nodes[id]=node;root.append(node)}
  const document={getElementById:id=>nodes[id]||root.querySelectorAll('tbody').find(node=>node.id===id)||root.querySelectorAll('input').find(node=>node.id===id),createElement:tag=>new Element(tag),createTextNode:value=>value};
  nodes.surveyVersion.value='1';nodes.method.value='tape';
  const requests=[];
  const context={document,crypto:webcrypto,TextDecoder,Uint8Array,AbortSignal,fetch:async(route,request)=>{
    requests.push({route,body:JSON.parse(request.body)});
    return {ok:true,json:async()=>({digest:'f'.repeat(64),camera_status:'unavailable',required_review:[],qualified:false})};
  }};
  vm.runInNewContext(script,context,{filename:'actual-desk-guide-script.js',timeout:1000});
  nodes.equipment.files=[file({schema:'home-equipment-registration-1',version:1,scope:{site_id:'victoria'},revision:2,digest:'a'.repeat(64),
    binding:{content_revision:'authored',registration_version:1,manifest_digest:'b'.repeat(64),frame_digest:'c'.repeat(64),canonical_collision_digest:'d'.repeat(64)},
    references:{frame:'z_up_metric_floor0',units:'m',floor:{id:'floor',normal:[0,0,1],offset_m:0},walls:[
      {id:'left',label:'Left',normal_xy:[1,0],offset_m:0},{id:'front',label:'Front',normal_xy:[0,1],offset_m:0}]}})];
  await nodes.equipment.onchange();nodes.wall0.value='left';nodes.wall1.value='front';
  nodes.referenceError.value='.01';nodes.thickness.value='.02';nodes.coverage.checked=true;
  function fill(id,rows){document.getElementById(id).rows.forEach((row,i)=>row.querySelectorAll('input').forEach((input,j)=>input.value=String(rows[i][j])))}
  fill('work',[[1,2,.75,.01],[2,3,.75,.01]]);fill('envelope',[[.5,.5,0,.01],[2.5,2,1.8,.01]]);
  nodes.surveyVersion.value=String(version);nodes.surveyVersion.onchange();
  if(version===2){
    nodes.protectionPolicy.files=[file({schema:'home-live-task-delivery-policy-4',version:4,protection:{policy_ref:{id:'authored-policy',digest:'e'.repeat(64)}}})];
    await nodes.protectionPolicy.onchange();
    for(const [index,name]of ['body','head','hand_left','hand_right','protection'].entries()){
      fill('part-'+name,[[1+index,.5,.5,.01],[1.5+index,1,1.5,.01]]);
      document.getElementById('coverage-'+name).checked=true;
    }
  }
  return {nodes,document,requests};
}

test('v2 actual form emits separate measured parts and imported protection pin',async()=>{
  const f=await form(2);await f.nodes.prepare.onclick();
  assert.equal(f.requests.length,1);const value=f.requests[0].body;
  assert.equal(value.schema,'home-desk-geometry-survey-2');assert.equal(value.version,2);
  assert.equal(value.seated_envelope,undefined);assert.equal(value.parts.head.outer_corners[0].wall_distances_m[0],2);
  assert.notDeepEqual(value.parts.body.outer_corners,value.parts.head.outer_corners);
  assert.equal(value.protection_policy.policy_ref.id,'authored-policy');assert.match(value.protection_policy.source_file_sha256,/^[a-f0-9]{64}$/);
  assert.equal(f.nodes.legacyEnvelope.hidden,true);assert.equal(f.nodes.distinctParts.hidden,false);
  assert.match(f.nodes.status.textContent,/Qualified: false/);
});
test('v2 missing independent measurement never copies a complete part',async()=>{
  const f=await form(2);f.document.getElementById('part-hand_left').rows[0].querySelectorAll('input')[0].value='';
  await f.nodes.prepare.onclick();assert.equal(f.requests.length,0);assert.match(f.nodes.status.textContent,/unknown is not zero/);
});
test('v2 requires protection policy import and every independent coverage declaration',async()=>{
  let f=await form(2);f.nodes.protectionPolicy.files=[];await f.nodes.protectionPolicy.onchange();await f.nodes.prepare.onclick();
  assert.equal(f.requests.length,0);assert.match(f.nodes.status.textContent,/Import the prepared protection policy/);
  f=await form(2);f.document.getElementById('coverage-hand_right').checked=false;await f.nodes.prepare.onclick();
  assert.equal(f.requests.length,0);assert.match(f.nodes.status.textContent,/coverage for hand right/);
});
test('historical v1 form retains its original whole-envelope request',async()=>{
  const f=await form(1);await f.nodes.prepare.onclick();assert.equal(f.requests.length,1);
  const value=f.requests[0].body;assert.equal(value.schema,'home-desk-geometry-survey-1');assert.equal(value.version,1);
  assert.equal(value.parts,undefined);assert.equal(value.protection_policy,undefined);
  assert.equal(value.seated_envelope.coverage_declaration,'body_head_both_hands_supported_seated_reach');
});
