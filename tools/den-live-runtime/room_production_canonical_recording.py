"""Separate production-family originals; DATA grammar never creates Home birth."""
from copy import deepcopy
import os
from pathlib import Path
import threading
import time

from room_fake_plant_recording import RoomNativeRecorder,RecordingIncomplete,require,canonical,sha
from room_canonical_plant_recording import validate_header as validate_canonical_header
from room_production_canonical_client import production_protocol_validator
from room_production_canonical_provider import FAMILY,PROTOCOL,verified_production_provider_export

RECORD_SCHEMA='home-room-production-canonical-record-1'
RECEIPT_SCHEMA='home-room-production-canonical-recording-receipt-1'


def validate_header(header,qualification):
    from room_production_canonical_owner import ProductionSourceQualification
    require(type(qualification) is ProductionSourceQualification,'actual_production_source_birth_required')
    validate_canonical_header(header)
    qualification.verify_header(header)
    return qualification.public()


class RoomProductionCanonicalRecorder(RoomNativeRecorder):
    def __init__(self,path,*,header,qualification,max_records=8192,max_bytes=33554432):
        birth=validate_header(header,qualification)
        require(type(max_records) is int and 8<=max_records<=8192 and type(max_bytes) is int
                and 4096<=max_bytes<=33554432,'record_capacity_bounds')
        self.path=Path(path);self._header=deepcopy(header);self.qualification=qualification
        self._lock=threading.RLock();self._sequence=0;self._previous='0'*64;self._bytes=0
        self._max_records,self._max_bytes=max_records,max_bytes
        self._failure=None;self._closed=False;self._receipt=None;self._final_input=None
        self._final_guard=threading.RLock();self._final_done=threading.Event()
        self._final_thread=None;self._final_members=None;self._storage_receipt=None;self._finishing=False
        self._state=[dict(ordinal=0,stdout_eof=False,stderr_eof=False,exit_code=None) for _ in range(4)]
        self._validators=[None]*4;self._birth_retained=False
        self._file=self.path.open('xb')
        try:
            self._append('header',self._header);self._append('source_birth',birth)
            self._birth_retained=True
        except BaseException as exc:
            self._failure='production_header_or_birth_not_durable';self._closed=True;self._file.close()
            raise RecordingIncomplete(self._failure) from exc

    @property
    def ready(self):return super().ready and self._birth_retained

    def _append(self,kind,payload):
        require(not self._closed,'record_already_finalized')
        require(self._sequence<(self._max_records if kind=='final' else self._max_records-1),'record_count_exhausted')
        row=dict(schema=RECORD_SCHEMA,version=1,sequence=str(self._sequence+1),kind=kind,
                 previous_sha256=self._previous,payload=payload)
        row['record_sha256']=sha(canonical(row));raw=canonical(row)+b'\n'
        require(len(raw)<=49152 and self._bytes+len(raw)+(0 if kind=='final' else 4096)<=self._max_bytes,
                'record_byte_capacity_exhausted')
        require(self._file.write(raw)==len(raw),'record_partial_write')
        self._file.flush();os.fsync(self._file.fileno())
        self._sequence+=1;self._previous=row['record_sha256'];self._bytes+=len(raw)

    def observer(self,index):
        require(type(index) is int and 0<=index<4 and self._validators[index] is None,'one_production_observer_per_member')
        self.qualification.verify_header(self._header)
        _,manifest,_=verified_production_provider_export()
        validator=production_protocol_validator(manifest,self._header,index)
        self._validators[index]=validator;durable=super().observer(index)
        def callback(event):
            durable(event)  # Original bytes/clocks are fsynced before interpretation.
            try:
                self.qualification.verify_header(self._header)
                if event['kind']=='stdin_attempt':validator.request(event['raw'])
                elif event['kind']=='stdout_bytes':validator.stdout(event['raw'])
                elif event['kind']=='stdout_eof':validator.finish()
            except BaseException as exc:
                self.abort('production_stream_invalid')
                raise RecordingIncomplete('production_stream_invalid') from exc
        return callback

    def validated_response(self,index):
        require(type(index) is int and 0<=index<4 and self._validators[index] is not None,'production_observer_not_bound')
        with self._validators[index].lock:return deepcopy(self._validators[index].last_response)

    def _result(self,members,*,clean,failure,durable,unconfirmed):
        result=super()._result(members,clean=clean,failure=failure,durable=durable,unconfirmed=unconfirmed)
        result.update(schema=RECEIPT_SCHEMA,selection=FAMILY,protocol=PROTOCOL,
                      source_birth_sha256=self.qualification.public()['birth_sha256'],physical_truth=False)
        return result

    def close_unstarted(self):
        """Finite inert-file cleanup, never a four-member native archive pass."""
        deadline=time.monotonic()+1
        with self._final_guard:
            if self._receipt is not None:return deepcopy(self._receipt)
            if self._final_thread is None:
                require(self._sequence==2 and not any(self._validators) and all(s['ordinal']==0 for s in self._state)
                    and self._failure is None,'only_original_inert_recording_can_close_unstarted')
                self._finishing=True
                def finish():
                    result=dict(schema='home-production-canonical-inert-close-1',version=1,
                        status='incomplete',cleanup_unconfirmed=True,native_processes_started=0,
                        native_archive_complete=False,native_archive_acceptance='not_started',
                        native_recomputed=False,home_room_replayed=False,physical_permission=False,physical_truth=False,
                        path=str(self.path),failure_reason='inert_file_close_unconfirmed')
                    try:
                        with self._lock:
                            self._append('inert_final',dict(reason='native_never_started',native_processes_started=0,
                                native_archive_complete=False,physical_permission=False,physical_truth=False))
                            self._file.close();self._closed=True
                            result.update(status='complete',cleanup_unconfirmed=False,failure_reason=None,
                                records=self._sequence,terminal_record_sha256=self._previous,footer_durable=True)
                    except BaseException:
                        self._failure=self._failure or 'inert_file_close_unconfirmed'
                    finally:
                        with self._final_guard:self._storage_receipt=result;self._final_done.set()
                self._final_thread=threading.Thread(target=finish,name='production-inert-recording-close',daemon=True)
                self._final_thread.start()
        done=self._final_done.wait(max(0,deadline-time.monotonic()))
        with self._final_guard:
            if self._receipt is None:
                if done and time.monotonic()<=deadline:self._receipt=deepcopy(self._storage_receipt)
                else:
                    self._receipt=dict(schema='home-production-canonical-inert-close-1',version=1,status='incomplete',
                        cleanup_unconfirmed=True,native_processes_started=0,native_archive_complete=False,
                        native_archive_acceptance='not_started',physical_permission=False,physical_truth=False,
                        failure_reason='inert_file_close_unconfirmed')
            return deepcopy(self._receipt)
