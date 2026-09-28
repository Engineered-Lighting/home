import base64
import hashlib
import hmac
import os
import re
from pathlib import Path

import pytest

from app import shared_preference_roles as roles

ALEMBIC = Path(__file__).resolve().parents[1] / "alembic/versions"


def test_scram_verifier_matches_postgres_format_and_proves_the_password():
    salt = b"s" * 16
    verifier = roles.scram_verifier("correct horse", salt=salt)
    match = re.fullmatch(r"SCRAM-SHA-256\$4096:([^$]+)\$([^:]+):(.+)", verifier)
    assert match and base64.b64decode(match.group(1)) == salt
    salted = hashlib.pbkdf2_hmac("sha256", b"correct horse", salt, 4096)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    assert base64.b64decode(match.group(2)) == hashlib.sha256(client_key).digest()
    assert "correct horse" not in verifier
    assert roles.scram_verifier("correct horse") != roles.scram_verifier("correct horse")


def test_every_granted_kernel_function_exists_in_the_migrations():
    sources = "\n".join(path.read_text(encoding="utf-8") for path in ALEMBIC.glob("00*.py"))
    for _limit, functions in roles.ROLES.values():
        for signature in functions:
            name = signature.split("(")[0].split(".")[1]
            assert f"identity.{name}" in sources, signature


def test_connection_limits_match_the_admitted_budget():
    limits = {role: limit for role, (limit, _functions) in roles.ROLES.items()}
    assert limits[roles.COORDINATOR] == 2
    assert all(limits[role] == 2 for role in roles.PROOF)
    assert all(limits[role] == 1 for role in roles.SESSION)
    assert roles.ROLES[roles.CONSENT] == (2, ())


def _staged(tmp_path, **overrides):
    for index, role in enumerate(roles.ROLES):
        (tmp_path / f"{role}.password").write_text(overrides.get(role, f"{index:02d}" + "a" * 62), encoding="utf-8")
    return tmp_path


def test_passwords_are_read_only_from_regular_distinct_staged_files(tmp_path):
    assert set(roles.read_passwords(_staged(tmp_path))) == set(roles.ROLES)


@pytest.mark.parametrize("problem", ["missing", "short", "duplicate", "symlink"])
def test_unsafe_password_staging_is_rejected(tmp_path, problem):
    _staged(tmp_path)
    target = tmp_path / f"{roles.COORDINATOR}.password"
    if problem == "missing":
        target.unlink()
    elif problem == "short":
        target.write_text("tooshort", encoding="utf-8")
    elif problem == "duplicate":
        target.write_text((tmp_path / f"{roles.PROOF[0]}.password").read_text(), encoding="utf-8")
    else:
        other = tmp_path / "elsewhere"
        other.write_text("b" * 64, encoding="utf-8")
        target.unlink()
        try:
            os.symlink(other, target)
        except OSError:
            pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        roles.read_passwords(tmp_path)


def test_password_directory_is_required_only_for_activation(monkeypatch, tmp_path):
    url = tmp_path / "url"
    url.write_text("postgresql://home_agent_owner@db/home_agent", encoding="utf-8")
    for argv in (["activate", "--database-url-file", str(url)],
                 ["status", "--database-url-file", str(url), "--password-dir", str(tmp_path)]):
        monkeypatch.setattr("sys.argv", ["shared_preference_roles", *argv])
        with pytest.raises(ValueError):
            roles.main()
