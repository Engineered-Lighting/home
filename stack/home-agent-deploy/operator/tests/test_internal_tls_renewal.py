"""Renewal of the shared-preference internal TLS leaves, against throwaway CAs.

The fixture mirrors the live layout inspected on 2026-09-29: Core leaves are
root:10001 0440 in 0750 directories with keyUsage digitalSignature, and the
Victoria ingress leaf is 1000:1000 0444/0400 with keyEncipherment added. Here
every file belongs to the test user, so ownership is checked as preserved.
"""
from __future__ import annotations

import configparser
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

if os.name == "nt":
    pytest.skip("POSIX file-descriptor semantics required; run under WSL or Linux", allow_module_level=True)
if shutil.which("openssl") is None:
    pytest.skip("openssl required", allow_module_level=True)

DEPLOY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DEPLOY / "shared-preferences"))

import generate_link_profiles as generator  # noqa: E402
import renew_internal_tls as tool  # noqa: E402

CA_CN = "Home Shared Preferences Internal CA"
CORE_LEAVES = ("echo-preferences", "victoria-preferences", "echo-identity", "victoria-identity", "link-coordinator")


@pytest.fixture(autouse=True)
def _openssl(monkeypatch):
    monkeypatch.setattr(tool, "OPENSSL", shutil.which("openssl"))


def _run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True, cwd=cwd)


def make_ca(directory: Path, *, days: int = 365, cn: str = CA_CN) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", f"/CN={cn}",
         "-days", str(days), "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-keyout", str(directory / "ca.key"), "-out", str(directory / "ca.crt"))
    os.chmod(directory / "ca.key", 0o600)
    os.chmod(directory / "ca.crt", 0o600)


def issue(authority: Path, target: Path, *, cn: str, ip: str, usage: str, days: int = 90) -> None:
    """Issue a leaf the way the originals were, independently of the tool."""
    work = target.parent / f".issue-{target.name}"
    work.mkdir()
    (work / "ext.cnf").write_text(f"subjectAltName={ip}\nextendedKeyUsage=serverAuth\nkeyUsage=critical,{usage}\n"
                                  "basicConstraints=critical,CA:FALSE\n")
    _run("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", f"/CN={cn}",
         "-keyout", str(work / "server.key"), "-out", str(work / "server.csr"))
    _run("x509", "-req", "-in", str(work / "server.csr"), "-CA", str(authority / "ca.crt"),
         "-CAkey", str(authority / "ca.key"), "-set_serial", str(int.from_bytes(os.urandom(16), "big")),
         "-days", str(days), "-extfile", str(work / "ext.cnf"), "-out", str(work / "server.crt"))
    for name in ("server.crt", "server.key"):
        path = target / name
        if path.exists():
            os.chmod(path, 0o600)
            path.unlink()
        shutil.copyfile(work / name, path)
    shutil.rmtree(work)


def set_modes(root: Path, leaf: tool.Leaf) -> None:
    target = root / leaf.directory
    if leaf.name == "victoria-link-ingress":
        os.chmod(target / "server.crt", 0o444)
        os.chmod(target / "server.key", 0o400)
        os.chmod(target, 0o700)
    else:
        os.chmod(target / "server.crt", 0o440)
        os.chmod(target / "server.key", 0o440)
        os.chmod(target, 0o750)


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "prepared"
    root.mkdir(mode=0o700)
    make_ca(root / "authority")
    ca = (root / "authority/ca.crt").read_bytes()
    for leaf in tool.LEAVES.values():
        if leaf.optional:
            continue
        target = root / leaf.directory
        target.mkdir(parents=True)
        issue(root / "authority", target, cn=leaf.common_name, ip=f"IP:{leaf.address}", usage=",".join(leaf.key_usage))
        if leaf.name in CORE_LEAVES:
            (target / "ca.crt").write_bytes(ca)
            (target / "listener.json").write_text("{}")
        set_modes(root, leaf)
    for relative in tool.CA_TRUST_COPIES:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(ca)
    return root


_CONTEXT = tool.Context


def ctx(root: Path) -> tool.Context:
    return _CONTEXT(root, trusted_uid=os.getuid(), check_parents=False)


