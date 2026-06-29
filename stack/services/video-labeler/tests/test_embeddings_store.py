"""fp16 .npy shard store (stdlib-only): write/read round-trip, shard
rolling, numbering continuation, ref-based reads, vector math, and (when
numpy happens to be installed) np.load bit-compatibility."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler.embeddings import store  # noqa: E402

MODEL_ID = "vjepa2_1_vit_base_384@personcrop_v1"


def test_npy_round_trip_exact_for_fp16_representable(tmp_path):
    rows = [[0.5, -1.25, 2.0, 0.0], [1.0, -0.0625, 4096.0, -2.5]]
    p = str(tmp_path / "a.npy")
    store.write_npy_fp16(p, rows)
    assert store.read_npy_fp16(p) == rows


def test_npy_quantizes_and_clamps(tmp_path):
    p = str(tmp_path / "b.npy")
    store.write_npy_fp16(p, [[0.1, 1e9, -1e9]])
    out = store.read_npy_fp16(p)[0]
    assert out[0] == pytest.approx(0.1, rel=1e-3)  # fp16 precision
    assert out[1] == pytest.approx(65504.0)        # clamped to fp16 max
    assert out[2] == pytest.approx(-65504.0)


def test_npy_rejects_empty_and_ragged(tmp_path):
    with pytest.raises(ValueError):
        store.write_npy_fp16(str(tmp_path / "c.npy"), [])
    with pytest.raises(ValueError):
        store.write_npy_fp16(str(tmp_path / "d.npy"), [[1.0, 2.0], [3.0]])


def test_npy_numpy_compatible(tmp_path):
    np = pytest.importorskip("numpy")
    p = str(tmp_path / "e.npy")
    store.write_npy_fp16(p, [[0.5, -1.25], [2.0, 3.75]])
    arr = np.load(p)
    assert arr.dtype == np.float16
    assert arr.tolist() == [[0.5, -1.25], [2.0, 3.75]]


def test_shard_writer_rolls_and_reads_back(tmp_path):
    w = store.ShardWriter(str(tmp_path), MODEL_ID, max_rows=2)
    refs = [w.add([float(i), float(-i)]) for i in range(5)]
    w.flush()
    # 5 rows at max 2/shard -> 3 shards; rel paths are DATA_DIR-relative
    slug = store.model_slug(MODEL_ID)
    assert [r[0] for r in refs] == [
        f"embeddings/{slug}/shard_00000.npy", f"embeddings/{slug}/shard_00000.npy",
        f"embeddings/{slug}/shard_00001.npy", f"embeddings/{slug}/shard_00001.npy",
        f"embeddings/{slug}/shard_00002.npy"]
    assert [r[1] for r in refs] == [0, 1, 0, 1, 0]
    vecs = store.read_rows(str(tmp_path), refs)
    assert vecs == [[float(i), float(-i)] for i in range(5)]


def test_shard_writer_continues_numbering(tmp_path):
    w1 = store.ShardWriter(str(tmp_path), MODEL_ID, max_rows=4)
    w1.add([1.0])
    w1.flush()
    w2 = store.ShardWriter(str(tmp_path), MODEL_ID, max_rows=4)
    rel, idx = w2.add([2.0])
    assert rel.endswith("shard_00001.npy") and idx == 0
    w2.flush()
    slug = store.model_slug(MODEL_ID)
    files = sorted(os.listdir(tmp_path / "embeddings" / slug))
    assert files == ["shard_00000.npy", "shard_00001.npy"]


def test_flush_empty_is_noop(tmp_path):
    w = store.ShardWriter(str(tmp_path), MODEL_ID)
    assert w.flush() is None
    assert w.pending_rows == 0


def test_vector_math():
    assert store.l2_normalize([3.0, 4.0]) == [0.6, 0.8]
    assert store.l2_normalize([0.0, 0.0]) == [0.0, 0.0]  # no div-by-zero
    assert store.mean_vector([[1.0, 2.0], [3.0, 4.0]]) == [2.0, 3.0]
    a = store.l2_normalize([1.0, 0.0])
    assert store.cosine_distance(a, a) == pytest.approx(0.0)
    assert store.cosine_distance(a, store.l2_normalize([0.0, 1.0])) == \
        pytest.approx(1.0)
    assert store.cosine_distance(a, store.l2_normalize([-1.0, 0.0])) == \
        pytest.approx(2.0)
