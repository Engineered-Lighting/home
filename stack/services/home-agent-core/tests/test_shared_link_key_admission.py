import os
import sys

import pytest

from app import shared_link_key_admission as admission
from app.shared_link_commitments import SharedLinkCommitments


def _key(tmp_path, content, name="commitment_key"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_key_file_is_read_exactly_like_the_coordinator_profile(tmp_path):
    key = "ab" * 32
    commitments = admission.read_commitments(_key(tmp_path, (key + "\n").encode()), "shared-link-20260928")
    expected = SharedLinkCommitments(bytes.fromhex(key), key_id="shared-link-20260928")
    assert (commitments.key_id, commitments.fingerprint) == (expected.key_id, expected.fingerprint)
    assert key not in commitments.fingerprint


@pytest.mark.parametrize("content", [b"", b"AB" * 32, b"ab" * 31, b"ab" * 32 + b"\n\n", b"ab" * 33])
def test_malformed_key_files_are_refused(tmp_path, content):
    with pytest.raises(ValueError):
        admission.read_commitments(_key(tmp_path, content), "shared-link-20260928")


def test_relative_missing_and_linked_key_paths_are_refused(tmp_path):
    real = _key(tmp_path, b"ab" * 32)
    with pytest.raises(ValueError):
        admission.read_commitments("commitment_key", "shared-link-20260928")
    with pytest.raises(ValueError):
        admission.read_commitments(tmp_path / "absent", "shared-link-20260928")
    link = tmp_path / "linked"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        admission.read_commitments(link, "shared-link-20260928")


@pytest.mark.parametrize("key_id", ["", "Shared", "-lead", "x" * 65])
def test_key_reference_must_be_a_stable_identifier(tmp_path, key_id):
    with pytest.raises(ValueError):
        admission.read_commitments(_key(tmp_path, b"ab" * 32), key_id)


@pytest.mark.parametrize("argv", [
    ["admit", "--database-url-file", "/x"],
    ["admit", "--database-url-file", "/x", "--key-file", "/k"],
    ["status", "--database-url-file", "/x", "--key-id", "shared-link-20260928"],
])
def test_admission_requires_key_file_and_id_together(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["shared_link_key_admission", *argv])
    monkeypatch.setattr(admission, "_engine", lambda _path: pytest.fail("no database access expected"))
    with pytest.raises(ValueError):
        admission.main()


def test_admission_targets_the_scope_the_kernels_check():
    from pathlib import Path
    kernel = (Path(__file__).resolve().parents[1] / "alembic/versions/0040_shared_link_issuance_kernel.py").read_text(encoding="utf-8")
    assert f"admitted.scope='{admission.SCOPE}'" in kernel
    assert admission.TABLE == "privacy.shared_link_key_admission"
