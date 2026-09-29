"""Tests for the typed Frigate routes and the agent profile listing.

Standalone and standard-library only, like the other in-package tests: Home
Assistant, aiohttp and httpx are stubbed. Run:

    py -3 ha-config/extended_openai_conversation/test_frigate_proxy.py

What is pinned:
  * every accepted request shape builds exactly the Frigate path the contract
    names, and nothing else reaches Frigate;
  * traversal (`..`, `%2e%2e`, encoded slashes and backslashes), other `api/*`
    paths, bad ids, names, files and limits, unknown names, the `train`
    bucket, unexpected query keys, and the old `?path=` form are refused;
  * redirects are never followed, non-image content types are refused, and no
    response carries Frigate's URL or origin;
  * the view classes in __init__.py are exactly the typed routes, CORS-enabled,
    authenticated and admin-guarded;
  * `agent_profiles` no longer returns `frigate_url`.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRIGATE = "http://frigate.internal.example:5000"
PACKAGE = "eoc_frigate_proxy_under_test"


# -- stubs ------------------------------------------------------------------


class _Response:
    def __init__(self, *, status=200, body=None, content_type=None, headers=None, **_kw):
        self.status = status
        self.body = body if body is not None else b""
        self.content_type = content_type
        self.headers = dict(headers or {})


_web = types.ModuleType("aiohttp.web")
_web.Response = _Response
_web.Request = object
_aiohttp = types.ModuleType("aiohttp")
_aiohttp.web = _web
sys.modules.setdefault("aiohttp", _aiohttp)
sys.modules.setdefault("aiohttp.web", _web)
sys.modules["aiohttp"].web = sys.modules["aiohttp.web"]
sys.modules["aiohttp.web"].Response = _Response
sys.modules["aiohttp.web"].Request = object


class _HomeAssistantView:
    def json(self, result, status_code=200):
        return _Response(
            status=status_code,
            body=json.dumps(result).encode("utf-8"),
            content_type="application/json",
        )


for _name in ("homeassistant", "homeassistant.components", "homeassistant.components.http"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["homeassistant.components.http"].HomeAssistantView = _HomeAssistantView

_pkg = types.ModuleType(PACKAGE)
_pkg.__path__ = [str(HERE)]
sys.modules[PACKAGE] = _pkg

_frigate_sync = types.ModuleType(f"{PACKAGE}.frigate_sync")
_frigate_sync.base_url = lambda: FRIGATE
sys.modules[f"{PACKAGE}.frigate_sync"] = _frigate_sync

_agent_profiles = types.ModuleType(f"{PACKAGE}.agent_profiles")
_agent_profiles.list_profiles = lambda: [
    {"person_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12, "display_name": "Alex"}
]
sys.modules[f"{PACKAGE}.agent_profiles"] = _agent_profiles
_pkg.agent_profiles = _agent_profiles


def _load(module: str):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module}", HERE / f"{module}.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


proxy = _load("frigate_proxy")
profile_views = _load("agent_profile_views")


class _UpstreamResponse:
    def __init__(self, status=200, body=b"", content_type="application/json", headers=None):
        self.status_code = status
        self.content = body
        self.headers = {"content-type": content_type, **(headers or {})}


LIBRARY = {
    "Alex": ["alex-1.webp", "alex-2.jpg"],
    "Sam Lee": ["sam.png"],
    "train": ["1727650000.123456-abcd12-unknown-0.9.webp"],
}
EVENTS = [{"id": "1727650000.123456-abcd12", "sub_label": ["Alex", 0.93]}]


class _Upstream:
    """Records every URL requested; answers from a small route table."""

    def __init__(self):
        self.requests: list[str] = []
        self.client_kwargs: list[dict] = []
        self.routes: dict[str, _UpstreamResponse] = {}
        self.raise_on_get: Exception | None = None
        self.reset()

    def reset(self):
        self.requests.clear()
        self.client_kwargs.clear()
        self.raise_on_get = None
        self.routes = {
            "api/faces": _UpstreamResponse(body=json.dumps(LIBRARY).encode()),
            "api/events?sub_labels=Alex&limit=50&include_thumbnails=0": _UpstreamResponse(
                body=json.dumps(EVENTS).encode()
            ),
            "api/events?sub_labels=Sam%20Lee&limit=200&include_thumbnails=0": _UpstreamResponse(
                body=b"[]"
            ),
            "api/events?sub_labels=Alex&limit=1&include_thumbnails=0": _UpstreamResponse(
                body=json.dumps(EVENTS).encode()
            ),
            "api/events/1727650000.123456-abcd12/thumbnail.jpg": _UpstreamResponse(
                body=b"\xff\xd8jpeg", content_type="image/jpeg"
            ),
            "clips/faces/Alex/alex-1.webp": _UpstreamResponse(
                body=b"RIFFwebp", content_type="image/webp"
            ),
            "clips/faces/Sam%20Lee/sam.png": _UpstreamResponse(
                body=b"\x89PNG", content_type="image/png; charset=binary"
            ),
        }

    def factory(self, **kwargs):
        self.client_kwargs.append(kwargs)
        upstream = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **get_kwargs):
                assert not get_kwargs, get_kwargs
                upstream.requests.append(url)
                if upstream.raise_on_get is not None:
                    raise upstream.raise_on_get
                assert url.startswith(FRIGATE + "/"), url
                path = url[len(FRIGATE) + 1:]
                return upstream.routes.get(path, _UpstreamResponse(status=404, body=b"nope"))

        return _Client()


upstream = _Upstream()
proxy._client_factory = upstream.factory
proxy._frigate_base_url = lambda: FRIGATE


class _Request:
    def __init__(self, raw_path: str, query: list[tuple[str, str]] | None = None):
        path, _, qs = raw_path.partition("?")
        self.raw_path = raw_path
        self.path = path
        self.query_string = qs
        self._query = list(query or [])

    @property
    def query(self):
        pairs = self._query

        class _Multi(dict):
            def keys(self_inner):
                return [key for key, _ in pairs]

        multi = _Multi()
        for key, value in pairs:
            multi.setdefault(key, value)
        return multi


def _events_request(pairs: list[tuple[str, str]]) -> _Request:
    from urllib.parse import quote, urlencode

    qs = urlencode(pairs, quote_via=quote)
    return _Request(proxy.EVENTS_URL + ("?" + qs if qs else ""), pairs)


def _run(coro):
    return asyncio.run(coro)


def _body_json(response):
    return json.loads(response.body.decode("utf-8"))


passes = 0
fails: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    global passes
    if condition:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails.append(name)
        print(f"  FAIL  {name} -- {detail!r}")


def no_frigate_origin(response) -> bool:
    blob = (response.body or b"") + json.dumps(response.headers).encode()
    return b"frigate.internal" not in blob and b":5000" not in blob


def fresh():
    upstream.reset()
    proxy.reset_face_library_cache()


# -- accepted shapes --------------------------------------------------------

print("accepted shapes")

fresh()
response = _run(proxy.events(_events_request([("person", "Alex")])))
check("events: default limit is 50 and asks Frigate for exactly that query",
      response.status == 200
      and upstream.requests == [
          f"{FRIGATE}/api/faces",
          f"{FRIGATE}/api/events?sub_labels=Alex&limit=50&include_thumbnails=0",
      ], (response.status, upstream.requests))
check("events: returns Frigate's JSON list unchanged",
      response.body == json.dumps(EVENTS).encode()
      and response.content_type == "application/json"
      and response.headers.get("Cache-Control") == "no-store", response.body)
check("events: response carries no Frigate origin", no_frigate_origin(response))

fresh()
response = _run(proxy.events(_events_request([("person", "Sam Lee"), ("limit", "200")])))
check("events: a name with a space is encoded and limit 200 is accepted",
      response.status == 200
      and upstream.requests[-1]
      == f"{FRIGATE}/api/events?sub_labels=Sam%20Lee&limit=200&include_thumbnails=0",
      (response.status, upstream.requests))

fresh()
response = _run(proxy.events(_events_request([("limit", "1"), ("person", "Alex")])))
check("events: limit 1 is accepted", response.status == 200, response.status)

fresh()
response = _run(proxy.thumbnail(
    _Request(proxy.URL_PREFIX + "/events/1727650000.123456-abcd12/thumbnail.jpg"),
    "1727650000.123456-abcd12",
))
check("thumbnail: returns the image bytes with Frigate's image content type",
      response.status == 200 and response.body == b"\xff\xd8jpeg"
      and response.content_type == "image/jpeg"
      and upstream.requests == [
          f"{FRIGATE}/api/events/1727650000.123456-abcd12/thumbnail.jpg"
      ], (response.status, response.content_type, upstream.requests))
check("thumbnail: nosniff, sandboxed, private caching",
      response.headers.get("X-Content-Type-Options") == "nosniff"
      and "sandbox" in response.headers.get("Content-Security-Policy", "")
      and response.headers.get("Cache-Control", "").startswith("private"),
      response.headers)

fresh()
response = _run(proxy.faces(_Request(proxy.FACES_URL)))
check("faces: the face library listing without the train bucket",
      response.status == 200
      and _body_json(response) == {"Alex": LIBRARY["Alex"], "Sam Lee": LIBRARY["Sam Lee"]}
      and upstream.requests == [f"{FRIGATE}/api/faces"],
      (response.status, response.body, upstream.requests))
check("faces: response carries no Frigate origin", no_frigate_origin(response))

fresh()
response = _run(proxy.face_file(
    _Request(proxy.URL_PREFIX + "/faces/Alex/alex-1.webp"), "Alex", "alex-1.webp"
))
check("face file: returns the enrolled image",
      response.status == 200 and response.body == b"RIFFwebp"
      and response.content_type == "image/webp"
      and upstream.requests[-1] == f"{FRIGATE}/clips/faces/Alex/alex-1.webp",
      (response.status, upstream.requests))

fresh()
response = _run(proxy.face_file(
    _Request(proxy.URL_PREFIX + "/faces/Sam%20Lee/sam.png"), "Sam Lee", "sam.png"
))
check("face file: a name with a space, and a parameterised image type",
      response.status == 200 and response.content_type == "image/png"
      and upstream.requests[-1] == f"{FRIGATE}/clips/faces/Sam%20Lee/sam.png",
      (response.status, response.content_type, upstream.requests))

def _refuses(validator, value) -> bool:
    try:
        validator(value)
    except proxy.ProxyRejected as rejected:
        return rejected.status == 400
    return False


for value in ("Al..ex", "..", ".", " ", "train", "a/b", "a\\b", "","A" * 65, None):
    check(f"validate_person_name refuses {value!r} even if Frigate listed it",
          _refuses(proxy.validate_person_name, value))
for value in ("Alex", "Sam Lee", "J.R. Smith", "anne-marie_2"):
    check(f"validate_person_name accepts {value!r}", proxy.validate_person_name(value) == value)

for ext in ("jpg", "jpeg", "png", "webp"):
    check(f"face file: .{ext} is an accepted extension",
          proxy.validate_face_file(f"face-01.{ext}") == f"face-01.{ext}")


# -- rejected shapes --------------------------------------------------------

print("rejected shapes")


def rejected(call, expected_status: int, *, network: bool) -> tuple[bool, object]:
    fresh()
    response = _run(call())
    reached = [url for url in upstream.requests if not url.endswith("/api/faces")]
    ok = (
        response.status == expected_status
        and "error" in _body_json(response)
        and no_frigate_origin(response)
        and (network or not reached)
    )
    return ok, (response.status, response.body, upstream.requests)


for label, pairs in (
    ("missing person", []),
    ("empty person", [("person", "")]),
    ("dot-dot person", [("person", "..")]),
    ("dot-dot inside person", [("person", "Al..ex")]),
    ("traversal in person", [("person", "../config")]),
    ("slash in person", [("person", "Alex/../../config")]),
    ("backslash in person", [("person", "Alex\\config")]),
    ("quote in person", [("person", "Alex'")]),
    ("65-character person", [("person", "A" * 65)]),
    ("train bucket as a person", [("person", "train")]),
    ("limit 0", [("person", "Alex"), ("limit", "0")]),
    ("limit 201", [("person", "Alex"), ("limit", "201")]),
    ("negative limit", [("person", "Alex"), ("limit", "-1")]),
    ("non-numeric limit", [("person", "Alex"), ("limit", "ten")]),
    ("empty limit", [("person", "Alex"), ("limit", "")]),
    ("four-digit limit", [("person", "Alex"), ("limit", "1000")]),
    ("unexpected key", [("person", "Alex"), ("path", "api/config")]),
    ("duplicated person", [("person", "Alex"), ("person", "Sam Lee")]),
    ("include_thumbnails override", [("person", "Alex"), ("include_thumbnails", "1")]),
):
    ok, detail = rejected(lambda: proxy.events(_events_request(pairs)), 400, network=False)
    check(f"events: 400 for {label}", ok, detail)

ok, detail = rejected(
    lambda: proxy.events(_events_request([("person", "Nobody")])), 400, network=False
)
check("events: 400 for a name that is not in the face library", ok, detail)
fresh()
_run(proxy.events(_events_request([("person", "Nobody")])))
check("events: an unknown name is re-checked once against a fresh library",
      upstream.requests == [f"{FRIGATE}/api/faces", f"{FRIGATE}/api/faces"],
      upstream.requests)

for label, raw in (
    ("%2e%2e in the path", "/events%2f..%2fconfig"),
    ("encoded slash", "/events%2Fconfig"),
    ("encoded backslash", "/events%5cconfig"),
    ("literal dot-dot", "/events/../config"),
    ("double slash", "/events//x"),
):
    request = _Request(proxy.URL_PREFIX + raw + "?person=Alex", [("person", "Alex")])
    ok, detail = rejected(lambda: proxy.events(request), 400, network=False)
    check(f"events: 400 for {label}", ok, detail)

for label, event_id in (
    ("traversal", "../config"),
    ("encoded traversal", "%2e%2e"),
    ("slash", "1727650000.123456-abcd12/../../config"),
    ("upper case suffix", "1727650000.123456-ABCD12"),
    ("short timestamp", "17276500.123456-abcd12"),
    ("long timestamp", "172765000000.123456-abcd12"),
    ("no fraction", "1727650000-abcd12"),
    ("seven-digit fraction", "1727650000.1234567-abcd12"),
    ("short suffix", "1727650000.123456-abc"),
    ("long suffix", "1727650000.123456-abcdefghijklm"),
    ("config", "config"),
    ("empty", ""),
):
    request = _Request(proxy.URL_PREFIX + "/events/x/thumbnail.jpg")
    ok, detail = rejected(lambda: proxy.thumbnail(request, event_id), 400, network=False)
    check(f"thumbnail: 400 for {label}", ok, detail)

for label, raw in (
    ("%2e%2e event id", "/events/%2e%2e/thumbnail.jpg"),
    ("encoded slash event id", "/events/1727650000.123456-abcd12%2f..%2fconfig/thumbnail.jpg"),
):
    request = _Request(proxy.URL_PREFIX + raw)
    ok, detail = rejected(
        lambda: proxy.thumbnail(request, "1727650000.123456-abcd12"), 400, network=False
    )
    check(f"thumbnail: 400 for {label} in the raw path", ok, detail)

request = _Request(proxy.URL_PREFIX + "/events/1727650000.123456-abcd12/thumbnail.jpg?x=1")
ok, detail = rejected(lambda: proxy.thumbnail(request, "1727650000.123456-abcd12"), 400, network=False)
check("thumbnail: 400 for any query string", ok, detail)

request = _Request(proxy.FACES_URL + "?path=api/config")
ok, detail = rejected(lambda: proxy.faces(request), 400, network=False)
check("faces: 400 for any query string, including the old path parameter", ok, detail)

for label, name, file in (
    ("dot-dot name", "..", "a.webp"),
    ("dot-dot inside name", "Al..ex", "a.webp"),
    ("single-dot name", ".", "a.webp"),
    ("slash name", "Alex/..", "a.webp"),
    ("train bucket", "train", "1727650000.123456-abcd12-unknown-0.9.webp"),
    ("unknown name", "Nobody", "a.webp"),
    ("traversal file", "Alex", "../../config.webp"),
    ("dot-dot file", "Alex", "a..webp"),
    ("hidden file", "Alex", ".hidden.webp"),
    ("slash file", "Alex", "x/y.webp"),
    ("svg file", "Alex", "a.svg"),
    ("gif file", "Alex", "a.gif"),
    ("no extension", "Alex", "alex-1"),
    ("upper-case extension", "Alex", "a.WEBP"),
    ("json file", "Alex", "a.json"),
    ("129-character stem", "Alex", "a" * 129 + ".webp"),
    ("space in file", "Alex", "a b.webp"),
):
    request = _Request(proxy.URL_PREFIX + "/faces/x/y.webp")
    ok, detail = rejected(lambda: proxy.face_file(request, name, file), 400, network=False)
    check(f"face file: 400 for {label}", ok, detail)

for label, raw in (
    ("%2e%2e name", "/faces/%2e%2e/a.webp"),
    ("encoded slash in the file", "/faces/Alex/..%2fconfig.webp"),
):
    request = _Request(proxy.URL_PREFIX + raw)
    ok, detail = rejected(lambda: proxy.face_file(request, "Alex", "alex-1.webp"), 400, network=False)
    check(f"face file: 400 for {label} in the raw path", ok, detail)

request = _Request(proxy.URL_PREFIX + "?path=api/events/../config", [("path", "api/events/../config")])
ok, detail = rejected(lambda: proxy.events(request), 404, network=False)
check("the old ?path= form is not served by any handler", ok, detail)


# -- upstream answers -------------------------------------------------------

print("upstream answers")

fresh()
_run(proxy.faces(_Request(proxy.FACES_URL)))
check("never follows redirects, with a short timeout",
      upstream.client_kwargs
      and all(kw.get("follow_redirects") is False for kw in upstream.client_kwargs)
      and all(0 < kw.get("timeout", 0) <= 10 for kw in upstream.client_kwargs),
      upstream.client_kwargs)

fresh()
upstream.routes["api/events/1727650000.123456-abcd12/thumbnail.jpg"] = _UpstreamResponse(
    status=302, body=b"", content_type="text/html",
    headers={"location": f"{FRIGATE}/api/config"},
)
response = _run(proxy.thumbnail(
    _Request(proxy.URL_PREFIX + "/events/1727650000.123456-abcd12/thumbnail.jpg"),
    "1727650000.123456-abcd12",
))
check("a redirect answer becomes 502 and is not followed or echoed",
      response.status == 502 and upstream.requests == [
          f"{FRIGATE}/api/events/1727650000.123456-abcd12/thumbnail.jpg"
      ] and "location" not in {k.lower() for k in response.headers}
      and no_frigate_origin(response),
      (response.status, upstream.requests, response.headers))

for label, content_type in (
    ("text/html", "text/html"),
    ("SVG", "image/svg+xml"),
    ("JSON", "application/json"),
    ("missing", ""),
    ("GIF", "image/gif"),
):
    fresh()
    upstream.routes["clips/faces/Alex/alex-1.webp"] = _UpstreamResponse(
        body=b"<svg onload=alert(1)>", content_type=content_type
    )
    response = _run(proxy.face_file(
        _Request(proxy.URL_PREFIX + "/faces/Alex/alex-1.webp"), "Alex", "alex-1.webp"
    ))
    check(f"face file: a {label} answer is refused with 502",
          response.status == 502 and b"svg" not in response.body, response.status)
    fresh()
    upstream.routes["api/events/1727650000.123456-abcd12/thumbnail.jpg"] = _UpstreamResponse(
        body=b"<html>", content_type=content_type
    )
    response = _run(proxy.thumbnail(
        _Request(proxy.URL_PREFIX + "/events/1727650000.123456-abcd12/thumbnail.jpg"),
        "1727650000.123456-abcd12",
    ))
    check(f"thumbnail: a {label} answer is refused with 502", response.status == 502,
          response.status)

fresh()
upstream.routes["api/events?sub_labels=Alex&limit=50&include_thumbnails=0"] = _UpstreamResponse(
    body=b'{"not": "a list"}'
)
response = _run(proxy.events(_events_request([("person", "Alex")])))
check("events: a non-list answer is refused with 502", response.status == 502, response.status)

fresh()
upstream.routes["api/faces"] = _UpstreamResponse(body=b"<html>config</html>", content_type="text/html")
response = _run(proxy.faces(_Request(proxy.FACES_URL)))
check("faces: a non-JSON answer is refused with 502", response.status == 502, response.status)

fresh()
upstream.routes["api/faces"] = _UpstreamResponse(body=b'{"Alex": "not-a-list"}')
response = _run(proxy.faces(_Request(proxy.FACES_URL)))
check("faces: a malformed library is refused with 502", response.status == 502, response.status)

fresh()
upstream.routes["api/events/1727650000.123456-abcd12/thumbnail.jpg"] = _UpstreamResponse(
    status=404, body=b"missing"
)
response = _run(proxy.thumbnail(
    _Request(proxy.URL_PREFIX + "/events/1727650000.123456-abcd12/thumbnail.jpg"),
    "1727650000.123456-abcd12",
))
check("thumbnail: Frigate 404 stays 404 without Frigate's body",
      response.status == 404 and b"missing" not in response.body, response.body)

fresh()
upstream.routes["api/faces"] = _UpstreamResponse(status=500, body=f"{FRIGATE} exploded".encode())
response = _run(proxy.faces(_Request(proxy.FACES_URL)))
check("faces: a Frigate error is a fixed 502 without its text",
      response.status == 502 and no_frigate_origin(response), response.body)

fresh()
upstream.raise_on_get = OSError(f"connect to {FRIGATE} refused")
response = _run(proxy.faces(_Request(proxy.FACES_URL)))
check("faces: a transport error is a fixed 502 that does not echo the URL",
      response.status == 502 and _body_json(response) == {"error": "frigate_unreachable"}
      and no_frigate_origin(response), response.body)

fresh()
upstream.routes["api/events/1727650000.123456-abcd12/thumbnail.jpg"] = _UpstreamResponse(
    body=b"x" * (proxy.MAX_IMAGE_BYTES + 1), content_type="image/jpeg"
)
response = _run(proxy.thumbnail(
    _Request(proxy.URL_PREFIX + "/events/1727650000.123456-abcd12/thumbnail.jpg"),
    "1727650000.123456-abcd12",
))
check("thumbnail: an oversized image is refused with 502", response.status == 502, response.status)

fresh()
proxy._frigate_base_url = lambda: ""
response = _run(proxy.faces(_Request(proxy.FACES_URL)))
proxy._frigate_base_url = lambda: FRIGATE
check("faces: 503 when Frigate is not configured, before any request",
      response.status == 503 and upstream.requests == [], (response.status, upstream.requests))

fresh()
_run(proxy.face_file(_Request(proxy.URL_PREFIX + "/faces/Alex/alex-1.webp"), "Alex", "alex-1.webp"))
_run(proxy.face_file(_Request(proxy.URL_PREFIX + "/faces/Alex/alex-2.jpg"), "Alex", "alex-2.jpg"))
check("face file: the library lookup is cached between image requests",
      upstream.requests.count(f"{FRIGATE}/api/faces") == 1, upstream.requests)

# The real client factory: httpx is replaced so no network is touched.
recorded: list[dict] = []
_fake_httpx = types.ModuleType("httpx")
_fake_httpx.AsyncClient = lambda **kwargs: recorded.append(kwargs) or "client"
_saved_httpx = sys.modules.get("httpx")
sys.modules["httpx"] = _fake_httpx
try:
    proxy._default_client_factory(timeout=proxy.UPSTREAM_TIMEOUT_S, follow_redirects=False)
finally:
    if _saved_httpx is None:
        sys.modules.pop("httpx", None)
    else:
        sys.modules["httpx"] = _saved_httpx
check("the httpx client is built with follow_redirects=False",
      recorded == [{"timeout": proxy.UPSTREAM_TIMEOUT_S, "follow_redirects": False}], recorded)


# -- the views in __init__.py ----------------------------------------------

print("views in __init__.py")

INIT = (HERE / "__init__.py").read_text(encoding="utf-8")
tree = ast.parse(INIT)
classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def class_attr(node: ast.ClassDef, name: str):
    for item in node.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in item.targets
        ):
            return item.value
    return None


EXPECTED = {
    "FrigateEventsProxyView": ("EVENTS_URL", "events"),
    "FrigateEventThumbnailProxyView": ("THUMBNAIL_URL", "thumbnail"),
    "FrigateFacesProxyView": ("FACES_URL", "faces"),
    "FrigateFaceFileProxyView": ("FACE_FILE_URL", "face_file"),
}
for view, (url_const, handler) in EXPECTED.items():
    node = classes.get(view)
    check(f"{view} exists", node is not None)
    if node is None:
        continue
    url = class_attr(node, "url")
    auth = class_attr(node, "requires_auth")
    check(f"{view} subclasses CORSHomeAssistantView",
          [ast.unparse(base) for base in node.bases] == ["CORSHomeAssistantView"])
    check(f"{view} url is frigate_proxy.{url_const}",
          url is not None and ast.unparse(url) == f"_frigate_proxy.{url_const}",
          url and ast.unparse(url))
    check(f"{view} requires auth", auth is not None and ast.unparse(auth) == "True")
    methods = [item.name for item in node.body if isinstance(item, ast.AsyncFunctionDef)]
    check(f"{view} serves GET only", methods == ["get"], methods)
    check(f"{view} delegates to frigate_proxy.{handler}",
          f"_frigate_proxy.{handler}(" in ast.unparse(node))

check("the URLs are exactly the typed contract",
      (proxy.EVENTS_URL, proxy.THUMBNAIL_URL, proxy.FACES_URL, proxy.FACE_FILE_URL) == (
          "/api/extended_openai_conversation/frigate_proxy/events",
          "/api/extended_openai_conversation/frigate_proxy/events/{event_id}/thumbnail.jpg",
          "/api/extended_openai_conversation/frigate_proxy/faces",
          "/api/extended_openai_conversation/frigate_proxy/faces/{name}/{file}",
      ))
check("the ?path= passthrough view is gone",
      "FrigateProxyView" not in classes and "_ALLOWED_PREFIXES" not in INIT
      and 'request.query.get("path")' not in INIT)
check("nothing is registered at the bare frigate_proxy URL",
      '"/api/extended_openai_conversation/frigate_proxy"' not in INIT)
check("the typed views are admin-guarded with the other legacy private views",
      "    *FRIGATE_PROXY_VIEW_CLASSES,\n    AvatarView,\n)\nfor _view_class in _LEGACY_PRIVATE_VIEW_CLASSES:" in INIT)
check("the typed views are registered at setup",
      "AvatarView, *FRIGATE_PROXY_VIEW_CLASSES,\n    ):\n        try:\n            hass.http.register_view(view_cls())" in INIT)
check("FRIGATE_PROXY_VIEW_CLASSES lists exactly the four typed views",
      "FRIGATE_PROXY_VIEW_CLASSES = (\n    FrigateEventsProxyView,\n"
      "    FrigateEventThumbnailProxyView,\n    FrigateFacesProxyView,\n"
      "    FrigateFaceFileProxyView,\n)" in INIT)


# -- agent_profiles ---------------------------------------------------------

print("agent_profiles")


class _Hass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _ProfilesRequest:
    app = {"hass": _Hass()}


_frigate_sync.base_url = lambda: FRIGATE
response = _run(profile_views.AgentProfilesView().get(_ProfilesRequest()))
payload = _body_json(response)
check("agent_profiles keeps profiles and frigate_faces_available",
      set(payload) == {"profiles", "frigate_faces_available"}
      and payload["frigate_faces_available"] is True
      and payload["profiles"] == _agent_profiles.list_profiles(), payload)
check("agent_profiles returns no frigate_url and no Frigate origin",
      "frigate_url" not in payload and no_frigate_origin(response), payload)

_frigate_sync.base_url = lambda: ""
payload = _body_json(_run(profile_views.AgentProfilesView().get(_ProfilesRequest())))
check("agent_profiles reports Frigate unavailable when it is not configured",
      payload.get("frigate_faces_available") is False and "frigate_url" not in payload,
      payload)

print()
print(f"{passes} pass · {len(fails)} fail")
if fails:
    print("Failures:")
    for name in fails:
        print(f"  - {name}")
sys.exit(0 if not fails else 1)
