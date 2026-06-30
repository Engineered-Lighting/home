#!/usr/bin/env python3
"""Create a smaller mobile-friendly Apartment Gaussian-splat PLY.

The full `apartment.ply` is intentionally kept as runtime data, not Git data.
This tool keeps the same binary PLY property layout and writes a deterministic
stride sample to `apartment.mobile.ply` so phone browsers do not need to fetch
and parse the full scan before photo mode becomes usable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


PLY_TYPES = {
    "char": 1,
    "uchar": 1,
    "int8": 1,
    "uint8": 1,
    "short": 2,
    "ushort": 2,
    "int16": 2,
    "uint16": 2,
    "int": 4,
    "uint": 4,
    "int32": 4,
    "uint32": 4,
    "float": 4,
    "float32": 4,
    "double": 8,
    "float64": 8,
}


def sha8(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def parse_header(blob: bytes) -> tuple[str, int, int, int]:
    marker = b"end_header\n"
    end = blob.find(marker)
    if end < 0:
        raise SystemExit("source PLY is missing end_header")
    header_end = end + len(marker)
    header = blob[:header_end].decode("ascii")
    vertex_count = None
    record_size = 0
    in_vertex = False
    for line in header.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[:2] == ["element", "vertex"]:
            vertex_count = int(parts[2])
            in_vertex = True
            continue
        if len(parts) >= 2 and parts[0] == "element" and parts[1] != "vertex":
            in_vertex = False
        if in_vertex and len(parts) >= 3 and parts[0] == "property":
            if parts[1] == "list":
                raise SystemExit("list properties are not supported for splat PLY downsampling")
            try:
                record_size += PLY_TYPES[parts[1]]
            except KeyError as exc:
                raise SystemExit(f"unsupported PLY property type: {parts[1]}") from exc
    if not vertex_count or not record_size:
        raise SystemExit("could not infer vertex count or record size")
    return header, header_end, vertex_count, record_size


def rewrite_vertex_count(header: str, count: int) -> bytes:
    lines = []
    replaced = False
    for line in header.splitlines():
        if line.startswith("element vertex "):
            lines.append(f"element vertex {count}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        raise SystemExit("could not rewrite vertex count")
    return ("\n".join(lines) + "\n").encode("ascii")


def update_manifest(asset_dir: Path, output: Path, source: Path, count: int, source_count: int) -> None:
    path = asset_dir / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {"version": 1, "files": {}}
    manifest.setdefault("files", {})[output.name] = {
        "sha8": sha8(output),
        "bytes": output.stat().st_size,
        "splats": count,
        "source": source.name,
        "source_splats": source_count,
    }
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    default_dir = repo / "app" / "data" / "apartment"
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-dir", type=Path, default=default_dir)
    parser.add_argument("--source", default="apartment.ply")
    parser.add_argument("--output", default="apartment.mobile.ply")
    parser.add_argument("--target-splats", type=int, default=120_000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    asset_dir = args.asset_dir.resolve()
    source = asset_dir / args.source
    output = asset_dir / args.output
    if not source.is_file():
        raise SystemExit(f"missing source: {source}")
    if output.exists() and not args.force and output.stat().st_mtime >= source.stat().st_mtime:
        if not args.quiet:
            print(f"ok {output} already current ({output.stat().st_size} bytes)")
        return 0

    blob = source.read_bytes()
    header, header_end, total, record_size = parse_header(blob)
    body = memoryview(blob)[header_end:]
    expected = total * record_size
    if len(body) < expected:
        raise SystemExit(f"source body too short: expected {expected}, got {len(body)}")
    stride = max(1, math.ceil(total / max(1, args.target_splats)))
    selected = list(range(0, total, stride))
    out_header = rewrite_vertex_count(header, len(selected))
    with output.open("wb") as f:
        f.write(out_header)
        for idx in selected:
            start = idx * record_size
            f.write(body[start:start + record_size])
    update_manifest(asset_dir, output, source, len(selected), total)
    if not args.quiet:
        print(
            f"wrote {output} ({len(selected)} of {total} splats, "
            f"stride {stride}, {output.stat().st_size} bytes)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
