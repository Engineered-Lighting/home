"""Independent bounded LA pose pump and read-only presentation ingress.

Uses the admitted resident RemoteReceiver, preserving model fingerprints,
single execution, expiry, resource/count limits and durable completion proof.
No Qwen/JEV, Lab recording, PNG fetch, original retention or lighting calls.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import threading
import time
import uuid

from reasoning import canonical,digest
from fast_tracking_depth import decode_paired_depth_frame,DepthProjector


def tracking_trace(stage,packet,*,stream_id=None,at_s=None,**extra):
    if os.environ.get('DEN_FAST_TRACKING_TRACE')!='1':return
    print('[den-fast-tracking-trace-v1] '+canonical(dict(stage=stage,at_s=time.monotonic() if at_s is None else at_s,
        clock_domain='la_monotonic',stream_id=stream_id,attempt_id=packet.get('attempt_id'),sequence=packet.get('sequence'),
        frame_key=packet.get('frame_key'),**extra)),flush=True)


def private_json(path,maximum=131072):
    return json.loads(private_bytes(path,maximum))


def private_bytes(path,maximum=131072):
    p=Path(path)
    if not p.is_absolute() or any(q.is_symlink() for q in (p,*p.parents)):raise ValueError('tracking_private_path')
    import os
    s=p.stat()
    if not p.is_file() or s.st_uid!=os.geteuid() or s.st_mode&0o077 or s.st_nlink!=1 or not 0<s.st_size<=maximum:
        raise ValueError('tracking_private_configuration')
    return p.read_bytes()


def load_tracking_alignment(config):
    from apartment_view_alignment import bind_view_alignment_successor
    if not isinstance(config,dict) or set(config)!={'files','pack'} or set(config['files'])!={'alignment','review','source_candidate','successor','successor_review'}:
        raise ValueError('tracking_reviewed_alignment_required')
    root=Path('/home/marcelo-lima/home-lab-yolo26-trial/fast-tracking-private')
    originals={}
    for role,entry in config['files'].items():
        if set(entry)!={'path','sha256'} or not Path(entry['path']).is_relative_to(root):raise ValueError('tracking_alignment_path')
        raw=private_bytes(entry['path'])
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('tracking_alignment_digest')
        originals[role]=raw
    bound=bind_view_alignment_successor(alignment_original_bytes=originals['alignment'],review_original_bytes=originals['review'],
        source_candidate_original_bytes=originals['source_candidate'],successor_original_bytes=originals['successor'],
        successor_review_original_bytes=originals['successor_review'])
    return config,bound


class CameraClient:
    """One provisioned camera endpoint over the private LAN/Tailscale path."""
    def __init__(self,config):
        if set(config)!={'host','port','token_file'} or config['host']!='192.168.1.168' or config['port']!=8090:
            raise ValueError('tracking_fixed_camera_endpoint')
        self.config=config;self.connection=None
    def call(self,path,value=None):
        if path not in {'/tracking/frame','/tracking/project'}:raise ValueError('tracking_camera_route')
        import os
        p=Path(self.config['token_file']);s=p.stat()
        if p.is_symlink() or s.st_uid!=os.geteuid() or s.st_mode&0o077 or s.st_nlink!=1 or not 32<=s.st_size<=1024:
            raise ValueError('tracking_camera_grant')
        token=p.read_text().strip()
        if '\n' in token or not 32<=len(token)<=512:raise ValueError('tracking_camera_grant')
        if self.connection is None:self.connection=http.client.HTTPConnection(self.config['host'],self.config['port'],timeout=1)
        start=time.monotonic()
        try:
            self.connection.request('POST' if value is not None else 'GET',path,
                canonical(value).encode() if value is not None else None,
                {'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept-Encoding':'identity'})
            reply=self.connection.getresponse();raw=reply.read(1024*1024+1);end=time.monotonic()
            if reply.will_close:self.connection.close();self.connection=None
            if reply.status!=200 or len(raw)>1024*1024 or reply.getheader('Content-Type')!='application/json':
                raise ValueError('tracking_camera_unavailable')
            result=json.loads(raw);return result,start,end
        except BaseException:
            if self.connection:self.connection.close();self.connection=None
            raise
    def close(self):
        if self.connection:self.connection.close();self.connection=None


class TrackingPump:
    def __init__(self,receiver,config,fresh,*,camera=None,clock=time.monotonic):
        if (set(config)!={'camera','read_token_sha256','expected_camera_epoch','calibration_content_id','scene','alignment'}
                or not isinstance(config['read_token_sha256'],str) or len(config['read_token_sha256'])!=64):
            raise ValueError('tracking_configuration_contract')
        self.config,bound=deepcopy(config),None
        configured,bound=load_tracking_alignment(config['alignment'])
        self.alignment=bound.document;self.local_projector=DepthProjector()
        if (self.alignment['camera']['calibration_content_id']!=config['calibration_content_id']
                or configured['pack']!=self.alignment['pack']
                or config['expected_camera_epoch']!=(getattr(bound,'verified_epoch',None) or self.alignment['camera']['capture_epoch'])):
            raise ValueError('tracking_reviewed_camera_binding')
        scene=config['scene'];pack=self.alignment['pack']
        if scene!={'siteId':'victoria','contentRevision':pack['content_revision'],'frameDigest':pack['frame_sha256'],
                  'registrationVersion':pack['registration_version'],'collisionDigest':pack['collision_sha256'],'cameraId':'den'}:
            raise ValueError('tracking_reviewed_scene_binding')
        self.receiver,self.camera,self.fresh,self.clock=receiver,camera or CameraClient(config['camera']),fresh,clock
        self.capture_camera=camera or CameraClient(config['camera'])
        self.lock=threading.Lock();self.stop_event=threading.Event();self.thread=None
        self.attempt=uuid.uuid4().hex;self.sequence=0;self.latest=None;self.previous=None
        self.samples=[];self.drops=0;self.capture_thread=None;self.offered=None
        self.reason='awaiting_first_frame'
    def start(self):
        self.capture_thread=threading.Thread(target=self.capture_loop,name='den-fast-frames',daemon=True);self.capture_thread.start()
        self.thread=threading.Thread(target=self.run,name='den-fast-tracking',daemon=True);self.thread.start()
    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(4)
        if self.capture_thread:self.capture_thread.join(2)
        if self.thread and self.thread.is_alive() or self.capture_thread and self.capture_thread.is_alive():return False
        self.camera.close();self.capture_camera.close()
        with self.lock:self.latest=None
        return True
    def snapshot(self):
        with self.lock:
            if self.latest is None or self.clock()-self.latest['published_monotonic_s']>.75:
                return {'schema':'home-fast-tracking-1','attempt_id':self.attempt,'sequence':self.sequence,
                        'status':'unavailable','reason':self.reason,'dropped':self.drops,'lighting_authority':False,'physical_permission':False}
            return deepcopy(self.latest)
    def tick(self):
        if self.capture_thread is None:
            frame,sent,received=self.capture_camera.call('/tracking/frame')
        else:
            with self.lock:offered=self.offered;self.offered=None
            if offered is None:return False
            frame,sent,received=offered
        identity=frame['identity'];key=frame['frame_key']
        if (frame.get('schema') not in ('home-fast-camera-1','home-fast-camera-2') or digest(identity)!=key
                or identity['epoch']!=self.config['expected_camera_epoch']
                or identity['calibration_content_id']!=self.config['calibration_content_id']):
            raise ValueError('tracking_camera_binding_changed')
        if self.previous is not None and identity['sequence']<=self.previous:return False
        self.previous=identity['sequence']
        # Every request supplies its own network bracket. No wall-clock/NTP
        # assumption and no old clock fit promoted to an exposure guarantee.
        interval=frame['source_interval_s'];reply=frame['camera_reply_s']
        observed=[sent-reply+interval[0],received-reply+interval[1]]
        if not 0<=received-observed[1]<=.75 or observed[1]<observed[0]:raise ValueError('tracking_original_expired')
        paired=decode_paired_depth_frame(frame) if frame['schema']=='home-fast-camera-2' else None
        now_wall=time.time();age=max(0,self.clock()-observed[0]);expiry=now_wall+.75-age
        if age>=.75:raise ValueError('tracking_original_expired')
        source=dict(site_id='victoria',camera_id='den',session_id=self.attempt,capture_epoch=identity['epoch'],
            frame_id=key,media_ids=[key],observed_at=now_wall-age,available_at=now_wall,valid_until=expiry)
        image=dict(encoding='jpeg',channel_order='RGB',decoded_worker_order='BGR',width=640,height=360,
            source_media_id=key,encoded_sha256=frame['jpeg_sha256'],bgr_sha256=None,data_base64=frame['jpeg_b64'])
        request=dict(request_id=digest([self.attempt,key]),profile=self.receiver.profile,frame=source,arm='b',generation=0)
        body={'schema_version':1,'request':request,'image':image}
        reference=dict(request_id=request['request_id'],body_sha256=digest(body),site_id='victoria',camera_id='den',
            session_id=self.attempt,profile=self.receiver.profile)
        if not self.fresh():raise ValueError('tracking_admission_ended')
        started=self.clock()
        proof=self.receiver.execute({**reference,'body':body},source_site='victoria')
        model_completed=self.clock();inference_ms=(model_completed-started)*1000
        if proof.get('terminal') is not True or proof.get('state')!='completed':return False
        if proof['result'].get('frame_id')!=key:raise ValueError('tracking_model_frame_mismatch')
        poses=proof['result'].get('pose_instances',[])
        if paired is not None:
            projected=dict(schema='home-fast-depth-1',frame_key=key,identity=identity,instances=self.local_projector.project(paired,poses))
        else:
            projected,_,_=self.camera.call('/tracking/project',{'frame_key':key,'identity':identity,'poses':poses})
        if projected.get('schema')!='home-fast-depth-1' or projected['frame_key']!=key or projected['identity']!=identity:
            raise ValueError('tracking_depth_frame_mismatch')
        if not self.fresh() or time.time()>expiry or self.clock()-observed[0]>.75:raise ValueError('tracking_original_expired')
        m=self.alignment['matrix'];people=[]
        for instance in projected['instances']:
            joints=[]
            for joint in instance['joints']:
                xyz=joint['camera_xyz_m']
                point=[sum(m[r][k]*xyz[k] for k in range(3))+m[r][3] for r in range(3)]
                joints.append(dict(id=joint['id'],position_m=point,visible=True))
            people.append(dict(instance_id=instance['instance_id'],joints=joints))
        now=self.clock();self.sequence+=1
        packet=dict(schema='home-fast-tracking-1',attempt_id=self.attempt,sequence=self.sequence,status='available',
            frame_key=key,identity=identity,scene=self.config['scene'],people=people,
            source_la_monotonic_interval_s=observed,published_monotonic_s=now,expires_monotonic_s=observed[0]+.75,
            source_clock='sdk_rgb_host_timestamp',exposure_accuracy='unqualified',
            projection='unqualified_nominal_eeprom',label='Approximate room placement',
            model=self.receiver.profile,inference_ms=inference_ms,inference_and_projection_ms=(now-started)*1000,stage_timings_ms=proof['result'].get('stage_timings_ms'),
            dropped=self.drops,lighting_authority=False,physical_permission=False,identity_authority=False,
            alignment_sha256=digest(self.alignment),mount_check='mount_check_skipped_development')
        # Opt-in diagnostic evidence travels with this exact packet and its
        # original expiry. It supplies neither a fitted body nor a temporal
        # person track. Keep coordinates out of the journal trace path.
        instances=projected['instances']
        if (os.environ.get('DEN_FAST_TRACKING_BODY_DIAGNOSTICS')=='1'
                and all('joint_observations' in instance for instance in instances)):
            packet['pose_observations']=dict(schema='home-body-observations-1',frame_key=key,
                association='frame_scoped_detection',image_size=[640,360],
                camera_coordinate_space='rectified_left_x_right_y_down_z_forward_m',
                point_semantics='visible_depth_surface_not_anatomical_joint_center',
                people=[dict(instance_id=instance['instance_id'],joints=deepcopy(instance['joint_observations']))
                        for instance in instances])
        with self.lock:self.latest=packet;self.reason=None
        tracking_trace('worker_model_completed',packet,at_s=model_completed)
        tracking_trace('worker_published',packet,at_s=now,people=len(people),joints=sum(len(p['joints']) for p in people),
            expires_monotonic_s=packet['expires_monotonic_s'],source_la_monotonic_interval_s=observed)
        return True
    def capture_loop(self):
        while not self.stop_event.is_set() and self.fresh():
            start=self.clock()
            try:
                offered=self.capture_camera.call('/tracking/frame')
                with self.lock:
                    if self.offered is not None:self.drops+=1
                    self.offered=offered  # exactly one supersedable pending frame
            except Exception:
                with self.lock:self.offered=None
                self.stop_event.wait(.1)
            self.stop_event.wait(max(0,1/15-(self.clock()-start)))
    def run(self):
        while not self.stop_event.is_set() and self.fresh():
            start=self.clock()
            try:self.tick()
            except Exception as error:
                self.drops+=1
                import re
                reason=str(error)
                with self.lock:
                    self.latest=None
                    self.reason=reason if re.fullmatch(r'tracking_[a-z_]{1,80}',reason) else 'tracking_'+type(error).__name__
                # No resend of uncertain model work. A new frame may proceed
                # only while receiver admission confirms no unresolved work.
                self.stop_event.wait(.1)
            self.stop_event.wait(max(0,.1-(self.clock()-start)))


class TrackingHTTP:
    def __init__(self,worker,pump):self.worker,self.pump=worker,pump;self.clients=0;self.readers=0
    async def __call__(self,scope,receive,send):
        if scope.get('type')=='lifespan':
            while True:
                message=await receive()
                if message['type']=='lifespan.startup':self.pump.start();await send({'type':'lifespan.startup.complete'})
                elif message['type']=='lifespan.shutdown':
                    if await asyncio.to_thread(self.pump.close) is not True:
                        await send({'type':'lifespan.shutdown.failed','message':'tracking execution unresolved; ledger retained'});return
                    await self.worker.shutdown()
                    await send({'type':'lifespan.shutdown.complete'});return
        if scope.get('type')!='http':return
        path=scope.get('path');headers=scope.get('headers',[])
        values=[v for k,v in headers if k.lower()==b'authorization']
        valid=(len(values)==1 and values[0].startswith(b'Bearer ') and
               hmac.compare_digest(hashlib.sha256(values[0][7:]).hexdigest(),self.pump.config['read_token_sha256']))
        status=200 if valid and scope.get('scheme')=='https' and scope.get('method')=='GET' and not scope.get('query_string') else 403
        if path not in {'/v1/tracking/stream','/v1/tracking/clock'}:status=404
        # Long-lived viewers cannot consume the clock refresh admission slots.
        if path.endswith('/stream') and self.clients>=2:status=429
        if path.endswith('/clock') and self.readers>=2:status=429
        if status!=200:
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':b'{"error":"tracking_unavailable"}'});return
        if path.endswith('/clock'):
            self.readers+=1
            try:
                data=canonical({'attempt_id':self.pump.attempt,'monotonic_s':time.monotonic()}).encode()
                await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'no-store')]})
                await send({'type':'http.response.body','body':data})
            finally:self.readers-=1
            return
        self.clients+=1
        import re
        ids=[v.decode('ascii',errors='ignore') for k,v in headers if k.lower()==b'x-home-tracking-stream']
        stream_id=ids[0] if len(ids)==1 and re.fullmatch(r'[a-f0-9]{32}',ids[0]) else None
        try:
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream'),(b'cache-control',b'no-store')]})
            previous=None
            disconnected=False
            while self.pump.fresh():
                packet=self.pump.snapshot();key=(packet.get('sequence'),packet.get('status'))
                if key!=previous:
                    data=b'data: '+canonical(packet).encode()+b'\n\n'
                    await asyncio.wait_for(send({'type':'http.response.body','body':data,'more_body':True}),1)
                    tracking_trace('worker_stream_sent',packet,stream_id=stream_id)
                    previous=key
                try:
                    message=await asyncio.wait_for(receive(),.02)
                    if message['type']=='http.disconnect':disconnected=True;break
                except asyncio.TimeoutError:pass
            if not disconnected:await send({'type':'http.response.body','body':b'','more_body':False})
        finally:
            self.clients-=1
