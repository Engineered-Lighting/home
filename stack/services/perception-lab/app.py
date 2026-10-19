"""FastAPI application factory. No module-level devices, models, or subprocesses."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import base64
import json
import hashlib
import os
import re
import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from auth import Authorizer, AuthorizationError
from config import Settings
from controller import Controller
from store import Store, LabError
from exports import export_page
from portable_export import archive_chunks

PREFIX="/api/perception-lab/v1"


def parse_range(value,size,maximum):
    if not value:
        if size>maximum:
            raise LabError("bounded byte Range required",416,"range_required")
        return 0,size-1,False
    match=re.fullmatch(r"bytes=(\d+)-(\d*)",value)
    if not match:
        raise LabError("one forward byte range required",416,"invalid_range")
    start=int(match[1]); end=int(match[2]) if match[2] else min(size-1,start+maximum-1)
    if start>=size or end<start or end-start+1>maximum:
        raise LabError("byte range outside bound",416,"invalid_range")
    return start,min(end,size-1),True


def create_app(settings=None,runtime=None,store=None,*,runtime_factory=None,previews=None,live_planes=None,live_videos=None,refresh_backend=None,refresh_gateway_subject=None):
    if settings is None:
        path=os.environ.get("PERCEPTION_LAB_CONFIG")
        if not path:
            raise RuntimeError("PERCEPTION_LAB_CONFIG must explicitly provision this service")
        settings=Settings.from_file(path)
    if runtime is not None and runtime_factory is not None:
        raise ValueError("provide a runtime or a factory, not both")
    ledger=store or Store(settings)
    try:
        # A reviewed Python wrapper may compose workers against this exact ledger.
        # Configuration cannot select modules or arbitrary code to import.
        if runtime_factory is not None:
            runtime=runtime_factory(ledger)
            if runtime is None:
                raise ValueError("runtime factory did not return a runtime")
        controller=Controller(ledger,runtime)
        authorizer=Authorizer(settings.site_id,settings.token_grants)
    except Exception:
        drained=runtime is None
        if runtime is not None and callable(getattr(runtime,"close",None)):
            try:
                drained=runtime.close().get("drained") is True
            except Exception:
                drained=False
        if store is None and drained:
            ledger.close()
        raise

    @asynccontextmanager
    async def lifespan(application):
        stopping=asyncio.Event()
        async def expiry():
            while not stopping.is_set():
                try:
                    await asyncio.wait_for(stopping.wait(),timeout=1)
                except asyncio.TimeoutError:
                    await asyncio.to_thread(controller.expire)
        task=asyncio.create_task(expiry())
        try:
            if refresh_backend is not None and callable(getattr(refresh_backend,'start',None)):
                await refresh_backend.start()
            yield
        finally:
            stopping.set()
            clean=True
            guard=getattr(application.state,'den_shutdown_guard',None)
            if guard is not None:
                try:
                    clean=await asyncio.to_thread(guard) is True
                except Exception:
                    clean=False
                if not clean:
                    # A native operation may still own the camera/model/store.
                    # Preserve it and report unconfirmed drain; never close the
                    # incumbent resources underneath an unresolved operation.
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    application.state.shutdown_drained=False
            if clean:
                if refresh_backend is not None and callable(getattr(refresh_backend,'close',None)):
                    try:
                        await refresh_backend.close()
                    except Exception:
                        clean=False
                for preview in [*(previews or {}).values(),*(live_videos or {}).values()]:
                    if callable(getattr(preview,'close',None)):
                        try:
                            clean=(await asyncio.to_thread(preview.close)).get('drained') is True and clean
                        except Exception:
                            clean=False
                try:
                    await task
                except Exception:
                    clean=False
                for session in ledger.list_sessions():
                    if session["state"] in {"starting","recording","stopping"}:
                        try:
                            await asyncio.to_thread(controller.stop,session["id"],"controller_shutdown")
                        except Exception:
                            clean=False
                drained = runtime is None
                if runtime is not None and callable(getattr(runtime,"close",None)):
                    try:
                        outcome=await asyncio.to_thread(runtime.close)
                        drained=isinstance(outcome,dict) and outcome.get("drained") is True
                    except Exception:
                        drained=False
                # Injected stores remain caller-owned. For a production runtime,
                # absence of a drain receipt cannot establish safe database closure.
                application.state.shutdown_drained=drained and clean
                if store is None and drained and clean:
                    ledger.close()

    app=FastAPI(title="Perception Lab",version="1",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.controller=controller
    app.state.store=ledger
    archive_active=False

    @app.middleware("http")
    async def security(request,call_next):
        try:
            principal=authorizer.authenticate(request.headers.get("authorization"))
            selected=request.headers.get("x-home-lab-site")
            if selected is not None and selected!=settings.site_id:
                raise LabError("credential site mismatch",403,"site_mismatch")
            request.state.principal=principal
            response=await call_next(request)
        except AuthorizationError:
            response=JSONResponse({"error":{"code":"unauthorized","message":"Lab credential unavailable or invalid"}},status_code=401)
        except LabError as error:
            response=JSONResponse({"error":{"code":error.code,"message":str(error)}},status_code=error.status)
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Content-Security-Policy"]="default-src 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(LabError)
    async def lab_error(request,error):
        return JSONResponse({"error":{"code":error.code,"message":str(error)}},status_code=error.status)

    @app.exception_handler(AuthorizationError)
    async def denied(request,error):
        return JSONResponse({"error":{"code":"forbidden","message":"credential lacks operation capability"}},status_code=403)

    @app.exception_handler(Exception)
    async def internal(request,error):
        return JSONResponse({"error":{"code":"internal_error","message":"Lab operation failed"}},status_code=500)

    def require(request,scope):
        request.state.principal.require(scope)
        return request.state.principal

    async def bounded_body(request,maximum):
        result=bytearray()
        async for chunk in request.stream():
            if len(result)+len(chunk)>maximum:
                raise LabError("request exceeds size bound",413,"too_large")
            result.extend(chunk)
        return bytes(result)

    from camera_refresh import install_camera_refresh
    install_camera_refresh(app, settings=settings, require=require, bounded_body=bounded_body,
                           backend=refresh_backend, gateway_subject=refresh_gateway_subject)

    async def body(request,required=(),optional=()):
        try:
            def bad_constant(value):
                raise ValueError("non-finite JSON")
            data=json.loads(await bounded_body(request,65536),parse_constant=bad_constant)
        except (ValueError,UnicodeError):
            raise LabError("invalid JSON request",422,"invalid_request") from None
        if not isinstance(data,dict) or any(k not in data for k in required):
            raise LabError("required request fields missing",422,"invalid_request")
        if "site_id" in data and data["site_id"]!=settings.site_id:
            raise LabError("request site mismatch",403,"site_mismatch")
        if set(data)-set(required)-set(optional)-{"site_id"}:
            raise LabError("unknown request fields",422,"invalid_request")
        data.pop("site_id",None)
        return data

    @app.get(PREFIX+"/capabilities")
    async def capabilities(request:Request):
        require(request,"read")
        result=await asyncio.to_thread(controller.capabilities)
        result['live_previews']={camera:{'modality':'rgb','sample_interval_ms':1000}
            for camera in (previews or {})}
        result['live_planes']={camera:['depth','validity','left','right'] for camera in (live_planes or {})}
        result['live_videos']={camera:True for camera in (live_videos or {})}
        return result

    @app.get(PREFIX+"/cameras/{camera_id}/video")
    async def camera_video(camera_id:str,request:Request):
        require(request,'read')
        if camera_id not in {c.get('id') for c in settings.cameras}:
            raise LabError('Camera not registered',404,'unknown_camera')
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=len(dict(pairs)) or set(dict(pairs))-{'after_sequence','generation'}:
            raise LabError('Invalid video cursor',422,'invalid_cursor')
        cursor=request.query_params.get('after_sequence')
        if cursor is not None and (not re.fullmatch(r'(0|[1-9][0-9]{0,14})',cursor)):
            raise LabError('Invalid video cursor',422,'invalid_cursor')
        source=(live_videos or {}).get(camera_id)
        if source is None:raise LabError('Live video not configured',503,'preview_not_configured')
        return await asyncio.to_thread(source.read,int(cursor) if cursor is not None else None,request.query_params.get('generation'))

    @app.get(PREFIX+"/cameras/{camera_id}/planes/{modality}")
    async def camera_plane(camera_id:str,modality:str,request:Request):
        require(request,'read')
        if camera_id not in {c.get('id') for c in settings.cameras}:
            raise LabError('Camera not registered',404,'unknown_camera')
        source=(live_planes or {}).get(camera_id)
        if source is None:raise LabError('Numerical preview not configured',503,'plane_not_configured')
        return await asyncio.to_thread(source.read,modality)

    @app.get(PREFIX+"/cameras/{camera_id}/preview")
    async def camera_preview(camera_id:str,request:Request):
        require(request,"read")
        if camera_id not in {c.get('id') for c in settings.cameras}:
            raise LabError('Camera not registered at this site',404,'unknown_camera')
        source=(previews or {}).get(camera_id)
        if source is None:
            raise LabError('Live preview is not connected',503,'preview_not_configured')
        return await asyncio.to_thread(source.read)

    @app.get(PREFIX+"/cameras/{camera_id}/context")
    async def camera_context(camera_id:str,request:Request):
        require(request,"read")
        if camera_id not in {c.get('id') for c in settings.cameras}:
            raise LabError('Camera not registered at this site',404,'unknown_camera')
        path=os.environ.get('PERCEPTION_LAB_SHARED_CAPTION_FILE')
        if not path:
            return {'status':'missing','site_id':settings.site_id,'camera_id':camera_id}
        from shared_caption import read_caption
        return await asyncio.to_thread(read_caption,path,settings.site_id,camera_id)

    @app.get(PREFIX+"/sessions")
    async def sessions(request:Request):
        require(request,"read")
        return {"items":await asyncio.to_thread(ledger.list_sessions),"site_id":settings.site_id,"version":1}

    @app.post(PREFIX+"/sessions",status_code=201)
    async def start_session(request:Request):
        require(request,"record")
        value=await body(request,("camera_id","experiment_id","name","request_id"),("reasoning_enabled",))
        if value.get("reasoning_enabled") is True:require(request,"run")
        return await asyncio.to_thread(controller.start,value)

    @app.get(PREFIX+"/sessions/{sid}")
    async def session_detail(sid:str,request:Request,at_ms:int|None=None):
        require(request,"read")
        return await asyncio.to_thread(controller.detail,sid,at_ms=at_ms)

    @app.get(PREFIX+"/sessions/{sid}/live")
    async def live(sid:str,request:Request):
        require(request,"read")
        return await asyncio.to_thread(controller.live,sid)

    def exact_query(request,allowed,required=()):
        pairs=list(request.query_params.multi_items())
        if len(pairs)!=len(dict(pairs)) or set(dict(pairs))-set(allowed) or set(required)-set(dict(pairs)):
            raise LabError("unsupported query",422,"invalid_query")

    # Graph reads are bounded, read-only ledger projections computed off the loop.
    @app.get(PREFIX+"/sessions/{sid}/graph")
    async def graph(sid:str,request:Request,at_ms:int|None=None):
        require(request,"read")
        exact_query(request,{"at_ms"})
        return await asyncio.to_thread(controller.graph,sid,at_ms)

    @app.get(PREFIX+"/sessions/{sid}/timeline")
    async def timeline(sid:str,request:Request,from_ms:int|None=None,to_ms:int|None=None):
        require(request,"read")
        exact_query(request,{"from_ms","to_ms"})
        return await asyncio.to_thread(controller.timeline,sid,from_ms,to_ms)

    @app.get(PREFIX+"/sessions/{sid}/replay")
    async def replay(sid:str,request:Request,at_ms:int|None=None):
        require(request,"read")
        exact_query(request,{"at_ms"},("at_ms",))
        return await asyncio.to_thread(controller.replay,sid,at_ms)

    @app.post(PREFIX+"/sessions/{sid}/stop")
    async def stop(sid:str,request:Request):
        require(request,"record")
        await body(request,(),("request_id",))
        return await asyncio.to_thread(controller.stop,sid)

    @app.post(PREFIX+"/sessions/{sid}/switch")
    async def switch(sid:str,request:Request):
        require(request,"run")
        value=await body(request,("request_id",),("arm","profile_id","experiment_id","reasoning_enabled","expected_reasoning_revision"))
        return await asyncio.to_thread(controller.switch,sid,value)

    @app.post(PREFIX+"/sessions/{sid}/bookmarks")
    async def bookmark(sid:str,request:Request):
        principal=require(request,"review")
        value=await body(request,("at_ms","label","request_id"))
        return controller.bookmark(sid,value,principal.subject)

    @app.put(PREFIX+"/sessions/{sid}/annotations/{aid}")
    async def annotation(sid:str,aid:str,request:Request):
        principal=require(request,"review")
        value=await body(request,("expected_revision","at_ms","label","origin"),("end_ms","notes","request_id","reviewed","time_basis","video_init_id"))
        expected=value.pop("expected_revision")
        return ledger.save_annotation(sid,aid,value,expected,principal.subject)

    @app.delete(PREFIX+"/sessions/{sid}/annotations/{aid}")
    async def delete_annotation(sid:str,aid:str,request:Request):
        principal=require(request,"review")
        value=await body(request,("expected_revision",))
        return ledger.save_annotation(sid,aid,{},value["expected_revision"],principal.subject,delete=True)

    @app.get(PREFIX+"/jobs")
    async def jobs(request:Request,session_id:str=None):
        require(request,"read")
        return {"items":ledger.list_jobs(session_id)}

    @app.post(PREFIX+"/jobs",status_code=201)
    async def create_job(request:Request):
        value=await body(request,("kind","request_id"),("parameters","session_id"))
        require(request,"prepare" if value["kind"]=="preparation" else "run")
        if not isinstance(value.get("parameters",{}),dict):
            raise LabError("job parameters must be an object",422,"invalid_request")
        return await asyncio.to_thread(controller.job,value)

    @app.post(PREFIX+"/diagnostics",status_code=201)
    async def diagnostics(request:Request):
        require(request,"run")
        value=await body(request,("request_id",),("parameters","session_id"))
        return await asyncio.to_thread(controller.job,{**value,"kind":"diagnostic"})

    @app.post(PREFIX+"/jobs/{jid}/cancel")
    async def cancel(jid:str,request:Request):
        job=ledger.get_job(jid)
        require(request,"prepare" if job["kind"]=="preparation" else "run")
        await body(request,(),("request_id",))
        return await asyncio.to_thread(controller.cancel_job,jid)

    @app.delete(PREFIX+"/sessions/{sid}")
    async def delete_session(sid:str,request:Request):
        require(request,"delete")
        value=await body(request,("expected_revision",),("erase_records",))
        if 'erase_records' in value and type(value['erase_records']) is not bool:
            raise LabError('explicit erasure boolean required',422,'invalid_scope')
        if value.get('erase_records') is True:
            return await asyncio.to_thread(controller.delete,sid,value['expected_revision'],erase_records=True)
        return ledger.delete_session(sid,value["expected_revision"])

    @app.get(PREFIX+"/sessions/{sid}/export")
    async def export(sid:str,request:Request,snapshot_id:str|None=None,cursor:int=0,limit:int=100):
        require(request,"export")
        return await asyncio.to_thread(export_page,ledger,sid,snapshot_id=snapshot_id,cursor=cursor,limit=limit)

    @app.get(PREFIX+"/sessions/{sid}/archive")
    async def archive(sid:str,request:Request,generation:int,snapshot_id:str):
        nonlocal archive_active
        principal=require(request,"export")
        principal.require("read")
        if set(request.query_params)!={"generation","snapshot_id"} or len(request.query_params.multi_items())!=2:
            raise LabError("exact archive bindings required",422,"invalid_archive_request")
        session=ledger.get_session(sid)
        if generation!=session["generation"]:
            raise LabError("archive generation changed",409,"generation_conflict")
        # One stream per controller, with no disk spool or unbounded writer queue.
        # This flag is accessed only on the application's async event loop.
        if archive_active: raise LabError("archive transfer already active",429,"archive_busy")
        archive_active=True
        def authorized():
            current=authorizer.authenticate(request.headers.get("authorization"))
            current.require("export"); current.require("read")
            return current==principal
        iterator=archive_chunks(ledger,sid,authorize=authorized,snapshot_id=snapshot_id)
        try:
            # Validate before committing HTTP headers so stale snapshots return
            # a typed error rather than a successful empty download.
            first=await anyio.to_thread.run_sync(next,iterator,None)
        except BaseException:
            iterator.close(); archive_active=False
            raise
        async def stream():
            nonlocal archive_active
            try:
                yield first
                while True:
                    chunk=await anyio.to_thread.run_sync(next,iterator,None)
                    if chunk is None: break
                    yield chunk
            finally:
                # Close open media handles even when the client disconnects.
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(iterator.close)
                archive_active=False
        return StreamingResponse(stream(),media_type="application/x-tar",headers={
            "Content-Disposition":f'attachment; filename="perception-lab-{sid}.tar"',
            "X-Lab-Snapshot":snapshot_id,"X-Lab-Generation":str(generation)})

    @app.get(PREFIX+"/sessions/{sid}/media")
    async def manifest(sid:str,request:Request,cursor:int=0,limit:int=100,kind:str|None=None,at_ms:int|None=None,encoded_at_ms:int|None=None,radius_ms:int=30000):
        require(request,"read")
        return await asyncio.to_thread(ledger.list_media,sid,cursor,limit,kind=kind,at_ms=at_ms,encoded_at_ms=encoded_at_ms,radius_ms=radius_ms)

    @app.get(PREFIX+"/sessions/{sid}/events")
    async def events(sid:str,request:Request,after_id:int=0,limit:int=1000):
        require(request,"read")
        if after_id<0 or not 1<=limit<=5000:
            raise LabError("invalid event page",422,"invalid_page")
        items=await asyncio.to_thread(ledger.list_events,sid,after_id,limit)
        return {"items":items,"after_id":items[-1]["id"] if items else after_id}

    @app.get(PREFIX+"/sessions/{sid}/findings")
    async def findings(sid:str,request:Request):
        require(request,"read")
        return await asyncio.to_thread(controller.findings,sid)

    @app.get(PREFIX+"/sessions/{sid}/media/{mid}")
    async def media(sid:str,mid:str,request:Request,generation:int):
        require(request,"read")
        # Whole-object hashing and reads stay off the event loop so one media
        # request cannot delay /live responses for other clients.
        def read():
            from apartment_viewing_source import registered_media,is_viewing_id,viewing_bytes
            # Home routes only: a development-retained metric PNG stays readable.
            item=registered_media(ledger,sid,mid,generation,fresh=True,retained=True)
            start,end,partial=parse_range(request.headers.get("range"),item["size"],settings.max_read_bytes)
            if is_viewing_id(mid):
                original=viewing_bytes(ledger,sid,mid,generation,retained=True)
                if len(original)!=item['size'] or hashlib.sha256(original).hexdigest()!=item['sha256']:
                    raise LabError('viewing media integrity failure',409,'media_integrity')
                return item,start,end,partial,original[start:end+1]
            path=ledger.media_path(sid,mid)
            try:
                with path.open("rb") as source:
                    if os.fstat(source.fileno()).st_size!=item["size"]:
                        raise LabError("media integrity failure",409,"media_integrity")
                    digest=hashlib.sha256()
                    while chunk:=source.read(65536):
                        digest.update(chunk)
                    if digest.hexdigest()!=item["sha256"]:
                        raise LabError("media integrity failure",409,"media_integrity")
                    source.seek(start); data=source.read(end-start+1)
            except FileNotFoundError:
                raise LabError("media unavailable",410,"media_unavailable") from None
            return item,start,end,partial,data
        item,start,end,partial,data=await asyncio.to_thread(read)
        # Reading an already-open file is not proof that its handle remains
        # valid. Recheck deletion/generation and authority before delivery.
        require(request,"read")
        from apartment_viewing_source import registered_media
        await asyncio.to_thread(registered_media,ledger,sid,mid,generation,fresh=True,retained=True)
        headers={"Accept-Ranges":"bytes","ETag":'"'+item["sha256"]+'"',"Content-Length":str(len(data)),"Content-Disposition":"inline"}
        if partial:
            headers["Content-Range"]=f"bytes {start}-{end}/{item['size']}"
        return Response(data,status_code=206 if partial else 200,media_type=item["mime_type"],headers=headers)

    @app.get(PREFIX+"/sessions/{sid}/media/{mid}/descriptor")
    async def describe_media(sid:str,mid:str,request:Request,generation:int):
        require(request,"read")
        from apartment_viewing_source import registered_media
        return registered_media(ledger,sid,mid,generation,fresh=True,retained=True)

    @app.post(PREFIX+"/sessions/{sid}/samples",status_code=201)
    async def ingest(sid:str,request:Request):
        require(request,"ingest")
        raw=request.headers.get("x-perception-sample","")
        if len(raw)>32768:
            raise LabError("sample metadata too large",413,"too_large")
        try:
            metadata=json.loads(base64.b64decode(raw+"="*((-len(raw))%4),altchars=b"-_",validate=True))
            if not isinstance(metadata,dict):
                raise ValueError()
        except (ValueError,UnicodeError):
            raise LabError("invalid sample metadata",422,"invalid_sample") from None
        data=await bounded_body(request,settings.max_object_bytes)
        try:
            return await asyncio.to_thread(controller.recorder.ingest,sid,data,metadata,request.headers.get("content-type","application/octet-stream").split(";")[0])
        except LabError as error:
            if error.code in {"quota_exceeded","duration_limit"}:
                await asyncio.to_thread(controller.stop,sid,error.code)
            raise

    return app
