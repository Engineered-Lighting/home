import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/src/home-perception-lab-transport.js', import.meta.url), 'utf8');

test('activity-loop status is an exact Victoria GET, bounded and unavailable to native authority', async () => {
  const f=fixture({fetch:async()=>Response.json({schema:'perception-lab-activity-loop-1',state:'paused'})});
  await f.transport.request('activityLoop.get',{siteId:'victoria'});
  assert.equal(f.calls[0][0],'/api/perception-lab/v1/activity-loop');
  assert.equal(f.calls[0][1].method,'GET'); assert.equal(f.calls[0][1].credentials,'same-origin');
  assert.equal(f.calls[0][1].headers['X-Home-Lab-Site'],'victoria');
  assert.equal(f.calls[0][1].headers['X-Home-Lab-CSRF'],undefined);
  for(const args of [{siteId:'echo'},{siteId:'victoria',body:{}},{siteId:'victoria',path:'/resume'}])
    await assert.rejects(f.transport.request('activityLoop.get',args));
  const large=fixture({fetch:async()=>Response.json({padding:'x'.repeat(65536)})});
  await assert.rejects(large.transport.request('activityLoop.get',{siteId:'victoria'}),{code:'lab_response_too_large'});
  const invoked=[];
  const native=fixture({window:{__TAURI__:{core:{invoke:async(...args)=>invoked.push(args)}}}});
  await assert.rejects(native.transport.request('activityLoop.get',{siteId:'victoria'}),{code:'lab_native_activity_loop_not_available'});
  assert.equal(invoked.length,0); assert.equal(native.calls.length,0);
});
test('structured camera context is a fixed bounded browser-only read',async()=>{
  const f=fixture({fetch:async()=>Response.json({schema:'home-camera-structured-context-1'})});
  const args={siteId:'victoria',cameraId:'den'};
  await f.transport.request('cameras.structuredContext',args);
  assert.equal(f.calls[0][0],'/api/perception-lab/v1/cameras/den/structured-context');
  assert.equal(f.calls[0][1].method,'GET');
  assert.equal(f.calls[0][1].headers['X-Home-Lab-CSRF'],undefined);
  for(const patch of [{siteId:'echo'},{cameraId:'other'},{body:{}},{url:'https://other'}])
    await assert.rejects(f.transport.request('cameras.structuredContext',{...args,...patch}));
  const large=fixture({fetch:async()=>Response.json({padding:'x'.repeat(32768)})});
  await assert.rejects(large.transport.request('cameras.structuredContext',args),{code:'lab_response_too_large'});
  const invoked=[];
  const native=fixture({window:{__TAURI__:{core:{invoke:async(...args)=>invoked.push(args)}}}});
  await assert.rejects(native.transport.request('cameras.structuredContext',args),{code:'lab_native_preview_not_available'});
  assert.equal(invoked.length,0);assert.equal(native.calls.length,0);
});
test('event cursor is a bounded browser read and never expands native authority', async () => {
  const page = {items:[{id:8,session_id:'s1',kind:'observation',created_at:100,payload:{site_id:'victoria'}}],after_id:8};
  const f=fixture({fetch:async()=>Response.json(page)}), args={siteId:'victoria',sessionId:'s1',afterId:3,limit:200};
  assert.equal((await f.transport.request('sessions.events',args)).after_id,8);
  assert.equal(f.calls[0][0],'/api/perception-lab/v1/sessions/s1/events?after_id=3&limit=200');
  assert.equal(f.calls[0][1].method,'GET');assert.equal(f.calls[0][1].headers['X-Home-Lab-CSRF'],undefined);
  for(const extra of [{afterId:-1},{afterId:1.5},{limit:201},{limit:0},{body:{start:true}},{url:'https://other'}])
    await assert.rejects(f.transport.request('sessions.events',{...args,...extra}));
  const calls=[];
  const native=fixture({window:{__TAURI__:{core:{invoke:async(...args)=>calls.push(args)}}}});
  await assert.rejects(native.transport.request('sessions.events',args),{code:'lab_native_events_not_available'});
  assert.equal(calls.length,0);assert.equal(native.calls.length,0);
});
test('event cursor refuses malformed, cross-session and oversized pages', async () => {
  const event={id:8,session_id:'s1',kind:'observation',created_at:100,payload:{}};
  for(const page of [{items:[{...event,session_id:'s2'}],after_id:8},{items:[event,event],after_id:8},
    {items:[event],after_id:9},{items:[{...event,payload:{site_id:'echo'}}],after_id:8}]) {
    const f=fixture({fetch:async()=>Response.json(page)});
    await assert.rejects(f.transport.request('sessions.events',{siteId:'victoria',sessionId:'s1'}));
  }
  const f=fixture({fetch:async()=>Response.json({items:[],after_id:0,padding:'x'.repeat(1024*1024)})});
  await assert.rejects(f.transport.request('sessions.events',{siteId:'victoria',sessionId:'s1'}),{code:'lab_response_too_large'});
});
test('refresh uses fixed Victoria camera and request ID without forwarding caller extras',async()=>{
  const f=fixture(), requestId='12345678-1234-1234-1234-123456789abc';
  const args={siteId:'victoria',cameraId:'den',requestId,body:{url:'https://untrusted'}};
  await f.transport.request('cameras.refresh',args);
  assert.equal(f.calls[0][0],'/api/perception-lab/v1/cameras/den/context-refresh');
  assert.equal(f.calls[0][1].method,'POST');
  assert.deepEqual(JSON.parse(f.calls[0][1].body),{version:1,request_id:requestId,site_id:'victoria'});
  await f.transport.request('cameras.refreshStatus',args);
  assert.equal(f.calls[1][0],'/api/perception-lab/v1/cameras/den/context-refresh/'+requestId);
  await f.transport.request('cameras.refreshImage',args);
  assert.equal(f.calls[2][0],'/api/perception-lab/v1/cameras/den/context-refresh/'+requestId+'/image');
  await assert.rejects(f.transport.request('cameras.refreshImage',{...args,siteId:'echo'}));
  await assert.rejects(f.transport.request('cameras.refresh',{...args,siteId:'echo'}));
  const invoked=[];
  const native=fixture({window:{__TAURI__:{core:{invoke:async(...call)=>{invoked.push(call);return 1;}}}}});
  for(const operation of ['cameras.refresh','cameras.refreshStatus','cameras.refreshImage'])
    await assert.rejects(native.transport.request(operation,args),{code:'lab_native_preview_not_available',status:0});
  assert.equal(native.calls.length,0);assert.equal(invoked.length,0);
});
test('native camera reads use typed operations with exact fields and no HTTP fallback',async()=>{
  const calls=[];
  const f=fixture({window:{__TAURI__:{core:{invoke:async(command,args)=>{
    calls.push([command,JSON.parse(JSON.stringify(args??null))]);
    if(command==='lab_connection_reset')return 1;
    return {status:200,payload:{site_id:'victoria',camera_id:'den',op:args.request.operation}};
  }}}}});
  const common={connectionGeneration:1,siteId:'victoria',sessionId:null,jobId:null,annotationId:null,mediaId:null,generation:null,
    cursor:null,limit:null,snapshotId:null,kind:null,atMs:null,encodedAtMs:null,radiusMs:null,body:null,cameraId:'den'};
  const generation='0123456789abcdef0123456789abcdef';
  for(const [operation,args,extra] of [
    ['cameras.preview',{},{}],
    ['cameras.context',{},{}],
    ['cameras.plane',{modality:'depth'},{modality:'depth'}],
    ['cameras.video',{afterSequence:undefined,videoGeneration:undefined},{afterSequence:null,videoGeneration:null}],
    ['cameras.video',{afterSequence:999999999999999,videoGeneration:generation},{afterSequence:999999999999999,videoGeneration:generation}],
  ]) {
    const payload=await f.transport.request(operation,{siteId:'victoria',cameraId:'den',...args});
    assert.equal(payload.op,operation);
    assert.equal(calls.at(-1)[0],'lab_request');
    assert.deepEqual(calls.at(-1)[1].request,{operation,...common,...extra});
  }
  assert.equal(calls.filter(c=>c[0]==='lab_connection_reset').length,1);
  // Browser-side bounds still apply before any IPC.
  const before=calls.length;
  for(const [operation,args,code] of [
    ['cameras.plane',{modality:'validity'},'lab_unsupported_plane'],
    ['cameras.video',{afterSequence:1e15},'lab_invalid_range'],
    ['cameras.video',{videoGeneration:'A'.repeat(32)},'lab_invalid_generation'],
    ['cameras.preview',{cameraId:'../den'},'lab_invalid_identifier'],
  ]) await assert.rejects(f.transport.request(operation,{siteId:'victoria',cameraId:'den',...args}),{code});
  assert.equal(calls.length,before);assert.equal(f.calls.length,0);
  const unavailable=fixture({window:{__TAURI__:{core:{invoke:async command=>command==='lab_connection_reset'?1:
    {status:503,payload:{error:{code:'preview_not_configured'}}}}}}});
  await assert.rejects(unavailable.transport.request('cameras.preview',{siteId:'victoria',cameraId:'den'}),{code:'preview_not_configured',status:503});
});
test('an abandoned native camera read is never delivered, like an aborted fetch',async()=>{
  let deliver;const calls=[];
  const f=fixture({window:{__TAURI__:{core:{invoke:async(command)=>{calls.push(command);if(command==='lab_connection_reset')return 1;
    return new Promise(resolve=>{deliver=()=>resolve({status:200,payload:{site_id:'victoria',camera_id:'den',jpeg_base64:'late'}});});}}}}});
  const early=new AbortController();early.abort();
  await assert.rejects(f.transport.request('cameras.preview',{siteId:'victoria',cameraId:'den'},{signal:early.signal}),{code:'lab_unavailable',status:0});
  assert.equal(calls.length,0);
  const abort=new AbortController();
  const pending=f.transport.request('cameras.plane',{siteId:'victoria',cameraId:'den',modality:'left'},{signal:abort.signal});
  while(!deliver)await new Promise(resolve=>setTimeout(resolve,0));
  abort.abort();deliver();
  await assert.rejects(pending,{code:'lab_unavailable'});
});
test('full erasure scope survives browser and native typed operation transport',async()=>{
  const body={expected_revision:4,erase_records:true};
  const web=fixture();
  await web.transport.request('sessions.delete',{siteId:'victoria',sessionId:'s1',body});
  assert.equal(web.calls[0][1].method,'DELETE');
  assert.deepEqual(JSON.parse(web.calls[0][1].body),{...body,site_id:'victoria'});
  const calls=[];
  const native=fixture({window:{__TAURI__:{core:{invoke:async(command,args)=>{
    calls.push([command,args]);return command==='lab_connection_reset'?1:{status:200,payload:{id:'s1',deleted:true,scope:'session'}};
  }}}}});
  await native.transport.request('sessions.delete',{siteId:'victoria',sessionId:'s1',body});
  const request=calls.find(c=>c[0]==='lab_request')[1].request;
  assert.equal(request.operation,'sessions.delete');assert.equal(request.body.erase_records,true);
  assert.equal(request.siteId,'victoria');assert.equal(native.calls.length,0);
});
function fixture(extras = {}) {
  const calls = []; const window = { ...extras.window };
  const context = vm.createContext({ window, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer, AbortController, setTimeout, clearTimeout,
    DOMException, TypeError,
    fetch: async (...args) => { calls.push(args); return extras.fetch ? extras.fetch(...args) : Response.json({ ok: true }); },
  });
  const load = file => vm.runInContext(file ? fs.readFileSync(new URL('../app/src/' + file, import.meta.url), 'utf8') : source, context);
  load(); return { transport: window.HomePerceptionLabTransport, calls, window, load };
}
test('browser transport uses fixed same-origin operations and opaque identifiers', async () => {
  const f = fixture(); await f.transport.request('sessions.start', { siteId: 'victoria', body: { camera_id: 'den' } });
  const [url, options] = f.calls[0]; assert.equal(url, '/api/perception-lab/v1/sessions');
  assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error');
  assert.equal(options.headers['X-Home-Lab-CSRF'], '1'); assert.equal(options.headers['X-Home-Lab-Site'], 'victoria');
  assert.equal(JSON.parse(options.body).site_id, 'victoria');
  await assert.rejects(f.transport.request('http.get', { siteId: 'victoria' }), { code: 'lab_operation_denied' });
  await assert.rejects(f.transport.request('sessions.get', { siteId: 'victoria', sessionId: '../x' }), { code: 'lab_invalid_identifier' });
  await assert.rejects(f.transport.request('sessions.start', { siteId: 'victoria', body: { site_id: 'echo' } }), { code: 'lab_site_mismatch' });
  assert.equal(f.calls.length, 1);
});
test('native is narrow and never falls back to HTTP after auth or invoke failure', async () => {
  const nativeCalls = [];
  const f = fixture({ window: { __TAURI__: { core: { invoke: async (...args) => { nativeCalls.push(args); return args[0] === 'lab_connection_reset' ? 1 : { status: 200, payload: { ok: true } }; } } } } });
  await f.transport.request('sessions.get', { siteId: 'victoria', sessionId: 's1' });
  assert.equal(nativeCalls[0][0], 'lab_connection_reset');
  assert.equal(nativeCalls[1][0], 'lab_request'); assert.equal(nativeCalls[1][1].request.operation, 'sessions.get');
  assert.equal(nativeCalls[1][1].request.connectionGeneration, 1);
  assert.equal(f.calls.length, 0);
  const missing = fixture({ window: { __TAURI_INTERNALS__: {} } });
  await assert.rejects(missing.transport.request('sessions.list', { siteId: 'echo' }), { code: 'lab_native_transport_unavailable' });
  assert.equal(missing.calls.length, 0);
  const failure = fixture({ window: { __TAURI__: { core: { invoke: async () => { throw 'private failure'; } } } } });
  await assert.rejects(failure.transport.request('sessions.list', { siteId: 'echo' }), { code: 'lab_native_transport_failed' });
  assert.equal(failure.calls.length, 0);
});
test('native media uses only binary IPC bound to an authorized manifest and connection generation', async () => {
  const calls = []; let epoch = 0;
  const f = fixture({ window: { __TAURI__: { core: { invoke: async (command, args) => {
    calls.push([command, args]);
    if (command === 'lab_connection_reset') return ++epoch;
    if (command === 'lab_media_read') return new Uint8Array([2, 3, 4, 5]).buffer;
    if (args.request.operation === 'media.describe') return { status: 200, payload: { site_id: 'victoria', session_id: 's1', media_id: 'm1', generation: 2, size_bytes: 8, content_type: 'video/mp4' } };
    return { status: 200, payload: { generation: 2, items: [{ site_id: 'victoria', session_id: 's1', media_id: 'm1', generation: 2, size_bytes: 8, content_type: 'video/mp4' }] } };
  } } } } });
  const args = { siteId: 'victoria', sessionId: 's1', mediaId: 'm1', generation: 2, offset: 2, length: 4 };
  assert.deepEqual([...(await f.transport.requestMedia(args)).data], [2, 3, 4, 5]);
  assert.equal(calls[1][1].request.operation, 'media.describe');
  await f.transport.request('media.manifest', { siteId: 'victoria', sessionId: 's1' });
  assert.deepEqual([...(await f.transport.requestMedia(args)).data], [2, 3, 4, 5]);
  assert.equal(calls.at(-1)[1].request.connectionGeneration, 1);
  await f.transport.invalidate();
  assert.deepEqual([...(await f.transport.requestMedia(args)).data], [2, 3, 4, 5]);
  assert.equal(calls.at(-1)[1].request.connectionGeneration, 2);
  assert.equal(f.calls.length, 0);
});
test('manifest pagination is bounded and only accepts fixed scalar queries', async () => {
  const f = fixture({ fetch: () => Response.json({ generation: 0, items: [], next_cursor: 200 }) });
  await f.transport.request('media.manifest', { siteId: 'victoria', sessionId: 's1', cursor: 100, limit: 200 });
  assert.equal(f.calls[0][0], '/api/perception-lab/v1/sessions/s1/media?cursor=100&limit=200');
  await assert.rejects(f.transport.request('media.manifest', { siteId: 'victoria', sessionId: 's1', limit: 201 }), { code: 'lab_invalid_range' });
  await f.transport.request('media.manifest',{siteId:'victoria',sessionId:'s1',kind:'video',encodedAtS:100.25,radiusMs:1000});
  assert.equal(f.calls.at(-1)[0],'/api/perception-lab/v1/sessions/s1/media?kind=video&encoded_at_ms=100250&radius_ms=1000');
  await assert.rejects(f.transport.request('media.manifest',{siteId:'victoria',sessionId:'s1',kind:'depth',encodedAtS:1}));
  await f.transport.request('sessions.get',{siteId:'victoria',sessionId:'s1',atMs:125000});
  assert.equal(f.calls.at(-1)[0],'/api/perception-lab/v1/sessions/s1?at_ms=125000');
});
test('reset discards late results from the previous browser connection', async () => {
  let deliver; const f = fixture({ fetch: () => new Promise(resolve => { deliver = resolve; }) });
  const pending = f.transport.request('sessions.list', { siteId: 'victoria' });
  await f.transport.invalidate(); deliver(Response.json({ private_old_context: true }));
  await assert.rejects(pending, { code: 'lab_stale_connection' });
});
test('binary interface validates ranges and returns bytes instead of renderer URLs', async () => {
  const args = { siteId: 'victoria', sessionId: 's1', mediaId: 'm1', generation: 2, offset: 2, length: 4 };
  const f = fixture({ fetch: () => new Response(new Uint8Array([2, 3, 4, 5]), { status: 206, headers: { 'content-type': 'video/mp4', 'content-range': 'bytes 2-5/8' } }) });
  const result = await f.transport.requestMedia(args);
  assert.deepEqual([...result.data], [2, 3, 4, 5]); assert.equal(result.generation, 2);
  assert.equal(f.calls[0][1].headers.Range, 'bytes=2-5');
  assert.equal(f.calls[0][0], '/api/perception-lab/v1/sessions/s1/media/m1?generation=2');
  await assert.rejects(f.transport.requestMedia({ ...args, length: 4 * 1024 * 1024 + 1 }), { code: 'lab_invalid_range' });
  const bad = fixture({ fetch: () => new Response('abcd', { status: 206, headers: { 'content-type': 'video/mp4', 'content-range': 'bytes 0-3/8' } }) });
  await assert.rejects(bad.transport.requestMedia(args), { code: 'lab_invalid_media_range' });
});
test('simulation transport and untrusted error details fail closed', async () => {
  const f = fixture({ window: { __SIM_ACTIVE: true } });
  await assert.rejects(f.transport.request('sessions.list', { siteId: 'echo' }), { code: 'lab_simulation_transport_disabled' });
  assert.equal(f.calls.length, 0);
  const bad = fixture({ fetch: () => Response.json({ error: 'Bearer private /path' }, { status: 403 }) });
  await assert.rejects(bad.transport.request('sessions.list', { siteId: 'echo' }), { code: 'lab_request_failed', status: 403 });
});

