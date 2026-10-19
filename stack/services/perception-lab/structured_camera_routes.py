"""Read the incumbent Qwen provider; this route starts no inference or session."""
import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from store import LabError

PREFIX = '/api/perception-lab/v1/cameras/den/structured-context'


def install_structured_camera_context(app, *, source):
    settings = app.state.store.settings
    if settings.site_id != 'victoria' or not any(c.get('id') == 'den' for c in settings.cameras):
        raise ValueError('fixed_victoria_den_context_required')
    if any(getattr(route, 'path', None) == PREFIX for route in app.routes):
        raise ValueError('one_structured_context_reader_required')

    @app.get(PREFIX)
    async def structured_context(request: Request):
        request.state.principal.require('read')
        if request.query_params:
            raise LabError('Structured context accepts no query', 422, 'invalid_query')
        reader = getattr(source, 'structured_context', None)
        if callable(reader):
            value = await asyncio.to_thread(reader)
        else:
            from structured_camera_context import missing_context
            value = missing_context(site_id='victoria', camera_id='den')
        if (not isinstance(value, dict) or value.get('schema') != 'home-camera-structured-context-1'
                or value.get('version') != 1 or value.get('site_id') != 'victoria'
                or value.get('camera_id') != 'den' or value.get('physical_commands') is not False
                or value.get('qualification') is not None
                or len(json.dumps(value, allow_nan=False).encode('utf8')) > 32768):
            raise LabError('Structured context unavailable', 503, 'structured_context_invalid')
        return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})
