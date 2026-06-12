"""Custom-label lifecycle: create with auto slug, duplicate 409, list
filters, live usage counts, merge (rewrites current segment values + bumps
affected video revisions), hide, promote (mapping recorded, segments
untouched until export). Needs httpx (starlette TestClient)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from videolabeler import db  # noqa: E402
from videolabeler.api import custom_labels as custom_labels_api  # noqa: E402
from videolabeler.api import labels as labels_api  # noqa: E402

VID = "vid_cl"
BASE = "/api/video-labeler/custom-labels"
LABELS_URL = f"/api/video-labeler/videos/{VID}/labels"


@pytest.fixture
def client(conn, cfg):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, duration_s,"
            " import_status, created_at, updated_at)"
            " VALUES (?, 'cl.mp4', 'sum_cl', 30.0, 'ready', ?, ?)",
            (VID, now, now))
        conn.execute("INSERT INTO video_label_state (video_id, revision,"
                     " updated_at) VALUES (?, 0, ?)", (VID, now))
    app = FastAPI()
    app.include_router(labels_api.build_router(cfg))
    app.include_router(custom_labels_api.build_router(cfg))
    return TestClient(app)


def test_create_auto_slug_and_duplicate_409(client):
    r = client.post(BASE, json={"axis": "custom", "name": "Folding Laundry!",
                                "color": "#aabbcc"})
    assert r.status_code == 200, r.text
    lab = r.json()["custom_label"]
    assert lab["slug"] == "folding_laundry"
    assert lab["status"] == "active"
    assert lab["usage_count"] == 0
    assert lab["color"] == "#aabbcc"
    # different spelling, same slug -> conflict
    assert client.post(BASE, json={"axis": "custom",
                                   "name": "folding   laundry"}).status_code == 409


def test_create_rejects_unusable_input(client):
    assert client.post(BASE, json={"axis": "custom",
                                   "name": "???"}).status_code == 400
    assert client.post(BASE, json={"axis": "bogus",
                                   "name": "ok name"}).status_code == 400


def test_list_filters(client):
    client.post(BASE, json={"axis": "custom", "name": "Folding Laundry"})
    client.post(BASE, json={"axis": "activity", "name": "Watering Plants"})
    all_slugs = {c["slug"] for c in client.get(BASE).json()["custom_labels"]}
    assert all_slugs == {"folding_laundry", "watering_plants"}
    by_axis = client.get(BASE, params={"axis": "activity"}).json()["custom_labels"]
    assert [c["slug"] for c in by_axis] == ["watering_plants"]
    by_q = client.get(BASE, params={"q": "fold"}).json()["custom_labels"]
    assert [c["slug"] for c in by_q] == ["folding_laundry"]


def test_patch_updates_fields(client):
    client.post(BASE, json={"axis": "custom", "name": "Folding Laundry"})
    r = client.patch(f"{BASE}/folding_laundry",
                     json={"description": "towels etc", "color": "#112233"})
    lab = r.json()["custom_label"]
    assert lab["description"] == "towels etc" and lab["color"] == "#112233"
    assert lab["slug"] == "folding_laundry"  # slug is identity: rename-safe
    assert client.patch(f"{BASE}/nope", json={"name": "x"}).status_code == 404


def test_hide(client):
    client.post(BASE, json={"axis": "custom", "name": "Folding Laundry"})
    r = client.post(f"{BASE}/folding_laundry/hide")
    assert r.json()["custom_label"]["status"] == "hidden"


def test_usage_counts_live_over_current_segments(client):
    client.post(BASE, json={"axis": "custom", "name": "Folding Laundry"})
    client.post(BASE, json={"axis": "quality", "name": "Lens Smudge"})
    r = client.put(LABELS_URL, json={"revision": 0, "axes": {
        "custom": [
            {"start_s": 0, "end_s": 5, "value": "custom:folding_laundry"},
            {"start_s": 5, "end_s": 10, "value": "custom:folding_laundry"},
        ],
        "quality": [{"start_s": 0, "end_s": 5,
                     "value": ["dark", "custom:lens_smudge"]}],
    }})
    assert r.status_code == 200, r.text
    by_slug = {c["slug"]: c for c in client.get(BASE).json()["custom_labels"]}
    assert by_slug["folding_laundry"]["usage_count"] == 2
    assert by_slug["lens_smudge"]["usage_count"] == 1
    onto = client.get("/api/video-labeler/labels/ontology").json()
    assert {c["slug"]: c["usage_count"] for c in onto["custom"]} == \
        {"folding_laundry": 2, "lens_smudge": 1}


def test_merge_rewrites_segments_and_bumps_revision(client):
    client.post(BASE, json={"axis": "custom", "name": "Folding Laundry"})
    client.post(BASE, json={"axis": "custom", "name": "Laundry"})
    client.post(BASE, json={"axis": "quality", "name": "Lens Smudge"})
    client.post(BASE, json={"axis": "quality", "name": "Dirty Lens"})
    assert client.put(LABELS_URL, json={"revision": 0, "axes": {
        "custom": [{"start_s": 0, "end_s": 5,
                    "value": "custom:folding_laundry"}],
        "quality": [{"start_s": 0, "end_s": 5,
                     "value": ["custom:lens_smudge", "custom:dirty_lens"]}],
    }}).status_code == 200

    r = client.post(f"{BASE}/folding_laundry/merge", json={"into_slug": "laundry"})
    assert r.status_code == 200, r.text
    lab = r.json()["custom_label"]
    assert lab["status"] == "merged" and lab["merged_into"] == "laundry"
    assert r.json()["segments_rewritten"] == 1
    doc = client.get(LABELS_URL).json()
    assert doc["axes"]["custom"][0]["value"] == "custom:laundry"
    assert doc["revision"] == 2  # merge is a human content write -> bumped

    # the merged slug is refused in new segment writes
    bad = client.put(LABELS_URL, json={"revision": 2, "axes": {
        "custom": [{"start_s": 6, "end_s": 8,
                    "value": "custom:folding_laundry"}]}})
    assert bad.status_code == 400

    # quality arrays rewrite element-wise and dedupe when both were present
    r2 = client.post(f"{BASE}/dirty_lens/merge", json={"into_slug": "lens_smudge"})
    assert r2.status_code == 200
    doc = client.get(LABELS_URL).json()
    assert doc["axes"]["quality"][0]["value"] == ["custom:lens_smudge"]

    # usage moved to the merge target
    by_slug = {c["slug"]: c for c in client.get(BASE).json()["custom_labels"]}
    assert by_slug["folding_laundry"]["usage_count"] == 0
    assert by_slug["laundry"]["usage_count"] == 1
    assert by_slug["lens_smudge"]["usage_count"] == 1


def test_merge_guards(client):
    client.post(BASE, json={"axis": "custom", "name": "aa"})
    client.post(BASE, json={"axis": "custom", "name": "bb"})
    assert client.post(f"{BASE}/aa/merge",
                       json={"into_slug": "aa"}).status_code == 409
    assert client.post(f"{BASE}/aa/merge",
                       json={"into_slug": "nope"}).status_code == 404
    assert client.post(f"{BASE}/nope/merge",
                       json={"into_slug": "aa"}).status_code == 404
    assert client.post(f"{BASE}/aa/merge",
                       json={"into_slug": "bb"}).status_code == 200
    assert client.post(f"{BASE}/aa/merge",
                       json={"into_slug": "bb"}).status_code == 409  # already merged
    assert client.post(f"{BASE}/bb/merge",
                       json={"into_slug": "aa"}).status_code == 409  # dead target


def test_promote_records_mapping_without_rewriting(client):
    client.post(BASE, json={"axis": "custom", "name": "Folding Laundry"})
    assert client.put(LABELS_URL, json={"revision": 0, "axes": {
        "custom": [{"start_s": 0, "end_s": 5,
                    "value": "custom:folding_laundry"}]}}).status_code == 200
    assert client.post(f"{BASE}/folding_laundry/promote",
                       json={"canonical_value": "not_a_value"}).status_code == 400
    r = client.post(f"{BASE}/folding_laundry/promote",
                    json={"canonical_value": "cleaning"})
    assert r.status_code == 200, r.text
    lab = r.json()["custom_label"]
    assert lab["status"] == "promoted"
    assert lab["canonical_mapping"] == "cleaning"
    assert lab["mapping_approved"] is True
    # segments are rewritten at EXPORT, not here
    doc = client.get(LABELS_URL).json()
    assert doc["axes"]["custom"][0]["value"] == "custom:folding_laundry"

