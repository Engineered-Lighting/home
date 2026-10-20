'use strict';
// Execute the actual HTML script. Files, DOM and download port are authored;
// no browser, server, storage API, camera or private file is accessed.
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
  set selectedIndex(index){this.value=this.children[index].value}
  click(){this.clicked=true}
}
const equipment={schema:'home-equipment-registration-1',version:1,scope:{site_id:'victoria'},revision:2,digest:'a'.repeat(64),
  binding:{content_revision:'authored',registration_version:1,manifest_digest:'b'.repeat(64),frame_digest:'c'.repeat(64),canonical_collision_digest:'d'.repeat(64)},
  references:{frame:'z_up_metric_floor0',units:'m',floor:{id:'floor',normal:[0,0,1],offset_m:0},walls:[
    {id:'left',label:'Left',normal_xy:[1,0],offset_m:0},{id:'front',label:'Front',normal_xy:[0,1],offset_m:0}]}};
const policy={schema:'home-live-task-delivery-policy-4',version:4,protection:{policy_ref:{id:'authored-policy',digest:'e'.repeat(64)}}};
function file(value){const raw=Buffer.from(JSON.stringify(value));return{size:raw.length,text:async()=>raw.toString(),arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)}}
function form(){
  const root=new Element('main'),nodes={},downloads=[],requests=[];
  for(const [,id]of html.matchAll(/id="([^"]+)"/g)){nodes[id]=new Element('div');nodes[id].id=id;root.append(nodes[id])}
  const document={getElementById:id=>nodes[id]||root.querySelectorAll('tbody').find(n=>n.id===id)||root.querySelectorAll('input').find(n=>n.id===id),createElement:tag=>new Element(tag),createTextNode:s=>s};
  nodes.surveyVersion.value='1';nodes.method.value='tape';
  const context={document,crypto:webcrypto,TextDecoder,Uint8Array,Blob,AbortSignal,setTimeout:fn=>fn(),
    URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:authored-'+downloads.length},revokeObjectURL:()=>{}},
    fetch:async(route,request)=>{requests.push({route,body:JSON.parse(request.body)});return{ok:true,json:async()=>({status:'stopped'})}}};
  vm.runInNewContext(html.match(/<script>([\s\S]*)<\/script>/)[1].replaceAll('__CAMERA_TARGETS__','enabled').replaceAll('__NONCE__','SECRET_NONCE'),context,{timeout:1000});
  return {nodes,document,context,downloads,requests};
}
async function select(f,id,value){f.nodes[id].files=[file(value)];await f.nodes[id].onchange()}
async function prepared(version='2'){
  const f=form();await select(f,'equipment',equipment);f.nodes.wall0.value='front';f.nodes.wall1.value='left';
  f.nodes.surveyVersion.value=version;f.nodes.surveyVersion.onchange();
  if(version==='2')await select(f,'protectionPolicy',policy);
  f.nodes.referenceError.value='';f.nodes.thickness.value='.0200';f.nodes.method.value='laser';
  for(const id of ['work','envelope',...'body head hand_left hand_right protection'.split(' ').map(n=>'part-'+n)]){
    f.document.getElementById(id).rows.forEach((row,i)=>row.querySelectorAll('input').forEach((input,j)=>input.value=j===3?'':String(i+j+1)));
  }
  f.nodes.targets.rows.forEach((row,index)=>{row.querySelector('select').value=index%2?'check':'fit';row.querySelectorAll('input').slice(4).forEach((input,j)=>input.value=j===3?'':String(index+j+1))});
  return f;
}
async function download(f){f.nodes.draftDownload.onclick();assert.ok(f.downloads.length,f.nodes.draftStatus.textContent);return JSON.parse(await f.downloads.at(-1).text())}
const clone=value=>JSON.parse(JSON.stringify(value));

