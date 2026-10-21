"""Build a reproducible managed Lab tar from a committed source revision."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = "stack/services/perception-lab/"


def build(repo, ref, out):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])
    source = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    entries = git("ls-tree", "-r", "-z", source, ROOT).split(b"\0")
    files = {}
    for entry in filter(None, entries):
        meta, raw = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        name = raw.decode("utf-8")
        relative = name[len(ROOT):]
        parts = relative.split("/")
        if kind != "blob" or mode not in ("100644", "100755"):
            continue
        if any(p.startswith(("test_", ".")) or p in ("tests", "__pycache__") for p in parts):
            continue
        if relative.startswith("deploy/"):
            destination = relative
        elif "/" not in relative and Path(relative).suffix in (".py", ".json", ".txt", ".lock"):
            destination = "service/" + relative
        else:
            continue
        files[destination] = (git("cat-file", "blob", oid), int(mode[-3:], 8))
    files["service/lifecycle.py"] = (git("show", source + ":tools/la-detector-trial/lifecycle.py"), 0o644)
    if "service/managed_detector_app.py" not in files:
        raise ValueError("source has no managed Lab entrypoint")
    hashes = {name: hashlib.sha256(data).hexdigest() for name, (data, _) in sorted(files.items())}
    revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    entrypoints = ["jetson_detector_app:create_jetson_detector_app", "la_detector_app:create_la_detector_app",
                   "managed_detector_app:create_managed_refresh_app"]
    if "service/continuous_app.py" in files:
        entrypoints.append("continuous_app:create_app")
    manifest = {"schema_version": 1, "purpose": "Victoria managed Lab controller",
        "deployment_authorized": False, "controller_revision": revision, "source_ref": source,
        "entrypoints": entrypoints, "files": hashes}
    files["manifest.json"] = ((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), 0o644)
    with open(out, "xb") as target:
        with tarfile.open(fileobj=target, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, (data, mode) in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.size, item.mode, item.mtime = len(data), mode, 0
                archive.addfile(item, io.BytesIO(data))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True, help="committed revision; resolved SHA is sealed in manifest")
    parser.add_argument("--out", type=Path, required=True, help="new tar path; never overwrites")
    args = parser.parse_args()
    result = build(args.repo, args.ref, args.out)
    print(json.dumps({"release": result["controller_revision"][:16], "source": result["source_ref"],
                      "files": len(result["files"])}))


if __name__ == "__main__":
    main()
