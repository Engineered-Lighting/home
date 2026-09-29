"""The generated linking profiles must load in the real BFF provision loaders."""
from __future__ import annotations

import base64
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2]
REPO = DEPLOY.parents[1]
sys.path.insert(0, str(DEPLOY / "shared-preferences"))

import generate_link_profiles as generator  # noqa: E402

TAILNET = "taild52a15.ts.net"


def test_profiles_use_distinct_hostnames_and_fixed_private_endpoints():
    echo, victoria = generator.build_profiles(TAILNET)
    hosts = {
        "home": echo["personalMemoryHomeOrigins"][0],
        "echo_agent": victoria["echoOrigins"][0],
        "victoria": echo["victoriaBrowserOrigin"],
    }
    assert hosts == {
        "home": "https://home-app.taild52a15.ts.net",
        "echo_agent": "https://echo-agent.taild52a15.ts.net",
        "victoria": "https://victoria-agent.taild52a15.ts.net",
    }
    assert victoria["browserOrigin"] == hosts["victoria"]
    assert victoria["haOrigin"] == "https://home-app.taild52a15.ts.net:10001"
    assert victoria["redirectUri"].startswith(victoria["clientId"].rstrip("/"))
    assert echo["victoriaHandoff"]["endpoint"] == (
        "https://172.23.0.36:9451/internal/shared-identity/v1/victoria-handoff")
    assert victoria["browserListener"] == {"address": "172.23.0.36", "port": 9450}
    # The Victoria session DB is named so the runtime backup excludes it.
    assert Path(victoria["sessionDbPath"]).name == "sessions.sqlite"
    assert victoria["browserTls"]["privateKeyFile"] == "/tls/browser.key"
    # No secret material is ever inlined.
    text = json.dumps([echo, victoria])
    assert "credential\":" not in text.replace("credentialFile", "")


def test_tailnet_domain_is_validated():
    with pytest.raises(ValueError):
        generator.build_profiles("example.com")


