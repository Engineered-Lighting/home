"""Guarded entry point for a commissioned continuous Den controller.

The loop stays stopped until the owner explicitly resumes it. Reject legacy
configuration before the application's lifespan can start its background work.
"""


def create_app():
    from managed_detector_app import create_managed_refresh_app
    app = create_managed_refresh_app()
    loop = getattr(app.state, 'activity_loop', None)
    if loop is None:
        raise RuntimeError('continuous_activity_loop_required')
    loop.stop('operator_stop')
    cleanup = loop.deep.snapshot().get('media_cleanup') or {}
    if cleanup.get('enabled') is not True or cleanup.get('ok') is not True:
        raise RuntimeError('continuous_media_cleanup_required')
    return app