test('export continuation is narrow and snapshot bound', async () => {
  const f=fixture({fetch:()=>Response.json({ok:true})});
  await f.transport.request('sessions.export',{siteId:'victoria',sessionId:'s1',snapshotId:'a'.repeat(64),cursor:200,limit:200});
  assert.equal(f.calls[0][0],'/api/perception-lab/v1/sessions/s1/export?snapshot_id='+'a'.repeat(64)+'&cursor=200&limit=200');
  await assert.rejects(f.transport.request('sessions.export',{siteId:'victoria',sessionId:'s1',snapshotId:'../bad'}),{code:'lab_invalid_export_snapshot'});
});

test('re-executing the boot file keeps the working transport and its in-flight native reads',async()=>{
  const calls=[];let release;
  const f=fixture({window:{__TAURI__:{core:{invoke:async(command,args)=>{
    calls.push(command);
    if(command==='lab_connection_reset')return calls.filter(c=>c===command).length;
    if(args.request.operation==='sessions.live')return new Promise(resolve=>{release=()=>resolve({status:200,payload:{id:'s1'}});});
    return {status:200,payload:{generation:0,items:[{site_id:'victoria',session_id:'s1',media_id:'m1',generation:0,size_bytes:8,content_type:'video/mp4'}]}};
  }}}}});
  await f.transport.request('media.manifest',{siteId:'victoria',sessionId:'s1'});
  const inflight=f.transport.request('sessions.live',{siteId:'victoria',sessionId:'s1'});
  await new Promise(resolve=>setTimeout(resolve,0));
  f.load();f.load();
  assert.equal(f.window.HomePerceptionLabTransport,f.transport);
  release();
  assert.deepEqual(JSON.parse(JSON.stringify(await inflight)),{id:'s1'});
  await f.transport.request('sessions.get',{siteId:'victoria',sessionId:'s1'});
  assert.equal(calls.filter(c=>c==='lab_connection_reset').length,1);
  // The manifest cache survived: no describe is needed before a read.
  const before=calls.length;
  await assert.rejects(f.transport.requestMedia({siteId:'victoria',sessionId:'s1',mediaId:'m1',generation:0,offset:0,length:4}),{code:'lab_native_binary_required'});
  assert.deepEqual(calls.slice(before),['lab_media_read']);
});

