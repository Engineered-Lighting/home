"""Explicit Uvicorn factory for a reviewed, operator-stopped Den deployment.

The service owns the working directory and --app-dir; this helper accepts no
request-selected module or path. It stops the activity loop before the ASGI
lifespan can start its thread. Resume still requires the existing record grant.
"""
import os
import sys


def create_app():
    sys.path.insert(0, os.getcwd())
    from managed_detector_app import create_managed_refresh_app
    app = create_managed_refresh_app()
    loop = getattr(app.state, 'activity_loop', None)
    if loop is None:
        raise RuntimeError('required_activity_loop_missing')
    loop.stop('operator_stop')
    return app
