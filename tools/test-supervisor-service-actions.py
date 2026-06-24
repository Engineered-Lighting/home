#!/usr/bin/env python3
"""Regression tests for stack supervisor service-operation routes.

These tests import ``stack/services/supervisor/main.py`` directly and patch the
compose runner, so they never touch Docker or the live AI box.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
SUPERVISOR = REPO / "stack" / "services" / "supervisor" / "main.py"


try:
    from fastapi import HTTPException
except ModuleNotFoundError:
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _Route:
        def __init__(self, path: str):
            self.path = path

    class _FastAPI:
        def __init__(self, *args, **kwargs):
            self.routes = []

        def add_middleware(self, *args, **kwargs):
            return None

        def on_event(self, _event):
            return lambda fn: fn

        def get(self, path, **_kwargs):
            def deco(fn):
                self.routes.append(_Route(path))
                return fn
            return deco

        def post(self, path, **_kwargs):
            def deco(fn):
                self.routes.append(_Route(path))
                return fn
            return deco

    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.FastAPI = _FastAPI
    fastapi_stub.HTTPException = HTTPException
    fastapi_stub.Request = object
    sys.modules["fastapi"] = fastapi_stub

    cors_stub = types.ModuleType("fastapi.middleware.cors")
    cors_stub.CORSMiddleware = object
    sys.modules["fastapi.middleware.cors"] = cors_stub

    responses_stub = types.ModuleType("fastapi.responses")
    responses_stub.StreamingResponse = object
    sys.modules["fastapi.responses"] = responses_stub

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    httpx_stub = types.ModuleType("httpx")

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, _url):
            raise RuntimeError("httpx stub should not be used in this test")

    httpx_stub.AsyncClient = _AsyncClient
    sys.modules["httpx"] = httpx_stub

os.environ.setdefault("STACK_TOKEN", "test-token-123456789")
os.environ.setdefault("BIND_ADDR", "127.0.0.1")
os.environ.setdefault("STACK_SH", str(REPO / "stack" / "scripts" / "stack.sh"))

spec = importlib.util.spec_from_file_location("hav_supervisor_under_test", SUPERVISOR)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
assert spec.loader is not None
spec.loader.exec_module(mod)


passes = 0
fails = 0
failures: list[str] = []


def check(name: str, cond: bool, detail: object = None) -> None:
    global passes, fails
    if cond:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        msg = f"{name}: {detail!r}" if detail is not None else name
        failures.append(msg)
        print(f"  FAIL  {msg}")


class Req:
    def __init__(self, token: str | None = None, confirm: str | None = None):
        headers = {}
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        if confirm is not None:
            headers["x-confirm"] = confirm
        self.headers = headers
        self.client = SimpleNamespace(host="127.0.0.1")

    async def is_disconnected(self) -> bool:
        return False


def ok_proc(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["docker", "compose"], 0, stdout=stdout, stderr=stderr)


def bad_proc(stderr: str = "bad") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["docker", "compose"], 2, stdout="", stderr=stderr)


def compose_default_services() -> list[dict[str, str]]:
    text = (REPO / "stack" / "docker-compose.yml").read_text(
        encoding="utf-8", errors="replace"
    )
    services: list[dict[str, str]] = []
    in_services = False
    current: str | None = None
    block: list[str] = []

    def flush() -> None:
        if not current:
            return
        body = "\n".join(block)
        if re.search(r"^\s{4}profiles:\s*\[", body, re.M):
            return
        container = re.search(r"^\s{4}container_name:\s*([^\s#]+)", body, re.M)
        services.append({
            "service": current,
            "container": container.group(1) if container else "",
        })

    for line in text.splitlines():
        if not in_services:
            if re.fullmatch(r"services:\s*", line):
                in_services = True
            continue
        if re.match(r"^[A-Za-z0-9_-]+:\s*$", line):
            break
        service = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*$", line)
        if service:
            flush()
            current = service.group(1)
            block = []
            continue
        if current is not None:
            block.append(line)
    flush()
    return services


async def expect_http(name: str, status: int, coro) -> None:
    try:
        await coro
    except HTTPException as exc:
        check(name, exc.status_code == status, {"expected": status, "actual": exc.status_code, "detail": exc.detail})
    else:
        check(name, False, "expected HTTPException")


async def main_async() -> None:
    print("\nroute_inventory_test")
    paths = {getattr(r, "path", None) for r in mod.app.routes}
    check("free-gpu route registered", "/api/stack/free-gpu" in paths)
    check("free_gpu compatibility route registered", "/api/stack/free_gpu" in paths)
    check("service logs route registered", "/api/services/{service}/logs" in paths)
    check("service stop route registered", "/api/services/{service}/stop" in paths)
    check("service restart route registered", "/api/services/{service}/restart" in paths)

    print("\nservice_allowlist_test")
    check("kokoro alias resolves to kokoro compose service",
          mod._resolve_service("kokoro")["compose"] == "kokoro-tts")
    check("parakeet alias resolves",
          mod._resolve_service("parakeet")["name"] == "wyoming-parakeet")
    try:
        mod._resolve_service("bad;service")
    except HTTPException as exc:
        check("unknown service rejected as 404", exc.status_code == 404)
    else:
        check("unknown service rejected as 404", False)
    default_services = compose_default_services()
    action_compose = {cfg["compose"] for cfg in mod.SERVICE_ACTIONS.values()}
    status_containers = {svc["container"] for svc in mod.SERVICES}
    check(
        "service actions cover every default compose service",
        {svc["service"] for svc in default_services} <= action_compose,
        sorted({svc["service"] for svc in default_services} - action_compose),
    )
    check(
        "status probes cover every default compose container",
        {svc["container"] for svc in default_services} <= status_containers,
        sorted({svc["container"] for svc in default_services} - status_containers),
    )
    check("chatterbox alias resolves",
          mod._resolve_service("chatterbox")["compose"] == "chatterbox-tts")
    check("intelligence service resolves",
          mod._resolve_service("intelligence")["compose"] == "intelligence")

    print("\nauth_and_confirm_test")
    await expect_http(
        "missing bearer rejected",
        401,
        mod.post_service_restart(Req(token=None), "vllm"),
    )
    await expect_http(
        "bad stop confirm rejected",
        400,
        mod.post_service_stop(Req(token=mod.STACK_TOKEN, confirm="stop-wrong"), "vllm"),
    )
    await expect_http(
        "bad free-gpu confirm rejected",
        400,
        mod.post_free_gpu(Req(token=mod.STACK_TOKEN, confirm="wrong")),
    )

    print("\ncompose_command_test")
    calls: list[tuple[list[str], float]] = []
    audits: list[tuple[str, str, str, int | None]] = []

    async def fake_run_compose(args, timeout_s=30.0):
      calls.append((list(args), timeout_s))
      return ok_proc("line1\nline2\n")

    def fake_audit(verb, caller_ip, task_id, exit_code):
      audits.append((verb, caller_ip, task_id, exit_code))

    mod._run_compose = fake_run_compose
    mod.write_audit = fake_audit

    logs = await mod.get_service_logs(Req(token=mod.STACK_TOKEN), "kokoro", n=2)
    check("service logs maps kokoro to compose kokoro-tts",
          calls[-1][0] == ["logs", "--tail", "2", "kokoro-tts"], calls[-1])
    check("service logs returns tail lines", logs["lines"] == ["line1", "line2"], logs)

    restart = await mod.post_service_restart(Req(token=mod.STACK_TOKEN), "vllm")
    check("service restart runs docker compose restart vllm",
          calls[-1][0] == ["restart", "vllm"], calls[-1])
    check("service restart response names vllm", restart["service"] == "vllm", restart)
    check("service restart audit recorded", audits[-1][0] == "restart:vllm", audits[-1])

    stop = await mod.post_service_stop(
        Req(token=mod.STACK_TOKEN, confirm="stop-vision-sidecar"),
        "vision-sidecar",
    )
    check("service stop runs docker compose stop vision-sidecar",
          calls[-1][0] == ["stop", "vision-sidecar"], calls[-1])
    check("service stop response names service", stop["service"] == "vision-sidecar", stop)

    free = await mod.post_free_gpu(Req(token=mod.STACK_TOKEN, confirm="free-gpu"))
    check("free-gpu stops only gpu-heavy compose services",
          calls[-1][0] == ["stop", "vllm"], calls[-1])
    check("free-gpu response lists vllm", free["services"] == ["vllm"], free)

    print("\nsupervisor_log_test")
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
        fh.write("a\nb\nc\n")
        audit_path = fh.name
    try:
        mod.AUDIT_LOG_PATH = audit_path
        res = await mod.get_service_logs(Req(token=mod.STACK_TOKEN), "supervisor", n=2)
        check("supervisor logs read audit tail", res["lines"] == ["b", "c"], res)
    finally:
        try:
            os.unlink(audit_path)
        except OSError:
            pass

    print("\ncompose_failure_test")
    async def fake_bad_compose(args, timeout_s=30.0):
        calls.append((list(args), timeout_s))
        return bad_proc("compose exploded")

    mod._run_compose = fake_bad_compose
    await expect_http(
        "compose failure maps to 500",
        500,
        mod.post_service_restart(Req(token=mod.STACK_TOKEN), "vllm"),
    )


def main() -> int:
    asyncio.run(main_async())
    print()
    if failures:
        for failure in failures:
            print("FAIL", failure)
        print(f"{passes} pass . {fails} fail")
        return 1
    print(f"{passes} pass . 0 fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