def _stage(tmp_path: Path):
    echo_root, victoria_root = tmp_path / "echo", tmp_path / "victoria"
    for root, names in ((echo_root / "secrets", generator.ECHO_SECRETS),
                        (victoria_root / "secrets", generator.VICTORIA_SECRETS)):
        root.mkdir(parents=True)
        for name in names:
            value = (base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
                     if name.endswith("_key") else secrets.token_hex(32))
            (root / name).write_text(value, encoding="utf-8")
    (echo_root / "journals").mkdir()
    (victoria_root / "journals").mkdir()
    for name in ("tls/browser.crt", "tls/browser.key",
                 "config/ingress-tls/server.crt", "config/ingress-tls/server.key"):
        path = victoria_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    return echo_root, victoria_root


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_generated_profiles_pass_the_actual_bff_loaders(tmp_path):
    echo_root, victoria_root = _stage(tmp_path)
    echo, victoria = generator.build_profiles(
        TAILNET, echo_root=echo_root.as_posix(), victoria_secrets=(victoria_root / "secrets").as_posix(),
        victoria_config=(victoria_root / "config").as_posix(), victoria_journals=(victoria_root / "journals").as_posix(),
        victoria_tls=(victoria_root / "tls").as_posix())
    echo_file, victoria_file = tmp_path / "echo.json", tmp_path / "victoria.json"
    echo_file.write_bytes(generator._render(echo))
    victoria_file.write_bytes(generator._render(victoria))
    src = (REPO / "stack/services/home-agent-bff/src").as_uri()
    script = (
        f"const e=await import('{src}/echo-link-provision.mjs');"
        f"const v=await import('{src}/victoria-link-provision.mjs');"
        "const a=e.loadEchoLinkProvision(process.argv[1]);"
        "const b=v.loadVictoriaLinkProvision(process.argv[2]);"
        "console.log(JSON.stringify({home:a.personalMemoryHomeOrigins,memory:!!a.personalMemory,"
        "browser:b.browserOrigin,echo:[...b.echoOrigins],listener:b.browserListener}));"
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(echo_file), str(victoria_file)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert loaded == {
        "home": ["https://home-app.taild52a15.ts.net"], "memory": True,
        "browser": "https://victoria-agent.taild52a15.ts.net",
        "echo": ["https://echo-agent.taild52a15.ts.net"],
        "listener": {"address": "172.23.0.36", "port": 9450},
    }


def test_lighting_is_opt_in_and_leaves_existing_profiles_byte_identical():
    plain, _ = generator.build_profiles(TAILNET)
    assert "lighting" not in plain
    lit, _ = generator.build_profiles(TAILNET, lighting=True)
    assert lit["lighting"] == {"origin": "https://172.23.0.37:9448", "credentialFile": "/link/secrets/lighting_credential"}
    assert {k: v for k, v in lit.items() if k != "lighting"} == plain


def test_lighting_listener_profile_names_only_paths_and_the_serve_ports():
    profile = generator.build_lighting_profile(TAILNET, ["echo", "victoria"])
    assert profile["address"] == "172.23.0.37" and profile["port"] == 9448
    assert profile["homes"] == {
        "echo": {"origin": "https://home-app.taild52a15.ts.net:10000", "secret_file": "/run/secrets/echo_home_secret"},
        "victoria": {"origin": "https://home-app.taild52a15.ts.net:10001",
                     "secret_file": "/run/secrets/victoria_home_secret"}}
    assert profile["grant_lifetime_seconds"] == 365 * 86400
    # Never Core's own API login, which every Core listener reads from database_url.
    assert profile["database_url_file"] == "/run/secrets/lighting_database_url"
    assert generator.build_lighting_profile(TAILNET, ["echo"])["homes"].keys() == {"echo"}
    for homes in ([], ["paris"], ["echo", "echo"]):
        with pytest.raises(ValueError):
            generator.build_lighting_profile(TAILNET, homes)


def test_lighting_profile_passes_the_actual_core_loader(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlalchemy")
    sys.path.insert(0, str(REPO / "stack/services/home-agent-core"))
    try:
        from app.lighting_server import load_profile
    finally:
        sys.path.pop(0)
    secrets_dir, config, journals = tmp_path / "secrets", tmp_path / "config", tmp_path / "journals"
    for directory in (secrets_dir, config, journals):
        directory.mkdir()
    for name in ("credential", "action_key", "consent_key", "journal_key", "echo_home_secret", "victoria_home_secret"):
        (secrets_dir / name).write_bytes(secrets.token_hex(32).encode())
    (secrets_dir / "lighting_database_url").write_bytes(b"postgresql://home_agent_lighting@db/home")
    for name in ("server.crt", "server.key"):
        (config / name).write_bytes(b"placeholder")
    profile = generator.build_lighting_profile(TAILNET, ["echo", "victoria"], secrets=secrets_dir.as_posix(),
                                               config=config.as_posix(), journals=journals.as_posix())
    path = tmp_path / "listener.json"
    path.write_bytes(generator._render(profile))
    loaded = load_profile(str(path))
    assert loaded.address == "172.23.0.37" and set(loaded.homes) == {"echo", "victoria"}
    assert loaded.homes["victoria"].origin == "https://home-app.taild52a15.ts.net:10001"
    assert loaded.grant_lifetime_seconds == 365 * 86400


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_echo_profile_with_lighting_passes_the_actual_bff_loader(tmp_path):
    echo_root, _ = _stage(tmp_path)
    (echo_root / "secrets" / "lighting_credential").write_text(secrets.token_hex(32), encoding="utf-8")
    echo, _ = generator.build_profiles(TAILNET, echo_root=echo_root.as_posix(), lighting=True)
    echo_file = tmp_path / "echo.json"
    echo_file.write_bytes(generator._render(echo))
    src = (REPO / "stack/services/home-agent-bff/src").as_uri()
    script = (f"const e=await import('{src}/echo-link-provision.mjs');"
              "const a=e.loadEchoLinkProvision(process.argv[1]);"
              "console.log(JSON.stringify({origin:a.lighting.origin,credential:/^[a-f0-9]{64}$/.test(a.lighting.credential)}));")
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(echo_file)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"origin": "https://172.23.0.37:9448", "credential": True}
