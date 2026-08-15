from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = (
    ROOT / "stack/home-agent-deploy/operator/imported_image_identity.py"
)
COMMIT = "a" * 40
LOCAL_ID = "sha256:" + "b" * 64
DIFF_IDS = ["sha256:" + "c" * 64, "sha256:" + "d" * 64]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_imported_image_identity_e5ai",
        VERIFIER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _config() -> bytes:
    return _json_bytes(
        {
            "architecture": "amd64",
            "config": {
                "Labels": {"org.opencontainers.image.revision": COMMIT},
            },
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": DIFF_IDS},
        }
    )


def _config_id(config_raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(config_raw).hexdigest()


def _core_manifest(config_raw: bytes) -> bytes:
    return _json_bytes(
        {
            "schema_version": 1,
            "repository": "Engineered-Lighting/home",
            "source_ref": "refs/heads/main",
            "source_commit": COMMIT,
            "workflow_run_id": "31905342824",
            "platform": "linux/amd64",
            "base_image": {
                "reference": "python:3.12-slim",
                "index_digest": (
                    "sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad"
                    "142b1877b5830d36"
                ),
                "linux_amd64_digest": (
                    "sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce2"
                    "3c07330c5a89e64"
                ),
            },
            "image": {
                "source_tag": f"engineered-lighting/home-agent-core:ci-{COMMIT}",
                "deployment_tag": "engineered-lighting/home-agent-core:local",
                "image_id": _config_id(config_raw),
                "archive": "home-agent-core-linux-amd64.tar.gz",
                "archive_sha256": "e" * 64,
                "revision_label": COMMIT,
            },
        }
    )


def _web_manifest(config_raw: bytes) -> bytes:
    def image(name: str, digest: str) -> dict[str, str]:
        return {
            "source_tag": f"engineered-lighting/home-agent-{name}:ci-{COMMIT}",
            "deployment_tag": f"engineered-lighting/home-agent-{name}:local",
            "image_id": digest,
            "archive": f"home-agent-{name}-linux-amd64.tar.gz",
            "archive_sha256": "f" * 64,
        }

    return _json_bytes(
        {
            "schema_version": 1,
            "repository": "Engineered-Lighting/home",
            "source_ref": "refs/heads/main",
            "source_commit": COMMIT,
            "platform": "linux/amd64",
            "base_image": {
                "reference": "node:24-alpine",
                "index_digest": (
                    "sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1"
                    "a95432d679c54fbfd"
                ),
                "linux_amd64_digest": (
                    "sha256:4ba75f835bb8802193e4c114572113d4b26f95f6f094f4b5"
                    "229d2a77773e0afc"
                ),
            },
            "images": {
                "bff": image("bff", _config_id(config_raw)),
                "origin": image("origin", "sha256:" + "1" * 64),
            },
        }
    )


def _inspection(identity: dict[str, str], *, local_id: str = LOCAL_ID) -> dict:
    return {
        "Id": local_id,
        "RepoTags": [identity["source_tag"]],
        "Config": {
            "Labels": {"org.opencontainers.image.revision": COMMIT},
        },
        "RootFS": {"Type": "layers", "Layers": DIFF_IDS},
    }


def _archive_manifest(identity: dict[str, str]) -> bytes:
    config_name = identity["manifest_config_id"].removeprefix("sha256:")
    return _json_bytes(
        [
            {
                "Config": f"blobs/sha256/{config_name}",
                "RepoTags": [identity["source_tag"]],
                "Layers": ["blobs/sha256/layer-one", "layer-two/layer.tar"],
            }
        ]
    )


def _verify(module: ModuleType, *, local_id: str = LOCAL_ID) -> dict:
    config_raw = _config()
    identity = module.manifest_identity(_core_manifest(config_raw), "core")
    return module.verify_identity(
        identity=identity,
        inspected=_inspection(identity, local_id=local_id),
        archive_manifest_raw=_archive_manifest(identity),
        config_raw=config_raw,
    )


def test_containerd_local_id_is_bound_to_the_attested_config_and_rootfs() -> None:
    module = _module()

    report = _verify(module)

    assert report == {
        "contract": "home-agent-imported-image-identity-v1",
        "status": "verified",
        "kind": "core",
        "source_commit": COMMIT,
        "source_tag": f"engineered-lighting/home-agent-core:ci-{COMMIT}",
        "deployment_tag": "engineered-lighting/home-agent-core:local",
        "manifest_config_id": _config_id(_config()),
        "local_image_id": LOCAL_ID,
        "image_store_identity": "translated_manifest_digest",
        "rootfs_diff_ids_digest": hashlib.sha256(
            json.dumps(DIFF_IDS, separators=(",", ":")).encode("ascii")
        ).hexdigest(),
    }


def test_classic_store_config_id_remains_accepted() -> None:
    module = _module()
    config_id = _config_id(_config())

    report = _verify(module, local_id=config_id)

    assert report["image_store_identity"] == "config_digest"
    assert report["local_image_id"] == config_id


@pytest.mark.parametrize("kind", ["core", "bff"])
def test_exact_core_and_web_manifest_schemas_are_accepted(kind: str) -> None:
    module = _module()
    config_raw = _config()
    raw = _core_manifest(config_raw) if kind == "core" else _web_manifest(config_raw)

    identity = module.manifest_identity(raw, kind)

    assert identity["kind"] == kind
    assert identity["source_commit"] == COMMIT
    assert identity["manifest_config_id"] == _config_id(config_raw)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(repository="attacker/home"),
        lambda value: value.update(source_ref="refs/heads/feature"),
        lambda value: value.update(platform="linux/arm64"),
        lambda value: value.update(unexpected=True),
        lambda value: value["base_image"].update(reference="python:latest"),
        lambda value: value["image"].update(source_tag="attacker/image:latest"),
        lambda value: value["image"].update(revision_label="f" * 40),
    ],
)
def test_manifest_header_schema_base_and_image_drift_fail_closed(mutation) -> None:
    module = _module()
    manifest = json.loads(_core_manifest(_config()))
    mutation(manifest)

    with pytest.raises(module.ImportedImageError):
        module.manifest_identity(_json_bytes(manifest), "core")


