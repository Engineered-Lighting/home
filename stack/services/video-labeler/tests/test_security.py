from __future__ import annotations

from pathlib import Path

import pytest

from videolabeler.security import (
    SecurityConfigurationError,
    bearer_is_valid,
    load_api_token,
)
from videolabeler.config import Config


def test_missing_token_is_contained() -> None:
    assert load_api_token({}) is None
    assert bearer_is_valid("Bearer anything", None) is False


def test_file_token_and_constant_time_comparison(tmp_path: Path) -> None:
    token = "a" * 64
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n", encoding="utf-8")
    assert load_api_token({"VIDEO_LABELER_API_TOKEN_FILE": str(token_file)}) == token
    assert bearer_is_valid(f"Bearer {token}", token)
    assert not bearer_is_valid(f"Bearer {token[:-1]}b", token)
    assert not bearer_is_valid(token, token)


@pytest.mark.parametrize(
    "env",
    [
        {"VIDEO_LABELER_API_TOKEN": "short"},
        {"VIDEO_LABELER_API_TOKEN": "a" * 32 + "\n"},
        {
            "VIDEO_LABELER_API_TOKEN": "a" * 32,
            "VIDEO_LABELER_API_TOKEN_FILE": "/unreachable",
        },
    ],
)
def test_unsafe_token_configuration_fails_closed(env: dict[str, str]) -> None:
    with pytest.raises(SecurityConfigurationError):
        load_api_token(env)


def test_token_file_rejects_multiple_lines(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(f"{'a' * 32}\n{'b' * 32}\n", encoding="utf-8")
    with pytest.raises(SecurityConfigurationError, match="extra lines"):
        load_api_token({"VIDEO_LABELER_API_TOKEN_FILE": str(token_file)})


def test_bind_host_is_fail_closed_and_container_honors_it() -> None:
    assert Config({}).bind_host == "127.0.0.1"
    with pytest.raises(ValueError, match="loopback"):
        Config({"BIND_HOST": "0.0.0.0"})
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert '--host \\"$host\\"' in dockerfile
    assert "--host 0.0.0.0" not in dockerfile
