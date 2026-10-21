import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {aimWiringProof,FILE_TIMEOUT_MS} from './fixtures/apartment-aim-wiring-harness.mjs';
import {bodyPerson,estimatedLowerBody} from './fixtures/body-form-fixtures.mjs';
import {bodyFormTrackingService} from './fixtures/body-form-tracking-service.mjs';

const renderer=process.env.HOME_BODY_RENDERER||'webgl',fixture=bodyFormTrackingService();
// Keep the native full-size proof. The CPU renderer gets the same desktop
// layout at 960x600 so a large software canvas does not invalidate every
// clock probe before the stream can open. All timing/expiry gates stay intact.
const softwareFixture=process.platform==='linux'||process.env.HOME_BODY_SOFTWARE==='1';
test('mounted Home body forms preserve controls, actual drawing, partial evidence and withdrawal',{timeout:FILE_TIMEOUT_MS},()=>
  aimWiringProof('body-form-'+renderer,async({page,check,until,artifactDir,receipt,ids})=>{
    const actual=await page.evaluate(()=>({backend:window.__APT_ENGINE.rendererBackend,gpu:window.__APT_ENGINE.gpu,node:window.__APT_ENGINE.webgpuReadback?.()}));
    assert.equal(actual.backend,renderer==='webgpu-webgl'?'webgpu-webgl2':renderer,'qualify the actual renderer, not the requested backend');
    if(process.env.HOME_BODY_SOFTWARE==='1')assert.match(actual.gpu,/SwiftShader|llvmpipe|software/i,'the controlled software repro must actually use software rendering');
    receipt.body_renderer=actual;
    const read=()=>page.evaluate(()=>window.__APT_ENGINE.fastTrackingReadback());
    const still=async name=>{if(process.env.HOME_BODY_STILLS==='1'){
      receipt.screenshots=true;await page.screenshot({path:path.join(artifactDir,name+'.png')});}};
    await check('standing and seated authored poses reach actual GPU-completed Home draws',async()=>{
      await until('body GPU receipt',()=>window.__APT_ENGINE.fastTrackingReadback?.().receipts.some(r=>r.bodyForm?.status==='complete'&&!r.expired_at_completion));
      assert.equal(await page.locator('[data-fast-tracking]').isVisible(),true,'the real camera-dock status mounts');
      assert.match(await page.locator('[data-fast-tracking]').innerText(),/Body form/);
      const pixels=()=>page.evaluate(async()=>{const frame=await window.__APT_ENGINE.captureFrame({scale:.5});
        if(!frame)throw Error('actual Home frame unavailable');return {width:frame.width,height:frame.height,pixels:[...frame.pixels]};});
      const shown=await pixels();let absent;const beforeReference=(await read()).sequence;
      await page.evaluate(async()=>{
        const THREE=await import('three'),engine=window.__APT_ENGINE;
        // One large triangle avoids testing an MSAA crack along a quad's
        // internal diagonal instead of testing the body's depth behavior.
        const geometry=new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute([-20,-20,0,60,-20,0,-20,60,0],3));
        const wall=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:0x135724,side:THREE.DoubleSide}));
        wall.position.copy(engine.camera.getWorldPosition(new THREE.Vector3())).addScaledVector(engine.camera.getWorldDirection(new THREE.Vector3()),.5);
        wall.quaternion.copy(engine.camera.getWorldQuaternion(new THREE.Quaternion()));
        // Isolate the depth experiment from animated survey glows (which
        // intentionally draw through depth). Full-room visibility is checked
        // separately above/below, with every object restored afterward.
        wall.userData.restoreVisibility=[];
        for(const object of [...engine.apartmentRoot.parent.children,...engine.apartmentRoot.children]){
          if(object===engine.apartmentRoot||object.name==='home-fast-tracking-owned')continue;
          wall.userData.restoreVisibility.push([object,object.visible]);object.visible=false;
        }
        engine.apartmentRoot.parent.add(wall);window.__authoredBodyOccluder=wall;
      });
      try{
        const coveredShown=await pixels();
        // Change only body drawing, not producer/UI state or camera layout.
        await page.evaluate(()=>{window.__APT_ENGINE.apartmentRoot.getObjectByName('home-fast-tracking-owned').visible=false;});
        const coveredAbsent=await pixels();
        assert.equal(coveredShown.width,coveredAbsent.width);assert.equal(coveredShown.height,coveredAbsent.height);
        const max=coveredShown.pixels.reduce((n,v,i)=>Math.max(n,Math.abs(v-coveredAbsent.pixels[i])),0);
        if(max>1){
          const channelMax=[0,0,0,0],counts=[0,0,0,0],samples=[];
          for(let i=0;i<coveredShown.pixels.length;i++){
            const d=Math.abs(coveredShown.pixels[i]-coveredAbsent.pixels[i]);channelMax[i%4]=Math.max(channelMax[i%4],d);
            if(d>1){counts[i%4]++;if(samples.length<24&&i%4===0)samples.push({pixel:i/4,a:coveredShown.pixels.slice(i,i+4),b:coveredAbsent.pixels.slice(i,i+4)});}
          }
          fs.writeFileSync(path.join(artifactDir,'depth-diff.json'),JSON.stringify({width:coveredShown.width,height:coveredShown.height,channelMax,counts,samples},null,2));
        }
        assert.ok(max<=1,'an opaque room-depth surface must fully occlude the body: '+max);
      }finally{
        await page.evaluate(()=>{const wall=window.__authoredBodyOccluder;
          window.__APT_ENGINE.apartmentRoot.getObjectByName('home-fast-tracking-owned').visible=true;
          for(const [object,visible] of wall.userData.restoreVisibility)object.visible=visible;
          wall.removeFromParent();wall.geometry.dispose();wall.material.dispose();delete window.__authoredBodyOccluder;});
      }
      await page.evaluate(()=>{window.__APT_ENGINE.apartmentRoot.getObjectByName('home-fast-tracking-owned').visible=false;});
      try{absent=await pixels();}finally{await page.evaluate(()=>{window.__APT_ENGINE.apartmentRoot.getObjectByName('home-fast-tracking-owned').visible=true;});}
      assert.equal(shown.width,absent.width);assert.equal(shown.height,absent.height);
      const changed=shown.pixels.reduce((n,v,i)=>n+(i%4!==3&&Math.abs(v-absent.pixels[i])>8?1:0),0);
      assert.ok(changed>75,'body changes actual rendered pixels, not just scene state: '+changed);
      await until('body draw after pixel reference',n=>window.__APT_ENGINE.fastTrackingReadback().receipts.at(-1)?.bodyForm?.status==='complete'
        &&window.__APT_ENGINE.fastTrackingReadback().receipts.at(-1).sequence>n
        &&!window.__APT_ENGINE.fastTrackingReadback().receipts.at(-1).expired_at_completion
        &&window.__APT_ENGINE.fastTrackingReadback().bodyForm.parts>0,beforeReference);
      await still('standing-home');
      const before=(await read()).sequence;fixture.setPeople([bodyPerson({seated:true,offset:[1.2,1.4,0]})]);
      await until('seated body GPU receipt',n=>window.__APT_ENGINE.fastTrackingReadback().receipts.some(r=>r.sequence>n&&r.bodyForm?.status==='complete'),before);
      const drawn=await page.evaluate(()=>{const root=window.__APT_ENGINE.apartmentRoot.getObjectByName('home-fast-tracking-owned');
        const thigh=root.getObjectByName('body-form-left-thigh');return {meshes:root.children.filter(m=>m.visible).length,
          thigh:thigh.position.toArray(),depth:root.children.filter(m=>m.visible).every(m=>m.material.depthTest)};});
      assert.ok(drawn.meshes>10);assert.ok(Math.abs(drawn.thigh[2]-.53)<.02,'seated thigh stays horizontal above the floor');assert.equal(drawn.depth,true);
      await still('seated-home');return {authored:true,actual_gpu_receipt:true,body_meshes:drawn.meshes,
        visible_pixel_difference:changed,opaque_depth_occlusion:true};
    });
    await check('partial, inferred and two-person forms never inherit missing limbs from another detection',async()=>{
      const partial=bodyPerson({offset:[1.2,1.4,0]});partial.joints=partial.joints.filter(j=>[0,5,6,7,9].includes(j.id));
      fixture.setPeople([partial]);
      await until('partial form',()=>window.__APT_ENGINE.fastTrackingReadback().receipts.at(-1)?.bodyForm?.status==='partial');
      assert.equal((await read()).bodyForm.estimatedJoints,0);await still('partial-home');
      fixture.setPeople([estimatedLowerBody(bodyPerson({seated:true,offset:[1.2,1.4,0]}))]);
      await until('estimated pose',()=>window.__APT_ENGINE.fastTrackingReadback().receipts.at(-1)?.bodyForm?.status==='estimated'
        &&document.querySelector('[data-fast-tracking]')?.textContent.includes('estimated pose'));
      assert.match(await page.locator('[data-fast-tracking]').innerText(),/estimated pose/);await still('estimated-home');
      fixture.setPeople([bodyPerson({offset:[1.4,2,0]}),bodyPerson({seated:true,offset:[2.7,2,0],id:'new-frame-crop'})]);
      await until('two forms',()=>window.__APT_ENGINE.fastTrackingReadback().receipts.at(-1)?.bodyForm?.people===2);await still('two-people-home');
      fixture.setPeople([{instance_id:'new-frame-crop',joints:[]}]);
      await until('no support',()=>window.__APT_ENGINE.fastTrackingReadback().bodyForm.parts===0);
      await page.locator('[data-fast-tracking]').filter({hasText:'Body unavailable'}).waitFor();
      assert.match(await page.locator('[data-fast-tracking]').innerText(),/Body unavailable/);
      return {frame_scoped:true,hidden_joints_not_measured:true};
    });
    await check('expiry and scene mismatch withdraw the body; normal fixture controls recover unchanged',async()=>{
      fixture.setPeople([bodyPerson({offset:[2,2,0]})]);await until('restored form',()=>window.__APT_ENGINE.fastTrackingReadback().bodyForm.parts>0);
      for(const mode of ['stale','wrong_scene','unavailable']){
        fixture.setMode(mode);await until('withdrawn '+mode,()=>window.__APT_ENGINE.fastTrackingReadback().bodyForm.parts===0);
        fixture.setPeople([bodyPerson({offset:[2,2,0]})]);await until('recovery '+mode,()=>window.__APT_ENGINE.fastTrackingReadback().bodyForm.parts>0);
      }
      await page.locator(`[data-apt-fixture-label="${ids[0]}"]`).click();
      await page.getByRole('tab',{name:'Aim',exact:true}).click();
      if(await page.locator('[data-apt-aim-fine-tune]').count())await page.locator('[data-apt-aim-fine-tune] > summary').click();
      assert.equal(await page.getByRole('slider',{name:'Pan',exact:true}).isVisible(),true,'the existing Home inspector stays usable');
      const final=await read();assert.ok(final.receipts.some(r=>r.bodyForm.parts>0));
      assert.ok(final.diagnostics.events.some(e=>e.reason==='evidence_expired'));
      assert.ok(final.diagnostics.events.some(e=>e.reason==='scene_binding'));
      receipt.body_form={renderer,authored_only:true,source_frames_not_interpolated:true,live_acceptance:false};
      fs.writeFileSync(path.join(artifactDir,'body-form-readback.json'),JSON.stringify(final,null,2));
      return {expiry_preserved:true,scene_binding_preserved:true,normal_controls:true};
    });
    await check('home switching disposes the old form and reconnects only to the new current room',async()=>{
      await page.evaluate(()=>{window.__bodyPriorEngine=window.__APT_ENGINE;});
      await page.getByRole('group',{name:'Home',exact:true}).getByRole('button',{name:'Echo Park',exact:true}).click();
      await until('Echo ready',()=>window.__APT_ENGINE?.siteId==='echo');
      await until('old body disposed',()=>window.__bodyPriorEngine.fastTrackingReadback===undefined);
      assert.equal(await page.evaluate(()=>window.__bodyPriorEngine.apartmentRoot.getObjectByName('home-fast-tracking-owned')===undefined),true);
      await page.getByRole('group',{name:'Home',exact:true}).getByRole('button',{name:'Victoria',exact:true}).click();
      await until('new Victoria draws its own form',()=>window.__APT_ENGINE?.siteId==='victoria'
        &&window.__APT_ENGINE!==window.__bodyPriorEngine&&window.__APT_ENGINE.fastTrackingReadback?.().receipts.length>0);
      await page.evaluate(()=>{delete window.__bodyPriorEngine;});
      return {prior_engine_disposed:true,current_room_only:true};
    });
  },{trackingFixture:fixture,renderer,browserChannel:process.env.HOME_BODY_BROWSER_CHANNEL||null,
    viewport:softwareFixture?{width:960,height:600}:{width:1366,height:768}}));