def test_config_byte_drift_fails_closed() -> None:
    module = _module()
    config_raw = _config()
    identity = module.manifest_identity(_core_manifest(config_raw), "core")

    with pytest.raises(module.ImportedImageError, match="config identity"):
        module.verify_identity(
            identity=identity,
            inspected=_inspection(identity),
            archive_manifest_raw=_archive_manifest(identity),
            config_raw=config_raw + b" ",
        )


def test_rootfs_drift_fails_closed() -> None:
    module = _module()
    config_raw = _config()
    identity = module.manifest_identity(_core_manifest(config_raw), "core")
    inspected = _inspection(identity)
    inspected["RootFS"]["Layers"] = ["sha256:" + "9" * 64]

    with pytest.raises(module.ImportedImageError, match="rootfs"):
        module.verify_identity(
            identity=identity,
            inspected=inspected,
            archive_manifest_raw=_archive_manifest(identity),
            config_raw=config_raw,
        )


def test_extra_tag_and_revision_drift_fail_closed() -> None:
    module = _module()
    config_raw = _config()
    identity = module.manifest_identity(_core_manifest(config_raw), "core")
    inspected = _inspection(identity)
    inspected["RepoTags"].append("attacker/image:latest")

    with pytest.raises(module.ImportedImageError, match="inspection"):
        module.verify_identity(
            identity=identity,
            inspected=inspected,
            archive_manifest_raw=_archive_manifest(identity),
            config_raw=config_raw,
        )

    inspected = _inspection(identity)
    inspected["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    with pytest.raises(module.ImportedImageError, match="inspection"):
        module.verify_identity(
            identity=identity,
            inspected=inspected,
            archive_manifest_raw=_archive_manifest(identity),
            config_raw=config_raw,
        )


def test_archive_path_tag_and_layer_drift_fail_closed() -> None:
    module = _module()
    config_raw = _config()
    identity = module.manifest_identity(_core_manifest(config_raw), "core")
    record = json.loads(_archive_manifest(identity))[0]

    for key, value in (
        ("Config", "../escape.json"),
        ("RepoTags", [identity["source_tag"], "attacker/image:latest"]),
        ("Layers", ["../escape/layer.tar"]),
        ("Layers", [""]),
    ):
        changed = dict(record)
        changed[key] = value
        with pytest.raises(module.ImportedImageError):
            module.verify_identity(
                identity=identity,
                inspected=_inspection(identity),
                archive_manifest_raw=_json_bytes([changed]),
                config_raw=config_raw,
            )


def test_signed_platform_and_revision_drift_fail_closed() -> None:
    module = _module()
    original = json.loads(_config())
    for path, value in (
        (("architecture",), "arm64"),
        (("os",), "windows"),
        (("config", "Labels", "org.opencontainers.image.revision"), "0" * 40),
    ):
        changed = json.loads(_config())
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        changed_raw = _json_bytes(changed)
        identity = module.manifest_identity(_core_manifest(changed_raw), "core")
        with pytest.raises(module.ImportedImageError, match="config identity"):
            module.verify_identity(
                identity=identity,
                inspected=_inspection(identity),
                archive_manifest_raw=_archive_manifest(identity),
                config_raw=changed_raw,
            )
    assert original == json.loads(_config())


def test_live_verifier_uses_fixed_argv_without_a_shell_or_mutation() -> None:
    source = VERIFIER.read_text(encoding="utf-8")

    for required in (
        'subprocess.run(',
        'shell=False',
        '["docker", "image", "inspect", identity["source_tag"]]',
        '["docker", "image", "save", source_tag]',
    ):
        assert required in source
    for forbidden in (
        '"docker", "image", "load"',
        '"docker", "image", "tag"',
        '"docker", "compose"',
        'shell=True',
        'os.system(',
    ):
        assert forbidden not in source
