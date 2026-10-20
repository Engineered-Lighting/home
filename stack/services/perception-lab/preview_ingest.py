"""Translate the camera owner's commissioned JPEG into registered Lab RGB.

Only a server-provisioned authenticated camera client may call this adapter.
It never opens DepthAI or treats network receipt as camera exposure time.
"""
import base64
import io
import json
from decimal import Decimal
from reasoning import canonical, finite
from store import identifier


class PreviewIngestor:
    def __init__(self, store, *, site_id, camera_id, clock_validation_id, clock_uncertainty_s, media_writer=None):
        if not clock_validation_id or not finite(clock_uncertainty_s) or not 0<=clock_uncertainty_s<=1:
            raise ValueError("commissioned receiver clock bound required")
        self.store=store
        self.media_writer=media_writer
        self.site_id,self.camera_id=site_id,camera_id
        self.clock_id,self.clock_uncertainty=clock_validation_id,clock_uncertainty_s

    def ingest(self, sid, data, metadata_header):
        session=self.store.get_session(sid)
        if session["site_id"]!=self.site_id or session["camera_id"]!=self.camera_id or session["state"]!="recording":
            raise ValueError("registered recording source required")
        if not isinstance(data,bytes) or not 0<len(data)<=256*1024 or not isinstance(metadata_header,str) or len(metadata_header)>16384:
            raise ValueError("bounded JPEG and metadata required")
        raw=base64.b64decode(metadata_header+"="*((-len(metadata_header))%4),altchars=b"-_",validate=True)
        source=json.loads(raw)
        causal=type(source.get("schema")) is int and source["schema"]==3
        if causal:
            # Reuse the shared camera contract: exact fields, JPEG hash, causal
            # ordering and uncertainty arithmetic. Missing dependency fails closed.
            from home_shared.protocol import camera_frame
            camera_frame(data,metadata_header+"="*((-len(metadata_header))%4))
        elif (source.get("schema")!=2 or source.get("source_clock")!="depthai_host_monotonic"
                or source.get("timestamp_semantics")!="commissioned_source_frame_timestamp_mapping"
                or source.get("width")!=640 or source.get("height")!=360 or not source.get("validation_id")):
            raise ValueError("commissioned schema-2 preview required")
        epoch=identifier(source.get("session"))
        sequence,timing=source.get("sequence"),source.get("timing_generation")
        stamp=source.get("observed_at_unix_ns")
        uncertainty=source.get("uncertainty_seconds")
        exposure=source.get("exposure_duration_seconds")
        if (type(sequence) is not int or sequence<0 or type(timing) is not int or timing<0
                or type(stamp) is not int or stamp<=0 or not finite(uncertainty) or not 0<=uncertainty<=1
                or not finite(exposure) or not 0<exposure<=uncertainty
                or (not causal and not finite(source.get("source_timestamp_seconds")))):
            raise ValueError("invalid preview timing")
        # Scale the original integer before the one epoch-second wire rounding;
        # converting modern UTC nanoseconds to float first can shift the result
        # by one ULP and conflict with the journal's exact original-clock check.
        observed=float(Decimal(stamp)/Decimal(1_000_000_000))
        now=self.store.clock()
        # The live causal client validates the current host receipt on this same
        # Jetson for every image. Do not reuse activation-time clock authority.
        receiver_error=source["host_clock_uncertainty_seconds"] if causal else self.clock_uncertainty
        receiver_id=source["host_clock_validation_id"] if causal else self.clock_id
        # Schema3 already includes this same host error in its validated full
        # causal bound; schema2 still needs the independent receiver addition.
        total=uncertainty if causal else uncertainty+receiver_error
        if total>1 or observed>now or now-observed+total>3:
            raise ValueError("preview stale, future or clock uncertainty too large")
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format!="JPEG" or image.size!=(640,360) or getattr(image,"n_frames",1)!=1:
                raise ValueError("preview JPEG dimensions/type mismatch")
            image.load()
        key=canonical([epoch,"rgb_preview",timing,sequence])
        meta={"site_id":self.site_id,"camera_id":self.camera_id,"capture_epoch":epoch,
            "frame_id":f"{epoch}-{timing}-{sequence}","sequence":sequence,"stream":"rgb",
            "width":640,"height":360,"format":"jpeg","observed_at":observed,"available_at":now,
            "valid_until":observed+3-total,"uncertainty_seconds":total,
            "clock_mapping_status":"verified","clock_mapping_id":source["validation_id"],
            "receiver_clock_validation_id":receiver_id,"timing_generation":timing,
            "session_time_s":observed-session["started_at"],"timestamp_semantics":source["timestamp_semantics"],
            "source_timestamp_seconds":source.get("source_timestamp_seconds"),"source_clock":source["source_clock"]}
        if causal:
            # Preserve the original evidence, without pretending device time is
            # the old host-clock mapping or replacing exposure with receipt time.
            meta["source_timing_receipt"]=source
            meta["source_timestamp_seconds"]=source["frame_device_ns"]/1e9
        if meta["session_time_s"]<0:
            raise ValueError("preview predates recording")
        with self.store.lock:
            prior=(self.media_writer.prior(sid,key) if self.media_writer is not None else
                self.store.db.execute("SELECT metadata FROM media WHERE session_id=? AND source_key=?",(sid,key)).fetchone())
            if prior:
                # Duplicate receipt cannot refresh availability or expiry.
                meta["available_at"]=(prior if self.media_writer is not None else json.loads(prior[0]))["available_at"]
            item=(self.media_writer if self.media_writer is not None else self.store).add_media(sid,data,"image/jpeg",meta,key)
        return {**meta,"session_id":sid,"media_ids":[item["id"]]}
