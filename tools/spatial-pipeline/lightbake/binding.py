"""Read-only access to an accepted content pack, and the layer's SHA-256 binding.

The accepted pack is never written. This module:

- snapshots the SHA-256 of every file in the pack directory (and its
  provenance sibling) so a run can prove the bytes are unchanged afterwards;
- computes the same binding the equipment registration uses
  (web-gateway/equipment-registration.mjs readActiveBinding):
  content_revision, registration_version, manifest_digest =
  sha256(canonicalJson(content-pack.json)), frame_digest and
  canonical_collision_digest, each verified against the bytes on disk;
- refuses an output directory inside the pack directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

DIGEST_ASSETS = ("frame.json", "collision.glb")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _js_number(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if not math.isfinite(v):
        raise TypeError("finite_json_required")
    if v == int(v) and abs(v) < 1e21:
        return str(int(v))
    s = repr(v)
    if "e" in s:  # JS: 1e-7, 1e+21 (no zero padding, explicit +)
        m, e = s.split("e")
        e = int(e)
        s = f"{m}e{'+' if e > 0 else '-'}{abs(e)}"
    return s


def canonical_json(value):
    """Python twin of HomeEquipmentRegistration.canonicalJson (sorted keys, no spaces)."""
    if value is None:
        return "null"
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(value[k])
                              for k in sorted(value)) + "}"
    raise TypeError("finite_json_required")


def canonical_digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot(pack_dir):
    """{relative path: sha256} of every file under the pack dir plus its .provenance.json sibling."""
    pack_dir = Path(pack_dir)
    out = {}
    for root, _, files in os.walk(pack_dir):
        for name in files:
            p = Path(root) / name
            out[p.relative_to(pack_dir.parent).as_posix()] = sha256_file(p)
    prov = pack_dir.parent / (pack_dir.name + ".provenance.json")
    if prov.exists():
        out[prov.name] = sha256_file(prov)
    return dict(sorted(out.items()))


def snapshot_digest(snap):
    return canonical_digest(snap)


def read_binding(pack_dir):
    """Binding of the accepted pack, verified against the bytes on disk."""
    pack_dir = Path(pack_dir)
    with open(pack_dir / "content-pack.json", "rb") as f:
        manifest = json.loads(f.read().decode("utf-8"))
    if manifest.get("revision") != pack_dir.name:
        raise ValueError("pack revision does not match its directory name")
    assets = manifest.get("assets") or {}
    verified = {}
    for name, a in assets.items():
        p = pack_dir / Path(*a["path"].split("/"))
        if not p.exists():
            continue
        h = sha256_file(p)
        if h != a["sha256"] or p.stat().st_size != a["bytes"]:
            raise ValueError(f"pack asset {name} does not match content-pack.json")
        verified[name] = {"sha256": h, "bytes": a["bytes"]}
    for name in DIGEST_ASSETS:
        if name not in verified:
            raise ValueError(f"pack asset {name} missing")
    binding = {
        "content_revision": manifest["revision"],
        "registration_version": manifest["registrationVersion"],
        "manifest_digest": canonical_digest(manifest),
        "frame_digest": verified["frame.json"]["sha256"],
        "canonical_collision_digest": verified["collision.glb"]["sha256"],
    }
    return binding, verified, manifest


def resolve_active_pack(site_dir):
    site_dir = Path(site_dir)
    rev = (site_dir / "ACTIVE").read_text(encoding="utf-8").strip()
    return site_dir / rev


def assert_outside(out_dir, pack_dir):
    out_dir, pack_dir = Path(out_dir).resolve(), Path(pack_dir).resolve()
    if out_dir == pack_dir or pack_dir in out_dir.parents:
        raise ValueError("refusing to write inside the accepted pack directory")


def default_layer_dir(pack_dir):
    pack_dir = Path(pack_dir)
    return pack_dir.parent / (pack_dir.name + ".lighting")


def verify_layer(layer_dir, pack_dir):
    """The layer's load rule (L4 mirrors it): binding, pack assets and layer files must all match.

    Returns the manifest; raises ValueError naming the first mismatch.
    """
    layer_dir = Path(layer_dir)
    with open(layer_dir / "lighting-manifest.json", "rb") as f:
        m = json.loads(f.read())
    if m.get("schema") != "home-lighting-layer-1" or m.get("provenance", {}).get("status") != "modeled":
        raise ValueError("lighting_layer_schema")
    binding, _, _ = read_binding(pack_dir)
    if m["binding"] != binding:
        raise ValueError("lighting_layer_binding_mismatch")
    for name, a in m["pack_assets"].items():
        if sha256_file(Path(pack_dir) / name) != a["sha256"]:
            raise ValueError(f"lighting_layer_pack_asset_mismatch:{name}")
    for name, a in m["files"].items():
        p = layer_dir / name
        if not p.exists() or p.stat().st_size != a["bytes"] or sha256_file(p) != a["sha256"]:
            raise ValueError(f"lighting_layer_file_mismatch:{name}")
    return m
