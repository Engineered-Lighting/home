"""Delivery to the lighting brain over HTTPS, standard library only."""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

from . import __version__
from .core import PostResult

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpTransport:
    def __init__(
        self,
        events_url: str,
        token: str,
        *,
        timeout_s: float = 5.0,
        ca_file: str | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._url = events_url
        self._token = token
        self._timeout = timeout_s
        self._context = ssl.create_default_context(cafile=ca_file) if events_url.startswith("https:") else None
        self._open = opener or urllib.request.urlopen

    def post(self, body: dict[str, Any]) -> PostResult:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": f"victoria-ha-bridge/{__version__}",
            },
        )
        try:
            with self._open(request, timeout=self._timeout, context=self._context) as response:
                return PostResult(ok=True, status=response.status)
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace") if exc.fp else ""
            return PostResult(ok=False, status=exc.code, retryable=exc.code in RETRYABLE_STATUS, detail=detail)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return PostResult(ok=False, retryable=True, detail=f"{type(exc).__name__}: {reason}")


class DryRunTransport:
    """Prints each batch as one JSON line instead of sending it."""

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream or sys.stdout

    def post(self, body: dict[str, Any]) -> PostResult:
        print(json.dumps(body, separators=(",", ":")), file=self._stream, flush=True)
        return PostResult(ok=True, status=None)
