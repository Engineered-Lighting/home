"""Resolve one registered RGB observation into a bounded BGR worker input."""
import hashlib
import io


class RegisteredImage:
    def __init__(self, store):
        self.store=store

    def __call__(self, frame):
        session=self.store.get_session(frame["session_id"])
        if any(frame.get(k)!=session[k] for k in ("site_id","camera_id")):
            raise ValueError("source is outside the registered session")
        ids=frame.get("media_ids")
        if not isinstance(ids,list) or not 1<=len(ids)<=8:
            raise ValueError("bounded registered media IDs required")
        candidates=[]
        for mid in ids:
            from apartment_viewing_source import registered_media
            item=registered_media(self.store,session["id"],mid,generation=session["generation"])
            meta=item["metadata"]
            if item["mime_type"] in {"image/jpeg","image/png"} and meta.get("stream")=="rgb":
                candidates.append(item)
        if len(candidates)!=1:
            raise ValueError("one unambiguous registered RGB image required")
        item=candidates[0]; meta=item["metadata"]
        if any(not frame.get(k) or meta.get(k)!=frame[k] for k in ("site_id","camera_id","frame_id","capture_epoch")):
            raise ValueError("RGB metadata does not match offered frame")
        if meta.get("observed_at")!=frame.get("observed_at"):
            raise ValueError("RGB observation time mismatch")
        width,height=meta.get("width"),meta.get("height")
        if type(width) is not int or type(height) is not int or not 0<width<=3840 or not 0<height<=2160:
            raise ValueError("RGB dimensions exceed decode allowance")
        if not 0<item["size"]<=4*1024**2:
            raise ValueError("RGB encoded object exceeds decode allowance")
        from apartment_viewing_source import is_viewing_id, viewing_bytes
        if is_viewing_id(item['id']):
            # Only a development preview dispatch carries a deadline beyond the
            # image's own; that one read may use the owner's development retention.
            late=isinstance(frame.get("valid_until"),(int,float)) and frame["valid_until"]>meta.get("valid_until",frame["valid_until"])
            data=viewing_bytes(self.store,session['id'],item['id'],session['generation'],retained=late)
        else:
            with self.store.media_path(session["id"],item["id"]).open("rb") as source:
                data=source.read(item["size"]+1)
        if len(data)!=item["size"] or hashlib.sha256(data).hexdigest()!=item["sha256"]:
            raise ValueError("registered RGB bytes failed integrity check")
        from PIL import Image
        import numpy as np
        with Image.open(io.BytesIO(data)) as image:
            expected="JPEG" if item["mime_type"]=="image/jpeg" else "PNG"
            if image.format!=expected or image.size!=(width,height) or getattr(image,"n_frames",1)!=1:
                raise ValueError("RGB decoder dimensions/type mismatch")
            # Do not apply EXIF rotation: source coordinates describe the captured
            # pixel array. Normalize only channels, with a copied BGR byte array.
            rgb=np.asarray(image.convert("RGB"),dtype=np.uint8)
            bgr=np.ascontiguousarray(rgb[:,:,::-1])
        return bgr,{**meta,"session_id":session["id"],"media_id":item["id"]}
