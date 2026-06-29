"""HTTP routers (/api/video-labeler/*). Each module exposes build_router(cfg);
handlers open a short-lived db connection per request (WAL makes this cheap
and keeps the API process honest as one of two writer processes)."""
