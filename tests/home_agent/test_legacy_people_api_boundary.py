from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = (
    ROOT / "ha-config" / "extended_openai_conversation" / "__init__.py"
)
PEOPLE_VIEWS = (
    "IdentityListView",
    "IdentityDetailView",
    "IdentityBackupView",
    "IdentityCreateView",
    "RelationshipsView",
    "PreferencesView",
    "FrigateProxyView",
    "AvatarView",
)


class _HTTPForbidden(Exception):
    def __init__(self, *, reason: str):
        super().__init__(reason)


class _Response:
    def __init__(self, *, status=200, body=None, headers=None, **_kwargs):
        self.status = status
        self.body = body
        self.headers = headers or {}


class _Web:
    Request = object
    Response = _Response
    HTTPForbidden = _HTTPForbidden


class _View:
    def json(self, payload, *, status_code=200):
        return {"payload": payload, "status": status_code}


class _LegacyIdentityFenceError(Exception):
    pass


def _load_people_namespace() -> dict:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    wanted = {
        *PEOPLE_VIEWS,
        "_request_has_ha_admin_capability",
        "_admin_only_legacy_handler",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in wanted
    ]
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            *nodes,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "CORSHomeAssistantView": _View,
        "DOMAIN": "extended_openai_conversation",
        "HomeAssistant": object,
        "IdentityStore": object,
        "LegacyIdentityFenceError": _LegacyIdentityFenceError,
        "Path": Path,
        "_AVATARS_DIR": Path("private-avatars"),
        "wraps": __import__("functools").wraps,
        "web": _Web,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


def _listed_private_views() -> set[str]:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_LEGACY_PRIVATE_VIEW_CLASSES"
                for target in node.targets
            )
            and isinstance(node.value, ast.Tuple)
        ):
            return {
                item.id
                for item in node.value.elts
                if isinstance(item, ast.Name)
            }
    raise AssertionError("_LEGACY_PRIVATE_VIEW_CLASSES not found")


class _GuardRequest(dict):
    def __init__(self, user):
        super().__init__()
        if user is not None:
            self["hass_user"] = user
        self.downstream_reads = 0

    @property
    def app(self):
        self.downstream_reads += 1
        raise AssertionError("authorization guard allowed downstream I/O")


@pytest.mark.parametrize(
    "user",
    [None, SimpleNamespace(is_admin=False)],
    ids=["missing-user", "non-admin"],
)
def test_every_people_handler_rejects_before_downstream_io(user) -> None:
    namespace = _load_people_namespace()
    assert set(PEOPLE_VIEWS).issubset(_listed_private_views())
    decorator = namespace["_admin_only_legacy_handler"]

    for class_name in PEOPLE_VIEWS:
        view_class = namespace[class_name]
        view = view_class()
        for method_name in ("get", "head", "post", "put", "patch", "delete"):
            original = view_class.__dict__.get(method_name)
            if original is None:
                continue
            wrapped = decorator(original).__get__(view, view_class)
            request = _GuardRequest(user)
            parameters = list(inspect.signature(wrapped).parameters.values())
            path_args = ["subject"] * max(0, len(parameters) - 1)
            with pytest.raises(_HTTPForbidden):
                asyncio.run(wrapped(request, *path_args))
            assert request.downstream_reads == 0, (
                class_name,
                method_name,
            )


def test_admin_guard_marks_private_responses_uncacheable() -> None:
    namespace = _load_people_namespace()
    decorator = namespace["_admin_only_legacy_handler"]

    async def handler(_self, _request):
        return _Response(headers={"Existing": "preserved"})

    wrapped = decorator(handler).__get__(object(), object)
    request = {"hass_user": SimpleNamespace(is_admin=True)}
    response = asyncio.run(wrapped(request))
    assert response.headers == {
        "Existing": "preserved",
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }


def test_frigate_proxy_accepts_only_the_exact_typed_operation() -> None:
    view = _load_people_namespace()["FrigateProxyView"]
    exact = view.url

    def request(path: str, raw_path: str, query_string: str = ""):
        return SimpleNamespace(
            path=path,
            raw_path=raw_path,
            query_string=query_string,
        )

    assert view._is_exact_request(request(exact, exact))
    rejected = (
        request(f"{exact}/", f"{exact}/"),
        request(f"{exact}-extra", f"{exact}-extra"),
        request(exact, f"{exact}?path=api/faces", "path=api/faces"),
        request(exact, f"{exact}?x=1", "x=1"),
        request(exact, f"{exact}#fragment"),
        request(exact, f"{exact}/../faces"),
        request(exact, exact.replace("/faces", "/%66aces")),
        request(exact, exact.replace("/faces", "/%2e/faces")),
    )
    assert all(not view._is_exact_request(item) for item in rejected)
    instance = view()
    for item in rejected:
        assert asyncio.run(instance.get(item)) == {
            "payload": {"error": "exact typed operation required"},
            "status": 400,
        }


