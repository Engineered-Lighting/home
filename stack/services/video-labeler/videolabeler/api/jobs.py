"""Job introspection + cancel/retry."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import db
from ..jobqueue import queue as q
from ..models import Job

log = logging.getLogger("videolabeler.api.jobs")


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    @router.get("/jobs")
    def list_jobs(state: Optional[str] = None, type: Optional[str] = None):
        with db.open_db(cfg.db_path) as conn:
            rows = q.list_jobs(conn, state=state, type=type, limit=200)
            return {"jobs": [Job.from_row(r).model_dump(mode="json") for r in rows]}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        with db.open_db(cfg.db_path) as conn:
            row = q.get_job(conn, job_id)
            if row is None:
                raise HTTPException(404, f"job {job_id} not found")
            return {"job": Job.from_row(row).model_dump(mode="json")}

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        with db.open_db(cfg.db_path) as conn:
            try:
                row = q.cancel(conn, job_id)
            except KeyError:
                raise HTTPException(404, f"job {job_id} not found")
            except ValueError as e:
                raise HTTPException(409, str(e))
            return {"job": Job.from_row(row).model_dump(mode="json")}

    @router.post("/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        with db.open_db(cfg.db_path) as conn:
            try:
                row = q.retry(conn, job_id)
            except KeyError:
                raise HTTPException(404, f"job {job_id} not found")
            except ValueError as e:
                raise HTTPException(409, str(e))
            return {"job": Job.from_row(row).model_dump(mode="json")}

    return router
