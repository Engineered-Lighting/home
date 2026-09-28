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
