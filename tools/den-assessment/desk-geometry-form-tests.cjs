'use strict';
// Generated equipment through the actual Chromium form and loopback guide.
// No camera, model, private assets or installed service participates.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{createHash}=require('node:crypto');
const {once}=require('node:events'),readline=require('node:readline');
const {chromium}=require('playwright');
const Core=require('../../app/src/home-equipment-registration.js');
const ROOT=path.resolve(__dirname,'../..');
async function run(){
  const dir=fs.mkdtempSync(path.join(__dirname,'desk-geometry-form-'));
  const output=path.join(dir,'candidate.json');
  const child=spawn('python',['-B',path.join(__dirname,'desk-geometry-guide.py'),'--output',output,'--seconds','60'],{cwd:ROOT,stdio:['ignore','pipe','pipe'],windowsHide:true});
  let errors='';child.stderr.on('data',chunk=>{errors+=chunk});let browser;
  const timeout=setTimeout(()=>child.kill(),65000);
  try{
    const lines=readline.createInterface({input:child.stdout});
    const [line]=await Promise.race([once(lines,'line'),once(child,'exit').then(()=>{throw Error('Guide exited before readiness: '+errors)})]);const ready=JSON.parse(line);lines.close();
    assert.equal(ready.status,'ready');const origin=new URL(ready.url).origin;
    browser=await chromium.launch({headless:true,channel:fs.existsSync(chromium.executablePath())?'chromium':'msedge',args:['--disable-background-networking']});
    const page=await browser.newPage();const pageErrors=[];page.on('pageerror',error=>pageErrors.push(error.message));
    await page.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
    await page.goto(ready.url);await page.getByRole('heading',{name:'Prepare the desk and seated envelope'}).waitFor();
    const binding={content_revision:'authored-desk',registration_version:1,manifest_digest:'a'.repeat(64),frame_digest:'b'.repeat(64),canonical_collision_digest:'c'.repeat(64)};
    const refs={frame:'z_up_metric_floor0',units:'m',floor:{id:'floor',normal:[0,0,1],offset_m:0},walls:[
      {id:'left',label:'Left',normal_xy:[1,0],offset_m:0},
      {id:'front',label:'Front',normal_xy:[0,1],offset_m:0},
      {id:'right',label:'Right',normal_xy:[-1,0],offset_m:-5}],confirmed:true,provenance:{kind:'authored-test'}};
    const equipment=Core.sealDocument(Core.createDocument(binding,refs)),raw=Buffer.from(JSON.stringify(equipment));
    assert.equal(Core.validateDocument(equipment,{binding}).ok,true);
    await page.locator('#equipment').setInputFiles({name:'authored-equipment.json',mimeType:'application/json',buffer:raw});
    await page.waitForFunction(()=>!document.getElementById('wall0').disabled);
    await page.locator('#referenceError').fill('.01');await page.locator('#thickness').fill('.02');await page.locator('#coverage').check();
    async function fill(id,rows){for(let i=0;i<rows.length;i++)for(let j=0;j<rows[i].length;j++)await page.locator('#'+id+' tr').nth(i).locator('input').nth(j).fill(String(rows[i][j]));}
    await fill('work',[[1,2,.75,.01],[2,3,.75,.01]]);await fill('envelope',[[.5,.5,0,.01],[2.5,2,1.8,.01]]);
    await page.locator('#prepare').click();await page.waitForFunction(()=>document.getElementById('status').textContent.startsWith('Candidate saved'));
    const candidate=JSON.parse(fs.readFileSync(output));
    assert.equal(candidate.qualified,false);assert.equal(candidate.operational_geometry,null);
    assert.deepEqual(candidate.original_survey.references.floor,refs.floor);
    assert.equal(candidate.original_survey.references.equipment_ref.file_sha256,createHash('sha256').update(raw).digest('hex'));
    assert.deepEqual(candidate.work_patch.center_m.slice(0,2),[1.5,2.5]);
    assert.equal(candidate.camera_registration.status,'unavailable');
    assert.deepEqual(pageErrors,[]);await page.locator('#stop').click();await page.waitForFunction(()=>document.getElementById('status').textContent.startsWith('Local guide stopped'));
    if(child.exitCode===null)await once(child,'exit');assert.equal(child.exitCode,0,errors);
    process.stdout.write('Actual generated equipment import -> Chromium form -> HTTP candidate save -> Stop PASS\n');
  }finally{
    clearTimeout(timeout);if(browser)await browser.close();if(child.exitCode===null){child.kill();await once(child,'exit');}
    // Only this generated test directory, created immediately above.
    const resolved=path.resolve(dir);assert.equal(path.dirname(resolved),__dirname);assert.match(path.basename(resolved),/^desk-geometry-form-/);
    fs.rmSync(resolved,{recursive:true,force:true});
  }
}
run().catch(error=>{process.stderr.write(String(error.stack)+'\n');process.exitCode=1});
