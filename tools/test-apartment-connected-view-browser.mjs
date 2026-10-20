// Full mounted Home + actual read transports/player/Three layer/follower.
// All HTTP, media, room, camera, model and coordinator inputs are authored.
// This is fixture acceptance, never actual-room or live-stack acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { runFullUiShell } from './fixtures/home-full-ui-harness.mjs';
import { authoredLightingLayer } from './fixtures/apartment-lit-layer-fixtures.mjs';
const fixtureBytes = fs.readFileSync(new URL('./fixtures/apartment-connected-view.json', import.meta.url));
const fixture = JSON.parse(fixtureBytes);
const boundBrain = JSON.parse(fs.readFileSync(new URL('../docs/contracts/den/fixtures/brain-state-reading.json', import.meta.url)));
const ids = ['victoria-den-proposed-fixture-01', 'victoria-den-proposed-fixture-02',
  'victoria-den-mount-03-virtual-fixture', 'victoria-den-mount-04-virtual-fixture'];
const digest = value => createHash('sha256').update(Buffer.from(value, 'base64')).digest('hex');

test('connected Apartment: actual viewer joins authored body, camera playback, scoped Qwen/JEV and coordinator-driven rendering', { timeout: 145000 }, async () => {
  await runFullUiShell('apartment-connected-view-browser', async ({page, origin, openShell, waitSite, homes, checks, receipt, inViewport, victoria}) => {
    const calls = [], state = { loop: 'running', qwen: 0, advanceQwen: false, cameraFailed: false, brain: 0 };
    const layer = authoredLightingLayer(victoria);
    await page.context().route('**/content-packs/**',route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==origin||!url.pathname.startsWith(layer.base))return route.fallback();
      assert.equal(request.method(),'GET');
      const bytes=layer.files[url.pathname.slice(layer.base.length)];
      assert.ok(bytes,'only declared authored lighting files are requested');
      return route.fulfill({status:200,contentType:url.pathname.endsWith('.json')?'application/json':'application/octet-stream',body:bytes});
    });
    const body = fixture.body, sid = body.live.session.id, graph = structuredClone(body.graph);
    Object.assign(graph.arms.b.chain.find(row=>row.stage==='result'),{status:'completed',activity:'reading',
      request_id:'authored-jev-request',output_event_id:42,source_end_s:.4,available_at_s:.7,
      available_at:1800000000.7,valid_until:1800000002,freshness:'fresh',model_version:'authored-jev-v1'});
    // Each decision owns immutable source clocks. Repeated GET/SSE delivery
    // must never mint a new lease from its arrival time.
    const decisions = new Map();
    const brain = () => {
      if (decisions.has(state.brain)) return decisions.get(state.brain);
      const doc = structuredClone(boundBrain), now = Date.now()/1000, shift = now - doc.generated_at;
      for (const source of [doc.reading_evidence.activity, doc.reading_evidence.pose]) {
        for (const key of ['interval_start','observed_at','available_at','valid_until']) source[key] += shift;
      }
      doc.generated_at = now; doc.state_revision += state.brain;
      doc.decision_id = 'authored-brain:' + state.brain; doc.why = 'Authored coordinator decision ' + state.brain;
      const lease = state.brain === 4 ? 2 : 10;
      doc.reading_evidence.activity_valid_until = doc.reading_evidence.pose_valid_until = now + lease;
      doc.reading_evidence.activity.valid_until = doc.reading_evidence.pose.valid_until = now + lease;
      if (state.brain === 2) delete doc.reading_evidence;
      doc.fixtures = ids.map(id => ({fixture_id: id, ambient: {radial_level: .1, down_level: .1, cct_k: 3000, fade_s: 0},
        spot: {on: true, level: state.brain ? .6 : .25, cct_k: 3000, fade_s: 0, target_m: [state.brain ? 2.5 : 1.5, 2, .7]},
        paused_until: null, pause_reason: null}));
      doc.decisions = []; decisions.set(state.brain, doc); return doc;
    };
    // A real open HTTP event stream: a finite fulfilled body would test a
    // disconnection instead of coordinator following and original expiry.
    const streams = new Set();
    const streamServer = http.createServer((req,res) => {
      assert.equal(req.method,'GET');
      res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store'});
      const send = () => res.write('event: state\ndata: ' + JSON.stringify(brain()) + '\n\n');
      send(); const timer = setInterval(send,250); streams.add(res);
      res.on('close',()=>{clearInterval(timer);streams.delete(res);});
    });
    await new Promise(resolve=>streamServer.listen(0,'127.0.0.1',resolve));
    const streamUrl = 'http://127.0.0.1:' + streamServer.address().port + '/stream';
    try {
    await page.context().route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== origin) return route.fallback();
      const p = url.pathname;
      if (!p.startsWith('/api/perception-lab/v1/') && !p.startsWith('/api/lighting-brain/') && p !== '/api/den-ops/v1/status') return route.fallback();
      calls.push({path: p, method: request.method()});
      assert.equal(request.method(), 'GET', 'connected viewer cannot mutate any service');
      const json = (value, status = 200) => route.fulfill({status, contentType: 'application/json',
        headers: {'X-Home-Lab-Access': 'read-only'}, body: JSON.stringify(value)});
      if (p.endsWith('/homes/victoria/state')) return json(brain());
      if (p.endsWith('/homes/victoria/stream')) return route.continue({url:streamUrl});
      if (p === '/api/den-ops/v1/status') return json({schema:'den-ops-banner-1',banner:null});
      const name = p.slice('/api/perception-lab/v1/'.length);
      if (name === 'capabilities') return json({site_id:'victoria',cameras:[{id:'den'}],live_previews:{den:false},live_videos:{den:true},
        recording_allowed:true,experiments:[],apartment_viewing:{provisioned:false}});
      if (name === 'activity-loop') return json({schema:'perception-lab-activity-loop-1',state:state.loop,
        stopped_reason:state.loop === 'paused' ? 'authored_resource_backoff' : null,
        deep_stack:{session_id:sid,producer_epoch:'a'.repeat(32),wanted:true,rolls:0}});
      if (name === 'sessions') return json({site_id:'victoria',items:[body.live.session]});
      if (name === 'sessions/' + sid) return json({session:body.live.session});
      if (name === 'sessions/' + sid + '/live') return json(body.live);
      if (name === 'sessions/' + sid + '/graph') return json(graph);
      if (name === 'sessions/' + sid + '/events') return json({items:[],after_id:Number(url.searchParams.get('after_id'))});
      if (name === 'sessions/' + sid + '/media/' + body.descriptor.media_id + '/descriptor') return json(body.descriptor);
      if (name === 'sessions/' + sid + '/media/' + body.descriptor.media_id) {
        const pixels = Buffer.from(body.png_base64,'base64');
        const match = /^bytes=(\d+)-(\d+)$/.exec(request.headers().range || '');
        assert.ok(match,'the actual media transport requests an explicit bounded range');
        const start = Number(match[1]), end = Math.min(Number(match[2]),pixels.length-1);
        return route.fulfill({status:206,contentType:'image/png',headers:{'Content-Range':`bytes ${start}-${end}/${pixels.length}`},body:pixels.subarray(start,end+1)});
      }
      if (name === 'cameras/den/video') {
        if (state.cameraFailed) return json({error:{code:'authored_camera_unavailable'}},503);
        const cursor = Number(url.searchParams.get('after_sequence') || 0), camera = fixture.camera;
        return json({version:1,site_id:'victoria',camera_id:'den',recorded:false,generation:'b'.repeat(32),
          timestamp_semantics:'remux_receipt_only',exposure_timing:'unverified',receipt_age_seconds:0,
          codec_mime:camera.codec_mime,init_base64:cursor === 0 ? camera.init_base64 : null,
          fragments:camera.fragments.filter(row=>row.sequence>cursor),reset:false});
      }
      if (name === 'cameras/den/structured-context') {
        const value = fixture.qwen[state.qwen];
        if (state.advanceQwen) state.qwen = Math.min(state.qwen+1,fixture.qwen.length-1);
        return json(value);
      }
      if (name === 'cameras/den/context') return json({site_id:'victoria',camera_id:'den',status:'missing'});
      return route.fallback();
    });
    receipt.connected = {authored_inputs:true,actual_producer_fixture:true,actual_camera_decoder:true,actual_renderer:true,
      household_data:false,physical_commands:false,fixture_sha256:createHash('sha256').update(fixtureBytes).digest('hex'),calls};
    await openShell('?apartmentRedesign=1&worldZoom=off&den-lighting-evidence=1'); await waitSite('echo');
    await homes().getByRole('button',{name:'Victoria',exact:true}).click(); await waitSite('victoria');
    const scene = await page.evaluate(()=>window.__APT_ENGINE.sceneIdentity);
    for (const key of ['siteId','contentRevision','registrationVersion','frameDigest']) assert.equal(scene[key],fixture.scene[key], 'frozen producer fixture matches authored mounted scene ' + key);
    const dock = page.getByRole('complementary',{name:'Den camera dock',exact:true});
    await dock.getByRole('button',{name:'View perception',exact:true}).click();
    await dock.getByRole('button',{name:'View activity loop',exact:true}).click();
    await page.locator('[data-apartment-approximate-badge="1"]').waitFor();
    const bodyState = await page.evaluate(()=>{
      const group=window.__APT_ENGINE.apartmentRoot.getObjectByName('home-apartment-perception-owned');
      const joints=group?.getObjectByName('perception-supplied-joints');
      return {count:joints?.geometry.attributes.position.count,positions:joints?Array.from(joints.geometry.attributes.position.array):[]};
    });
    assert.equal(bodyState.count,body.packet.approximate_pose.joints.length,'only actually supplied viewing joints reach the real Three layer');
    const supplied=body.packet.approximate_pose.joints.flatMap(row=>row.position_m);
    assert.ok(bodyState.positions.every((value,index)=>Math.abs(value-supplied[index])<1e-6),'the mounted body layer retains the actual supplied positions');
    assert.equal(await dock.getByRole('button',{name:'Follow',exact:true}).isDisabled(),true);
    const video = dock.getByRole('region',{name:'Live Den camera',exact:true}).getByLabel('Live Den camera video',{exact:true});
    await page.waitForFunction(()=>{const video=document.querySelector('video[aria-label="Live Den camera video"]');return video?.readyState>=2&&video.videoWidth===320;});
    const before = await video.evaluate(node=>node.currentTime);
    await page.waitForFunction(before=>document.querySelector('video[aria-label="Live Den camera video"]')?.currentTime>before+.1,before);
    assert.equal(await dock.locator('[data-dock-live="1"] svg[aria-label="Exact-frame pose overlay"]').count(),0,'analyzed pose is never painted on independent video');
    const qwen = dock.getByRole('region',{name:'Structured camera facts',exact:true});
    await qwen.getByText(/Qwen · artifact/).waitFor();
    await qwen.getByRole('group',{name:'Recent Qwen updates',exact:true}).locator('summary').first().click();
    await qwen.locator('[data-qwen-update]').first().waitFor();
    assert.match(await qwen.innerText(),/Daily perceiver.*separate from Lab recordings/);
    const jevHistory=dock.getByRole('region',{name:'Recent model updates',exact:true}).getByText(/jev.*recorded update.*completed/i);
    await jevHistory.waitFor();assert.equal(await jevHistory.count(),1,'one supplied JEV result remains one history entry after the frame arrives');
    checks.push({stage:'actual_viewer_decodes_authored_camera_and_draws_supplied_body',bodyState});
    // The original approximate receipt retires even if the same live packet is polled again.
    await page.locator('[data-apartment-approximate-badge="1"]').waitFor({state:'hidden',timeout:5000});
    assert.equal(await jevHistory.count(),1,'repeated polling and display expiry cannot invent producer updates');
    const readFixture = () => page.evaluate(id=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id),ids[0]);
    await page.waitForFunction(id=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.state.spotlight.brightness===.25,ids[0]);
    const first = await readFixture();
    await page.getByRole('button',{name:'Lighting',exact:true}).click();
    await page.waitForFunction(()=>{
      const s=window.__APT_ENGINE.litState();
      return s.active&&s.frame?.active&&s.frame.format==='RGBA16F'&&s.frame.frames>0&&s.receivers?.blends>0;
    });
    const lighting=await page.evaluate(()=>window.__APT_ENGINE.litState());
    state.brain = 1;
    await page.waitForFunction(id=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.state.spotlight.brightness===.6,ids[0]);
    const second = await readFixture();
    assert.notDeepEqual(second.target,first.target,'the actual follower changes virtual aim from coordinator messages');
    // Observe the ordinary visible submission before starting an offscreen
    // readback, which can temporarily occupy the renderer on software GL.
    // A diagnostic capture must never substitute for this normal-frame proof.
    try {
      await page.waitForFunction(()=>window.HomeDenLightingEvidence?.readback().events.some(row=>row.stage==='gpu_completed_frame'&&row.decision_id==='authored-brain:1'));
    } catch(error) {
      receipt.connected.render_failure=await page.evaluate(()=>({evidence:window.HomeDenLightingEvidence?.readback(),
        lighting:window.__APT_ENGINE?.litState?.()})).catch(()=>null);
      throw error;
    }
    const renderEvidence = await page.evaluate(()=>window.HomeDenLightingEvidence.readback());
    const capture = await page.evaluate(async()=>{const frame=await window.__APT_ENGINE.captureFrame({scale:.25});return frame&&{width:frame.width,height:frame.height,bytes:frame.pixels.length};});
    assert.ok(capture?.width>0&&capture.bytes===capture.width*capture.height*4,'the real renderer completes a frame after coordinator application');
    assert.ok(renderEvidence.events.filter(row=>row.stage==='gpu_completed_frame').every(row=>row.presentation_observed===false&&row.physical_commands===false));
    assert.equal(lighting.frame.format,'RGBA16F','the normal-frame receipt follows the actual composed HDR render');
    checks.push({stage:'authored_coordinator_updates_virtual_state_aim_and_completed_hdr_render',capture,renderEvidence,lighting});
    state.brain = 2;
    await page.waitForFunction(id=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.state.spotlight.on===false,ids[0]);
    await page.waitForFunction(()=>window.HomeDenLightingEvidence.readback().events.some(row=>row.stage==='offline_spot_stop'&&row.reason==='state_reading_evidence_unbound'));
    state.brain = 3;
    await page.waitForFunction(id=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.state.spotlight.on===true,ids[0]);
    // A new decision with the same output must not renew an old render's lease.
    state.brain = 4;
    await page.waitForFunction(id=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.state.spotlight.on===false,ids[0]);
    assert.ok(Date.now()/1000>=decisions.get(4).reading_evidence.pose_valid_until);
    const replayed = structuredClone(decisions.get(4));
    await page.waitForTimeout(500);
    assert.deepEqual(decisions.get(4),replayed,'repeated stream messages retain the original evidence times');
    assert.equal((await readFixture()).state.spotlight.on,false,'expired authority cannot be revived by replay');
    checks.push({stage:'bound_reading_missing_expired_and_replayed_authority_refused',original:replayed});
    state.advanceQwen = true;
    const latest = fixture.qwen.at(-1).latest.receipt;
    await qwen.locator('[data-qwen-update="' + latest.request_id + '"]').waitFor({timeout:40000});
    const received = await qwen.locator('[data-qwen-update]').evaluateAll(nodes=>nodes.map(node=>node.dataset.qwenUpdate));
    assert.equal(new Set(received).size,20,'clock ticks and duplicate responses do not duplicate model history');
    await qwen.locator('[data-qwen-update]').first().locator('summary').click();
    assert.match(await qwen.innerText(),/browser receipt cadence, not capture or inference latency/);
    assert.match(await qwen.innerText(),/Source scope: perceiver_daily/);
    assert.ok(!received.includes(fixture.qwen[0].latest.receipt.request_id),'bounded history evicts the oldest original');
    state.loop = 'paused';
    await dock.locator('[data-activity-loop-status="paused"]').waitFor();
    assert.equal(await page.locator('[data-apartment-approximate-badge="1"]').count(),0);
    state.cameraFailed = true;
    await dock.getByText(/authored camera unavailable/).first().waitFor();
    await dock.getByRole('button',{name:'Hide camera dock',exact:true}).click();
    const count = calls.filter(row=>/activity-loop|cameras\/den\//.test(row.path)).length;
    await page.waitForTimeout(1400);
    assert.equal(calls.filter(row=>/activity-loop|cameras\/den\//.test(row.path)).length,count,'hiding a paused viewer stops both camera and activity-loop reads');
    await page.setViewportSize({width:390,height:844});
    await page.locator('.apt-home-tabs').getByRole('tab',{name:'Camera · JEV',exact:true}).click();
    await dock.getByRole('button',{name:'Show camera',exact:true}).click();
    const reopen=dock.getByRole('button',{name:'View perception',exact:true});
    if(await reopen.isVisible())await reopen.click();
    await qwen.getByRole('group',{name:'Recent Qwen updates',exact:true}).locator('summary').first().click();
    await qwen.locator('[data-qwen-update]').first().waitFor();
    assert.equal(await qwen.locator('[data-qwen-update]').count(),1,'hidden camera history cannot leak into a new owner view');
    await inViewport(page.getByRole('button',{name:/^Open Perception Lab/}));
    assert.equal(await page.locator('.pl-global-recording').count(),0,'recording status lives in the header and cannot cover dock or toolbar');
    checks.push({stage:'bounded_scoped_history_expiry_failure_phone_and_hidden_cleanup',lastOriginalExpiry:latest.valid_until,mediaDigest:digest(body.png_base64)});
    } finally {
      for (const response of streams) response.end();
      streamServer.closeAllConnections(); await new Promise(resolve=>streamServer.close(resolve));
    }
  });
});
