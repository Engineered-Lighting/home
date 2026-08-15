#!/usr/bin/env python3
"""Bind an imported Docker image to its attested archive across image stores.

Docker's classic image store reports the config digest as ``.Id``. Newer
containerd-backed stores report a synthesized manifest digest instead. This
verifier accepts either representation only after re-exporting the loaded
image and proving that its config bytes, source tag, revision label, and rootfs
diff IDs still match the externally attested deployment manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Mapping, Sequence


CONTRACT = "home-agent-imported-image-identity-v1"
BUNDLE_ROOT = Path("/srv/home-agent/image-bundles")
REPOSITORY = "Engineered-Lighting/home"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID = re.compile(r"^[1-9][0-9]{5,19}$")
MAX_JSON = 16 * 1024 * 1024
CORE_BASE = {
    "reference": "python:3.12-slim",
    "index_digest": (
        "sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36"
    ),
    "linux_amd64_digest": (
        "sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64"
    ),
}
WEB_BASE = {
    "reference": "node:24-alpine",
    "index_digest": (
        "sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd"
    ),
    "linux_amd64_digest": (
        "sha256:4ba75f835bb8802193e4c114572113d4b26f95f6f094f4b5229d2a77773e0afc"
    ),
}


class ImportedImageError(RuntimeError):
    """An imported image could not be bound to its attested archive."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ImportedImageError("JSON contains a duplicate key")
        result[key] = value
    return result


