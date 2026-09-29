"""Typed, read-only Frigate routes for the Home People tab.

The previous `frigate_proxy?path=` view forwarded any path that merely started
with `api/faces` or `api/events`, so `api/events/../config` reached Frigate's
configuration, which carries camera credentials. These handlers replace it with
four exact operations. Each one validates every input against a strict
character class, and names against the live face library. It builds the Frigate
path itself, never follows a redirect, and returns only Frigate's JSON or raster
image bytes. Nothing a handler returns names Frigate's URL or origin.

  GET frigate_proxy/events?person=<name>&limit=<1..200>
      -> api/events?sub_labels=<name>&limit=<n>&include_thumbnails=0
  GET frigate_proxy/events/<event_id>/thumbnail.jpg
      -> api/events/<event_id>/thumbnail.jpg
  GET frigate_proxy/faces
      -> api/faces, without the `train` bucket
  GET frigate_proxy/faces/<name>/<file>
      -> clips/faces/<name>/<file>

The view classes live in `__init__.py` (CORS-enabled, `requires_auth`, and
admin-guarded like every other legacy private view) and delegate here. This
module imports nothing from Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote, urlencode

from aiohttp import web

from .frigate_sync import base_url as _frigate_base_url

URL_PREFIX = "/api/extended_openai_conversation/frigate_proxy"
EVENTS_URL = f"{URL_PREFIX}/events"
THUMBNAIL_URL = f"{URL_PREFIX}/events/{{event_id}}/thumbnail.jpg"
FACES_URL = f"{URL_PREFIX}/faces"
FACE_FILE_URL = f"{URL_PREFIX}/faces/{{name}}/{{file}}"

PERSON_NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,64}")
EVENT_ID = re.compile(r"[0-9]{9,11}\.[0-9]{1,6}-[a-z0-9]{4,12}")
FACE_FILE = re.compile(r"[A-Za-z0-9_.\-]{1,128}\.(?:webp|jpg|jpeg|png)")
LIMIT = re.compile(r"[0-9]{1,3}")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Frigate's unclassified crops. They are strangers' faces, not anyone's
# enrolment, so they are neither listed nor servable.
TRAIN_BUCKET = "train"

# Raster formats only. SVG is an image type that can carry script, and it
# would run on Home Assistant's origin if someone opened the URL directly.
IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

UPSTREAM_TIMEOUT_S = 5.0
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
FACE_LIBRARY_TTL_S = 30.0

# Encodings that could turn into a separator or a dot after decoding. A client
# of this contract never needs them. Names travel with spaces as %20 only.
_RAW_PATH_FORBIDDEN = ("%2f", "%5c", "%2e", "%00", "\\", "..", "//")

_JSON_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_IMAGE_HEADERS = {
    "Cache-Control": "private, max-age=300",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}

ClientFactory = Callable[..., Any]


class ProxyRejected(Exception):
    """A request or upstream answer outside the contract."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _error(status: int, code: str) -> web.Response:
    return web.Response(
        status=status,
        body=json.dumps({"error": code}).encode("utf-8"),
        content_type="application/json",
        headers=dict(_JSON_HEADERS),
    )


def _default_client_factory(**kwargs: Any):
    import httpx

    return httpx.AsyncClient(**kwargs)


_client_factory: ClientFactory = _default_client_factory
_face_library_cache: dict[str, Any] = {"at": None, "library": None}


def reset_face_library_cache() -> None:
    _face_library_cache["at"] = None
    _face_library_cache["library"] = None


# -- input validation -------------------------------------------------------


def _require_clean_raw_path(request: web.Request) -> None:
    raw = str(getattr(request, "raw_path", "") or "")
    path_only = raw.split("?", 1)[0].lower()
    if not path_only.startswith(URL_PREFIX + "/"):
        raise ProxyRejected(404, "not_found")
    if any(token in path_only[len(URL_PREFIX):] for token in _RAW_PATH_FORBIDDEN):
        raise ProxyRejected(400, "invalid_path")


def _require_no_query(request: web.Request) -> None:
    if getattr(request, "query_string", ""):
        raise ProxyRejected(400, "unexpected_query")


def validate_person_name(raw: object) -> str:
    if (
        not isinstance(raw, str)
        or PERSON_NAME.fullmatch(raw) is None
        or ".." in raw
        or raw.strip(" .") == ""
        or raw == TRAIN_BUCKET
    ):
        raise ProxyRejected(400, "invalid_person")
    return raw


def validate_event_id(raw: object) -> str:
    if not isinstance(raw, str) or EVENT_ID.fullmatch(raw) is None:
        raise ProxyRejected(400, "invalid_event_id")
    return raw


def validate_face_file(raw: object) -> str:
    if (
        not isinstance(raw, str)
        or FACE_FILE.fullmatch(raw) is None
        or ".." in raw
        or raw.startswith(".")
    ):
        raise ProxyRejected(400, "invalid_file")
    return raw


def parse_limit(query: Mapping[str, str]) -> int:
    if "limit" not in query:
        return DEFAULT_LIMIT
    raw = query["limit"]
    if not isinstance(raw, str) or LIMIT.fullmatch(raw) is None:
        raise ProxyRejected(400, "invalid_limit")
    value = int(raw)
    if not 1 <= value <= MAX_LIMIT:
        raise ProxyRejected(400, "invalid_limit")
    return value


def _events_query(request: web.Request) -> tuple[str, int]:
    query = getattr(request, "query", {}) or {}
    keys = list(query.keys())
    if set(keys) - {"person", "limit"} or len(keys) != len(set(keys)):
        raise ProxyRejected(400, "unexpected_query")
    if "person" not in query:
        raise ProxyRejected(400, "invalid_person")
    return validate_person_name(query["person"]), parse_limit(query)