function nativeMedia(read, item = {}) {
  const calls=[];
  const f=fixture({window:{__TAURI__:{core:{invoke:async(command,args)=>{
    calls.push(command);
    if(command==='lab_connection_reset')return 1;
    if(command==='lab_media_read')return read(calls.filter(c=>c===command).length);
    return {status:200,payload:{site_id:'victoria',session_id:'s1',media_id:'m1',generation:2,size_bytes:4,content_type:'image/png',...item}};
  }}}}});
  return {...f,calls};
}
test('native media failures keep their status so only transient reads are retried',async()=>{
  const args={siteId:'victoria',sessionId:'s1',mediaId:'m1',generation:2,offset:0,length:4};
  for(const [raw,code,status] of [
    ['lab_media_unavailable:404','lab_media_unavailable',404],
    ['lab_media_unavailable:410','lab_media_unavailable',410],
    ['lab_media_unavailable:503','lab_media_unavailable',503],
    ['lab_transport_busy:429','lab_transport_busy',429],
    ['lab_upstream_unavailable:502','lab_upstream_unavailable',502],
    ['lab_media_integrity','lab_media_integrity',0],
    ['lab_media_unavailable:999','lab_native_media_failed',0],
    ['lab_media_unavailable:50','lab_native_media_failed',0],
    ['private failure','lab_native_media_failed',0],
  ]) {
    const f=nativeMedia(()=>Promise.reject(raw));
    await assert.rejects(f.transport.requestMedia(args),{name:'LabTransportError',code,status});
  }
  // Describe failures keep their HTTP status instead of becoming a native failure.
  const gone=fixture({window:{__TAURI__:{core:{invoke:async command=>command==='lab_connection_reset'?1:{status:410,payload:{error:'media_deleted'}}}}}});
  await assert.rejects(gone.transport.requestMedia(args),{code:'media_deleted',status:410});
  // The shared frame reader retries native 503/429/502 and stops at 404/410.
  for(const [raw,reads] of [['lab_media_unavailable:503',3],['lab_transport_busy:429',3],['lab_upstream_unavailable:502',3],['lab_media_unavailable:404',1],['lab_media_unavailable:410',1]]) {
    const f=nativeMedia(n=>n<3?Promise.reject(raw):new Uint8Array([1,2,3,4]).buffer);f.load('home-perception-lab-media.js');
    const frame={media_id:'m1',size_bytes:4,content_type:'image/png'},delays=[];
    const read=f.window.HomePerceptionLabMedia.readFrame(frame,{siteId:'victoria',sessionId:'s1',generation:2},new AbortController().signal,()=>{},async ms=>delays.push(ms));
    if(reads===3)assert.deepEqual([...(await read).bytes],[1,2,3,4]);else await assert.rejects(read,{code:'lab_media_unavailable'});
    assert.equal(f.calls.filter(c=>c==='lab_media_read').length,reads);
  }
});
test('native lists unreadable stored media without reading it',async()=>{
  const args={siteId:'victoria',sessionId:'s1',mediaId:'m1',generation:2,offset:0,length:4};
  const f=nativeMedia(()=>new Uint8Array([1,2,3,4]).buffer,{content_type:'application/x-npz',native_readable:false});
  await assert.rejects(f.transport.requestMedia(args),{code:'lab_media_type_denied'});
  assert.equal(f.calls.includes('lab_media_read'),false);
  const readable=nativeMedia(()=>new Uint8Array([1,2,3,4]).buffer);
  assert.deepEqual([...(await readable.transport.requestMedia(args)).data],[1,2,3,4]);
});
test('graph, timeline and replay reads are fixed GET operations with bounded session-time cursors',async()=>{
  const f=fixture({fetch:()=>Response.json({site_id:'victoria'})});
  const base='/api/perception-lab/v1/sessions/s1';
  for(const [operation,args,path] of [
    ['sessions.graph',{},'/graph'],
    ['sessions.graph',{atMs:0},'/graph?at_ms=0'],
    ['sessions.replay',{atMs:1800000},'/replay?at_ms=1800000'],
    ['sessions.timeline',{},'/timeline'],
    ['sessions.timeline',{fromMs:5,toMs:5},'/timeline?from_ms=5&to_ms=5'],
    ['sessions.timeline',{toMs:9},'/timeline?to_ms=9'],
  ]) {
    await f.transport.request(operation,{siteId:'victoria',sessionId:'s1',...args});
    const [url,options]=f.calls.at(-1);
    assert.equal(url,base+path);assert.equal(options.method,'GET');assert.equal(options.body,undefined);
    assert.equal(options.headers['X-Home-Lab-CSRF'],undefined);
  }
  const before=f.calls.length;
  for(const [operation,args,code] of [
    ['sessions.replay',{},'lab_invalid_range'],
    ['sessions.graph',{atMs:1800001},'lab_invalid_range'],
    ['sessions.graph',{atMs:1.5},'lab_invalid_range'],
    ['sessions.timeline',{fromMs:6,toMs:5},'lab_invalid_range'],
    ['sessions.timeline',{fromMs:-1},'lab_invalid_range'],
    ['sessions.graph',{sessionId:'../s1'},'lab_invalid_identifier'],
  ]) await assert.rejects(f.transport.request(operation,{siteId:'victoria',sessionId:'s1',...args}),{code});
  assert.equal(f.calls.length,before);
});
test('graph reads are capped at 1 MiB in the browser while detail keeps the general cap',async()=>{
  const big='x'.repeat(1024*1024);
  const f=fixture({fetch:()=>Response.json({site_id:'victoria',pad:big})});
  for(const [operation,args] of [['sessions.graph',{}],['sessions.timeline',{}],['sessions.replay',{atMs:0}]])
    await assert.rejects(f.transport.request(operation,{siteId:'victoria',sessionId:'s1',...args}),{code:'lab_response_too_large'});
  assert.equal((await f.transport.request('sessions.get',{siteId:'victoria',sessionId:'s1'})).pad.length,big.length);
});
test('native graph reads are typed operations; only the timeline carries its range',async()=>{
  const calls=[];
  const f=fixture({window:{__TAURI__:{core:{invoke:async(command,args)=>{
    calls.push([command,JSON.parse(JSON.stringify(args??null))]);
    return command==='lab_connection_reset'?1:{status:200,payload:{site_id:'victoria',op:args.request.operation}};
  }}}}});
  const common={connectionGeneration:1,siteId:'victoria',sessionId:'s1',jobId:null,annotationId:null,mediaId:null,generation:null,
    cursor:null,limit:null,snapshotId:null,kind:null,atMs:null,encodedAtMs:null,radiusMs:null,body:null};
  for(const [operation,args,extra] of [
    ['sessions.graph',{},{}],
    ['sessions.graph',{atMs:250},{atMs:250}],
    ['sessions.replay',{atMs:250},{atMs:250}],
    ['sessions.timeline',{},{fromMs:null,toMs:null}],
    ['sessions.timeline',{fromMs:0,toMs:9000},{fromMs:0,toMs:9000}],
  ]) {
    assert.equal((await f.transport.request(operation,{siteId:'victoria',sessionId:'s1',...args})).op,operation);
    assert.deepEqual(calls.at(-1),['lab_request',{request:{operation,...common,...extra}}]);
  }
  // Other operations keep their exact request shape.
  await f.transport.request('sessions.get',{siteId:'victoria',sessionId:'s1',atMs:1});
  assert.deepEqual(calls.at(-1)[1].request,{operation:'sessions.get',...common,atMs:1});
  assert.equal(f.calls.length,0);
});
