"""The unit templates, the renderer and the brain health check."""
import configparser
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

OPS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, OPS)

import render_units  # noqa: E402

VALUES = json.load(open(os.path.join(OPS, "units", "values.example.json"), encoding="utf-8"))


def unit(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None, comment_prefixes=("#",))
    parser.optionxform = str
    with open(path, encoding="utf-8") as handle:
        parser.read_string(handle.read())
    return parser


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = {}
    for host in ("la", "jetson"):
        directory = tmp_path_factory.mktemp(host)
        for path in render_units.render_host(host, VALUES[host], str(directory), allow_placeholders=True):
            out[os.path.relpath(path, str(directory)).replace(os.sep, "/")] = path
    return out


def test_every_template_renders_with_no_token_left(rendered):
    assert sorted(rendered) == sorted([
        "lighting-brain.service", "lighting-brain-health.service", "den-ops-monitor.service",
        "victoria-ha-bridge.service", "perception-lab-managed.service.d/50-activity-loop.conf"])
    for path in rendered.values():
        assert not render_units.TOKEN.search(open(path, encoding="utf-8").read()), path


def test_render_refuses_missing_values_and_leftover_placeholders(tmp_path):
    with pytest.raises(ValueError, match="no value for"):
        render_units.render("ExecStart=@VENV@/bin/python", {})
    with pytest.raises(ValueError, match="placeholder"):
        render_units.render("ExecStart=@VENV@/bin/python", {"VENV": "<venv>"})
    with pytest.raises(ValueError, match="spans lines"):
        render_units.render("User=@U@", {"U": "a\nExecStart=/bin/sh"})
    assert render_units.render("User=@U@", {"U": "svc"}) == "User=svc"
    assert render_units.main(["--values", os.path.join(OPS, "units", "values.example.json"), "--host", "la",
                              "--out", str(tmp_path)]) == 1   # the example is all placeholders


def test_brain_unit_is_loopback_bounded_and_restarting(rendered):
    service = unit(rendered["lighting-brain.service"])["Service"]
    assert service["Restart"] == "always" and service["DynamicUser"] == "yes"
    assert service["MemoryMax"] == "256M" and service["CPUQuota"] == "50%" and service["TasksMax"] == "64"
    assert service["IPAddressDeny"] == "any" and service["IPAddressAllow"] == "localhost"
    environment = open(rendered["lighting-brain.service"], encoding="utf-8").read()
    assert "Environment=LIGHTING_BRAIN_BIND=127.0.0.1" in environment
    assert "LIGHTING_BRAIN_ALLOW_NON_LOOPBACK" not in environment
    for name in ("geometry.json", "tokens.json", "settings.json"):
        assert "LoadCredential=%s:" % name in environment
    assert unit(rendered["lighting-brain.service"])["Unit"]["StartLimitBurst"] == "10"


def test_jetson_units_have_back_off_limits_and_private_environment(rendered):
    bridge = unit(rendered["victoria-ha-bridge.service"])
    assert bridge["Service"]["Restart"] == "always" and bridge["Service"]["RestartSec"] == "5"
    assert bridge["Unit"]["StartLimitBurst"] == "10" and bridge["Unit"]["StartLimitIntervalSec"] == "600"
    assert bridge["Service"]["MemoryMax"] == "128M" and bridge["Service"]["CPUQuota"] == "25%"
    assert "User" not in bridge["Service"] and bridge["Install"]["WantedBy"] == "default.target"
    assert bridge["Service"]["EnvironmentFile"] and "${BRIDGE_CONFIG}" in bridge["Service"]["ExecStart"]
    loop = unit(rendered["perception-lab-managed.service.d/50-activity-loop.conf"])
    assert loop["Service"]["Restart"] == "on-failure" and loop["Unit"]["StartLimitBurst"] == "5"
    assert loop["Service"]["EnvironmentFile"] and loop["Service"]["MemoryMax"] and loop["Service"]["CPUQuota"]
    assert "ExecStart" not in loop["Service"]   # the loop lives in the existing managed process


def test_monitor_units_are_small_oneshots(rendered):
    monitor = unit(rendered["den-ops-monitor.service"])["Service"]
    assert monitor["Type"] == "oneshot" and monitor["MemoryMax"] == "96M"
    assert monitor["ExecStart"] == "/usr/bin/python3 -E -s -m den_ops check"
    for timer in glob.glob(os.path.join(OPS, "units", "*", "den-ops-monitor.timer")):
        assert unit(timer)["Timer"]["OnUnitActiveSec"] == "60s"


def test_no_private_addresses_usernames_or_tokens_in_ops_files():
    private = re.compile(r"\b(192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)\b"
                         r"|\.ts\.net\b|-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}")
    for path in glob.glob(os.path.join(OPS, "**", "*"), recursive=True):
        if os.path.isdir(path) or "__pycache__" in path or path.endswith((".pyc",)) or "pytest_cache" in path:
            continue
        text = open(path, encoding="utf-8").read()
        assert not private.search(text), (path, private.search(text).group(0))


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_health_check_restarts_only_the_brain_after_three_failures(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "systemctl.log"
    (bin_dir / "curl").write_text("#!/bin/sh\nexit ${FAKE_CURL_EXIT:-0}\n")
    (bin_dir / "systemctl").write_text('#!/bin/sh\necho "$@" >> "%s"\n' % str(log).replace("\\", "/"))
    for name in ("curl", "systemctl"):
        os.chmod(bin_dir / name, 0o755)
    script = os.path.join(OPS, "units", "la", "lighting-brain-healthcheck.sh")
    state = tmp_path / "failures"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
           "HEALTH_STATE": str(state).replace("\\", "/")}

    def run(code):
        return subprocess.run(["sh", script.replace("\\", "/")], env={**env, "FAKE_CURL_EXIT": str(code)},
                              capture_output=True, text=True).returncode

    assert run(0) == 0 and state.read_text().strip() == "0"
    assert run(7) == 1 and run(7) == 1 and not log.exists()
    assert run(0) == 0 and state.read_text().strip() == "0"          # a success resets the count
    assert [run(7) for _ in range(3)] == [1, 1, 1]
    assert log.read_text().split("\n")[0] == "restart lighting-brain.service"
    assert state.read_text().strip() == "0"
