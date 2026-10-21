"""Optional same-owner metric input; no pump, thread, Store, archive or model.

Only the incumbent ViewingSource calls this serially from its existing pump.
The default JPEG factory never constructs it. Slow capture/projection runs
outside the runtime and source locks; source Stop retires retained references
synchronously. Original clocks and both arms' common input remain unchanged.
"""
from copy import deepcopy
import hashlib,json,re

MAX_CAPTURE_BYTES=6*1024**2
MIN_CAPTURE_INTERVAL_S=1.
# Development preview timing only (owner decision, 2026-10-19): late originals
# are named warnings, a lease waits this long for its arm-B output, and a
# published PNG stays fetchable this long. Strict stays the default.
DEVELOPMENT_LEASE_S=10.
DEVELOPMENT_RETAIN_S=12.
INTEGRITY=frozenset(('approximate_camera_identity_changed','approximate_camera_epoch_changed',
    'approximate_camera_epoch_unverified','approximate_camera_calibration_changed',
    'approximate_scene_or_mount_identity_changed','approximate_scene_scope_required','desk_preview_scope_changed'))
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def failure_reason(error,phase):
    code=str(error)
    if isinstance(error,ValueError) and re.fullmatch(r'(metric|bundled|receiver_clock|execution_clock|fixed_camera|qualified_receiver_clock|approximate)_[a-z0-9_]{1,90}',code):
        return code
    return 'metric_'+phase+'_'+type(error).__name__

class MetricInputFactory:
    def __init__(self,client,*,clock=None,projection_factory=None,desk_preview=None,timing_policy='strict'):
        if not callable(getattr(client,'_read',None)):raise ValueError('incumbent bounded bundle client required')
        if timing_policy not in ('strict','development'):raise ValueError('explicit metric timing policy required')
        if projection_factory is not None and not callable(projection_factory):raise ValueError('trusted projection factory required')
        if desk_preview is not None:
            from apartment_desk_preview import ConfiguredDeskPreview
            if type(desk_preview) is not ConfiguredDeskPreview:raise ValueError('trusted configured desk preview required')
        from bundle_rgb_input import QualifiedReceiverClock
        from chrony_preview_client import _CAPTURE_LOCK
        self.client,self.clock,self.projection_factory=client,clock or QualifiedReceiverClock(),projection_factory
        self.desk_preview=desk_preview;self.timing_policy=timing_policy
        self.capture_lock=_CAPTURE_LOCK
    def new_session(self,owner):return MetricInputSession(owner,self)