def _json(raw: bytes, *, maximum: int = MAX_JSON) -> Any:
    if not raw or len(raw) > maximum or b"\0" in raw:
        raise ImportedImageError("JSON input is invalid")
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImportedImageError("JSON input is invalid") from error


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def manifest_identity(raw: bytes, kind: str) -> dict[str, str]:
    """Validate one exact deployment manifest and select an image identity."""

    manifest = _json(raw, maximum=64 * 1024)
    if not isinstance(manifest, Mapping):
        raise ImportedImageError("deployment manifest is invalid")
    common = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "source_ref": "refs/heads/main",
        "platform": "linux/amd64",
    }
    if any(manifest.get(key) != value for key, value in common.items()):
        raise ImportedImageError("deployment manifest header is invalid")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or COMMIT.fullmatch(commit) is None:
        raise ImportedImageError("deployment source commit is invalid")

    if kind == "core":
        if set(manifest) != {
            "schema_version",
            "repository",
            "source_ref",
            "source_commit",
            "workflow_run_id",
            "platform",
            "base_image",
            "image",
        }:
            raise ImportedImageError("Core manifest schema is invalid")
        if (
            not isinstance(manifest.get("workflow_run_id"), str)
            or RUN_ID.fullmatch(str(manifest["workflow_run_id"])) is None
            or manifest.get("base_image") != CORE_BASE
        ):
            raise ImportedImageError("Core manifest build identity is invalid")
        image = manifest.get("image")
        expected = {
            "source_tag": f"engineered-lighting/home-agent-core:ci-{commit}",
            "deployment_tag": "engineered-lighting/home-agent-core:local",
            "archive": "home-agent-core-linux-amd64.tar.gz",
            "revision_label": commit,
        }
        expected_fields = set(expected) | {"image_id", "archive_sha256"}
    elif kind in {"bff", "origin"}:
        if set(manifest) != {
            "schema_version",
            "repository",
            "source_ref",
            "source_commit",
            "platform",
            "base_image",
            "images",
        } or manifest.get("base_image") != WEB_BASE:
            raise ImportedImageError("web manifest schema is invalid")
        images = manifest.get("images")
        if not isinstance(images, Mapping) or set(images) != {"bff", "origin"}:
            raise ImportedImageError("web image set is invalid")
        image = images.get(kind)
        expected = {
            "source_tag": f"engineered-lighting/home-agent-{kind}:ci-{commit}",
            "deployment_tag": f"engineered-lighting/home-agent-{kind}:local",
            "archive": f"home-agent-{kind}-linux-amd64.tar.gz",
        }
        expected_fields = set(expected) | {"image_id", "archive_sha256"}
    else:
        raise ImportedImageError("image kind is unsupported")

    if not isinstance(image, Mapping) or set(image) != expected_fields:
        raise ImportedImageError("image manifest schema is invalid")
    if any(image.get(key) != value for key, value in expected.items()):
        raise ImportedImageError("image manifest identity is invalid")
    if (
        not isinstance(image.get("image_id"), str)
        or DIGEST.fullmatch(str(image["image_id"])) is None
        or not isinstance(image.get("archive_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(image["archive_sha256"])) is None
    ):
        raise ImportedImageError("image manifest digest is invalid")
    return {
        "kind": kind,
        "source_commit": commit,
        "source_tag": str(image["source_tag"]),
        "deployment_tag": str(image["deployment_tag"]),
        "manifest_config_id": str(image["image_id"]),
    }


def verify_identity(
    *,
    identity: Mapping[str, str],
    inspected: Mapping[str, Any],
    archive_manifest_raw: bytes,
    config_raw: bytes,
) -> dict[str, Any]:
    """Verify the loaded Docker representation against the signed config."""

    expected_config = identity["manifest_config_id"]
    source_tag = identity["source_tag"]
    deployment_tag = identity["deployment_tag"]
    local_id = inspected.get("Id")
    tags = inspected.get("RepoTags")
    config = inspected.get("Config")
    rootfs = inspected.get("RootFS")
    if (
        not isinstance(local_id, str)
        or DIGEST.fullmatch(local_id) is None
        or not isinstance(tags, list)
        or any(not isinstance(tag, str) for tag in tags)
        or source_tag not in tags
        or not set(tags).issubset({source_tag, deployment_tag})
        or not isinstance(config, Mapping)
        or not isinstance(config.get("Labels"), Mapping)
        or config["Labels"].get("org.opencontainers.image.revision")
        != identity["source_commit"]
        or not isinstance(rootfs, Mapping)
        or rootfs.get("Type") != "layers"
        or not isinstance(rootfs.get("Layers"), list)
    ):
        raise ImportedImageError("loaded image inspection is invalid")

    archive_manifest = _json(archive_manifest_raw)
    if not isinstance(archive_manifest, list) or len(archive_manifest) != 1:
        raise ImportedImageError("Docker archive manifest is invalid")
    record = archive_manifest[0]
    if not isinstance(record, Mapping) or set(record) != {
        "Config",
        "RepoTags",
        "Layers",
    }:
        raise ImportedImageError("Docker archive record is invalid")
    archive_tags = record.get("RepoTags")
    archive_layers = record.get("Layers")
    if (
        not isinstance(archive_tags, list)
        or any(not isinstance(tag, str) for tag in archive_tags)
        or source_tag not in archive_tags
        or not set(archive_tags).issubset({source_tag, deployment_tag})
        or not isinstance(archive_layers, list)
        or not archive_layers
    ):
        raise ImportedImageError("Docker archive tags are invalid")
    for layer in archive_layers:
        if not isinstance(layer, str):
            raise ImportedImageError("Docker archive layers are invalid")
        layer_path = PurePosixPath(layer)
        if (
            not layer_path.parts
            or layer_path == PurePosixPath(".")
            or layer_path.is_absolute()
            or ".." in layer_path.parts
        ):
            raise ImportedImageError("Docker archive layers are invalid")
    archive_config = record.get("Config")
    if not isinstance(archive_config, str):
        raise ImportedImageError("Docker archive config identity is invalid")
    config_path = PurePosixPath(archive_config)
    expected_hex = expected_config.removeprefix("sha256:")
    if (
        config_path.is_absolute()
        or ".." in config_path.parts
        or config_path.name.removesuffix(".json") != expected_hex
        or _digest(config_raw) != expected_config
    ):
        raise ImportedImageError("Docker archive config identity is invalid")

    config_document = _json(config_raw)
    if not isinstance(config_document, Mapping):
        raise ImportedImageError("Docker config is invalid")
    signed_config = config_document.get("config")
    config_rootfs = config_document.get("rootfs")
    if (
        config_document.get("architecture") != "amd64"
        or config_document.get("os") != "linux"
        or not isinstance(signed_config, Mapping)
        or not isinstance(signed_config.get("Labels"), Mapping)
        or signed_config["Labels"].get("org.opencontainers.image.revision")
        != identity["source_commit"]
    ):
        raise ImportedImageError("signed Docker config identity is invalid")
    if (
        not isinstance(config_rootfs, Mapping)
        or config_rootfs.get("type") != "layers"
        or not isinstance(config_rootfs.get("diff_ids"), list)
        or not config_rootfs["diff_ids"]
        or any(
            not isinstance(value, str) or DIGEST.fullmatch(value) is None
            for value in config_rootfs["diff_ids"]
        )
        or config_rootfs["diff_ids"] != rootfs["Layers"]
    ):
        raise ImportedImageError("loaded image rootfs differs from signed config")

    rootfs_digest = hashlib.sha256(
        json.dumps(
            config_rootfs["diff_ids"], separators=(",", ":"), sort_keys=False
        ).encode("ascii")
    ).hexdigest()
    return {
        "contract": CONTRACT,
        "status": "verified",
        "kind": identity["kind"],
        "source_commit": identity["source_commit"],
        "source_tag": source_tag,
        "deployment_tag": deployment_tag,
        "manifest_config_id": expected_config,
        "local_image_id": local_id,
        "image_store_identity": (
            "config_digest" if local_id == expected_config else "translated_manifest_digest"
        ),
        "rootfs_diff_ids_digest": rootfs_digest,
    }


def _safe_manifest(path: Path) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        details = path.lstat()
    except OSError as error:
        raise ImportedImageError("deployment manifest is unavailable") from error
    if (
        BUNDLE_ROOT not in resolved.parents
        or resolved != path
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o400
        or details.st_nlink != 1
    ):
        raise ImportedImageError("deployment manifest is unsafe")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ImportedImageError("deployment manifest is unavailable") from error


def _run(command: Sequence[str], *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ImportedImageError("Docker inspection failed") from error
    if result.returncode != 0 or not result.stdout:
        raise ImportedImageError("Docker inspection failed")
    return result.stdout


def _saved_identity(source_tag: str, expected_config: str) -> tuple[bytes, bytes]:
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed Docker argv, no shell
            ["docker", "image", "save", source_tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as error:
        raise ImportedImageError("Docker archive probe failed") from error
    if process.stdout is None:
        process.kill()
        raise ImportedImageError("Docker archive probe failed")
    manifest_raw: bytes | None = None
    config_raw: bytes | None = None
    expected_hex = expected_config.removeprefix("sha256:")
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ImportedImageError("Docker archive path is unsafe")
                is_manifest = member.name == "manifest.json"
                is_config = path.name.removesuffix(".json") == expected_hex
                if not (is_manifest or is_config):
                    continue
                if not member.isfile() or member.size > MAX_JSON:
                    raise ImportedImageError("Docker archive metadata is invalid")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ImportedImageError("Docker archive metadata is invalid")
                raw = stream.read(MAX_JSON + 1)
                if len(raw) > MAX_JSON:
                    raise ImportedImageError("Docker archive metadata is invalid")
                if is_manifest:
                    if manifest_raw is not None:
                        raise ImportedImageError("Docker archive manifest is duplicated")
                    manifest_raw = raw
                if is_config:
                    if config_raw is not None:
                        raise ImportedImageError("Docker archive config is duplicated")
                    config_raw = raw
        if process.wait(timeout=30) != 0:
            raise ImportedImageError("Docker archive probe failed")
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    if manifest_raw is None or config_raw is None:
        raise ImportedImageError("Docker archive identity is incomplete")
    return manifest_raw, config_raw


def live_report(kind: str, manifest_path: Path) -> dict[str, Any]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ImportedImageError("import verification requires root on Linux")
    identity = manifest_identity(_safe_manifest(manifest_path), kind)
    inspected = _json(
        _run(["docker", "image", "inspect", identity["source_tag"]])
    )
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise ImportedImageError("loaded image inspection is invalid")
    archive_manifest, config_raw = _saved_identity(
        identity["source_tag"], identity["manifest_config_id"]
    )
    return verify_identity(
        identity=identity,
        inspected=inspected[0],
        archive_manifest_raw=archive_manifest,
        config_raw=config_raw,
    )


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"core", "bff", "origin"}:
        print(
            "imported image verifier requires core, bff, or origin and one manifest",
            file=sys.stderr,
        )
        return 64
    try:
        report = live_report(sys.argv[1], Path(sys.argv[2]))
    except (ImportedImageError, KeyError, ValueError):
        print("imported image identity could not be verified", file=sys.stderr)
        return 78
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