def test_frigate_proxy_validates_and_bounds_untrusted_metadata() -> None:
    view = _load_people_namespace()["FrigateProxyView"]

    original = {"Marcelo": ["one.webp", "two.webp"]}
    sanitized = view._sanitize_faces_payload(original)
    assert sanitized == {"Marcelo": 2}
    assert sanitized is not original

    rejected = (
        [],
        {"": ["one.webp"]},
        {"Marcelo\n": ["one.webp"]},
        {"Marcelo": "one.webp"},
        {"Marcelo": [""]},
        {"Marcelo": ["bad\nname.webp"]},
        {f"person-{index}": [] for index in range(513)},
        {"Marcelo": ["one.webp"] * 2049},
    )
    for payload in rejected:
        with pytest.raises(ValueError, match="invalid Frigate faces payload"):
            view._sanitize_faces_payload(payload)


def test_frigate_proxy_filters_every_bucket_through_privacy_policy() -> None:
    view = _load_people_namespace()["FrigateProxyView"]

    class Store:
        recognition_metadata_available = True
        setup_error = None

        policies = {
            "Allowed": "allowed",
            "Unknown": "unmatched",
            "Private": "restricted",
            "Dnt": "blocked",
        }

        def legacy_recognition_persistence_policy(self, name):
            return self.policies[name]

    payload = {
        "Allowed": ["allowed.webp"],
        "Unknown": ["unknown.webp"],
        "Private": ["private.webp"],
        "Dnt": ["dnt.webp"],
    }
    assert view._filter_faces_payload(Store(), payload) == {
        "Allowed": 1,
        "Unknown": 1,
    }

    for store in (
        None,
        SimpleNamespace(
            recognition_metadata_available=False,
            setup_error=None,
        ),
        SimpleNamespace(
            recognition_metadata_available=True,
            setup_error="fence degraded",
        ),
    ):
        with pytest.raises(
            PermissionError,
            match="recognition metadata unavailable",
        ):
            view._filter_faces_payload(store, payload)


def test_frigate_proxy_fails_before_network_when_policy_is_unavailable() -> None:
    view = _load_people_namespace()["FrigateProxyView"]()

    class Hass:
        data = {
            "extended_openai_conversation": {
                "identity_store": SimpleNamespace(
                    recognition_metadata_available=False,
                    setup_error=None,
                ),
            },
        }
        executor_calls = 0

        async def async_add_executor_job(self, function, *args):
            self.executor_calls += 1
            return function(*args)

    hass = Hass()
    request = SimpleNamespace(
        path=view.url,
        raw_path=view.url,
        query_string="",
        app={"hass": hass},
    )
    assert asyncio.run(view.get(request)) == {
        "payload": {"error": "frigate metadata unavailable"},
        "status": 503,
    }
    assert hass.executor_calls == 0


@pytest.mark.parametrize("method_name", ["get", "head"])
def test_avatar_reads_fail_before_disk_io_when_store_is_degraded(
    method_name: str,
) -> None:
    view = _load_people_namespace()["AvatarView"]()

    class Store:
        setup_error = "semantic fence degraded"

        def assert_legacy_read_current(self):
            raise AssertionError("degraded store reached fence or disk I/O")

    class Hass:
        data = {"extended_openai_conversation": {"identity_store": Store()}}
        executor_calls = 0

        async def async_add_executor_job(self, function, *args):
            self.executor_calls += 1
            return function(*args)

    hass = Hass()
    request = SimpleNamespace(app={"hass": hass})
    result = asyncio.run(getattr(view, method_name)(request, "person-1"))
    if method_name == "get":
        assert result == {
            "payload": {"enabled": False},
            "status": 503,
        }
    else:
        assert getattr(result, "status", None) == 503
    assert hass.executor_calls == 0


def test_fenced_identity_backup_returns_gone_without_reading_rows() -> None:
    namespace = _load_people_namespace()
    view = namespace["IdentityBackupView"]()

    class Store:
        semantic_write_fence_installed = True

        def __init__(self):
            self.reads = 0
            self.fence_checks = 0

        def assert_legacy_read_current(self):
            self.fence_checks += 1

        def list_identities(self, **_kwargs):
            self.reads += 1
            raise AssertionError("fenced plaintext backup read identity rows")

    store = Store()

    class Hass:
        data = {"extended_openai_conversation": {"identity_store": store}}

        async def async_add_executor_job(self, function, *args):
            return function(*args)

    request = SimpleNamespace(
        app={"hass": Hass()},
        query={},
    )
    result = asyncio.run(view.get(request))
    assert result == {
        "payload": {
            "error": "legacy_identity_export_disabled_pending_cutover"
        },
        "status": 410,
    }
    assert store.fence_checks == 1
    assert store.reads == 0