test('download preserves independent raw blanks and choices but excludes machine data and authority',async()=>{
  const f=await prepared();f.nodes.coverage.checked=true;
  const row=f.nodes.targets.rows[0];row.cameraPoint={camera_original_ref:'SECRET_ORIGINAL',camera_xyz_m:[999,888,777]};row.cameraPixel=[321,123];
  row.querySelectorAll('input').slice(0,4).forEach(n=>n.value='999');
  for(const name of ['body','head','hand_left','hand_right','protection'])f.document.getElementById('coverage-'+name).checked=true;
  const draft=await download(f),raw=JSON.stringify(draft);
  assert.deepEqual(Object.keys(draft).sort(),['schema','version','equipment_sha256','protection_policy_sha256','form'].sort());
  assert.equal(draft.form.reference_uncertainty,'');assert.equal(draft.form.work[0][3],'');assert.equal(draft.form.work_thickness,'.0200');
  assert.deepEqual(draft.form.wall_ids,['front','left']);assert.equal(draft.form.camera_references[1].role,'check');
  assert.doesNotMatch(raw,/SECRET|999|888|777|camera_xyz|camera_original|rgb_pixel|coverage|qualified|nonce|credential|camera_uncertainty/);
  assert.equal(f.requests.length,0);
});
test('import waits for separately selected exact equipment and policy files',async()=>{
  const draft=await download(await prepared()),f=form();
  await select(f,'draftImport',draft);assert.match(f.nodes.draftStatus.textContent,/pending.*equipment/);
  await f.nodes.prepare.onclick();assert.equal(f.requests.length,0);
  await select(f,'equipment',{...equipment,revision:3});assert.match(f.nodes.draftStatus.textContent,/pending.*equipment/);
  await select(f,'equipment',equipment);assert.match(f.nodes.draftStatus.textContent,/pending.*policy/);
  await select(f,'protectionPolicy',{...policy,protection:{policy_ref:{id:'other-policy',digest:'e'.repeat(64)}}});
  assert.match(f.nodes.draftStatus.textContent,/pending.*policy/);
  await select(f,'protectionPolicy',policy);assert.match(f.nodes.draftStatus.textContent,/measurements restored/);
  assert.equal(f.nodes.referenceError.value,'');assert.equal(f.nodes.thickness.value,'.0200');
  assert.equal(f.nodes.wall0.value,'front');assert.equal(f.nodes.wall1.value,'left');assert.equal(f.nodes.surveyVersion.value,'2');
  assert.equal(f.nodes.targets.rows[1].querySelector('select').value,'check');
  assert.equal(f.nodes.targets.rows[1].querySelectorAll('input')[7].value,'');
  assert.equal(f.requests.length,0);
});
test('applying a draft clears coverage, locks, captured estimates, pixels and camera readiness',async()=>{
  const draft=await download(await prepared()),f=await prepared();
  f.nodes.useCapturedCamera.checked=true;f.nodes.useCapturedCamera.onchange();f.nodes.coverage.checked=true;
  for(const name of ['body','head','hand_left','hand_right','protection'])f.document.getElementById('coverage-'+name).checked=true;
  f.nodes.targets.rows[0].cameraPoint={camera_original_ref:'old-original'};f.nodes.targets.rows[0].cameraPixel=[1,2];
  f.nodes.targets.rows[0].referenceLocked=true;f.nodes.cameraPreview.hidden=false;
  await select(f,'draftImport',draft);assert.match(f.nodes.draftStatus.textContent,/measurements restored/);
  assert.equal(f.nodes.coverage.checked,false);assert.equal(f.nodes.fitCamera.checked,false);assert.equal(f.nodes.useCapturedCamera.checked,false);
  assert.equal(f.nodes.cameraPreview.hidden,true);assert.equal(f.context.cameraInputs(),null);
  for(const name of ['body','head','hand_left','hand_right','protection'])assert.equal(f.document.getElementById('coverage-'+name).checked,false);
  for(const row of f.nodes.targets.rows){assert.equal(row.cameraPoint,undefined);assert.equal(row.cameraPixel,undefined);assert.ok(!row.referenceLocked);assert.ok(row.querySelectorAll('input').slice(0,4).every(n=>n.value===''))}
  await f.nodes.prepare.onclick();assert.equal(f.requests.length,0);
});
test('malformed or authority-bearing drafts reject without restoring measurements',async()=>{
  const draft=await download(await prepared());
  for(const change of [d=>d.qualified=true,d=>d.form.camera_references[0].camera_original_ref='forged',
      d=>d.form.parts.head[0][0]='NaN',d=>d.form.work[0][3]=null,d=>d.form.camera_references[0].rgb_pixel=[1,2]]){
    const f=form(),bad=clone(draft);change(bad);await select(f,'draftImport',bad);
    assert.match(f.nodes.draftStatus.textContent,/import refused/);assert.equal(f.nodes.referenceError.value,'');assert.equal(f.requests.length,0);
  }
  const f=form();f.nodes.draftImport.files=[{size:65537,text:async()=>{throw Error('must not read')}}];await f.nodes.draftImport.onchange();
  assert.match(f.nodes.draftStatus.textContent,/import refused/);
});
test('v1 draft needs its exact equipment but no invented policy and remains unconfirmed',async()=>{
  const draft=await download(await prepared('1')),f=form();assert.equal(draft.protection_policy_sha256,null);
  await select(f,'equipment',equipment);await select(f,'draftImport',draft);
  assert.match(f.nodes.draftStatus.textContent,/measurements restored/);assert.equal(f.nodes.surveyVersion.value,'1');
  assert.equal(f.nodes.coverage.checked,false);assert.equal(f.nodes.fitCamera.checked,false);assert.equal(f.requests.length,0);
});
test('download still works after Stop and pending imports can be cancelled without server calls',async()=>{
  const f=await prepared();await f.nodes.stop.onclick();const calls=f.requests.length;
  const draft=await download(f);assert.equal(draft.form.survey_version,'2');assert.equal(f.requests.length,calls);
  const fresh=form();await select(fresh,'draftImport',draft);assert.match(fresh.nodes.draftStatus.textContent,/pending/);
  fresh.nodes.draftCancel.onclick();assert.match(fresh.nodes.draftStatus.textContent,/cancelled/);assert.equal(fresh.requests.length,0);
});
