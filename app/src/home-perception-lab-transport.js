/* Perception Lab's narrow transport. No credentials, endpoints or model URLs
 * are supplied by the renderer. Native and browser boundaries validate again. */
(function (root) {
  "use strict";
  const PREFIX = "/api/perception-lab/v1";
  const MAX_JSON = 4 * 1024 * 1024;
  const MAX_MEDIA = 4 * 1024 * 1024;
  const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
  let connectionEpoch = 0, nativeGeneration = null;
  const manifests = new Map(), pending = new Set(), responseCleanup = new WeakMap();
  class LabTransportError extends Error {
    constructor(code, status) { super(code); this.name = "LabTransportError"; this.code = code; this.status = status || 0; }
  }
  function fail(code, status) { throw new LabTransportError(code, status); }
  function id(value) { if (typeof value !== "string" || !ID.test(value)) fail("lab_invalid_identifier"); return value; }
  function site(value) { if (value !== "echo" && value !== "victoria") fail("lab_invalid_site"); return value; }
  function integer(value, min, max) { if (!Number.isSafeInteger(value) || value < min || value > max) fail("lab_invalid_range"); return value; }
  function bodyOf(args) {
    if (args.body !== undefined) {
      if (!args.body || typeof args.body !== "object" || Array.isArray(args.body)) fail("lab_invalid_body");
      return args.body;
    }
    const body = {};
    Object.keys(args).forEach(key => {
      if (!["siteId", "sessionId", "jobId", "annotationId", "mediaId", "generation", "offset", "length"].includes(key)) body[key] = args[key];
    });
    return body;
  }
  function describe(operation, args) {
    if (!args || typeof args !== "object" || Array.isArray(args)) fail("lab_invalid_arguments");
    const siteId = site(args.siteId);
    const sessionPath = () => "/sessions/" + id(args.sessionId);
    let method = "GET", path, body;
    switch (operation) {
      case "capabilities.get": path = "/capabilities"; break;
      case "cameras.preview": path = "/cameras/" + id(args.cameraId) + "/preview"; break;
      case "cameras.video": {
        const query=[];
        if(args.afterSequence!==undefined)query.push('after_sequence='+integer(args.afterSequence,0,999999999999999));
        if(args.videoGeneration!==undefined){if(!/^[a-f0-9]{32}$/.test(args.videoGeneration))fail('lab_invalid_generation');query.push('generation='+args.videoGeneration);}
        path='/cameras/'+id(args.cameraId)+'/video'+(query.length?'?'+query.join('&'):'');break;
      }
      case "cameras.plane":
        if (!['depth','left','right'].includes(args.modality)) fail('lab_unsupported_plane');
        path = "/cameras/" + id(args.cameraId) + "/planes/" + args.modality; break;
      case "cameras.context": path = "/cameras/" + id(args.cameraId) + "/context"; break;
      case "cameras.refresh":
      case "cameras.refreshStatus":
      case "cameras.refreshImage": {
        if (siteId !== 'victoria' || args.cameraId !== 'den' || typeof args.requestId !== 'string' ||
            !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(args.requestId)) fail('lab_invalid_refresh');
        path = '/cameras/den/context-refresh';
        if (operation === 'cameras.refresh') {
          method = 'POST'; body = {version:1, request_id:args.requestId};
        } else path += '/' + args.requestId + (operation === 'cameras.refreshImage' ? '/image' : '');
        break;
      }
      case "sessions.list": path = "/sessions"; break;
      case "sessions.get": path = sessionPath() + (args.atMs===undefined?'':'?at_ms='+integer(args.atMs,0,1800000)); break;
      case "sessions.live": path = sessionPath() + "/live"; break;
      case "sessions.findings": path = sessionPath() + "/findings"; break;
      case "sessions.start": method = "POST"; path = "/sessions"; break;
      case "sessions.stop": method = "POST"; path = sessionPath() + "/stop"; break;
      case "sessions.switch": method = "POST"; path = sessionPath() + "/switch"; break;
      case "sessions.delete": method = "DELETE"; path = sessionPath(); break;
      case "bookmarks.add": method = "POST"; path = sessionPath() + "/bookmarks"; break;
      case "annotations.save": method = "PUT"; path = sessionPath() + "/annotations/" + id(args.annotationId); break;
      case "annotations.delete": method = "DELETE"; path = sessionPath() + "/annotations/" + id(args.annotationId); break;
      case "jobs.list": path = "/jobs" + (args.sessionId ? "?session_id=" + id(args.sessionId) : ""); break;
      case "jobs.create": method = "POST"; path = "/jobs"; break;
      case "jobs.cancel": method = "POST"; path = "/jobs/" + id(args.jobId) + "/cancel"; break;
      case "diagnostics.create": method = "POST"; path = "/diagnostics"; break;
      case "sessions.export": {
        const query=[];
        if(args.snapshotId!==undefined){if(typeof args.snapshotId!=='string'||!/^[a-f0-9]{64}$/.test(args.snapshotId))fail('lab_invalid_export_snapshot');query.push('snapshot_id='+args.snapshotId);}
        if(args.cursor!==undefined)query.push('cursor='+integer(args.cursor,0,Number.MAX_SAFE_INTEGER));
        if(args.limit!==undefined)query.push('limit='+integer(args.limit,1,200));
        path=sessionPath()+'/export'+(query.length?'?'+query.join('&'):'');break;
      }
      case "media.manifest": {
        const query = [];
        if (args.cursor !== undefined) query.push("cursor=" + integer(args.cursor, 0, Number.MAX_SAFE_INTEGER));
        if (args.limit !== undefined) query.push("limit=" + integer(args.limit, 1, 200));
        if(args.kind!==undefined){if(!['video','depth','image'].includes(args.kind))throw Error('lab_invalid_filter');query.push('kind='+args.kind);}
        if(args.atMs!==undefined&&args.encodedAtS!==undefined)throw Error('lab_invalid_filter');
        if(args.atMs!==undefined)query.push('at_ms='+integer(args.atMs,0,1800000));
        if(args.encodedAtS!==undefined){if(args.kind!=='video'||!Number.isFinite(args.encodedAtS))throw Error('lab_invalid_filter');query.push('encoded_at_ms='+integer(Math.round(args.encodedAtS*1000),0,1800000));}
        if(args.radiusMs!==undefined)query.push('radius_ms='+integer(args.radiusMs,0,120000));
        path = sessionPath() + "/media" + (query.length ? "?" + query.join("&") : ""); break;
      }
      case "media.describe": path = sessionPath() + "/media/" + id(args.mediaId) + "/descriptor?generation=" + integer(args.generation, 0, Number.MAX_SAFE_INTEGER); break;
      default: fail("lab_operation_denied");
    }
    if (method !== "GET") {
      body = body || bodyOf(args);
      if (body.site_id !== undefined && body.site_id !== siteId) fail("lab_site_mismatch");
      body = Object.assign({}, body, { site_id: siteId });
      if (new TextEncoder().encode(JSON.stringify(body)).length > 65536) fail("lab_body_too_large");
    }
    return { operation, siteId, method, path, body };
  }
  function finishResponse(response) { const cleanup = responseCleanup.get(response); responseCleanup.delete(response); cleanup?.(); }
  async function boundedBytes(response, max) {
    try { return await readBoundedBytes(response, max); }
    catch (error) { if (error instanceof LabTransportError) throw error; fail("lab_unavailable"); }
    finally { finishResponse(response); }
  }
  async function readBoundedBytes(response, max) {
    const declared = response.headers.get("content-length");
    if (declared && (!/^\d+$/.test(declared) || Number(declared) > max)) fail("lab_response_too_large");
    if (!response.body || !response.body.getReader) {
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.length > max) fail("lab_response_too_large");
      return bytes;
    }
    const reader = response.body.getReader(), parts = []; let length = 0;
    try {
      for (;;) {
        const next = await reader.read(); if (next.done) break;
        length += next.value.length; if (length > max) { await reader.cancel(); fail("lab_response_too_large"); }
        parts.push(next.value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(length); let at = 0;
    parts.forEach(part => { bytes.set(part, at); at += part.length; }); return bytes;
  }
  function nativeInvoke() { return root.__TAURI__?.core?.invoke || root.__TAURI__?.tauri?.invoke; }
  function nativeContext() { return !!(root.__TAURI__ || root.__TAURI_INTERNALS__); }
  function generation() {
    if (!nativeGeneration) {
      const invoke = nativeInvoke(); if (!invoke) fail("lab_native_transport_unavailable");
      nativeGeneration = invoke("lab_connection_reset").then(value => integer(value, 1, Number.MAX_SAFE_INTEGER));
    }
    return nativeGeneration;
  }
  async function invalidate() {
    connectionEpoch++; manifests.clear(); pending.forEach(controller => controller.abort()); pending.clear();
    nativeGeneration = null;
    if (nativeContext()) return generation();
  }
  function current(epoch) { if (connectionEpoch !== epoch) fail("lab_stale_connection"); }
  async function browserFetch(path, init, options, timeoutMs = 15000) {
    const controller = new AbortController(); pending.add(controller);
    const abort = () => controller.abort();
    const timer = setTimeout(abort, timeoutMs);
    const cleanup = () => { clearTimeout(timer); pending.delete(controller); options?.signal?.removeEventListener("abort", abort); controller.abort(); };
    if (options?.signal?.aborted) abort();
    else options?.signal?.addEventListener("abort", abort, { once: true });
    try { const response = await fetch(path, Object.assign({}, init, { signal: controller.signal })); responseCleanup.set(response, cleanup); return response; }
    catch (_) { cleanup(); fail("lab_unavailable"); }
  }
  function rememberManifest(args, payload) {
    if (!Array.isArray(payload?.items) || payload.items.length > 4096) fail("lab_invalid_manifest");
    const generation = integer(payload.generation, 0, Number.MAX_SAFE_INTEGER);
    const key = args.siteId + ":" + args.sessionId, old = manifests.get(key);
    if (old?.generation > generation) fail("lab_stale_generation");
    const items = new Map(old?.generation === generation ? old.items : []);
    const seen = new Set();
    payload.items.forEach(item => {
      if (item.site_id !== args.siteId || item.session_id !== args.sessionId || item.generation !== generation) fail("lab_site_mismatch");
      const mediaId = id(item.media_id), size = integer(item.size_bytes, 0, Number.MAX_SAFE_INTEGER);
      if (seen.has(mediaId)) fail("lab_invalid_manifest"); seen.add(mediaId);
      const oldItem = items.get(mediaId);
      if (oldItem && (oldItem.size !== size || oldItem.contentType !== item.content_type)) fail("lab_invalid_manifest");
      items.set(mediaId, { size, contentType: item.content_type });
      while (items.size > 4096) items.delete(items.keys().next().value);
    });
    if (manifests.size >= 16 && !manifests.has(key)) manifests.clear();
    manifests.set(key, { generation, items });
  }
  function decodedError(payload, status) {
    const candidate = payload?.error?.code || payload?.error || payload?.detail?.code || payload?.detail;
    return typeof candidate === "string" && /^[a-z][a-z0-9_]{0,100}$/.test(candidate) ? candidate : "lab_request_failed";
  }
  function validateMedia(data, contentType, contentRange, offset, length) {
    const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(contentRange || "");
    const values = match && match.slice(1).map(Number);
    if (!values || !values.every(Number.isSafeInteger) || values[0] !== offset || values[1] < offset ||
        values[1] !== Math.min(offset + length - 1, values[2] - 1) || values[1] >= values[2] || values[1] - offset + 1 !== data.length) fail("lab_invalid_media_range");
    if (!/^(video\/(mp4|webm|x-matroska)|image\/(jpeg|png)|application\/(octet-stream|x-npy|zip))(;.*)?$/.test(contentType || "")) fail("lab_media_type_denied");
    return { data, contentType, contentRange };
  }
  async function request(operation, args, options) {
    if (operation === "media.read") return requestMedia(args, options);
    if (root.__SIM_ACTIVE === true) fail("lab_simulation_transport_disabled");
    const route = describe(operation, args);
    const epoch = connectionEpoch;
    if (nativeContext()) {
      if (operation === 'cameras.preview' || operation === 'cameras.context' || operation === 'cameras.plane' || operation === 'cameras.video' || operation === 'cameras.refresh' || operation === 'cameras.refreshStatus' || operation === 'cameras.refreshImage') fail('lab_native_preview_not_available');
      const invoke = nativeInvoke(); if (!invoke) fail("lab_native_transport_unavailable");
      let response;
      try { response = await invoke("lab_request", { request: { operation, connectionGeneration: await generation(), siteId: route.siteId,
        sessionId: args.sessionId || null, jobId: args.jobId || null,
        annotationId: args.annotationId || null, mediaId: args.mediaId || null, generation: args.generation ?? null,
        cursor: args.cursor ?? null, limit: args.limit ?? null, snapshotId:args.snapshotId??null, kind:args.kind??null, atMs:args.atMs??null, encodedAtMs:args.encodedAtS===undefined?null:Math.round(args.encodedAtS*1000), radiusMs:args.radiusMs??null, body: route.body || null } }); }
      catch (error) { fail(typeof error === "string" && /^lab_[a-z0-9_]+$/.test(error) ? error : "lab_native_transport_failed"); }
      current(epoch);
      if (response.status < 200 || response.status >= 300) fail(decodedError(response.payload, response.status), response.status);
      if (operation === "media.manifest") rememberManifest(args, response.payload);
      if (operation === "media.describe") rememberManifest(args, { generation: args.generation, items: [response.payload] });
      if (operation === "sessions.delete") manifests.delete(args.siteId + ":" + args.sessionId);
      return response.payload;
    }
    const response = await browserFetch(PREFIX + route.path, {
      method: route.method, credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: Object.assign({ "X-Home-Lab-Site": route.siteId, Accept: "application/json" },
        route.body ? { "Content-Type": "application/json", "X-Home-Lab-CSRF": "1" } : {}),
      body: route.body ? JSON.stringify(route.body) : undefined,
    }, options);
    let payload; const bytes = await boundedBytes(response, MAX_JSON);
    try { payload = JSON.parse(new TextDecoder().decode(bytes)); } catch (_) { fail("lab_invalid_response", response.status); }
    current(epoch);
    if (!response.ok) fail(decodedError(payload, response.status), response.status);
    if (operation === "media.manifest") rememberManifest(args, payload);
    if (operation === "media.describe") rememberManifest(args, { generation: args.generation, items: [payload] });
    if (operation === "sessions.delete") manifests.delete(args.siteId + ":" + args.sessionId);
    return payload;
  }
  async function requestMedia(args, options) {
    if (root.__SIM_ACTIVE === true) fail("lab_simulation_transport_disabled");
    const siteId = site(args.siteId), sessionId = id(args.sessionId), mediaId = id(args.mediaId);
    const generation = integer(args.generation, 0, Number.MAX_SAFE_INTEGER);
    const offset = integer(args.offset, 0, Number.MAX_SAFE_INTEGER);
    const length = integer(args.length, 1, MAX_MEDIA);
    integer(offset + length - 1, 0, Number.MAX_SAFE_INTEGER);
    const epoch = connectionEpoch;
    if (nativeContext()) {
      const invoke = nativeInvoke(); if (!invoke) fail("lab_native_transport_unavailable");
      let manifest = manifests.get(siteId + ":" + sessionId);
      if (!manifest || (manifest.generation === generation && !manifest.items.has(mediaId))) {
        await request("media.describe", { siteId, sessionId, mediaId, generation }, options);
        current(epoch); manifest = manifests.get(siteId + ":" + sessionId);
      }
      if (manifest.generation !== generation) fail("lab_stale_generation");
      const handle = manifest.items.get(mediaId); if (!handle || offset >= handle.size) fail("lab_media_unknown");
      let response;
      const read = async () => invoke("lab_media_read", { request: { connectionGeneration: await nativeGeneration, siteId, sessionId, mediaId, generation, offset, length } });
      try {
        try { response = await read(); }
        catch (error) {
          if (error !== "lab_manifest_required" && error !== "lab_media_unknown") throw error;
          await request("media.describe", { siteId, sessionId, mediaId, generation }, options);
          current(epoch); response = await read();
        }
      }
      catch (error) { fail(typeof error === "string" && /^lab_[a-z0-9_]+$/.test(error) ? error : "lab_native_media_failed"); }
      current(epoch);
      if (!(response instanceof ArrayBuffer)) fail("lab_native_binary_required");
      const data = new Uint8Array(response), end = Math.min(offset + length, handle.size) - 1;
      return Object.assign(validateMedia(data, handle.contentType, "bytes " + offset + "-" + end + "/" + handle.size, offset, length), { generation });
    }
    const response = await browserFetch(PREFIX + "/sessions/" + sessionId + "/media/" + mediaId + "?generation=" + generation, {
      credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: { "X-Home-Lab-Site": siteId, Range: "bytes=" + offset + "-" + (offset + length - 1) },
    }, options);
    if (response.status !== 206) { finishResponse(response); fail("lab_media_unavailable", response.status); }
    const data = await boundedBytes(response, length); current(epoch);
    return Object.assign(validateMedia(data, response.headers.get("content-type"),
      response.headers.get("content-range"), offset, length), { generation });
  }
  async function createNativeArchiveSink(args) {
    if(root.__SIM_ACTIVE===true)fail('lab_simulation_transport_disabled');
    const invoke=nativeInvoke();if(!nativeContext()||!invoke)fail('lab_native_transport_unavailable');
    const siteId=site(args.siteId),sessionId=id(args.sessionId),epoch=connectionEpoch;
    const connectionGeneration=await generation();current(epoch);
    const handle=await invoke('lab_archive_save_begin',{request:{connectionGeneration,siteId,sessionId}});
    if(handle===null)return null;
    if(typeof handle!=='string'||!ID.test(handle))fail('lab_invalid_archive_handle');
    let bytes=0,closed=false;
    const abort=async()=>{if(!closed){await invoke('lab_archive_save_abort',{request:{connectionGeneration,handle,bytes}});closed=true;}};
    try{current(epoch);}catch(error){await abort();throw error;}
    return {nativeSaveHandle:handle,
      async write(chunk){current(epoch);if(closed)fail('lab_archive_sink_unavailable');bytes+=chunk.length;},
      async close(){current(epoch);if(closed)fail('lab_archive_sink_unavailable');
        await invoke('lab_archive_save_commit',{request:{connectionGeneration,handle,bytes}});closed=true;},abort};
  }
  async function downloadArchive(args,sink,options) {
    if(root.__SIM_ACTIVE===true)fail('lab_simulation_transport_disabled');
    if(!sink||!['write','close','abort'].every(k=>typeof sink[k]==='function')||!root.HomePerceptionLabArchive?.verifier)fail('lab_archive_sink_unavailable');
    const siteId=site(args.siteId),sessionId=id(args.sessionId),epoch=connectionEpoch;
    let response,reader,abortNative;
    try {
      const page=await request('sessions.export',{siteId,sessionId,limit:1},options);current(epoch);
      const gen=integer(page.generation,0,Number.MAX_SAFE_INTEGER),snapshotId=page.snapshot_id;
      if(!/^[a-f0-9]{64}$/.test(snapshotId)||page.site_id!==siteId||page.session_id!==sessionId)fail('lab_invalid_archive_binding');
      const verify=root.HomePerceptionLabArchive.verifier({siteId,sessionId,generation:gen,snapshotId});
      if(options?.signal?.aborted)fail('lab_archive_cancelled');
      if(nativeContext()) {
        const invoke=nativeInvoke();if(!invoke)fail('lab_native_transport_unavailable');
        const connectionGeneration=await generation();current(epoch);
        const handle=await invoke('lab_archive_open',{request:{connectionGeneration,siteId,sessionId,generation:gen,snapshotId,saveHandle:sink.nativeSaveHandle??null}});
        if(typeof handle!=='string'||!ID.test(handle))fail('lab_invalid_archive_handle');
        let sequence=0;
        const cancel=()=>invoke('lab_archive_cancel',{request:{connectionGeneration,handle,sequence}});
        reader={
          async read(){
            const data=await invoke('lab_archive_read',{request:{connectionGeneration,handle,sequence}});
            if(!(data instanceof ArrayBuffer)||data.byteLength>65536)fail('lab_native_binary_required');
            sequence++;return {value:new Uint8Array(data),done:data.byteLength===0};
          },cancel,releaseLock(){}
        };
        abortNative=()=>{cancel().catch(()=>{});};
        options?.signal?.addEventListener('abort',abortNative,{once:true});
        current(epoch);
        if(options?.signal?.aborted){abortNative();fail('lab_archive_cancelled');}
      } else {
        response=await browserFetch(PREFIX+'/sessions/'+sessionId+'/archive?generation='+gen+'&snapshot_id='+snapshotId,{
        credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Home-Lab-Site':siteId,Accept:'application/x-tar'}
      },options,30*60*1000);
      if(response.status!==200||response.headers.get('content-type')!=='application/x-tar'||
        response.headers.get('x-lab-snapshot')!==snapshotId||response.headers.get('x-lab-generation')!==String(gen)||!response.body?.getReader)fail('lab_archive_unavailable');
        reader=response.body.getReader();
      }
      let size=0;
      for(;;){
        current(epoch);if(options?.signal?.aborted)fail('lab_archive_cancelled');
        const {value,done}=await reader.read();current(epoch);if(options?.signal?.aborted)fail('lab_archive_cancelled');if(done)break;
        size+=value.length;if(size>64*1024**3)fail('lab_archive_size_limit');
        await verify.push(value);current(epoch);if(options?.signal?.aborted)fail('lab_archive_cancelled');
        await sink.write(value);current(epoch);if(options?.signal?.aborted)fail('lab_archive_cancelled');
        options?.onProgress?.({bytes:size});
      }
      const result=verify.finish();current(epoch);if(options?.signal?.aborted)fail('lab_archive_cancelled');
      await sink.close();return {...result,bytes:size};
    } catch(error){
      try{await sink.abort();}catch(_){}
      throw error instanceof LabTransportError ? error : new LabTransportError(
        typeof error==='string'&&/^lab_[a-z0-9_]+$/.test(error)?error:'lab_archive_failed');
    } finally {
      try{await reader?.cancel();}catch(_){}
      if(abortNative)options?.signal?.removeEventListener('abort',abortNative);
      reader?.releaseLock();if(response)finishResponse(response);
    }
  }
  root.HomePerceptionLabTransport = Object.freeze({ request, requestMedia, createNativeArchiveSink, downloadArchive, invalidate, LabTransportError });
})(window);