class MetricInputSession:
    def __init__(self,owner,factory):
        self.owner,self.factory=owner,factory
        self.previous=None;self.previous_expiry=None;self.pending_capture=None;self.lease=None;self.image_record=None;self.reserved_bytes=0
        self.reason='awaiting_anchor';self.closed=False;self.last_capture_at=None;self.last_npz_identity=None
        self.projection=factory.projection_factory(owner) if factory.projection_factory else None
        self.packet=None;self.packet_bytes=None;self.prepared=0;self.projected=0;self.in_flight=False
        self.desk_preview=factory.desk_preview;self.preview_packet=None;self.preview_packet_bytes=None
        self.development=factory.timing_policy=='development';self.lease_until=None;self.lease_warnings=[]
        self.frame_warnings={};self.latest_png=None;self.evidence=None;self.evidence_event_id=None;self.evidence_media_id=None
        self.preview_published_at=None;self.pending_warnings=[]
    def retained_byte_allocations(self):
        # Caller owns the sole source lock. Returned bytes are aliases, not
        # another cache. The owner accounts their union with registered pixels.
        values=[]
        if self.previous:values.extend(self.previous)
        if self.pending_capture:values.extend(self.pending_capture['bytes'])
        if self.lease:values.extend(self.lease.retained_byte_allocations)
        if self.packet_bytes:values.append(self.packet_bytes)
        if self.preview_packet_bytes:values.append(self.preview_packet_bytes)
        if self.desk_preview is not None:values.extend(self.desk_preview.retained_byte_allocations)
        if self.projection is not None:values.extend(self.projection.retained_byte_allocations)
        return tuple(values)
    def clear_locked(self):
        self.closed=True;self.previous=None;self.previous_expiry=None;self.pending_capture=None;self.lease=None;self.image_record=None
        self.packet=None;self.packet_bytes=None;self.reserved_bytes=0
        self.preview_packet=None;self.preview_packet_bytes=None;self.desk_preview=None
        self.lease_until=None;self.lease_warnings=[];self.frame_warnings={};self.latest_png=None
        self.preview_published_at=None;self.pending_warnings=[]
        if self.projection is not None:self.projection.stop('approximate_source_stopped')
        self.projection=None
    def prune_locked(self,now):
        if self.previous_expiry is not None and now>=self.previous_expiry:self.previous=None;self.previous_expiry=None
        if self.pending_capture is not None and now>=self.pending_capture['retire_at']:
            self.pending_capture=None;self.reason='metric_original_expired'
        if self.lease is not None and now>=self._lease_deadline():
            self.lease=None;self.image_record=None;self.reason='metric_original_expired'
        for mid in [mid for mid in self.frame_warnings if mid not in self.owner.entries]:del self.frame_warnings[mid]
        if self.packet is not None and self.projection is not None:
            snapshot=self.projection.snapshot()
            if not snapshot.get('renderable'):
                self.packet=None;self.packet_bytes=None;self.reason=snapshot.get('reason') or 'approximate_room_pose_unavailable'
                kept=(self.development and self.reason=='approximate_receipt_expired' and self.preview_packet is not None
                    and self.preview_packet.get('available'))
                # Development: the preview outlives the 2 s display receipt inside its own window.
                if not kept:self.preview_packet,self.preview_packet_bytes=self._preview_refusal(self.reason)
        if (self.development and self.preview_packet is not None and self.preview_packet.get('available')
                and (self.preview_published_at is None or now>=self.preview_published_at+DEVELOPMENT_LEASE_S)):
            self.preview_packet,self.preview_packet_bytes=self._preview_refusal('desk_preview_hold_elapsed')
    def _preview_refusal(self,reason):
        # The preview reports the parent's own refusal, never a generic wait.
        if self.desk_preview is None:return None,None
        from apartment_desk_preview import unavailable
        packet=self._development(unavailable(reason));return packet,encoded(packet)
    def _lease_deadline(self):
        if self.development and self.lease_until is not None:return self.lease_until
        return self.lease.descriptor['retirement_deadline']
    def _warn(self,warnings,check,at,deadline):
        if all(item['check']!=check for item in warnings):warnings.append(dict(check=check,at=at,deadline=deadline))
    def _warnings(self,mid):return deepcopy(self.frame_warnings.get(mid,[]))
    def _development(self,packet,mid=None):
        """Development-only fields; strict packets stay byte-identical to the 1726 contract."""
        if not self.development or packet is None:return packet
        packet=deepcopy(packet);timing=packet.get('timing') if isinstance(packet.get('timing'),dict) else {}
        if mid is None and isinstance(packet.get('frame'),dict):mid=packet['frame'].get('media_id')
        if mid is None:mid=self.evidence_media_id  # a refusal names no frame; its evidence does
        with self.owner.lock:
            packet['timing']={**timing,'policy':'development','warnings':self._warnings(mid) if mid else []}
            packet.update(evidence=self.evidence or 'neutral',evidence_event_id=self.evidence_event_id)
        return packet
    def _evidence(self,payload,why,preview):
        if why in INTEGRITY or isinstance(why,str) and why.startswith('approximate_mount_signature_'):return 'integrity'
        from apartment_desk_preview import distinct_people
        try:people=len(distinct_people(payload['result']))
        except (KeyError,TypeError,AttributeError):return 'neutral'
        if people!=1:return 'contrary'
        return 'supporting' if isinstance(preview,dict) and preview.get('available') else 'neutral'
    def _current(self):
        if self.closed:raise ValueError('metric source stopped')
        return self.owner.current()
    def _set_anchor(self,jpeg,header,expiry):
        candidate=(jpeg,header.encode())
        with self.owner.lock:
            if self.closed or self.owner.closed:raise ValueError('metric source stopped')
            previous=self.previous;previous_expiry=self.previous_expiry;self.previous=candidate;self.previous_expiry=expiry
            if self.owner._resident_bytes_locked()>8388608:
                self.previous=previous;self.previous_expiry=previous_expiry;raise ValueError('metric RAM budget exhausted')
            return previous
    def _binding(self,session,runner):
        return {'session':{key:session[key] for key in ('id','site_id','camera_id')},'arm':'b',
            'model_generation':runner.generation,'media_generation':session['generation'],
            'lab_profile':deepcopy(runner.profiles['b']),
            'sampling_hz':runner.sampling_hz['b'] if runner.sampling_hz is not None else None}
    def after_offer(self,frame):
        """Retire only an exact lease BOTH arms provably declined to execute.

        The existing runner's source cadence stays authoritative. Keep its
        next slot free of unrelated JPEGs while the same pump waits for a new
        metric original; no resend, new owner, clock or lease is introduced.
        """
        with self.owner.lock:
            lease,record=self.lease,self.image_record
            if self.closed or lease is None or record is None:return False
            if frame.get('media_ids')!=[record['id']]:return False
        session,runner=self._current()
        required=('frame_id','capture_epoch','site_id','camera_id','session_id',
            'observed_at','available_at','valid_until','media_ids')
        expected={key:frame[key] for key in required}
        if (expected!={**{key:lease.image_metadata[key] for key in required
                if key not in ('session_id','media_ids')},'session_id':session['id'],'media_ids':[record['id']]}
                or runner.generation!=lease.binding['model_generation']):return False
        ids={arm:sha(encoded([session['id'],runner.generation,arm,
            frame['capture_epoch'],frame['frame_id']])) for arm in ('a','b')}
        # Runner -> Store -> owner is the incumbent reconciliation lock order.
        # No source lock spans the durable terminal-disposition lookup.
        with runner.lock:
            with self.owner.store.lock:
                rows=self.owner.store.db.execute(
                    'SELECT id,payload,state,result FROM paired_requests WHERE id IN (?,?)',
                    (ids['a'],ids['b'])).fetchall()
            if len(rows)!=2:return False
            from runtime import original_frame
            for row in rows:
                request=json.loads(row['payload']);arm=request.get('arm')
                if (arm not in ids or row['id']!=ids[arm] or request.get('request_id')!=ids[arm]
                        or request.get('session_id')!=session['id'] or request.get('generation')!=runner.generation
                        or request.get('profile')!=runner.profiles[arm] or original_frame(request)!=expected
                        or row['state']!='dropped' or json.loads(row['result'] or 'null')!={'reason':'source_sampling_cadence'}
                        or runner.active[arm]==ids[arm]
                        or runner.pending[arm] is not None and runner.pending[arm].get('request_id')==ids[arm]):return False
            self._current()
            with self.owner.lock:
                if self.closed or self.owner.closed or self.lease is not lease or self.image_record is not record:return False
                self.lease=None;self.image_record=None;self.reason='awaiting_metric_result'
                # last_capture_at and last_npz_identity deliberately survive.
                return True
    def finalize(self):
        # Never called by a worker under runner.lock or by live polling. The
        # same preview pump performs at most one bounded projection before its
        # next capture. A result can use only the lease's exact registered PNG.
        with self.owner.lock:
            lease,record=self.lease,self.image_record
            if self.closed or lease is None:return
        session,runner=self._current()
        with self.owner.store.lock:
            row=self.owner.store.db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output' AND json_extract(payload,'$.arm')='b' AND json_extract(payload,'$.generation')=? AND json_extract(payload,'$.media_ids')=? AND json_extract(payload,'$.frame_id')=? ORDER BY id DESC LIMIT 1",
                (session['id'],runner.generation,json.dumps([record['id']],separators=(',',':')),lease.image_metadata['frame_id'])).fetchone()
        if row:
            payload=json.loads(row[1])
            if payload.get('media_ids')==[record['id']]:
                event={'id':row[0],'session_id':session['id'],'kind':'perception.output','payload':payload}
                try:
                    late=[] if self.development else None;now=self.owner.store.clock()
                    if self.projection is None:
                        from apartment_viewing_metric import lift_supported_joints
                        candidate=lift_supported_joints(lease,event=event,image_record=record,now_s=now,warnings=late)
                        result=None;reason='reviewed_view_alignment_unavailable' if candidate['status']=='candidate' else 'metric_joint_support_unavailable'
                    else:
                        result=self.projection.ingest(lease,event=event,image_record=record,now_s=now,session_state=session['state'],
                            **({'timing_warnings':late,'development':True} if late is not None else {}))
                        reason=result.get('reason') or ('approximate_room_pose_available' if result.get('renderable') else 'approximate_room_pose_unavailable')
                    self._current()
                    if late is not None:late+=self._late_stages(lease,payload,now)
                    packet_bytes=encoded(result) if result is not None else None
                    if packet_bytes is not None and len(packet_bytes)>256*1024:raise ValueError('metric diagnostic packet exceeds bound')
                    if isinstance(result,dict) and not result.get('renderable'):preview,preview_bytes=self._preview_refusal(reason)
                    else:
                        preview=self.desk_preview.project(result,event=event,session_state=session['state'],
                            **({'development':True} if self.development else {})) if self.desk_preview is not None else None
                        preview_bytes=encoded(preview) if preview is not None else None
                    if preview_bytes is not None and len(preview_bytes)>32*1024:raise ValueError('metric desk preview packet exceeds bound')
                    with self.owner.lock:
                        if self.closed or self.lease is not lease:return
                        if self.development:
                            warnings=self.frame_warnings.setdefault(record['id'],[])
                            for item in late:self._warn(warnings,item['check'],item['at'],item['deadline'])
                            self.evidence=self._evidence(payload,result.get('reason') if isinstance(result,dict) and not result.get('renderable')
                                else (preview or {}).get('reason'),preview)
                            self.evidence_event_id=row[0];self.evidence_media_id=record['id']
                            preview=self._development(preview,record['id']);preview_bytes=encoded(preview) if preview is not None else None
                            self.owner.retain_metric_png_locked(record['id'],now+DEVELOPMENT_RETAIN_S,
                                supporting=self.evidence=='supporting')
                        if self.latest_png is not None and self.latest_png['media_id']==record['id']:self.latest_png['event_id']=row[0]
                        self.lease=None;self.image_record=None;self.packet=result;self.packet_bytes=packet_bytes
                        self.preview_packet=preview;self.preview_packet_bytes=preview_bytes;self.preview_published_at=now
                        if self.owner._resident_bytes_locked()>8388608:
                            self.packet=None;self.packet_bytes=None;self.preview_packet=None;self.preview_packet_bytes=None
                            raise ValueError('metric diagnostic RAM budget exhausted')
                        self.reason=reason;self.projected+=1
                    return
                except Exception as error:
                    with self.owner.lock:
                        if self.lease is lease:self.lease=None;self.image_record=None
                        self.reason=failure_reason(error,'projection')
                    return
        if self.development:
            # No arm-B output for this PNG will ever arrive once its request is
            # terminal (cancelled, rejected, expired or dropped/superseded), so
            # the single lease frees capture now instead of after its 10 s hold.
            meta=lease.image_metadata
            request_id=sha(encoded([session['id'],runner.generation,'b',meta['capture_epoch'],meta['frame_id']]))
            with self.owner.store.lock:
                ended=self.owner.store.db.execute(
                    "SELECT state FROM paired_requests WHERE id=? AND state IN ('dropped','cancelled','expired','rejected')",
                    (request_id,)).fetchone()
            if ended:
                with self.owner.lock:
                    if self.lease is lease:self.lease=None;self.image_record=None;self.reason='metric_arm_b_'+ended[0]
                return
        if self.owner.store.clock()>=self._lease_deadline():
            with self.owner.lock:
                if self.lease is lease:self.lease=None;self.image_record=None;self.reason='metric_original_expired'
    def _late_stages(self,lease,payload,now):
        # Stages strict timing would have cancelled, read from the actual ledger.
        late=[];deadline=lease.image_metadata['valid_until']
        with self.owner.store.lock:
            row=self.owner.store.db.execute('SELECT dispatched_at FROM paired_dispatch WHERE request_id=?',(payload.get('request_id'),)).fetchone()
        if row and row[0]>deadline:late.append(dict(check='dispatch_late',at=row[0],deadline=deadline))
        retired=lease.descriptor['retirement_deadline']
        if now>=retired:late.append(dict(check='lease_retired',at=now,deadline=retired))
        return late
    def ingest(self,jpeg,header):
        try:return self._ingest(jpeg,header)
        except Exception as error:
            with self.owner.lock:
                if not self.closed:self.reason=failure_reason(error,'anchor')
            raise
    def _ingest(self,jpeg,header):
        from assessment_existing_api import _receipt
        receipt=_receipt(jpeg,header);session,runner=self._current()
        if self.owner.generation is None:self.owner.generation=runner.generation
        if self.owner.epoch is not None and receipt['session']!=self.owner.epoch:
            self.owner.retire('capture_epoch_changed');raise ValueError('viewing capture epoch changed')
        self.owner.epoch=receipt['session'];self.finalize()
        with self.owner.lock:
            previous=self.previous
            if previous:
                prior=_receipt(previous[0],previous[1].decode())
                if receipt['sequence']==prior['sequence']:
                    if previous!=(jpeg,header.encode()):raise ValueError('metric original anchor identity changed')
                    return None
                if receipt['sequence']<prior['sequence']:raise ValueError('metric anchor order changed')
        from bundle_rgb_input import anchor_metadata
        anchored=[] if self.development else None
        anchor=anchor_metadata(receipt,site_id=session['site_id'],camera_id=session['camera_id'],
            available_at=self.owner.store.clock(),started_at=session['started_at'],
            **({'warnings':anchored} if anchored is not None else {}))
        if anchored:
            # Development: an anchor clock refusal becomes a warning on the next capture.
            stamp=self.owner.store.clock()
            with self.owner.lock:self.pending_warnings.extend(dict(item,at=stamp) for item in anchored)
        self._set_anchor(jpeg,header,anchor['valid_until'])
        now=self.owner.store.clock()
        with self.owner.lock:
            if self.lease is not None:self.reason='awaiting_metric_result';return None
            pending=self.pending_capture
            if pending is not None and now>=pending['retire_at']:
                self.pending_capture=None;pending=None
            if pending is None and self.last_capture_at is not None and now-self.last_capture_at<MIN_CAPTURE_INTERVAL_S:return None
            if pending is None and self.owner._resident_bytes_locked()+MAX_CAPTURE_BYTES>8388608:
                self.reason='metric_capture_budget_unavailable';return None
            self.reserved_bytes=MAX_CAPTURE_BYTES if pending is None else 0;self.in_flight=True
            if pending is None:self.last_capture_at=now
        locked=False;phase='capture_lock'
        try:
            if pending is None:
                locked=self.factory.capture_lock.acquire(timeout=1)
                if not locked:raise ValueError('commissioned camera capture busy')
                self._current();phase='receiver_clock';started=self.factory.clock.begin(receipt)
                phase='npz_read';raw,_=self.factory.client._read('/frame.npz');self._current()
                phase='calibration_read';calibration,_=self.factory.client._read('/calibration');self._current()
                self.factory.capture_lock.release();locked=False
                # A is already received; NPZ is captured now; B will arrive on
                # the next incumbent pump turn. Do not invent a past bracket
                # from two JPEGs received before the NPZ fetch. Retain one
                # bounded pending original, with its original A expiry.
                pending=dict(bytes=(raw,calibration,jpeg,header.encode()),started=started,
                    valid_until=anchor['valid_until'],binding=self._binding(session,runner),
                    retire_at=now+DEVELOPMENT_LEASE_S if self.development else anchor['valid_until'])
                if sum(len(v) for v in pending['bytes'])>MAX_CAPTURE_BYTES:raise ValueError('metric capture reservation exceeded')
                with self.owner.lock:
                    if self.closed or self.owner.closed:raise ValueError('metric source stopped')
                    self.reserved_bytes=0;self.pending_capture=pending
                    if self.owner._resident_bytes_locked()>8388608:
                        self.pending_capture=None;raise ValueError('metric RAM budget exhausted')
                    self.reason='awaiting_anchor'
                return None
            if pending['binding']!=self._binding(session,runner):raise ValueError('metric_fixed_binding_required')
            available=self.owner.store.clock()
            phase='receiver_clock';proof=self.factory.clock.finish(pending['started']);self._current()
            raw,calibration,prior_jpeg,prior_header=pending['bytes']
            prior=(prior_jpeg,prior_header)
            originals={'npz':raw,'calibration':calibration,'anchor_a':prior[0],'anchor_b':jpeg}
            headers={'a':prior[1].decode(),'b':header}
            from apartment_viewing_metric import prepare_metric_frame
            from bundle_ingest import calibration_content_id
            pins={'originals':{key:{'size':len(data),'sha256':sha(data)} for key,data in originals.items()},
                'anchor_headers':{key:sha(value.encode()) for key,value in headers.items()},'receiver_proof':sha(encoded(proof)),
                'capture_epoch':receipt['session'],'timing_generation':receipt['timing_generation'],
                'calibration_content_id':calibration_content_id(json.loads(calibration))}
            captured=None
            if self.development:
                with self.owner.lock:captured,self.pending_warnings=self.pending_warnings,[]
            phase='prepare';lease=prepare_metric_frame(originals=originals,anchor_headers=headers,receiver_proof=proof,
                binding=self._binding(session,runner),pins=pins,available_at=available,started_at=session['started_at'],now_s=self.owner.store.clock(),
                **({'warnings':captured} if captured is not None else {}))
            if lease.retained_bytes>MAX_CAPTURE_BYTES:raise ValueError('metric capture reservation exceeded')
            meta=lease.image_metadata;identity=(meta['capture_epoch'],meta['timing_generation'],meta['sequence'])
            if self.last_npz_identity is not None and (identity[:2]!=self.last_npz_identity[:2] or identity[2]<=self.last_npz_identity[2]):
                raise ValueError('metric NPZ identity did not advance')
            self._current()
            with self.owner.lock:
                if self.closed or self.owner.closed:raise ValueError('metric source stopped')
                self.reserved_bytes=0;self.pending_capture=None;self.lease=lease
                self.lease_until=self.owner.store.clock()+DEVELOPMENT_LEASE_S if self.development else None
                if self.owner._resident_bytes_locked()>8388608:
                    self.lease=None;raise ValueError('metric RAM budget exhausted')
            # current()/registration consult the Store and scope. Never hold
            # owner.lock across them: live/Stop and worker reconciliation can
            # already own outer lifecycle/Store locks before asking this owner.
            phase='register';record=self.owner.register_metric_frame(lease)
            self._current();self.owner.prune()
            with self.owner.lock:
                registered=self.owner.store.clock();late=registered>=lease.descriptor['retirement_deadline']
                if (self.closed or self.owner.closed or self.lease is not lease
                        or self.owner.entries.get(record['id'],{}).get('record')!=record
                        or late and not self.development):
                    raise ValueError('metric_registration_retired')
                if self.development:
                    warnings=[];self.frame_warnings[record['id']]=warnings
                    for item in captured:self._warn(warnings,item['check'],item['at'],item['deadline'])
                    if late:self._warn(warnings,'registration_expired',registered,lease.descriptor['retirement_deadline'])
                self.latest_png={'media_id':record['id'],'frame_id':record['metadata']['frame_id'],
                    'media_sha256':record['sha256'],'generation':record['generation'],'event_id':None}
                self.image_record=record;self.prepared+=1;self.reason='awaiting_metric_result';self.last_npz_identity=identity
            return {**lease.image_metadata,'session_id':session['id'],'media_ids':[record['id']]}
        except Exception as error:
            with self.owner.lock:
                self.pending_capture=None
                if self.image_record is None:self.lease=None
                self.reason=failure_reason(error,phase)
            return None
        finally:
            if locked:self.factory.capture_lock.release()
            with self.owner.lock:self.reserved_bytes=0;self.in_flight=False
    def snapshot(self):
        try:self._current()
        except Exception:return None
        with self.owner.lock:
            if self.closed:return None
            projection=self.projection
            if projection is None:return None
            # The tracker stores only a small packet. It never decodes or
            # acquires here and cannot renew its first-event display receipt.
            result=projection.snapshot()
            if self.development and result.get('renderable'):
                result['timing'].update(policy='development',warnings=self._warnings(result['frame']['media_id']))
            return result
    def preview_snapshot(self,observation,*,session_state):
        """Read the same original's preview; polling never renews its hold.

        The latest arm observation must still be the exact event used for the
        preview. A newer JPEG fallback, ambiguity, expiry or Stop cannot retain
        an old desk target. No acquisition or projection occurs on this path.
        """
        if self.factory.desk_preview is None:return None
        if self.development:return self._development_snapshot(observation,session_state)
        from apartment_desk_preview import unavailable
        try:self._current()
        except Exception:return unavailable('desk_preview_source_stopped')
        with self.owner.lock:
            if self.closed:return unavailable('desk_preview_source_stopped')
            retained=self.preview_packet;packet=deepcopy(retained)
        if packet is None:return unavailable('desk_preview_awaiting_observation')
        if not packet.get('available'):return packet
        if session_state!='recording':return unavailable('desk_preview_source_stopped')
        if observation!=packet.get('observation'):return unavailable('desk_preview_observation_changed')
        until=packet.get('valid_until');observed_until=observation.get('valid_until')
        if (type(until) not in (int,float) or type(observed_until) not in (int,float)
                or not self.owner.store.clock()<min(until,observed_until)):
            return unavailable('desk_preview_original_expired')
        approximate=self.snapshot()
        if isinstance(approximate,dict) and not approximate.get('renderable') and approximate.get('reason'):
            return unavailable(approximate['reason'])
        if (not isinstance(approximate,dict) or not approximate.get('renderable')
                or approximate.get('frame')!=packet.get('frame')
                or approximate.get('approximate_pose',{}).get('provenance',{}).get('source_event_id')
                    !=packet.get('provenance',{}).get('source_event_id')):
            return unavailable('desk_preview_parent_retired')
        packet['timing']=deepcopy(approximate['timing'])
        # Stop can race the cheap parent snapshot; recheck before publishing.
        try:self._current()
        except Exception:return unavailable('desk_preview_source_stopped')
        with self.owner.lock:
            if self.closed:return unavailable('desk_preview_source_stopped')
            if self.preview_packet is not retained:return unavailable('desk_preview_observation_changed')
        return packet
    def _development_snapshot(self,observation,session_state):
        """Development timing: the preview keeps its own window from publish.

        A late original, the 2 s display receipt of the approximate pose and a
        newer output that does not contradict (one person) become named
        warnings. Stop, integrity refusals, a contrary newer output and the end
        of the window still withdraw it at once.
        """
        from apartment_desk_preview import unavailable as refused
        unavailable=lambda reason:self._development(refused(reason))
        try:self._current()
        except Exception:return refused('desk_preview_source_stopped')
        with self.owner.lock:
            if self.closed:return refused('desk_preview_source_stopped')
            retained=self.preview_packet;packet=deepcopy(retained);published=self.preview_published_at
        if packet is None:return unavailable('desk_preview_awaiting_observation')
        if not packet.get('available'):return self._development(packet)
        if session_state!='recording':return unavailable('desk_preview_source_stopped')
        now=self.owner.store.clock();notes=[]
        if published is None or now>=published+DEVELOPMENT_LEASE_S:return unavailable('desk_preview_hold_elapsed')
        if observation!=packet.get('observation'):
            self._classify_newer_output()
            with self.owner.lock:contrary=self.evidence=='contrary'
            if contrary:return unavailable('desk_preview_observation_changed')
            notes.append(('newer_observation_not_projected',None))
        until=packet.get('valid_until');observed_until=(packet.get('observation') or {}).get('valid_until')
        if type(until) not in (int,float) or type(observed_until) not in (int,float):
            return unavailable('desk_preview_original_expired')
        if not now<min(until,observed_until):notes.append(('media_expired',min(until,observed_until)))
        approximate=self.snapshot()
        if isinstance(approximate,dict) and approximate.get('renderable'):
            if (approximate.get('frame')!=packet.get('frame')
                    or approximate.get('approximate_pose',{}).get('provenance',{}).get('source_event_id')
                        !=packet.get('provenance',{}).get('source_event_id')):
                return unavailable('desk_preview_parent_retired')
            packet['timing']=deepcopy(approximate['timing'])
        elif isinstance(approximate,dict) and approximate.get('reason')=='approximate_receipt_expired':
            # approximate_room_pose keeps its strict 2 s display hold; the
            # preview keeps the timing it was published with.
            notes.append(('approximate_receipt_hold_elapsed',None))
        else:
            return unavailable(approximate.get('reason') if isinstance(approximate,dict) and approximate.get('reason')
                else 'desk_preview_parent_retired')
        try:self._current()
        except Exception:return refused('desk_preview_source_stopped')
        with self.owner.lock:
            if self.closed:return refused('desk_preview_source_stopped')
            if self.preview_packet is not retained:return unavailable('desk_preview_observation_changed')
            warnings=self.frame_warnings.setdefault(packet['frame']['media_id'],[])
            for check,deadline in notes:self._warn(warnings,check,now,deadline)
        return self._development(packet)
    def _classify_newer_output(self):
        # A newer arm-B output without a preview packet yet (or a JPEG
        # fallback) is still evidence: anything but one person contradicts.
        try:session,runner=self._current()
        except Exception:return
        with self.owner.store.lock:
            row=self.owner.store.db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output' AND json_extract(payload,'$.arm')='b' AND json_extract(payload,'$.generation')=? ORDER BY id DESC LIMIT 1",(session['id'],runner.generation)).fetchone()
        if row is None:return
        from apartment_desk_preview import distinct_people
        try:people=len(distinct_people(json.loads(row[1])['result']))
        except (KeyError,TypeError,AttributeError,ValueError):people=None
        with self.owner.lock:
            if self.closed or self.evidence_event_id is not None and row[0]<=self.evidence_event_id:return
            self.evidence='neutral' if people in (1,None) else 'contrary';self.evidence_event_id=row[0]
            payload=json.loads(row[1]);self.evidence_media_id=(payload.get('media_ids') or [None])[0]

    def status(self):
        renderable=self.projection.snapshot().get('renderable') if self.projection is not None else False
        state=('stopped' if self.closed else 'capturing' if self.in_flight else 'awaiting_model' if self.lease is not None
            else 'available' if renderable else 'awaiting' if self.reason=='awaiting_anchor' else 'unavailable')
        # The latest registered PNG is announced as soon as it exists, so Home
        # can fetch it before arm B finishes, and while it stays fetchable.
        png=self.latest_png
        registered=None if self.closed or png is None or png['media_id'] not in self.owner.entries else dict(png)
        return {'enabled':True,'arm':'b','state':state,'prepared_frames':self.prepared,'projected_frames':self.projected,
            'registered_frame':registered,**({'timing_policy':'development'} if self.development else {}),
            'reason':self.reason,'in_flight':self.in_flight,'reserved_bytes':self.reserved_bytes,
            'metric_depth':'diagnostic_nominal_eeprom','room_alignment':'approximate_viewing_only' if self.projection is not None else 'unavailable',
            'operational_geometry':False,'physical_permission':False}
