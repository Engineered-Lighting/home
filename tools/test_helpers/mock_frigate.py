"""Shared MockFrigate helper for integration tests.

Wraps a stubbed httpx.AsyncClient with Frigate-specific affordances:
- `mf.set_faces([...])` configures the GET /api/faces response
- `mf.stub_rename(old, new, status=200)` programs the PUT rename
- `mf.stub_delete(name, status=200)` programs the DELETE
- `mf.calls_for(method, path_contains)` filter recorded calls
- `mf.assert_called(method, path, params=None)` assertion helper

Built on top of the same MockHttpClient pattern used in test_frigate_sync.py
so existing tests can migrate to this without re-architecture.

Self-test at the bottom: `py -3 mock_frigate.py`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


class MockResponse:
    """Minimal httpx.Response shape."""

    def __init__(self, status_code: int, body: Any = None, text: str = "",
                 headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.text = text if text else (json.dumps(body) if body is not None else "")
        self.headers = dict(headers or {})

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("MockResponse has no JSON body")
        return self._body


class MockHttpClient:
    """Generic stubbed AsyncClient. Match by exact suffix first, then
    by substring containment."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], MockResponse] = {}
        self.exceptions: dict[tuple[str, str], Exception] = {}
        self.calls: list[dict] = []

    def stub(self, method: str, path_suffix: str, response: MockResponse) -> None:
        self.responses[(method.upper(), path_suffix)] = response

    def stub_exception(self, method: str, path_suffix: str, exc: Exception) -> None:
        self.exceptions[(method.upper(), path_suffix)] = exc

    async def _do(self, method: str, url: str, **kwargs: Any) -> MockResponse:
        method = method.upper()
        record = {"method": method, "url": url, **kwargs}
        # Exact-suffix match first
        for (m, suf), exc in self.exceptions.items():
            if m == method and url.endswith(suf):
                self.calls.append(record)
                raise exc
        for (m, suf), resp in self.responses.items():
            if m == method and url.endswith(suf):
                self.calls.append(record)
                return resp
        # Substring fallback
        for (m, suf), resp in self.responses.items():
            if m == method and suf in url:
                self.calls.append(record)
                return resp
        for (m, suf), exc in self.exceptions.items():
            if m == method and suf in url:
                self.calls.append(record)
                raise exc
        # Default: 404
        self.calls.append(record)
        return MockResponse(404, text="not found")

    async def get(self, url: str, **kwargs: Any) -> MockResponse:
        return await self._do("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> MockResponse:
        return await self._do("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> MockResponse:
        return await self._do("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> MockResponse:
        return await self._do("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> MockResponse:
        return await self._do("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> MockResponse:
        return await self._do("HEAD", url, **kwargs)

    async def aclose(self) -> None:
        pass


class MockFrigate:
    """Higher-level Frigate API mock with named endpoint helpers.

    Default: GET /api/faces returns []. Override with `set_faces([...])`.

    Usage:
        mf = MockFrigate()
        mf.set_faces(["marcelo", "sarah"])
        mf.stub_rename("sarah", "sar", status=200)
        # ... run code that calls mf.client ...
        rename_calls = mf.calls_for("PUT", "/api/faces/sarah/rename")
        assert len(rename_calls) == 1
        assert rename_calls[0]["params"]["new_name"] == "sar"
    """

    def __init__(self) -> None:
        self.client = MockHttpClient()
        # Default empty faces response so unstubbed GETs don't 404.
        self.client.stub("GET", "/api/faces", MockResponse(200, body=[]))

    # ── Endpoint configuration ──

    def set_faces(self, faces: list[str] | dict[str, Any]) -> None:
        """Configure GET /api/faces response. Accepts a list (default
        shape Frigate returns) or a dict for advanced cases."""
        self.client.stub("GET", "/api/faces", MockResponse(200, body=faces))

    def stub_rename(
        self,
        old_name: str,
        new_name: str | None = None,
        status: int = 200,
        body: Any = None,
        method: str = "PUT",
    ) -> None:
        """Stub the rename endpoint. Frigate uses PUT in current
        versions; old versions used POST. Default to PUT (the modern
        correct method)."""
        path = f"/api/faces/{old_name}/rename"
        if body is None:
            body = {"updated": True} if status == 200 else None
        self.client.stub(method, path, MockResponse(status, body=body,
            headers={"allow": "PUT"} if status == 405 else {}))

    def stub_delete(self, name: str, status: int = 200, body: Any = None) -> None:
        path = f"/api/faces/{name}"
        if body is None:
            body = {"deleted": True} if status == 200 else None
        self.client.stub("DELETE", path, MockResponse(status, body=body))

    # ── Assertion helpers ──

    def calls_for(self, method: str, path_contains: str = "") -> list[dict]:
        method = method.upper()
        return [c for c in self.client.calls
                if c["method"] == method and path_contains in c["url"]]

    def call_count(self, method: str, path_contains: str = "") -> int:
        return len(self.calls_for(method, path_contains))

    def assert_called(
        self,
        method: str,
        path_contains: str,
        times: int = 1,
        params: dict | None = None,
    ) -> bool:
        calls = self.calls_for(method, path_contains)
        if len(calls) != times:
            return False
        if params:
            return all(
                all(c.get("params", {}).get(k) == v for k, v in params.items())
                for c in calls
            )
        return True


# ───────────────────────────────────────────────────────────────────
# Self-test — run with `py -3 mock_frigate.py`
# ───────────────────────────────────────────────────────────────────

def _selftest() -> int:
    pass_count = 0
    fail_count = 0

    def _assert(name: str, cond: bool, detail: Any = "") -> None:
        nonlocal pass_count, fail_count
        if cond:
            pass_count += 1
            print(f"  PASS  {name}")
        else:
            fail_count += 1
            print(f"  FAIL  {name}  ({detail!r})")

    print("\n[mock_frigate self-test]\n")

    async def _run() -> None:
        # ── default state ──
        mf = MockFrigate()
        r = await mf.client.get("http://192.168.0.125:5000/api/faces")
        _assert("default GET /api/faces returns 200 empty list",
                r.status_code == 200 and r.json() == [])

        # ── set_faces ──
        mf.set_faces(["marcelo", "sarah"])
        r = await mf.client.get("http://192.168.0.125:5000/api/faces")
        _assert("set_faces() updates GET response",
                r.json() == ["marcelo", "sarah"])

        # ── stub_rename (PUT, success) ──
        mf.stub_rename("sarah", "sar", status=200)
        r = await mf.client.put(
            "http://192.168.0.125:5000/api/faces/sarah/rename",
            params={"new_name": "sar"},
        )
        _assert("PUT rename returns configured 200",
                r.status_code == 200 and r.json()["updated"] is True)
        _assert("call recorded with method+url+params",
                mf.call_count("PUT", "/api/faces/sarah/rename") == 1)

        rename_calls = mf.calls_for("PUT", "/api/faces/sarah/rename")
        _assert("call params accessible via calls_for",
                rename_calls[0]["params"]["new_name"] == "sar")

        # ── stub_rename (POST returns 405 with allow: PUT — regression
        # instrument for the bug we shipped) ──
        mf2 = MockFrigate()
        mf2.stub_rename("davidm", method="POST", status=405)
        r = await mf2.client.post(
            "http://x/api/faces/davidm/rename",
            params={"new_name": "davidson"},
        )
        _assert("POST rename returns 405 (S3 bug regression instrument)",
                r.status_code == 405)
        _assert("405 response carries `allow: PUT` header",
                r.headers.get("allow") == "PUT")

        # ── stub_delete ──
        mf3 = MockFrigate()
        mf3.stub_delete("ghost", status=200)
        r = await mf3.client.delete("http://x/api/faces/ghost")
        _assert("DELETE returns configured 200",
                r.status_code == 200)
        _assert("delete recorded",
                mf3.call_count("DELETE", "/api/faces/ghost") == 1)

        # ── assert_called helper ──
        _assert("assert_called(times=1) matches",
                mf3.assert_called("DELETE", "/api/faces/ghost", times=1))
        _assert("assert_called(times=2) fails on mismatch",
                not mf3.assert_called("DELETE", "/api/faces/ghost", times=2))
        _assert("assert_called with params filter",
                mf.assert_called("PUT", "/api/faces/sarah/rename",
                                 params={"new_name": "sar"}))

        # ── exceptions ──
        mf4 = MockFrigate()
        mf4.client.stub_exception("GET", "/api/timeout", TimeoutError("boom"))
        try:
            await mf4.client.get("http://x/api/timeout")
            _assert("exception raised", False)
        except TimeoutError as exc:
            _assert("stubbed exception raised",
                    str(exc) == "boom")

        # ── default 404 for unstubbed paths ──
        mf5 = MockFrigate()
        r = await mf5.client.get("http://x/api/nonexistent")
        _assert("unstubbed path returns 404",
                r.status_code == 404)

        # ── HEAD support ──
        mf6 = MockFrigate()
        mf6.client.stub("HEAD", "/api/faces/x/rename",
                        MockResponse(405, headers={"allow": "PUT"}))
        r = await mf6.client.head("http://x/api/faces/x/rename")
        _assert("HEAD method supported", r.status_code == 405)

    asyncio.run(_run())

    print(f"\n{pass_count} pass · {fail_count} fail")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
