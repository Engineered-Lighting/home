"""calibration_stats (sqlite-only): increments on explicit accept/reject of
vlm suggestions, the human-PUT-overlap implicit verdict (counted at most
once, value match vs mismatch), holdout exclusion, the Wilson lower bound
formula, and GET /calibration shapes + the bulk_ok gate."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from videolabeler import db, labels  # noqa: E402

VID = "vid_cal"


def _seed_video(conn, vid=VID, holdout=0):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, duration_s,"
            " is_holdout, import_status, created_at, updated_at)"
            " VALUES (?, ?, ?, 30.0, ?, 'ready', ?, ?)",
            (vid, f"{vid}.mp4", f"sum_{vid}", holdout, now, now))
        conn.execute("INSERT INTO video_label_state (video_id, revision,"
                     " updated_at) VALUES (?, 0, ?)", (vid, now))
    return vid


def _suggest(conn, vid, value="cooking", conf=0.83, axis="activity_primary",
             start=0.0, end=8.0):
    return labels.insert_suggestion_segments(conn, vid, axis, [
        {"start_s": start, "end_s": end, "value": value,
         "confidence": conf}])[0]


def _stats(conn):
    return [dict(r) for r in conn.execute(
        "SELECT axis, value, conf_decile, n_prelabeled, n_accepted,"
        " n_rejected FROM calibration_stats"
        " ORDER BY axis, value, conf_decile")]


def test_accept_of_vlm_suggestion_increments(conn):
    vid = _seed_video(conn)
    sid = _suggest(conn, vid, conf=0.83)
    labels.review_segment(conn, vid, sid, "accept")
    assert _stats(conn) == [{"axis": "activity", "value": "cooking",
                             "conf_decile": 8, "n_prelabeled": 1,
                             "n_accepted": 1, "n_rejected": 0}]


def test_reject_increments_and_decile_binning(conn):
    vid = _seed_video(conn)
    sid = _suggest(conn, vid, value="walking", conf=0.42)
    labels.review_segment(conn, vid, sid, "reject")
    rows = _stats(conn)
    assert rows == [{"axis": "activity", "value": "walking",
                     "conf_decile": 4, "n_prelabeled": 1,
                     "n_accepted": 0, "n_rejected": 1}]
    # confidence 1.0 lands in decile 9, not 10
    sid2 = _suggest(conn, vid, value="walking", conf=1.0, start=10.0, end=18.0)
    labels.review_segment(conn, vid, sid2, "accept")
    assert any(r["conf_decile"] == 9 for r in _stats(conn))
    assert labels.conf_decile(1.0) == 9
    assert labels.conf_decile(0.0) == 0
    assert labels.conf_decile(0.799) == 7


def test_second_review_does_not_double_count(conn):
    vid = _seed_video(conn)
    sid = _suggest(conn, vid)
    labels.review_segment(conn, vid, sid, "accept")
    labels.review_segment(conn, vid, sid, "reject")  # human changed their mind
    rows = _stats(conn)
    assert rows[0]["n_prelabeled"] == 1  # first verdict only
    assert rows[0]["n_accepted"] == 1 and rows[0]["n_rejected"] == 0


def test_non_vlm_and_holdout_do_not_count(conn):
    vid = _seed_video(conn)
    rule = labels.insert_suggestion_segments(
        conn, vid, "activity_primary",
        [{"start_s": 0.0, "end_s": 8.0, "value": "no_person",
          "confidence": 0.95}], source="rule")[0]
    labels.review_segment(conn, vid, rule, "accept")
    assert _stats(conn) == []  # rule suggestions never calibrate

    hold = _seed_video(conn, vid="vid_holdout", holdout=1)
    sid = _suggest(conn, hold)
    labels.review_segment(conn, hold, sid, "accept")
    assert _stats(conn) == []  # holdout never feeds calibration


def test_put_overlap_mismatch_counts_as_reject_once(conn):
    vid = _seed_video(conn)
    sid = _suggest(conn, vid, value="cooking", conf=0.83)
    rev, _ = labels.save_labels(conn, vid, 0, {"activity_primary": [
        {"start_s": 2.0, "end_s": 6.0, "value": "walking"}]})
    rows = _stats(conn)
    assert rows == [{"axis": "activity", "value": "cooking",
                     "conf_decile": 8, "n_prelabeled": 1,
                     "n_accepted": 0, "n_rejected": 1}]
    sg = conn.execute("SELECT * FROM segments WHERE id = ?", (sid,)).fetchone()
    assert sg["reviewed_by"] == "put_overlap"
    assert sg["review_state"] == "prelabel"  # stays in the suggestion layer
    assert sg["is_current"] == 1
    # a second overlapping PUT must not re-count the stamped suggestion
    labels.save_labels(conn, vid, rev, {"activity_primary": [
        {"start_s": 0.0, "end_s": 8.0, "value": "walking"}]})
    assert _stats(conn)[0]["n_prelabeled"] == 1


def test_put_overlap_value_match_counts_as_accept(conn):
    vid = _seed_video(conn)
    _suggest(conn, vid, value="cooking", conf=0.91)
    labels.save_labels(conn, vid, 0, {"activity_primary": [
        {"start_s": 0.0, "end_s": 8.0, "value": "cooking"}]})
    assert _stats(conn) == [{"axis": "activity", "value": "cooking",
                             "conf_decile": 9, "n_prelabeled": 1,
                             "n_accepted": 1, "n_rejected": 0}]


def test_put_without_overlap_does_not_count(conn):
    vid = _seed_video(conn)
    _suggest(conn, vid, value="cooking", conf=0.83, start=0.0, end=8.0)
    labels.save_labels(conn, vid, 0, {"activity_primary": [
        {"start_s": 8.0, "end_s": 12.0, "value": "walking"}]})  # touching only
    assert _stats(conn) == []


def test_wilson_lower_bound_formula():
    # lower = (p + z^2/2n - z*sqrt(p(1-p)/n + z^2/4n^2)) / (1 + z^2/n)
    assert labels.wilson_lower(0, 0) == 0.0
    assert labels.wilson_lower(95, 100) == pytest.approx(0.888248, abs=1e-5)
    assert labels.wilson_lower(200, 200) == pytest.approx(0.981152, abs=1e-5)
    assert labels.wilson_lower(0, 50) == pytest.approx(0.0, abs=1e-9)
    # monotone in n at fixed p
    assert labels.wilson_lower(19, 20) < labels.wilson_lower(190, 200)


def _seed_calibration(conn, axis, value, decile, acc, rej):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO calibration_stats (axis, value, conf_decile,"
            " n_prelabeled, n_accepted, n_rejected, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (axis, value, decile, acc + rej, acc, rej, now))


def test_calibration_doc_bulk_gate(conn):
    # sparse data: bulk stays locked
    _seed_calibration(conn, "activity", "cooking", 9, 5, 0)
    doc = labels.calibration_doc(conn, api_axis="activity_primary")
    ax = doc["axes"]["activity_primary"]
    assert ax["bulk_ok"] is False
    assert ax["top_deciles"]["n"] == 5
    assert ax["deciles"][9]["acceptance"] == 1.0
    assert ax["deciles"][0]["acceptance"] is None
    # overwhelming evidence in the top deciles unlocks it
    _seed_calibration(conn, "posture", "standing", 9, 200, 0)
    doc = labels.calibration_doc(conn)
    assert doc["axes"]["posture"]["bulk_ok"] is True
    assert doc["axes"]["posture"]["top_deciles"]["wilson_lower"] >= 0.95
    assert doc["axes"]["activity_primary"]["bulk_ok"] is False
    # low-decile evidence never affects the gate
    _seed_calibration(conn, "quality", json.dumps(["dark"]), 2, 500, 0)
    doc = labels.calibration_doc(conn, api_axis="quality")
    assert doc["axes"]["quality"]["bulk_ok"] is False
    with pytest.raises(ValueError, match="unknown axis"):
        labels.calibration_doc(conn, api_axis="bogus")


def test_calibration_endpoint_shapes(conn, cfg):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from videolabeler.api import prelabel as prelabel_api
    _seed_calibration(conn, "activity", "cooking", 8, 3, 1)
    app = FastAPI()
    app.include_router(prelabel_api.build_router(cfg))
    client = TestClient(app)

    r = client.get("/api/video-labeler/calibration")
    assert r.status_code == 200
    body = r.json()
    assert set(body["axes"]) == {"activity_primary", "posture", "quality",
                                 "custom"}
    assert body["wilson_min"] == 0.95

    r = client.get("/api/video-labeler/calibration",
                   params={"axis": "activity_primary"})
    ax = r.json()["axes"]["activity_primary"]
    assert len(ax["deciles"]) == 10
    assert ax["deciles"][8] == {
        "decile": 8, "conf_lo": 0.8, "conf_hi": 0.9, "n_prelabeled": 4,
        "n_accepted": 3, "n_rejected": 1, "acceptance": 0.75}
    assert ax["bulk_ok"] is False

    assert client.get("/api/video-labeler/calibration",
                      params={"axis": "bogus"}).status_code == 400
