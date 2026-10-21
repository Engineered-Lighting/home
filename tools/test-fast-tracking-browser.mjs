// Authored data only. Real browser, real Home tracking layer and GPU fence.
// This validates the measurement seam; its timing is not live camera evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {bodyPerson} from './fixtures/body-form-fixtures.mjs';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../app/src');
const now=()=>Number(process.hrtime.bigint())/1e9;
const backend=process.env.DEN_FAST_PROOF_BACKEND||'webgl';
assert.ok(['webgl','webgpu','webgpu-webgl'].includes(backend));
test('actual Home joint draw produces GPU-completed receipts at the authored stream rate without images',{timeout:30000},async()=>{
  const sockets=new Set();let timer;
  const server=http.createServer((req,res)=>{
    if(req.url==='/api/perception-tracking/v1/clock'){
      res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify({attempt_id:'c'.repeat(32),monotonic_s:now()}));return;
    }
    if(req.url==='/api/perception-tracking/v1/stream'){
      assert.equal(req.headers['x-home-lab-site'],'victoria');res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store'});let sequence=0;
      timer=setInterval(()=>{
        const time=now();sequence++;
        const packet={schema:'home-fast-tracking-1',attempt_id:'c'.repeat(32),sequence,status:'available',frame_key:sequence.toString(16).padStart(64,'0'),
          identity:{epoch:'authored-capture'},scene:{siteId:'victoria',contentRevision:'authored',registrationVersion:1,frameDigest:'a'.repeat(64),collisionDigest:'b'.repeat(64),cameraId:'den'},
          lighting_authority:false,physical_permission:false,identity_authority:false,projection:'unqualified_nominal_eeprom',label:'Approximate room placement',
          source_la_monotonic_interval_s:[time-.04,time-.03],expires_monotonic_s:time+.71,
          source_clock:'authored_test_clock',exposure_accuracy:'authored',people:[bodyPerson({offset:[Math.sin(sequence)*.2,0,0]})]};
        res.write('data: '+JSON.stringify(packet)+'\n\n');
      },100);req.on('close',()=>clearInterval(timer));return;
    }
    if(req.url==='/'){
      res.writeHead(200,{'Content-Type':'text/html'});res.end(`<html><script type="importmap">{"imports":{"three":"/vendor/${backend==='webgl'?'three-0.180.0/three.module.js':'three-0.186.1/three.webgpu.js'}"}}</script><script type="module">
        import * as THREE from 'three';import {attachFastTracking} from '/home-3d/fast-tracking-layer.mjs';
        const renderer=${backend==='webgl'?'new THREE.WebGLRenderer({antialias:false})':`new THREE.WebGPURenderer({antialias:false,forceWebGL:${backend==='webgpu-webgl'}})`};
        ${backend==='webgl'?'':'await renderer.init();'}renderer.setSize(500,400);document.body.append(renderer.domElement);
        const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(50,1.25,.1,10);camera.position.z=3;
        camera.lookAt(0,.8,0);const apartmentRoot=new THREE.Group();apartmentRoot.rotation.x=-Math.PI/2;scene.add(apartmentRoot);
        const identity={siteId:'victoria',contentRevision:'authored',registrationVersion:1,frameDigest:'a'.repeat(64)};
        window.HomeContentPacks={get:()=>({manifest:{assets:{'collision.glb':{sha256:'b'.repeat(64)}}}})};
        const engine={siteId:'victoria',contentRevision:'authored',sceneIdentity:identity,apartmentRoot,renderer};
        window.layer=attachFastTracking(engine);window.engine=engine;
        function render(){requestAnimationFrame(render);renderer.render(scene,camera);}render();
      </script></html>`);return;
    }
    const pathname=new URL(req.url,'http://localhost').pathname;
    if(!/^\/(?:home-3d\/(?:fast-tracking-layer|body-form-model|body-form-renderer)\.mjs|vendor\/three-0\.(?:180\.0|186\.1)\/three\.(?:module|core|webgpu)\.js)$/.test(pathname)){res.writeHead(404);res.end();return;}
    const bytes=fs.readFileSync(path.join(root,pathname));res.writeHead(200,{'Content-Type':'application/javascript'});res.end(bytes);
  });
  server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,channel:'msedge',args:['--use-angle=d3d11','--disable-background-networking','--enable-unsafe-webgpu','--ignore-gpu-blocklist']});
    const page=await browser.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:'+server.address().port);
    await page.waitForFunction(()=>window.layer?.readback().receipts.length>=15,null,{timeout:12000});
    const result=await page.evaluate(()=>({readback:window.layer.readback(),draws:window.engine.renderer.info.render.calls,
      imageReads:performance.getEntriesByType('resource').filter(r=>/frame|png|jpe?g/.test(r.name)).length}));
    assert.deepEqual(errors,[]);assert.equal(result.imageReads,0);assert.ok(result.draws>0);
    assert.ok(result.readback.updateHz>=7&&result.readback.updateHz<=13,JSON.stringify(result.readback));
    // The source interval is 30--40 ms before send. A clock probe adds its
    // full RTT to the uncertainty, so its conservative lower bound may be
    // below 30 ms; asserting a 25 ms lower bound would discard honest clocks.
    assert.ok(result.readback.receipts.every(r=>r.joints===17&&r.bodyForm.status==='complete'&&r.camera_to_gpu_ms[0]>=0
      &&r.camera_to_gpu_ms[1]>=30&&r.camera_to_gpu_ms[1]<500&&r.camera_to_gpu_ms[1]>=r.camera_to_gpu_ms[0]),
      JSON.stringify(result.readback.receipts.filter(r=>r.joints!==17||r.camera_to_gpu_ms[0]<0||r.camera_to_gpu_ms[1]>=500)));
    console.log(JSON.stringify({evidence:'authored_data_real_browser_gpu_only',backend,rendered_updates:result.readback.receipts.length,render_hz:result.readback.updateHz,live_camera_proof:false}));
    await page.evaluate(()=>window.layer.dispose());
  }finally{await browser?.close();clearInterval(timer);for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));}
});