def snapshot(root: Path) -> dict:
    """Every file outside the renewal store, with its bytes and metadata."""
    state = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(tool.STORE):
            continue
        details = path.lstat()
        state[relative] = (details.st_uid, details.st_gid, stat.S_IMODE(details.st_mode),
                           path.read_bytes() if path.is_file() else None)
    return state


def info(path: Path) -> tool.CertInfo:
    return tool.inspect(path.read_bytes())


def verify(root: Path, path: Path) -> None:
    _run("verify", "-CAfile", str(root / "authority/ca.crt"), "-purpose", "sslserver", str(path))


# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(n for n in tool.LEAVES if not tool.LEAVES[n].optional))
def test_renew_reissues_with_the_same_subject_san_and_key_policy(prepared, name):
    leaf = tool.LEAVES[name]
    target = prepared / leaf.directory
    before = snapshot(prepared)
    old = info(target / "server.crt")
    old_key = (target / "server.key").read_bytes()

    result = tool.renew(ctx(prepared), name)

    new = info(target / "server.crt")
    verify(prepared, target / "server.crt")
    assert (new.subject, new.sans, new.key_usage, new.eku, new.curve) == (
        old.subject, old.sans, old.key_usage, old.eku, old.curve)
    assert new.subject == f"CN={leaf.common_name}" and new.sans == (f"IP:{leaf.address}",)
    assert new.serial != old.serial and new.public_key != old.public_key
    assert tool.key_matches((target / "server.key").read_bytes(), new)
    lifetime = new.not_after - new.not_before
    assert timedelta(days=89, hours=23) < lifetime <= timedelta(days=90, minutes=1)
    # Owner and mode of the directory and both files are exactly as before.
    after = snapshot(prepared)
    for relative in (leaf.directory, f"{leaf.directory}/server.crt", f"{leaf.directory}/server.key"):
        assert after[relative][:3] == before[relative][:3]
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {f"{leaf.directory}/server.crt", f"{leaf.directory}/server.key"}
    assert not [p for p in target.iterdir() if p.name.startswith(".")]
    # The displaced pair is kept byte-for-byte, root-only, with a receipt.
    saved = prepared / tool.STORE / name / result["rollback_stamp"]
    assert (saved / "server.key").read_bytes() == old_key
    assert info(saved / "server.crt").sha256 == old.sha256
    assert stat.S_IMODE((saved / "server.key").stat().st_mode) == 0o400
    receipt = json.loads((saved / "receipt.json").read_text())
    assert receipt["displaced"]["sha256"] == old.sha256 and receipt["installed"]["sha256"] == new.sha256
    assert "PRIVATE KEY" not in json.dumps(result) + json.dumps(receipt)
    assert result["ends_sessions"] is (name == "victoria-link-ingress")


