// Authored offline file only; no owner data or network service.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
test('owner records independent held-pose references with predictions hidden',{timeout:30000},async()=>{
  const root=path.resolve(__dirname,'../..'),testRoot=path.join(root,'artifacts','authored-assessment-test-work');
  fs.mkdirSync(testRoot,{recursive:true});const scratch=fs.mkdtempSync(path.join(testRoot,'reference-editor-'));
  const input=path.join(scratch,'authored-measurements.json');
  fs.writeFileSync(input,JSON.stringify({schema:'home-live-assessment-measurements-1',version:1,
    binding:{session_ref:{session_id:'authored-session'},source_id:'authored-oak',configuration_digest:'a'.repeat(64)},
    records:[{secret_estimated_position:'THIS_PREDICTION_MUST_STAY_HIDDEN'}]}));
  let browser;
  try{
    browser=await chromium.launch({headless:true,args:['--disable-background-networking'],...(!fs.existsSync(chromium.executablePath())?{channel:'msedge'}:{})});
    const page=await browser.newPage();const external=[],errors=[];
    page.on('request',request=>{if(/^https?:/.test(request.url()))external.push(request.url());});
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(pathToFileURL(path.join(__dirname,'reference-editor.html')).href);
    await page.locator('#measurements').setInputFiles(input);
    await page.locator('#binding').filter({hasText:'authored-session'}).waitFor();
    assert.equal((await page.locator('body').innerText()).includes('THIS_PREDICTION_MUST_STAY_HIDDEN'),false);
    for(const [id,value]of Object.entries({protocol:'independent-survey',digest:'b'.repeat(64),windowId:'held-one',start:'100',end:'102',uncertainty:'.02'}))await page.locator('#'+id).fill(value);
    await page.locator('#activity').selectOption('focused');await page.locator('#work-state').selectOption('available');
    for(const axis of ['x','y','z']){await page.locator('#work-center-'+axis).fill('0');await page.locator('#work-half-'+axis).fill('.5');}
    await page.getByRole('button',{name:'Add independent interval'}).click();
    assert.match(await page.locator('#count').innerText(),/^1 /);
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'Save reference JSON locally'}).click();
    const file=await download;const output=path.join(scratch,'references.json');await file.saveAs(output);
    const references=JSON.parse(fs.readFileSync(output,'utf8'));
    assert.equal(references.truth_role,'independent_evaluation_reference');
    assert.equal(references.windows[0].parts.work.status,'available');
    assert.equal(references.windows[0].parts.head.status,'unknown');
    assert.equal(references.requirements.min_reference_s,null);
    await page.screenshot({path:path.join(root,'artifacts','authored-assessment-reference-editor.png'),fullPage:true});
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  }finally{
    if(browser)await browser.close();
    if(!path.resolve(scratch).startsWith(path.resolve(testRoot)+path.sep))throw Error('authored cleanup escaped workspace');
    fs.rmSync(scratch,{recursive:true,force:true});
  }
});
