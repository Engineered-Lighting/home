"""fp16 .npy shard store for pooled embedding vectors + tiny vector math.

The .npy v1 format is written/read with stdlib struct ('e' = IEEE-754 half,
since py3.6) so the API process and the Windows test venv never need numpy
or torch -- yet every shard is bit-compatible with np.load on the GPU box.
Corpus scale (~800 windows x 4 rows x 768 dims) makes pure-python unpack and
cosine math comfortably fast; nothing here is on a hot path.

Layout: {DATA_DIR}/embeddings/<model_id slug>/shard_<n>.npy, <= max_rows
rows each (plan caps 2048). sqlite embeddings rows point at
(shard_path, row_index); rows are only INSERTed after their shard file is on
disk, so a crash can orphan shard bytes but never dangle a db pointer.
"""
from __future__ import annotations

import ast
import math
import os
import re
import struct

SHARD_MAX_ROWS = 2048
_HALF_MAX = 65504.0  # fp16 finite range; pooled ViT features sit far inside
_SHARD_RE = re.compile(r"^shard_(\d+)\.npy$")


def model_slug(model_id: str) -> str:
    """model_id -> filesystem-safe dir name ('@' and friends -> '_')."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_id)


# ------------------------------------------------------- npy v1 read/write

def _npy_header(n_rows: int, dim: int) -> bytes:
    head = ("{'descr': '<f2', 'fortran_order': False,"
            f" 'shape': ({n_rows}, {dim}), }}")
    pad = (64 - (10 + len(head) + 1) % 64) % 64  # total header % 64 == 0
    head = head + " " * pad + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(head)) + head.encode("latin1")


def write_npy_fp16(path: str, rows: list) -> None:
    """rows: list of equal-length float lists -> .npy ('<f2'), atomic
    .part + rename (a crash never leaves a half-written shard)."""
    if not rows:
        raise ValueError("refusing to write an empty shard")
    dim = len(rows[0])
    flat = []
    for r in rows:
        if len(r) != dim:
            raise ValueError(f"ragged shard row: {len(r)} != {dim}")
        for v in r:
            v = float(v)
            if v != v:  # NaN passes through; +/-inf would overflow pack('e')
                flat.append(v)
            else:
                flat.append(min(max(v, -_HALF_MAX), _HALF_MAX))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    with open(part, "wb") as f:
        f.write(_npy_header(len(rows), dim))
        f.write(struct.pack(f"<{len(flat)}e", *flat))
    os.replace(part, path)


def read_npy_fp16(path: str) -> list:
    """.npy ('<f2', C-order, 2-D) -> list of float lists."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:6] != b"\x93NUMPY":
        raise ValueError(f"not an .npy file: {path}")
    major = data[6]
    if major == 1:
        hlen, off = struct.unpack("<H", data[8:10])[0], 10
    else:  # v2/v3 use a 4-byte header length
        hlen, off = struct.unpack("<I", data[8:12])[0], 12
    header = ast.literal_eval(data[off:off + hlen].decode("latin1"))
    if header.get("descr") != "<f2" or header.get("fortran_order"):
        raise ValueError(f"unsupported npy layout {header} in {path}")
    n, dim = header["shape"]
    vals = struct.unpack(f"<{n * dim}e", data[off + hlen:off + hlen + n * dim * 2])
    return [list(vals[r * dim:(r + 1) * dim]) for r in range(n)]


# ------------------------------------------------------------ shard writer

class ShardWriter:
    """Appends vectors into <= max_rows .npy shards under
    {data_dir}/embeddings/<slug>/. add() returns the (DATA_DIR-relative
    forward-slash path, row_index) the sqlite row must point at -- the path
    is deterministic before the file exists; callers insert db rows only
    AFTER flush(). Numbering continues past existing shards, so a resumed
    job never overwrites a previous run's files."""

    def __init__(self, data_dir: str, model_id: str,
                 max_rows: int = SHARD_MAX_ROWS):
        self.data_dir = data_dir
        self.max_rows = max_rows
        slug = model_slug(model_id)
        self.rel_dir = f"embeddings/{slug}"
        self.abs_dir = os.path.join(data_dir, "embeddings", slug)
        os.makedirs(self.abs_dir, exist_ok=True)
        taken = [int(m.group(1)) for fn in os.listdir(self.abs_dir)
                 if (m := _SHARD_RE.match(fn))]
        self.shard_idx = max(taken) + 1 if taken else 0
        self._buf: list = []

    @property
    def rel_path(self) -> str:
        return f"{self.rel_dir}/shard_{self.shard_idx:05d}.npy"

    @property
    def pending_rows(self) -> int:
        return len(self._buf)

    def add(self, vector) -> tuple:
        """Buffer one vector; returns (rel_shard_path, row_index)."""
        if len(self._buf) >= self.max_rows:
            self.flush()
        self._buf.append([float(v) for v in vector])
        return self.rel_path, len(self._buf) - 1

    def flush(self):
        """Write the buffered rows (if any) and advance to the next shard.
        Returns the flushed shard's relative path, or None."""
        if not self._buf:
            return None
        rel = self.rel_path
        write_npy_fp16(os.path.join(self.abs_dir, f"shard_{self.shard_idx:05d}.npy"),
                       self._buf)
        self.shard_idx += 1
        self._buf = []
        return rel


def read_rows(data_dir: str, refs: list) -> list:
    """refs: [(rel_shard_path, row_index)] -> vectors, loading each shard
    once per call (no global cache -- shards are small and requests rare)."""
    cache: dict = {}
    out = []
    for rel, idx in refs:
        if rel not in cache:
            cache[rel] = read_npy_fp16(os.path.join(data_dir, *rel.split("/")))
        out.append(cache[rel][idx])
    return out


# ------------------------------------------------------------ vector math

def l2_normalize(vec: list) -> list:
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n for v in vec] if n > 0 else list(vec)


def mean_vector(vecs: list) -> list:
    return [sum(col) / len(vecs) for col in zip(*vecs)]


def dot(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))


def cosine_distance(a_unit: list, b_unit: list) -> float:
    """1 - cosine similarity for ALREADY L2-normalized vectors."""
    return 1.0 - dot(a_unit, b_unit)
