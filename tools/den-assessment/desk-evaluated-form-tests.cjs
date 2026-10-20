'use strict';
// Actual Guide script with authored DOM/HTTP only. No camera/model or authority.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const html=fs.readFileSync(path.join(__dirname,'desk-geometry-guide.html'),'utf8');
class Element{
  constructor(tag){Object.assign(this,{tag,children:[],value:'',files:[],checked:false,disabled:false,hidden:false,textContent:''})}
  append(...nodes){const first=this.children.length===0;this.children.push(...nodes);
    // A real single-select chooses its first option when no option was set.
    if(this.tag==='select'&&first&&nodes[0]?.tag==='option')this.value=nodes[0].value}
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
function form(exportAvailable=false){
  const root=new Element('main'),nodes={},requests=[];
  for(const [,id]of html.matchAll(/id="([^"]+)"/g)){nodes[id]=new Element('div');nodes[id].id=id;root.append(nodes[id])}
  const document={getElementById:id=>nodes[id]||['tbody','input','select','button'].flatMap(tag=>root.querySelectorAll(tag)).find(n=>n.id===id),createElement:tag=>new Element(tag),createTextNode:s=>s};
  nodes.surveyVersion.value='1';nodes.method.value='tape';nodes.heldActivity.value='unknown';
  const context={document,crypto:webcrypto,TextDecoder,Uint8Array,AbortSignal,
    fetch:async(route,request)=>{requests.push({route,body:JSON.parse(request.body)});const value=route.endsWith('/freeze')?{status:'frozen'}:
      route.endsWith('/start')?{status:'started',window_id:'held-1'}:route.endsWith('/end')?{status:'ended'}:
      route.endsWith('/report')?{decision:'block',export_status:exportAvailable?'available_for_private_save':'unavailable'}:
      route.endsWith('/export')?{status:'export_saved'}:{status:'stopped'};
      return{ok:true,json:async()=>value}}};
  const script=html.match(/<script>([\s\S]*)<\/script>/)[1].replaceAll('__ASSESSMENT_MODE__','enabled')
    .replaceAll('__EVALUATION_MODE__','enabled').replaceAll('__ASSESSMENT_REFERENCES__',JSON.stringify(refs));
  vm.runInNewContext(script,context,{timeout:1000});
  return{nodes,document,requests};
}
async function freeze(f){f.nodes.heldConfigurations.value='upright-desk';f.nodes.heldMaxWindows.value='2';await f.nodes.heldFreeze.onclick()}

test('evaluated form keeps selected anchor blank and submits raw wall measurements only',async()=>{
  const f=form(),anchor=f.document.getElementById('heldAnchor');
  assert.ok(f.nodes.heldAssessment.children.some(n=>n.textContent.includes('A residual is not a qualified error bound.')));
  assert.equal(f.document.getElementById('heldAnchorStatus').value,'unknown');
  assert.ok(anchor.querySelectorAll('input').every(n=>n.value===''));
  await freeze(f);assert.equal(f.requests[0].route,'/evaluation/freeze');
  assert.ok(Object.values(f.requests[0].body.requirements).every(v=>v===null));
  f.nodes.heldReferenceError.value='.001';f.document.getElementById('heldAnchorStatus').value='available';
  await f.nodes.heldStart.onclick();assert.equal(f.requests.length,1);
  anchor.querySelectorAll('input').forEach((field,i)=>field.value=['1','2','1.8','.01'][i]);
  await f.nodes.heldStart.onclick();assert.equal(f.requests[1].route,'/evaluation/start');
  assert.deepEqual(f.requests[1].body.selected_anchor,{status:'available',original_measurement:{wall_distances_m:[1,2],height_above_floor_m:1.8,uncertainty_m:.01}});
  assert.doesNotMatch(JSON.stringify(f.requests[1].body),/point_m|camera_xyz|observed_at|monotonic_ns|resolver|qualified|assessment_ref/);
  assert.ok(anchor.querySelectorAll('input').every(n=>n.readOnly));
});

test('missing qualification leaves export disabled and Stop never sends End',async()=>{
  const f=form();await freeze(f);f.nodes.heldReferenceError.value='.001';await f.nodes.heldStart.onclick();
  assert.deepEqual(f.requests[1].body.selected_anchor,{status:'unknown',original_measurement:null});
  await f.nodes.heldEnd.onclick();await f.nodes.heldReport.onclick();
  assert.equal(f.document.getElementById('heldExport').disabled,true);
  const g=form();await freeze(g);g.nodes.heldReferenceError.value='.001';await g.nodes.heldStart.onclick();
  await g.nodes.stop.onclick();await g.nodes.heldEnd.onclick();
  assert.deepEqual(g.requests.map(r=>r.route),['/evaluation/freeze','/evaluation/start','/stop']);
});

test('private export is a separate explicit server action without path or trust fields',async()=>{
  const f=form(true);await freeze(f);f.nodes.heldReferenceError.value='.001';await f.nodes.heldStart.onclick();
  await f.nodes.heldEnd.onclick();await f.nodes.heldReport.onclick();
  const button=f.document.getElementById('heldExport');assert.equal(button.disabled,false);
  await button.onclick();assert.deepEqual(f.requests.at(-1),{route:'/evaluation/export',body:{}});
});
