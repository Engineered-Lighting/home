"""Custom-label vocabulary CRUD: create (slug auto-derived from name), list
with live usage counts, edit, hide, merge, promote.

Semantics worth stating:
  * merge REWRITES current segment values (custom:<slug> -> custom:<into>)
    and is therefore a human content write: every affected video's revision
    is bumped so open editors rebase. The merged slug stays in the table
    (status='merged', merged_into set) but is refused in new segment writes.
  * promote only RECORDS the approved canonical mapping (canonical_mapping +
    mapping_approved + status='promoted'); rewriting segments to the
    canonical value happens at export, not here.
  * hide is a picker affordance -- existing segments keep the value and the
    slug stays writable.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import db, labels, ontology
from ..models import (CustomLabelCreate, CustomLabelMerge, CustomLabelPatch,
                      CustomLabelPromote)

log = logging.getLogger("videolabeler.api.custom_labels")

# a custom-axis label may promote into any canonical vocabulary; axis-bound
# labels promote within their own axis
_PROMOTE_TARGETS = {
    "activity": ontology.ACTIVITY_PRIMARY,
    "posture": ontology.POSTURE,
    "quality": ontology.QUALITY_FLAGS,
    "custom": ontology.ACTIVITY_PRIMARY + ontology.POSTURE + ontology.QUALITY_FLAGS,
}


def slugify(name: str) -> str:
    """lowercase, runs of non-[a-z0-9] -> '_', trimmed to the <=48-char slug
    shape that ontology.CUSTOM_VALUE_RE validates."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower())
    return s.strip("_-")[:48].strip("_-")


def _to_api(row, usage: int) -> dict:
    return {"slug": row["slug"], "axis": row["axis"], "name": row["name"],
            "description": row["description"], "color": row["color"],
            "status": row["status"], "merged_into": row["merged_into"],
            "canonical_mapping": row["canonical_mapping"],
            "mapping_approved": bool(row["mapping_approved"]),
            "usage_count": usage,
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    def _get(conn, slug: str):
        row = conn.execute("SELECT * FROM custom_labels WHERE slug = ?",
                           (slug,)).fetchone()
        if row is None:
            raise HTTPException(404, f"custom label {slug} not found")
        return row

    def _fresh(conn, slug: str) -> dict:
        counts = labels.custom_usage_counts(conn)
        return _to_api(_get(conn, slug), counts.get(slug, 0))

    @router.get("/custom-labels")
    def list_labels(q: Optional[str] = None, axis: Optional[str] = None):
        sql, where, args = "SELECT * FROM custom_labels", [], []
        if axis:
            where.append("axis = ?")
            args.append(axis)
        if q:
            where.append("(slug LIKE ? OR LOWER(name) LIKE ?)")
            args += [f"%{q.lower()}%"] * 2
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY slug"
        with db.open_db(cfg.db_path) as conn:
            counts = labels.custom_usage_counts(conn)
            rows = conn.execute(sql, args).fetchall()
            return {"custom_labels": [_to_api(r, counts.get(r["slug"], 0))
                                      for r in rows]}

    @router.post("/custom-labels")
    def create_label(body: CustomLabelCreate):
        if body.axis not in ontology.AXES:
            raise HTTPException(400, f"axis must be one of {ontology.AXES}")
        slug = slugify(body.name)
        if not ontology.is_custom_value(f"custom:{slug}"):
            raise HTTPException(400, f"name {body.name!r} does not yield a"
                                     " usable slug")
        now = db.iso_now()
        with db.open_db(cfg.db_path) as conn:
            try:
                with db.tx(conn):
                    conn.execute(
                        "INSERT INTO custom_labels (slug, axis, name,"
                        " description, color, status, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                        (slug, body.axis, body.name, body.description,
                         body.color, now, now))
            except sqlite3.IntegrityError:
                raise HTTPException(409, f"custom label {slug} already exists")
            return {"custom_label": _to_api(_get(conn, slug), 0)}

    @router.patch("/custom-labels/{slug}")
    def patch_label(slug: str, body: CustomLabelPatch):
        sets, args = [], []
        for col in ("name", "description", "color"):
            val = getattr(body, col)
            if val is not None:
                sets.append(f"{col} = ?")
                args.append(val)
        with db.open_db(cfg.db_path) as conn:
            _get(conn, slug)
            if sets:
                with db.tx(conn):
                    conn.execute(
                        f"UPDATE custom_labels SET {', '.join(sets)},"
                        " updated_at = ? WHERE slug = ?",
                        (*args, db.iso_now(), slug))
            return {"custom_label": _fresh(conn, slug)}

    @router.post("/custom-labels/{slug}/hide")
    def hide_label(slug: str):
        with db.open_db(cfg.db_path) as conn:
            row = _get(conn, slug)
            if row["status"] == "merged":
                raise HTTPException(409, f"custom label {slug} is merged")
            with db.tx(conn):
                conn.execute("UPDATE custom_labels SET status = 'hidden',"
                             " updated_at = ? WHERE slug = ?",
                             (db.iso_now(), slug))
            return {"custom_label": _fresh(conn, slug)}

    @router.post("/custom-labels/{slug}/merge")
    def merge_label(slug: str, body: CustomLabelMerge):
        if body.into_slug == slug:
            raise HTTPException(409, "cannot merge a label into itself")
        now = db.iso_now()
        with db.open_db(cfg.db_path) as conn:
            src = _get(conn, slug)
            dst = _get(conn, body.into_slug)
            if src["status"] == "merged":
                raise HTTPException(409, f"custom label {slug} is already merged")
            if dst["status"] == "merged":
                raise HTTPException(409, f"target {body.into_slug} is itself merged")
            with db.tx(conn):
                affected, rewritten = labels.rewrite_custom_value(
                    conn, slug, body.into_slug, now)
                for vid in sorted(affected):  # human content write -> rebase
                    labels.bump_revision(conn, vid, now)
                conn.execute("UPDATE custom_labels SET status = 'merged',"
                             " merged_into = ?, updated_at = ? WHERE slug = ?",
                             (body.into_slug, now, slug))
            log.info("merged custom label %s -> %s (%d segment(s) rewritten)",
                     slug, body.into_slug, rewritten)
            return {"custom_label": _fresh(conn, slug),
                    "segments_rewritten": rewritten}

    @router.post("/custom-labels/{slug}/promote")
    def promote_label(slug: str, body: CustomLabelPromote):
        with db.open_db(cfg.db_path) as conn:
            row = _get(conn, slug)
            if row["status"] == "merged":
                raise HTTPException(409, f"custom label {slug} is merged")
            if body.canonical_value not in _PROMOTE_TARGETS[row["axis"]]:
                raise HTTPException(400, f"'{body.canonical_value}' is not a"
                                         f" canonical value for axis {row['axis']}")
            with db.tx(conn):
                conn.execute("UPDATE custom_labels SET status = 'promoted',"
                             " canonical_mapping = ?, mapping_approved = 1,"
                             " updated_at = ? WHERE slug = ?",
                             (body.canonical_value, db.iso_now(), slug))
            return {"custom_label": _fresh(conn, slug)}

    return router