# -- upstream ---------------------------------------------------------------


def _media_type(headers: Mapping[str, str]) -> str:
    value = headers.get("content-type") or headers.get("Content-Type") or ""
    return value.split(";", 1)[0].strip().lower()


async def _fetch(path: str, *, max_bytes: int) -> tuple[str, bytes]:
    """GET `path` (already built and escaped here) from Frigate.

    Returns (media type, body) for a 200. Anything else becomes a fixed error
    code; neither the upstream URL nor its error text is ever surfaced.
    """

    base = (_frigate_base_url() or "").rstrip("/")
    if not base:
        raise ProxyRejected(503, "frigate_not_configured")
    try:
        async with _client_factory(
            timeout=UPSTREAM_TIMEOUT_S, follow_redirects=False
        ) as client:
            response = await client.get(f"{base}/{path}")
    except Exception as error:  # noqa: BLE001 - any transport failure
        raise ProxyRejected(502, "frigate_unreachable") from error
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 404:
        raise ProxyRejected(404, "not_found")
    if status != 200:
        raise ProxyRejected(502, "frigate_error")
    body = bytes(getattr(response, "content", b"") or b"")
    if len(body) > max_bytes:
        raise ProxyRejected(502, "frigate_response_too_large")
    return _media_type(getattr(response, "headers", {}) or {}), body


def _sanitize_face_library(payload: object) -> dict[str, list[str]]:
    if not isinstance(payload, dict) or len(payload) > 1024:
        raise ProxyRejected(502, "frigate_invalid_response")
    library: dict[str, list[str]] = {}
    for name, files in payload.items():
        if not isinstance(name, str) or not isinstance(files, list):
            raise ProxyRejected(502, "frigate_invalid_response")
        if name == TRAIN_BUCKET:
            continue
        if not all(isinstance(item, str) for item in files):
            raise ProxyRejected(502, "frigate_invalid_response")
        library[name] = list(files)
    return library


async def _face_library(*, fresh: bool = False) -> dict[str, list[str]]:
    now = time.monotonic()
    cached_at = _face_library_cache["at"]
    if (
        not fresh
        and cached_at is not None
        and now - cached_at < FACE_LIBRARY_TTL_S
        and _face_library_cache["library"] is not None
    ):
        return _face_library_cache["library"]
    _media, body = await _fetch("api/faces", max_bytes=MAX_JSON_BYTES)
    try:
        payload = json.loads(body)
    except ValueError as error:
        raise ProxyRejected(502, "frigate_invalid_response") from error
    library = _sanitize_face_library(payload)
    _face_library_cache["at"] = now
    _face_library_cache["library"] = library
    return library


async def _require_known_name(name: str) -> None:
    if name in await _face_library():
        return
    # A name enrolled in the last few seconds is not a reason to refuse.
    if name not in await _face_library(fresh=True):
        raise ProxyRejected(400, "unknown_person")


def _image_response(media: str, body: bytes) -> web.Response:
    if media not in IMAGE_CONTENT_TYPES:
        raise ProxyRejected(502, "frigate_not_an_image")
    return web.Response(
        status=200, body=body, content_type=media, headers=dict(_IMAGE_HEADERS)
    )


def _json_response(body: bytes) -> web.Response:
    return web.Response(
        status=200,
        body=body,
        content_type="application/json",
        headers=dict(_JSON_HEADERS),
    )


async def _guard(operation: Callable[[], Awaitable[web.Response]]) -> web.Response:
    try:
        return await operation()
    except ProxyRejected as rejected:
        return _error(rejected.status, rejected.code)


# -- handlers ---------------------------------------------------------------


async def events(request: web.Request) -> web.Response:
    async def run() -> web.Response:
        _require_clean_raw_path(request)
        person, limit = _events_query(request)
        await _require_known_name(person)
        path = "api/events?" + urlencode(
            {"sub_labels": person, "limit": str(limit), "include_thumbnails": "0"},
            quote_via=quote,
        )
        _media, body = await _fetch(path, max_bytes=MAX_JSON_BYTES)
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise ProxyRejected(502, "frigate_invalid_response") from error
        if not isinstance(payload, list):
            raise ProxyRejected(502, "frigate_invalid_response")
        return _json_response(body)

    return await _guard(run)


async def thumbnail(request: web.Request, event_id: str) -> web.Response:
    async def run() -> web.Response:
        _require_clean_raw_path(request)
        _require_no_query(request)
        checked = validate_event_id(event_id)
        media, body = await _fetch(
            f"api/events/{checked}/thumbnail.jpg", max_bytes=MAX_IMAGE_BYTES
        )
        return _image_response(media, body)

    return await _guard(run)


async def faces(request: web.Request) -> web.Response:
    async def run() -> web.Response:
        _require_clean_raw_path(request)
        _require_no_query(request)
        library = await _face_library(fresh=True)
        return _json_response(json.dumps(library).encode("utf-8"))

    return await _guard(run)


async def face_file(request: web.Request, name: str, file: str) -> web.Response:
    async def run() -> web.Response:
        _require_clean_raw_path(request)
        _require_no_query(request)
        checked_name = validate_person_name(name)
        checked_file = validate_face_file(file)
        await _require_known_name(checked_name)
        media, body = await _fetch(
            "clips/faces/"
            f"{quote(checked_name, safe='')}/{quote(checked_file, safe='')}",
            max_bytes=MAX_IMAGE_BYTES,
        )
        return _image_response(media, body)

    return await _guard(run)
