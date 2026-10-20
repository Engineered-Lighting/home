"""Read only registered session evidence; never fetch caller-selected URLs."""
from reasoning import finite
from persisted_evidence import observation_record
from qwen_adapter import MAX_IMAGE_BYTES


class RegisteredAnalysisInputs:
    def __init__(self, store):
        self.store=store

    def __call__(self, job):
        session=self.store.get_session(job["session_id"])
        frames=[]
        cursor=0
        count=0
        while True:
            page=self.store.list_media(session["id"],cursor=cursor,limit=200)
            count+=len(page["items"])
            if count>20000:
                raise ValueError("select a bounded media collection")
            for media in page["items"]:
                meta=media["metadata"]
                # Only stored JPEG samples within the admitted image bound are
                # sent unchanged; a PNG or oversize object would need re-encoding.
                if (media["mime_type"]!="image/jpeg" or meta.get("stream")!="rgb"
                        or type(media.get("size")) is not int or not 0<media["size"]<=MAX_IMAGE_BYTES):
                    continue
                # The existing encoded PTS and numerical packaging clocks do
                # not establish exposure time. A commissioned mapping must.
                if (meta.get("site_id")!=session["site_id"] or meta.get("camera_id")!=session["camera_id"]
                        or meta.get("clock_mapping_status")!="verified" or not meta.get("clock_mapping_id")
                        or not meta.get("capture_epoch") or not meta.get("frame_id")
                        or not all(finite(meta.get(k)) for k in ("observed_at","available_at","uncertainty_seconds"))
                        or not 0<=meta["uncertainty_seconds"]<=1 or meta["available_at"]<meta["observed_at"]):
                    continue
                frames.append({"media_id":media["id"],"site_id":session["site_id"],"session_id":session["id"],
                               "observed_at":meta["observed_at"],"available_at":meta["available_at"]})
            cursor=page["next_cursor"]
            if cursor is None:
                break
        if not frames:
            raise ValueError("no registered RGB JPEG samples with verified clock mapping; extraction required")
        observations=[]
        cursor=0
        count=0
        while True:
            page=self.store.list_events(session["id"],after_id=cursor,limit=5000)
            if not page:
                break
            count+=len(page)
            if count>100000:
                raise ValueError("select a bounded event history")
            cursor=page[-1]["id"]
            for event in page:
                if event["kind"] not in {"perception.output","ha.observation","room.observation"}:
                    continue
                record=observation_record(event["id"],event["kind"],event["payload"],session)
                if record is not None:
                    observations.append(record)
        return frames,observations
