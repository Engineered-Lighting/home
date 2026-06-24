"""Minimal CDP client to inspect the running Home Tauri app.

Connects to webView2's remote debugging port (9222), opens the Home page
target's WebSocket, then either:
  - dump  : print document.body.innerText (last N chars) — easiest way to see
            recent chat events + [dbg] diagnostics.
  - tail  : enable Runtime/Console/Network domains and stream messages for
            DURATION seconds — captures console.log, console.error, and any
            HTTP requests fired by the app.

Usage:
  python tools/cdp-inspect.py dump
  python tools/cdp-inspect.py tail 20
  python tools/cdp-inspect.py eval "window.__INFLIGHT_LAST_STUB"
"""
from __future__ import annotations
import json
import sys
import time
import urllib.request
# Force stdout to utf-8 so the chat's unicode glyphs (◆ etc.) don't crash on cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from typing import Any

# stdlib WebSocket would be simpler; use websocket-client if installed,
# else fall back to a thin wrapper around socket. We try websocket first.
try:
    import websocket  # type: ignore
except ImportError:
    print("pip install websocket-client", file=sys.stderr)
    sys.exit(2)


def get_target() -> str:
    raw = urllib.request.urlopen("http://localhost:9222/json/list", timeout=5).read()
    targets = json.loads(raw)
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise SystemExit("no page targets")
    return pages[0]["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=30)
        self._id = 0

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))
        # Wait for matching id (may receive event messages in between).
        while True:
            raw = self.ws.recv()
            m = json.loads(raw)
            if m.get("id") == self._id:
                return m
            # Ignore events while waiting for the reply.

    def recv_event(self, timeout: float = 1.0) -> dict | None:
        self.ws.settimeout(timeout)
        try:
            raw = self.ws.recv()
            return json.loads(raw)
        except Exception:
            return None
        finally:
            self.ws.settimeout(30)


def cmd_dump(cdp: CDP, tail: int = 4000) -> None:
    r = cdp.send("Runtime.evaluate", {
        "expression": "document.body.innerText",
        "returnByValue": True,
    })
    val = r.get("result", {}).get("result", {}).get("value", "")
    if len(val) > tail:
        val = val[-tail:]
    print(val)


def cmd_eval(cdp: CDP, expr: str) -> None:
    r = cdp.send("Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": True,
    })
    res = r.get("result", {})
    print(json.dumps(res, indent=2, default=str))


def cmd_tail(cdp: CDP, duration_s: int) -> None:
    cdp.send("Runtime.enable")
    cdp.send("Console.enable")
    cdp.send("Network.enable")
    print(f"# tailing console + network for {duration_s}s — exercise the app now",
          file=sys.stderr)
    end = time.time() + duration_s
    while time.time() < end:
        evt = cdp.recv_event(timeout=0.5)
        if evt is None:
            continue
        m = evt.get("method", "")
        p = evt.get("params", {})
        if m == "Runtime.consoleAPICalled":
            t = p.get("type", "log")
            args = p.get("args", [])
            parts = []
            for a in args:
                if a.get("type") == "string":
                    parts.append(a.get("value", ""))
                elif "value" in a:
                    parts.append(json.dumps(a["value"])[:200])
                elif "description" in a:
                    parts.append(a["description"][:200])
                else:
                    parts.append(str(a))
            print(f"[{t:>5}] " + " ".join(parts))
        elif m == "Console.messageAdded":
            msg = p.get("message", {})
            print(f"[{msg.get('level','?'):>5}] {msg.get('text','')}")
        elif m == "Network.requestWillBeSent":
            req = p.get("request", {})
            url = req.get("url", "")
            if any(s in url for s in ("/reason", "/describe", ":8091", ":8123")):
                print(f"[ net→] {req.get('method')} {url[:160]}")
        elif m == "Network.responseReceived":
            resp = p.get("response", {})
            url = resp.get("url", "")
            if any(s in url for s in ("/reason", "/describe", ":8091", ":8123")):
                print(f"[ net←] {resp.get('status')} {url[:160]}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    target = get_target()
    cdp = CDP(target)
    op = sys.argv[1]
    if op == "dump":
        cmd_dump(cdp)
    elif op == "tail":
        dur = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        cmd_tail(cdp, dur)
    elif op == "eval":
        cmd_eval(cdp, sys.argv[2])
    else:
        print(f"unknown command: {op}")


if __name__ == "__main__":
    main()
