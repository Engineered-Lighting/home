"""Fail-closed bearer authentication for the isolated research service."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
import stat
from typing import Mapping


class SecurityConfigurationError(RuntimeError):
    """Raised when credential configuration is ambiguous or unsafe."""


def load_api_token(env: Mapping[str, str] = os.environ) -> str | None:
    """Load one bearer without putting a file-backed secret in the environment.

    A missing token leaves the API contained (503) so health diagnostics can
    still explain why the research service is unavailable.  Direct and file
    forms are mutually exclusive.
    """

    direct = env.get("VIDEO_LABELER_API_TOKEN")
    filename = env.get("VIDEO_LABELER_API_TOKEN_FILE")
    if direct is not None and filename:
        raise SecurityConfigurationError("ambiguous video-labeler token source")
    if filename:
        path = Path(filename)
        try:
            if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise SecurityConfigurationError(
                    "video-labeler token file must be mode 0600 or stricter"
                )
            raw_value = path.read_text(encoding="utf-8")
        except SecurityConfigurationError:
            raise
        except OSError as exc:
            raise SecurityConfigurationError("video-labeler token file is unreadable") from exc
        if raw_value.endswith("\r\n"):
            value = raw_value[:-2]
        elif raw_value.endswith("\n"):
            value = raw_value[:-1]
        else:
            value = raw_value
        if "\n" in value or "\r" in value:
            raise SecurityConfigurationError("video-labeler token file has extra lines")
    elif direct is not None:
        value = direct
    else:
        return None
    if value != value.strip() or any(character.isspace() for character in value):
        raise SecurityConfigurationError("video-labeler token contains whitespace")
    if len(value) < 32:
        raise SecurityConfigurationError("video-labeler token must be at least 32 characters")
    return value


def bearer_is_valid(header: str | None, expected: str | None) -> bool:
    if expected is None or not isinstance(header, str):
        return False
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    supplied = header[len(prefix) :]
    return bool(supplied) and hmac.compare_digest(supplied, expected)