def test_renew_output_never_contains_key_material(prepared, capsys, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(tool, "Context", lambda root: ctx(root))
    assert tool.main(["renew", "--root", str(prepared), "--leaf", "echo-identity"]) == 0
    out, err = capsys.readouterr()
    assert "PRIVATE KEY" not in out + err and json.loads(out)["outcome"] == "renewed"


def test_refuses_when_the_ca_cannot_back_a_full_leaf_lifetime(tmp_path, prepared):
    # Replace the fixture CA with one expiring in 100 days, and reissue under it.
    shutil.rmtree(prepared / "authority")
    make_ca(prepared / "authority", days=100)
    ca = (prepared / "authority/ca.crt").read_bytes()
    for leaf in tool.LEAVES.values():
        if leaf.optional:
            continue
        target = prepared / leaf.directory
        issue(prepared / "authority", target, cn=leaf.common_name, ip=f"IP:{leaf.address}", usage=",".join(leaf.key_usage))
        set_modes(prepared, leaf)
        if (target / "ca.crt").exists():
            os.chmod(target / "ca.crt", 0o600)
            (target / "ca.crt").write_bytes(ca)
    for relative in tool.CA_TRUST_COPIES:
        (prepared / relative).write_bytes(ca)
    before = snapshot(prepared)
    with pytest.raises(tool.Refused, match="Rotate the CA first"):
        tool.renew(ctx(prepared), "echo-identity")
    assert snapshot(prepared) == before
    report = tool.check_expiry(ctx(prepared))
    assert any("internal CA expires in 99 days" in w for w in report["warnings"])


def test_never_accepts_a_silently_replaced_ca(tmp_path, prepared):
    # Someone swapped authority/ca.crt and ca.key for a fresh CA with the same name.
    make_ca(tmp_path / "other")
    for name in ("ca.crt", "ca.key"):
        os.chmod(prepared / "authority" / name, 0o600)
        shutil.copyfile(tmp_path / "other" / name, prepared / "authority" / name)
    before = snapshot(prepared)
    with pytest.raises(tool.Refused, match="client trust differs"):
        tool.renew(ctx(prepared), "echo-identity")
    # Even with every trust copy updated too, the live leaf no longer verifies.
    new_ca = (prepared / "authority/ca.crt").read_bytes()
    for path in prepared.rglob("*ca.crt"):
        if "authority" not in path.parts:
            os.chmod(path, 0o600)
            path.write_bytes(new_ca)
    with pytest.raises(tool.Refused, match="does not verify against authority/ca.crt"):
        tool.renew(ctx(prepared), "echo-identity")
    assert (prepared / "echo-identity/config/server.crt").read_bytes() == before["echo-identity/config/server.crt"][3]


def test_refuses_a_mismatched_ca_key(tmp_path, prepared):
    make_ca(tmp_path / "other")
    os.chmod(prepared / "authority/ca.key", 0o600)
    shutil.copyfile(tmp_path / "other/ca.key", prepared / "authority/ca.key")
    with pytest.raises(tool.Refused, match="ca.key does not match"):
        tool.renew(ctx(prepared), "link-coordinator")


def test_refuses_a_leaf_that_drifted_from_the_reviewed_policy(prepared):
    leaf = tool.LEAVES["echo-identity"]
    issue(prepared / "authority", prepared / leaf.directory, cn=leaf.common_name, ip="IP:172.23.0.99",
          usage="digitalSignature")
    set_modes(prepared, leaf)
    before = snapshot(prepared)
    with pytest.raises(tool.Refused, match="subjectAltName"):
        tool.renew(ctx(prepared), "echo-identity")
    assert snapshot(prepared) == before


def test_refuses_symlinked_leaf_directories_and_files(prepared, tmp_path):
    target = prepared / "victoria-bff/config/ingress-tls"
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(target, elsewhere)
    shutil.rmtree(target)
    target.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        tool.renew(ctx(prepared), "victoria-link-ingress")
    target.unlink()
    shutil.copytree(elsewhere, target)
    (target / "server.crt").unlink()
    (target / "server.crt").symlink_to(elsewhere / "server.crt")
    with pytest.raises(OSError):
        tool.renew(ctx(prepared), "victoria-link-ingress")


def test_refuses_world_readable_keys(prepared):
    os.chmod(prepared / "echo-identity/config/server.key", 0o444)
    with pytest.raises(tool.Unsafe, match="accessible to others"):
        tool.renew(ctx(prepared), "echo-identity")


def test_second_renew_waits_for_restart_and_probe(prepared):
    tool.renew(ctx(prepared), "echo-identity")
    with pytest.raises(tool.Refused, match="awaits a listener restart"):
        tool.renew(ctx(prepared), "echo-identity")


def test_rollback_restores_the_exact_previous_leaf(prepared):
    leaf = tool.LEAVES["victoria-link-ingress"]
    target = prepared / leaf.directory
    before = snapshot(prepared)
    renewed = tool.renew(ctx(prepared), leaf.name)
    renewed_crt = (target / "server.crt").read_bytes()

    result = tool.rollback(ctx(prepared), leaf.name, renewed["rollback_stamp"])

    assert snapshot(prepared) == before
    assert result["outcome"] == "rolled_back"
    # The renewed leaf is kept too, so the rollback is itself reversible.
    saved = prepared / tool.STORE / leaf.name / result["rollback_stamp"]
    assert (saved / "server.crt").read_bytes() == renewed_crt
    forward = tool.rollback(ctx(prepared), leaf.name, result["rollback_stamp"])
    assert (target / "server.crt").read_bytes() == renewed_crt and forward["outcome"] == "rolled_back"


def test_rollback_repairs_a_torn_pair_after_an_interrupted_swap(prepared, monkeypatch):
    target = prepared / "echo-identity/config"
    before = snapshot(prepared)
    real_rename, calls = os.rename, []

    def rename(src, dst, **kwargs):
        calls.append(dst)
        if dst == "server.crt":
            raise OSError("interrupted")
        return real_rename(src, dst, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(OSError):
        tool.renew(ctx(prepared), "echo-identity")
    monkeypatch.setattr(os, "rename", real_rename)
    assert "server.key" in calls
    assert not tool.key_matches((target / "server.key").read_bytes(), info(target / "server.crt"))
    # Only the public certificate is left staged, and the alert reports it.
    assert sorted(p.name for p in target.iterdir() if p.name.startswith(".")) == [".server.crt.renew"]
    warnings = tool.check_expiry(ctx(prepared))["warnings"]
    assert any(w.startswith("echo-identity: interrupted change") for w in warnings)
    stamp = next((prepared / tool.STORE / "echo-identity").iterdir()).name
    tool.rollback(ctx(prepared), "echo-identity", stamp)
    assert snapshot(prepared) == before


def test_failure_before_any_rename_leaves_nothing_pending(prepared, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    before = snapshot(prepared)
    monkeypatch.setattr(os, "rename", fail)
    with pytest.raises(OSError):
        tool.renew(ctx(prepared), "echo-preferences")
    monkeypatch.undo()
    monkeypatch.setattr(tool, "OPENSSL", shutil.which("openssl"))
    assert snapshot(prepared) == before  # no staged key left behind
    assert tool.check_expiry(ctx(prepared))["leaves"]["echo-preferences"].get("restart_pending_since") is None


def test_rollback_refuses_an_expired_or_tampered_saved_leaf(prepared):
    renewed = tool.renew(ctx(prepared), "echo-identity")
    with pytest.raises(tool.Refused, match="has expired"):
        tool.rollback(ctx(prepared), "echo-identity", renewed["rollback_stamp"],
                      now=datetime.now(timezone.utc) + timedelta(days=91))
    with pytest.raises(tool.Unsafe):
        tool.rollback(ctx(prepared), "echo-identity", "../../authority")


# --------------------------------------------------------------------------
# check-expiry


def test_check_expiry_is_quiet_for_fresh_leaves_and_skips_absent_lighting(prepared):
    report = tool.check_expiry(ctx(prepared))
    assert report["warnings"] == []
    assert report["leaves"]["lighting"] == {"status": "absent"}
    assert {entry["status"] for name, entry in report["leaves"].items() if name != "lighting"} == {"ok"}


def test_check_expiry_warns_thirty_days_before_a_leaf_expires(prepared):
    leaf = tool.LEAVES["link-coordinator"]
    issue(prepared / "authority", prepared / leaf.directory, cn=leaf.common_name, ip=f"IP:{leaf.address}",
          usage="digitalSignature", days=29)
    set_modes(prepared, leaf)
    report = tool.check_expiry(ctx(prepared))
    assert report["leaves"]["link-coordinator"]["status"] == "warn"
    assert [w for w in report["warnings"] if w.startswith("link-coordinator: live leaf expires in 28 days")]
    later = datetime.now(timezone.utc) + timedelta(days=61)
    assert any(w.startswith("echo-identity") for w in tool.check_expiry(ctx(prepared), now=later)["warnings"])


def test_check_expiry_counts_the_displaced_leaf_until_the_listener_restarts(prepared):
    leaf = tool.LEAVES["echo-identity"]
    issue(prepared / "authority", prepared / leaf.directory, cn=leaf.common_name, ip=f"IP:{leaf.address}",
          usage="digitalSignature", days=20)
    set_modes(prepared, leaf)
    tool.renew(ctx(prepared), leaf.name)
    entry = tool.check_expiry(ctx(prepared))["leaves"][leaf.name]
    assert entry["status"] == "warn" and "restart_pending_since" in entry
    assert entry["days_left"] < 20 < (datetime.fromisoformat(entry["not_after"][:-1] + "+00:00")
                                      - datetime.now(timezone.utc)).days


def test_check_expiry_warns_on_missing_leaf_and_trust_drift(prepared):
    (prepared / "victoria-identity/config/server.crt").unlink()
    os.chmod(prepared / "echo-bff/config/internal-ca.crt", 0o600)
    (prepared / "echo-bff/config/internal-ca.crt").write_text("stale")
    warnings = tool.check_expiry(ctx(prepared))["warnings"]
    assert "victoria-identity: server.crt missing" in warnings
    assert any("echo-bff/config/internal-ca.crt differs" in w for w in warnings)


def test_check_expiry_exit_status_drives_the_systemd_alert(prepared, capsys, monkeypatch):
    monkeypatch.setattr(tool, "Context", lambda root: ctx(root))
    assert tool.main(["check-expiry", "--root", str(prepared)]) == 0
    assert tool.main(["check-expiry", "--root", str(prepared), "--warn-days", "120"]) == 1
    assert "internal TLS: echo-identity: live leaf expires" in capsys.readouterr().err


# --------------------------------------------------------------------------
# probe


def _fake_docker(monkeypatch, *, running=("home-agent-bff-1", "home-shared-preferences-echo-identity-1"),
                 served: str | None = None, authorized=True, calls=None):
    def docker(*args):
        if calls is not None:
            calls.append(args)
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, "true\n" if args[-1] in running else "false\n", "")
        body = json.dumps({"authorized": authorized, "sha256": served})
        return subprocess.CompletedProcess(args, 0, body, "")

    monkeypatch.setattr(tool, "_docker", docker)


def _colon(hexdigest: str) -> str:
    return ":".join(hexdigest[i:i + 2] for i in range(0, 64, 2)).upper()


def test_probe_clears_pending_only_when_the_new_leaf_is_served(prepared, monkeypatch):
    old = info(prepared / "echo-identity/config/server.crt")
    tool.renew(ctx(prepared), "echo-identity")
    new = info(prepared / "echo-identity/config/server.crt")
    _fake_docker(monkeypatch, served=_colon(old.sha256))
    with pytest.raises(tool.Refused, match="still serves a different leaf"):
        tool.probe(ctx(prepared), "echo-identity")
    assert "restart_pending_since" in tool.check_expiry(ctx(prepared))["leaves"]["echo-identity"]
    calls = []
    _fake_docker(monkeypatch, served=_colon(new.sha256), calls=calls)
    result = tool.probe(ctx(prepared), "echo-identity")
    assert result["outcome"] == "serving" and result["pending_cleared"] is True
    assert ("exec", "home-agent-bff-1", "node", "-e", tool.PROBE_JS, "172.23.0.33", "9448") in calls
    assert "restart_pending_since" not in tool.check_expiry(ctx(prepared))["leaves"]["echo-identity"]


def test_probe_refuses_an_untrusted_leaf(prepared, monkeypatch):
    served = info(prepared / "echo-identity/config/server.crt").sha256
    _fake_docker(monkeypatch, served=_colon(served), authorized=False)
    with pytest.raises(tool.Refused, match="does not trust"):
        tool.probe(ctx(prepared), "echo-identity")


def test_probe_uses_the_real_client_for_each_listener(prepared, monkeypatch):
    calls = []
    served = info(prepared / "victoria-identity/config/server.crt").sha256
    _fake_docker(monkeypatch, running=(tool.VICTORIA_LINK, "home-shared-preferences-victoria-identity-1"),
                 served=_colon(served), calls=calls)
    tool.probe(ctx(prepared), "victoria-identity")
    assert calls[-1][:2] == ("exec", tool.VICTORIA_LINK)
    served = info(prepared / "victoria-bff/config/ingress-tls/server.crt").sha256
    _fake_docker(monkeypatch, running=(tool.BFF, tool.VICTORIA_LINK), served=_colon(served), calls=calls)
    tool.probe(ctx(prepared), "victoria-link-ingress")
    assert calls[-1][1:] == (tool.BFF, "node", "-e", tool.PROBE_JS, "172.23.0.36", "9451")


def test_probe_of_a_stopped_listener_defers_to_its_next_start(prepared, monkeypatch):
    tool.renew(ctx(prepared), "victoria-preferences")
    _fake_docker(monkeypatch, running=("home-agent-bff-1",))
    result = tool.probe(ctx(prepared), "victoria-preferences")
    assert result == {"outcome": "listener_not_running", "leaf": "victoria-preferences", "pending_cleared": True,
                      "note": "it loads the on-disk leaf when it next starts"}


# --------------------------------------------------------------------------
# Contracts with the generator and the host units


def test_leaf_table_matches_the_generator_and_compose():
    for name in ("echo-preferences", "echo-identity", "victoria-identity", "link-coordinator", "lighting"):
        assert tool.LEAVES[name].address == generator.LISTENERS[name]
        assert tool.LEAVES[name].port == generator.CORE_PORT
    ingress = tool.LEAVES["victoria-link-ingress"]
    assert (ingress.address, ingress.port) == (generator.VICTORIA_LINK_IP, generator.INGRESS_PORT)
    assert ingress.directory == "victoria-bff/config/ingress-tls"
    compose = json.loads((DEPLOY / "shared-preferences/compose.json").read_text())
    for name in CORE_LEAVES:
        assert "/config" in [v["target"] for v in compose["services"][name]["volumes"]]
    assert compose["name"] == "home-shared-preferences"
    assert tool.CA_MIN_DAYS == tool.LEAF_DAYS + tool.WARN_DAYS == 120


def test_expiry_units_follow_the_host_alert_convention():
    systemd = DEPLOY / "operator/systemd"
    service = configparser.ConfigParser(strict=False, interpolation=None)
    service.optionxform = str
    service.read(systemd / "home-agent-internal-tls-expiry.service")
    assert service["Unit"]["OnFailure"] == "ntfy-alert@%n.service"
    assert service["Service"]["Type"] == "oneshot"
    # The streak reset writes /run/ntfy-alert, so only it runs outside the sandbox.
    assert service["Service"]["ExecStartPost"] == "+/usr/local/sbin/ntfy-alert-reset %n"
    assert service["Service"]["ExecStart"] == (
        "/usr/bin/python3 -I -s /usr/local/libexec/home-agent/shared-preferences/renew_internal_tls.py "
        "check-expiry --root /srv/home-agent/shared-preferences/prepared-20260928")
    assert service["Service"]["ProtectSystem"] == "strict"
    assert "ReadWritePaths" not in service["Service"]
    timer = configparser.ConfigParser(strict=False, interpolation=None)
    timer.optionxform = str
    timer.read(systemd / "home-agent-internal-tls-expiry.timer")
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == "home-agent-internal-tls-expiry.service"


def test_check_expiry_warns_when_a_change_is_never_confirmed_live(prepared):
    tool.renew(ctx(prepared), "link-coordinator")
    assert tool.check_expiry(ctx(prepared))["warnings"] == []
    later = datetime.now(timezone.utc) + timedelta(days=2)
    warnings = tool.check_expiry(ctx(prepared), now=later)["warnings"]
    assert any(w.startswith("link-coordinator: changed on disk") and "restart home-shared-preferences-link-coordinator-1"
               in w for w in warnings)


def test_probe_refuses_when_docker_cannot_say_whether_the_listener_runs(prepared, monkeypatch):
    tool.renew(ctx(prepared), "echo-identity")
    monkeypatch.setattr(tool, "_docker", lambda *a: subprocess.CompletedProcess(a, 1, "", "daemon unreachable"))
    with pytest.raises(tool.Refused, match="cannot tell whether"):
        tool.probe(ctx(prepared), "echo-identity")
    assert "restart_pending_since" in tool.check_expiry(ctx(prepared))["leaves"]["echo-identity"]


def test_a_planted_fifo_is_refused_without_blocking(prepared):
    target = prepared / "victoria-bff/config/ingress-tls"
    (target / "server.crt").unlink()
    os.mkfifo(target / "server.crt")
    with pytest.raises(tool.Unsafe, match="not a regular file"):
        tool.renew(ctx(prepared), "victoria-link-ingress")
    with pytest.raises(tool.Unsafe):
        tool.check_expiry(ctx(prepared))


def test_a_planted_staging_symlink_is_replaced_not_followed(prepared, tmp_path):
    target = prepared / "victoria-bff/config/ingress-tls"
    decoy = tmp_path / "decoy"
    decoy.write_text("untouched")
    (target / ".server.key.renew").symlink_to(decoy)
    tool.renew(ctx(prepared), "victoria-link-ingress")
    assert decoy.read_text() == "untouched"
    assert not [p for p in target.iterdir() if p.name.startswith(".")]
    assert tool.key_matches((target / "server.key").read_bytes(), info(target / "server.crt"))
