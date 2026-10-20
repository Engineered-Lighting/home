// Actual gateway + authenticated HTTPS loopback Lab with an ephemeral test CA.
// No production credentials, cameras, model sessions or external requests.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import https from 'node:https';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';

test('the mounted gateway reaches only the pinned Victoria HTTPS Lab with its private read grant', { timeout: 45000 }, async t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'home-lab-gateway-'));
  const key=path.join(root,'fixture-key.pem'),ca=path.join(root,'fixture-ca.pem'),tokenFile=path.join(root,'read-token');
  const token='authored-read-only-grant-00000000000000000000';
  const openssl=process.platform==='win32'?'C:\\Program Files\\Git\\usr\\bin\\openssl.exe':'openssl';
  execFileSync(openssl,['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=authored-lab.invalid',
    '-addext','subjectAltName=IP:127.0.0.1','-keyout',key,'-out',ca],{timeout:10000,stdio:'pipe',windowsHide:true});
  fs.writeFileSync(tokenFile,token,{mode:0o600});
  const upstreamCalls=[];
  const lab=https.createServer({key:fs.readFileSync(key),cert:fs.readFileSync(ca)},(req,res)=>{
    upstreamCalls.push({url:req.url,method:req.method,headers:Object.keys(req.headers)});
    assert.equal(req.headers.authorization,'Bearer '+token);assert.equal(req.headers['x-home-lab-site'],'victoria');
    assert.equal(req.method,'GET');assert.equal(req.headers.cookie,undefined);assert.equal(req.headers['x-forwarded-for'],undefined);
    if(req.url.endsWith('/media/image-1?generation=0')){
      assert.equal(req.headers.range,'bytes=0-3');res.writeHead(206,{'Content-Type':'image/png','Content-Range':'bytes 0-3/4'});res.end(Buffer.from([1,2,3,4]));return;
    }
    res.writeHead(200,{'Content-Type':'application/json'});
    res.end(JSON.stringify(req.url.endsWith('/activity-loop')?{schema:'perception-lab-activity-loop-1',state:'paused'}:
      {site_id:'victoria',recording_allowed:false,cameras:[],experiments:[]}));
  });
  await new Promise(resolve=>lab.listen(0,'127.0.0.1',resolve));
  const reservation=http.createServer();await new Promise(resolve=>reservation.listen(0,'127.0.0.1',resolve));
  const port=reservation.address().port;await new Promise(resolve=>reservation.close(resolve));
  const origin=`http://127.0.0.1:${port}`,repo=fileURLToPath(new URL('../',import.meta.url));
  const env=Object.fromEntries(Object.entries(process.env).filter(([name])=>!name.startsWith('HOME_WEB_')&&name!=='HOME_CONTENT_PACKS_DIR'));
  const child=spawn(process.execPath,['web-gateway/server.mjs'],{cwd:repo,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{
    ...env,NODE_ENV:'test',HOME_WEB_HOST:'127.0.0.1',HOME_WEB_PORT:String(port),HOME_WEB_AUTH_REQUIRED:'1',
    HOME_WEB_BASIC_AUTH:'fixture-owner:fixture-password',HOME_WEB_AUTH_FILE:path.join(root,'absent-auth'),
    HOME_WEB_HA_TOKEN_FILE:path.join(root,'absent-ha'),HOME_WEB_STACK_TOKEN_FILE:path.join(root,'absent-stack'),
    HOME_WEB_DEN_LAB_ORIGIN:`https://127.0.0.1:${lab.address().port}`,HOME_WEB_DEN_LAB_CA_FILE:ca,HOME_WEB_DEN_LAB_READ_TOKEN_FILE:tokenFile,
    HOME_WEB_AGENT_ORIGINS:'http://agent.fixture',HOME_WEB_ALLOW_INSECURE_TEST_AGENT_ORIGINS:'1',
  }});
  let diagnostics='';const collect=chunk=>{diagnostics=(diagnostics+chunk.toString()).slice(-4000);};
  child.stdout.on('data',collect);child.stderr.on('data',collect);
  t.after(async()=>{
    const exited=once(child,'exit').catch(()=>{});child.kill();
    await Promise.race([exited,new Promise(resolve=>setTimeout(resolve,2000))]);
    lab.closeAllConnections();await new Promise(resolve=>lab.close(resolve));
    fs.rmSync(root,{recursive:true,force:true});
  });
  const startupDeadline=Date.now()+15000;
  for(;;){
    try{if((await fetch(origin+'/healthz',{signal:AbortSignal.timeout(1000)})).ok)break;}catch{}
    if(Date.now()>=startupDeadline||child.exitCode!==null)assert.fail('authored gateway startup failed: '+diagnostics);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  const auth={Authorization:'Basic '+Buffer.from('fixture-owner:fixture-password').toString('base64'),'X-Home-Lab-Site':'victoria'};
  const base='/api/perception-lab/v1',request=(url,options={})=>fetch(origin+url,{signal:AbortSignal.timeout(7000),...options});
  assert.equal((await request(base+'/capabilities')).status,401);
  assert.equal((await request(base+'/capabilities',{headers:{Authorization:auth.Authorization}})).status,403);
  assert.equal((await request(base+'/capabilities',{headers:{...auth,'X-Home-Lab-Site':'echo'}})).status,403);
  const response=await request(base+'/capabilities',{headers:{...auth,'X-Forwarded-For':'untrusted','Cookie':'do-not-forward=1'}});
  assert.equal(response.status,200);assert.equal((await response.json()).site_id,'victoria');assert.match(response.headers.get('cache-control'),/private.*no-store/);
  assert.equal(response.headers.get('x-home-lab-access'),'read-only');
  const status=await request(base+'/activity-loop',{headers:auth});assert.equal(status.status,200);assert.equal((await status.json()).state,'paused');
  assert.equal((await request(base+'/capabilities',{headers:{...auth,Origin:origin}})).status,200,'same-origin read works');
  const image=await request(base+'/sessions/session-one/media/image-1?generation=0',{headers:{...auth,Range:'bytes=0-3'}});
  assert.equal(image.status,206);assert.deepEqual(Buffer.from(await image.arrayBuffer()),Buffer.from([1,2,3,4]));
  const before=upstreamCalls.length;
  for(const url of ['/sessions/session-one/stop','/activity-loop/resume','/jobs','/cameras/other/preview'])
    assert.equal((await request(base+url,{headers:auth})).status,404,url);
  assert.equal((await request(base+'/sessions',{method:'POST',headers:auth})).status,405);
  assert.equal((await request(base+'/capabilities',{headers:{...auth,Origin:'http://foreign.fixture'}})).status,403);
  assert.equal((await request(base+'/capabilities',{headers:{...auth,'Sec-Fetch-Site':'cross-site'}})).status,403);
  const agent=await new Promise((resolve,reject)=>{const req=http.get(origin+base+'/capabilities',{headers:{...auth,Host:'agent.fixture'}},res=>{
    res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);});
  assert.equal(agent,404);assert.equal(upstreamCalls.length,before,'refused reads never contact Lab');
  const alias=path.join(root,'token-hardlink');fs.linkSync(tokenFile,alias);
  assert.equal((await request(base+'/capabilities',{headers:auth})).status,503,'linked credential file refuses');fs.unlinkSync(alias);
  fs.writeFileSync(tokenFile,'invalid');assert.equal((await request(base+'/capabilities',{headers:auth})).status,503);
  assert.equal(upstreamCalls.length,before,'invalid credentials do not fall back to a stack/admin grant');
  assert.ok(upstreamCalls.every(call=>call.url.startsWith(base+'/')));
});
