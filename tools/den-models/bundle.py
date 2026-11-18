#!/usr/bin/env python3
"""Build a deterministic, source-only worker tar from one explicit Git commit.

No environment, snapshot, model weight, private configuration or credential is
provisioned. This command never installs, starts or contacts a service.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

SUBTREE = "tools/den-models"
MANIFEST = "SOURCE-MANIFEST.json"
ROOT_FILES = frozenset(("README.md", "requirements.lock", "requirements-test.lock", "operator.example.json", "control.example.json",
                        "control-producer.example.json", "control-producer.service.example",
                        "serve.py", "run_bounded.py", "bundle.py"))
MODULES = frozenset(("__init__.py", "artifacts.py", "backend.py", "client.py", "contracts.py", "resources.py",
                     "runtime.py", "server.py", "specialists.py", "specialist_cli.py", "control_producer.py"))
REQUIRED = frozenset(("requirements.lock", "serve.py", "run_bounded.py", "den_models/__init__.py",
                      "den_models/artifacts.py", "den_models/backend.py", "den_models/client.py",
                      "den_models/contracts.py", "den_models/resources.py", "den_models/runtime.py",
                      "den_models/server.py", "profiles/vjepa-lm-qwen3-8b.json", "profiles/vjepa-lm-qwen3-4b.json"))
MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_FILES = 256


class BundleError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                            capture_output=True, timeout=20, env=env)
    if result.returncode:
        raise BundleError("git_source_inspection_failed")
    return result.stdout


def allowed(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or any(p in (".", "..") for p in path.parts)
            or "\\" in name or ":" in name):
        return False
    if name in ROOT_FILES:
        return True
    if len(path.parts) == 2 and path.parts[0] == "den_models":
        return path.name in MODULES
    if len(path.parts) == 2 and path.parts[0] == "tests":
        return re.fullmatch(r"test_[a-z0-9_]+\.py", path.name) is not None
    return (len(path.parts) in (2, 3) and path.parts[0] == "profiles"
            and (len(path.parts) == 2 or path.parts[1] == "specialists")
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.json", path.name) is not None)


def strict_json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise BundleError("duplicate_source_json_key")
            value[key] = item
        return value
    def nonfinite(_):
        raise BundleError("nonfinite_source_json")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)


def source(repo, commit):
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit):
        raise BundleError("full_immutable_commit_required")
    resolved = git(repo, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise BundleError("full_immutable_commit_required")
    if git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", SUBTREE):
        raise BundleError("dirty_model_source_subtree")
    tree = git(repo, "ls-tree", "-r", "-l", "-z", "--full-tree", commit, "--", SUBTREE)
    entries, total = {}, 0
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, encoded_path = entry.split(b"\t", 1)
        mode, kind, blob, length = metadata.decode("ascii").split()
        name = encoded_path.decode("utf-8")
        if not name.startswith(SUBTREE + "/"):
            raise BundleError("unexpected_source_tree")
        relative = name[len(SUBTREE) + 1:]
        if mode not in ("100644", "100755") or kind != "blob" or not allowed(relative):
            raise BundleError("forbidden_source_entry")
        size = int(length)
        total += size
        if size > MAX_FILE or total > MAX_TOTAL or len(entries) >= MAX_FILES:
            raise BundleError("source_bundle_over_bound")
        data = git(repo, "cat-file", "blob", blob)
        if len(data) != size:
            raise BundleError("source_blob_size_changed")
        entries[relative] = (data, int(mode[-3:], 8), blob)
    if not REQUIRED <= set(entries):
        raise BundleError("incomplete_model_source_tree")
    return entries


def build(repo, commit):
    """Payload bytes come from commit objects, never from working-tree files."""
    entries = source(Path(repo), commit)
    files, profiles = {}, {}
    payload = {}
    for name, (data, mode, blob) in sorted(entries.items()):
        payload[SUBTREE + "/" + name] = (data, mode)
        files[name] = {"sha256": digest(data), "bytes": len(data), "mode": mode, "git_blob": blob}
        if name.startswith("profiles/") and name.endswith(".json"):
            value = strict_json(data)
            identity = value.get("id") if isinstance(value, dict) else None
            if identity != PurePosixPath(name).stem or identity in profiles:
                raise BundleError("invalid_or_duplicate_source_profile")
            profiles[identity] = {"path": name, "profile_digest": digest(canonical(value).encode())}
    manifest = {"schema": "den.model-source-bundle/v1", "commit": commit, "subtree": SUBTREE,
                "profiles": profiles, "files": files}
    manifest_bytes = (canonical(manifest) + "\n").encode()
    payload[MANIFEST] = (manifest_bytes, 0o644)
    checksums = "".join(digest(data) + "  " + name + "\n" for name, (data, _) in sorted(payload.items()))
    payload["SHA256SUMS"] = (checksums.encode(), 0o644)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, (data, mode) in sorted(payload.items()):
            item = tarfile.TarInfo(name)
            item.mode, item.size, item.mtime = mode, len(data), 0
            item.uid = item.gid = 0
            item.uname = item.gname = ""
            archive.addfile(item, io.BytesIO(data))
    return output.getvalue(), manifest, manifest_bytes


def write_bundle(repo, commit, output):
    repo, output = Path(repo).resolve(), Path(output).absolute()
    if output.suffix != ".tar" or not output.parent.is_dir():
        raise BundleError("existing_output_directory_and_tar_name_required")
    model_root = repo / SUBTREE
    if output == model_root or model_root in output.resolve().parents:
        raise BundleError("output_must_be_outside_model_source")
    destinations = (output, Path(str(output) + ".manifest.json"), Path(str(output) + ".sha256"))
    if any(path.exists() or path.is_symlink() for path in destinations):
        raise BundleError("output_already_exists")
    data, manifest, manifest_bytes = build(repo, commit)
    contents = (data, manifest_bytes, (digest(data) + "  " + output.name + "\n").encode())
    created = []
    try:
        for path, content in zip(destinations, contents):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {"schema": manifest["schema"], "commit": commit, "artifact": str(output),
            "artifact_sha256": digest(data), "artifact_bytes": len(data), "source_files": len(manifest["files"]),
            "manifest_sha256": digest(manifest_bytes), "profiles": manifest["profiles"],
            "installed": False, "services_started": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--commit", required=True, help="full immutable Git commit hash")
    parser.add_argument("--output", required=True, type=Path, help="new .tar path outside the model subtree")
    args = parser.parse_args(argv)
    print(canonical(write_bundle(args.repo, args.commit, args.output)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BundleError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        reason = str(exc) if isinstance(exc, BundleError) else "source_inspection_or_output_failed"
        raise SystemExit("Source bundle refused: " + reason)
