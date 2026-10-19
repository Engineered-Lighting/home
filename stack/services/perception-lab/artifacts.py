"""Artifact inventory separates declarations, disk verification, and loaded state."""
import json
import re
import hashlib
from pathlib import Path
from store import LabError, encode, identifier


class ArtifactRegistry:
    def __init__(self, store):
        self.store = store

    def register(self, manifest):
        identifier(manifest.get("id"))
        required = ("checkpoint", "version", "license", "runtime", "precision", "device_id")
        if any(not isinstance(manifest.get(k),str) or not manifest[k] for k in required):
            raise LabError("exact artifact provenance required",422,"invalid_artifact")
        sha = manifest.get("sha256")
        if not isinstance(sha,str) or not re.fullmatch("[a-f0-9]{64}",sha):
            raise LabError("artifact digest required",422,"invalid_artifact")
        value = {**manifest,"on_disk":"unverified","loaded":False,"readiness":"unverified","state":"unverified","runtime_validated":False,"warmed":False}
        value.pop("path",None)
        if len(encode(value))>65536:
            raise LabError("artifact manifest too large",413,"too_large")
        with self.store.transaction() as db:
            previous=db.execute("SELECT payload FROM artifacts WHERE id=?",(value["id"],)).fetchone()
            if previous:
                prior=json.loads(previous[0])
                mutable={"state","on_disk","loaded","readiness","runtime_validated","warmed","verified_at","size_bytes"}
                if {k:v for k,v in prior.items() if k not in mutable}!={k:v for k,v in value.items() if k not in mutable}:
                    raise LabError("artifact identity is immutable; use a new artifact ID")
                return prior
            db.execute("INSERT INTO artifacts VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(value["id"],encode(value)))
        return value

    def list(self):
        with self.store.lock:
            rows=self.store.db.execute("SELECT payload FROM artifacts ORDER BY id").fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, artifact_id):
        identifier(artifact_id)
        with self.store.lock:
            row=self.store.db.execute("SELECT payload FROM artifacts WHERE id=?",(artifact_id,)).fetchone()
        if not row:
            raise LabError("artifact is not registered",404,"unknown_artifact")
        return json.loads(row[0])

    def verify(self, artifact_id, cache_root, relative_path, *, maximum_bytes, cancelled=lambda:False):
        """Bounded streaming checksum of a provisioned path; never loads a model.

        Paths come from the service's device-specific bindings, not job parameters.
        Refuse symlinks/junctions and changes during the read. Later loading must
        revalidate the artifact; this receipt is not an inference admission lease.
        """
        artifact=self.get(artifact_id)
        configured_root=Path(cache_root)
        if configured_root.is_symlink() or getattr(configured_root,"is_junction",lambda:False)():
            raise LabError("cache root links are not allowed",422,"invalid_binding")
        root=configured_root.resolve(strict=True)
        relative=Path(relative_path)
        if relative.is_absolute() or not relative.parts or any(p in {".",".."} for p in relative.parts):
            raise LabError("invalid cache binding",422,"invalid_binding")
        target=root
        for part in relative.parts:
            target=target/part
            if target.is_symlink() or getattr(target,"is_junction",lambda:False)():
                raise LabError("cache links are not allowed",422,"invalid_binding")
        target=target.resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise LabError("artifact outside cache",422,"invalid_binding")
        stat=target.stat()
        if type(maximum_bytes) is not int or maximum_bytes<=0 or not 0<stat.st_size<=maximum_bytes:
            raise LabError("artifact exceeds commissioned cache allowance",413,"artifact_size")
        checksum=hashlib.sha256()
        consumed=0
        with target.open('rb') as source:
            while True:
                if cancelled():
                    return {"state":"cancelled","id":artifact_id}
                block=source.read(1024*1024)
                if not block:break
                consumed+=len(block)
                if consumed>maximum_bytes:raise LabError("artifact grew during verification",409,"artifact_changed")
                checksum.update(block)
        after=target.stat()
        if (stat.st_size,stat.st_mtime_ns,stat.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or consumed!=stat.st_size:
            raise LabError("artifact changed during verification",409,"artifact_changed")
        matched=checksum.hexdigest()==artifact["sha256"]
        value={**artifact,"state":"stored" if matched else "invalid","on_disk":"verified" if matched else "checksum_mismatch",
               "readiness":"runtime_unvalidated","loaded":False,"warmed":False,"runtime_validated":False,
               "verified_at":self.store.clock(),"size_bytes":consumed}
        with self.store.transaction() as db:
            if self.get(artifact_id)["sha256"]!=artifact["sha256"]:
                raise LabError("artifact registration changed",409,"artifact_changed")
            db.execute("UPDATE artifacts SET payload=? WHERE id=?",(encode(value),artifact_id))
        return value


class ArtifactPreparation:
    """Verify pre-staged artifacts within a per-request read budget.

    This allowance does not enforce filesystem cache quota; that remains an
    independently commissioned storage boundary. No downloads or model loads.
    """
    def __init__(self, registry, cache_root, bindings, *, device_id, quota_bytes):
        if type(quota_bytes) is not int or quota_bytes<=0:
            raise ValueError("positive artifact verification byte budget required")
        identifier(device_id)
        self.registry,self.cache_root,self.bindings=registry,cache_root,dict(bindings)
        self.device_id,self.quota_bytes=device_id,quota_bytes

    def run(self, job, _frames=None, _observations=None, *, cancelled=lambda:False):
        parameters=job["parameters"]
        ids=parameters.get("artifact_ids")
        if (set(parameters)-{"artifact_ids","keep_ready"} or not isinstance(ids,list) or not 1<=len(ids)<=16
                or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids)):
            return {"state":"blocked","reason":"invalid_preparation_request"}
        if parameters.get("keep_ready") is True:
            return {"state":"blocked","reason":"residency_admission_not_provisioned"}
        if parameters.get("keep_ready") is not None and parameters.get("keep_ready") is not False:
            return {"state":"blocked","reason":"invalid_residency_request"}
        try:
            artifacts=[self.registry.get(i) for i in ids]
        except LabError:
            return {"state":"blocked","reason":"artifact_not_registered"}
        if any(a["id"] not in self.bindings or a["device_id"]!=self.device_id for a in artifacts):
            return {"state":"blocked","reason":"artifact_device_not_provisioned"}
        results=[]
        remaining=self.quota_bytes
        for artifact in artifacts:
            try:
                result=self.registry.verify(artifact["id"],self.cache_root,self.bindings[artifact["id"]],maximum_bytes=remaining,cancelled=cancelled)
            except (LabError,OSError):
                return {"state":"failed","reason":"artifact_verification_failed","artifacts":results}
            if result["state"]=="cancelled":return {"state":"cancelled","artifacts":results}
            results.append(result)
            remaining-=result["size_bytes"]
            if result["state"]!="stored":return {"state":"failed","reason":"artifact_checksum_mismatch","artifacts":results}
        return {"state":"completed","phase":"stored_only","artifacts":results,"runtime_validated":False,"loaded":False,"warmed":False}
