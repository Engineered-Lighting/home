from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import struct
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[4]
HARNESS = ROOT / "stack/home-agent-deploy/operator/haos_restore"
SCRIPT = HARNESS / "haos_restore_drill.sh"
COMPILER_PATH = HARNESS / "policy_compiler.py"
REFRESHER_PATH = HARNESS / "policy_refresher.py"
EXACT_DNS_PATH = HARNESS / "exact_dns_proxy.py"
NFT_CONTRACT_PATH = HARNESS / "nft_contract.py"
BOOTSTRAP_POLICY = HARNESS / "bootstrap-egress.tsv"
RESTORE_POLICY = HARNESS / "restore-egress.tsv"

sys.path.insert(0, str(HARNESS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compiler = load_module("policy_compiler", COMPILER_PATH)
refresher = load_module("policy_refresher", REFRESHER_PATH)
exact_dns = load_module("exact_dns_proxy", EXACT_DNS_PATH)
nft_contract = load_module("nft_contract", NFT_CONTRACT_PATH)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dns_query(
    name: str,
    *,
    transaction_id: int = 0x1234,
    query_type: int = 1,
    additional: bytes = b"",
) -> bytes:
    qname = b"".join(
        bytes((len(label),)) + label.encode("ascii") for label in name.split(".")
    ) + b"\0"
    return (
        struct.pack(
            "!HHHHHH", transaction_id, 0x0100, 1, 0, 0, int(bool(additional))
        )
        + qname
        + struct.pack("!HH", query_type, 1)
        + additional
    )


class HaosRestoreHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")
        cls.readme = (HARNESS / "README.md").read_text(encoding="utf-8")

    def test_shell_is_strict_and_has_no_trace_or_recursive_delete(self) -> None:
        self.assertTrue(self.script.startswith("#!/bin/bash\nset -Eeuo pipefail"))
        self.assertNotIn("set -x", self.script)
        self.assertNotIn("rm -rf", self.script)
        self.assertNotIn("chmod 777", self.script)
        bash = shutil.which("bash")
        if os.name != "nt" and bash:
            subprocess.run([bash, "-n", str(SCRIPT)], check=True)

    def test_recovery_inputs_and_policy_tools_are_digest_locked(self) -> None:
        expected = {
            "LOCKED_HAOS_IMAGE_SHA256": "60df08773901e1eac9b9cfe03d53e1d939e67b669c172c3a41037fb3cd295b9d",
            "LOCKED_BACKUP_SHA256": "f129c72cb93121de63ef9ba88f3d21e207db6f6e22e84c57b040a2ec3f0a9d28",
            "LOCKED_POLICY_COMPILER_SHA256": sha256(COMPILER_PATH),
            "LOCKED_POLICY_REFRESHER_SHA256": sha256(REFRESHER_PATH),
            "LOCKED_EXACT_DNS_PROXY_SHA256": sha256(EXACT_DNS_PATH),
            "LOCKED_NFT_CONTRACT_SHA256": sha256(NFT_CONTRACT_PATH),
            "LOCKED_BOOTSTRAP_POLICY_SHA256": sha256(BOOTSTRAP_POLICY),
            "LOCKED_RESTORE_POLICY_SHA256": sha256(RESTORE_POLICY),
        }
        for name, digest in expected.items():
            self.assertIn(f"readonly {name}={digest}", self.script)
        self.assertIn("LOCKED_HAOS_VERSION=18.1", self.script)
        self.assertIn("LOCKED_BACKUP_SLUG=7259fb6d", self.script)
        self.assertIn("LOCKED_BACKUP_ADDON_COUNT=12", self.script)

    def test_restore_policy_is_a_strict_subset_of_sterile_bootstrap(self) -> None:
        bootstrap = set(compiler.load_policy(BOOTSTRAP_POLICY))
        restore = set(compiler.load_policy(RESTORE_POLICY))
        self.assertTrue(restore < bootstrap)
        self.assertIn("github.com", {entry.domain for entry in bootstrap})
        self.assertNotIn("github.com", {entry.domain for entry in restore})
        for entry in bootstrap:
            self.assertIn((entry.protocol, entry.port), {("tcp", 443), ("udp", 123)})
            self.assertNotIn("*", entry.domain)

    def test_policy_parser_rejects_runtime_broadening(self) -> None:
        invalid = (
            "*.example.com\ttcp\t443\twildcard\n",
            "192.0.2.2\ttcp\t443\tliteral-address\n",
            "example.local\ttcp\t443\tprivate-name\n",
            "example.com\ttcp\t80\tarbitrary-port\n",
            "example.com\tudp\t53\tdirect-dns\n",
            "Example.com\ttcp\t443\tmixed-case\n",
        )
        for payload in invalid:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.tsv"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(compiler.PolicyError):
                    compiler.load_policy(path)

    def test_dnsmasq_is_dhcp_only(self) -> None:
        entries = compiler.load_policy(RESTORE_POLICY)
        rendered = compiler.render_dnsmasq(
            entries,
            interface="haosdrill0",
            gateway="192.0.2.1",
            guest="192.0.2.10",
            upstream="1.1.1.1",
            lease_file="/run/example/leases",
        )
        self.assertIn("no-resolv", rendered)
        self.assertIn("no-hosts", rendered)
        self.assertIn("port=0", rendered)
        self.assertNotIn("address=", rendered)
        self.assertNotIn("server=", rendered)

    def test_exact_dns_proxy_refuses_subdomains_and_unlisted_types(self) -> None:
        forward = mock.Mock(return_value=b"should-not-be-returned")
        resolver = exact_dns.ExactResolver({"ghcr.io"}, forward)
        for packet in (dns_query("evil.ghcr.io"), dns_query("ghcr.io", query_type=16)):
            with self.subTest(packet=packet):
                response = resolver.handle(packet, tcp=False)
                flags = struct.unpack("!H", response[2:4])[0]
                self.assertEqual(flags & 0xF, exact_dns.RCODE_REFUSED)
        forward.assert_not_called()

    def test_exact_dns_proxy_sanitizes_upstream_wire(self) -> None:
        forwarded: list[bytes] = []

        def forward(packet: bytes, *, tcp: bool) -> bytes:
            self.assertFalse(tcp)
            forwarded.append(packet)
            question = exact_dns.parse_question(packet)
            return (
                struct.pack(
                    "!HHHHHH", question.transaction_id, 0x8180, 1, 0, 0, 0
                )
                + question.wire
            )

        resolver = exact_dns.ExactResolver({"ghcr.io"}, forward)
        with mock.patch.object(exact_dns.secrets, "randbits", return_value=0xCAFE):
            response = resolver.handle(
                dns_query("GHCR.IO", transaction_id=0xBEEF, additional=b"secret-data"),
                tcp=False,
            )
        self.assertEqual(struct.unpack("!H", response[:2])[0], 0xBEEF)
        self.assertEqual(len(forwarded), 1)
        upstream = forwarded[0]
        self.assertEqual(struct.unpack("!H", upstream[:2])[0], 0xCAFE)
        self.assertNotIn(b"secret-data", upstream)
        self.assertIn(b"ghcr", upstream)
        self.assertNotIn(b"GHCR", upstream)
        self.assertEqual(struct.unpack("!HHHH", upstream[4:12]), (1, 0, 0, 0))

    def test_guest_dns_mode_cannot_open_a_direct_network_upstream(self) -> None:
        with mock.patch.object(exact_dns.sys, "stderr"):
            result = exact_dns.main(
                [
                    str(RESTORE_POLICY),
                    "--expected-sha256",
                    sha256(RESTORE_POLICY),
                    "--upstream",
                    "1.1.1.1",
                    "--bind",
                    "192.0.2.1",
                ]
            )
        self.assertEqual(result, 78)

    def test_refresher_rejects_private_answers(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="192.168.0.125 STREAM name\n", stderr=""
        )
        with mock.patch.object(refresher.subprocess, "run", return_value=completed):
            with self.assertRaises(compiler.PolicyError):
                refresher.resolve_exact("ghcr.io", "1.1.1.1")

    def test_refresher_builds_one_atomic_replace_transaction(self) -> None:
        answers = {ipaddress.ip_address("8.8.8.8")}
        with mock.patch.object(refresher, "resolve_exact", return_value=answers):
            transaction = refresher.render_transaction(
                RESTORE_POLICY, "haos_restore", 300, "1.1.1.1"
            )
        self.assertEqual(transaction.count("flush set"), 2)
        self.assertIn("add element inet haos_restore artifact_v4", transaction)
        self.assertIn("add element inet haos_restore ntp_v4", transaction)
        self.assertIn("timeout 300s", transaction)

    def test_successful_refresh_applies_rendered_transaction_exactly_once(self) -> None:
        args = SimpleNamespace(
            policy=RESTORE_POLICY,
            expected_sha256=sha256(RESTORE_POLICY),
            upstream="1.1.1.1",
            table="haos_restore",
            timeout_seconds=300,
        )
        transaction = "flush set inet haos_restore artifact_v4\n"
        with (
            mock.patch.object(refresher, "validate_resolver"),
            mock.patch.object(refresher, "render_transaction", return_value=transaction),
            mock.patch.object(refresher.subprocess, "run") as run,
        ):
            refresher.refresh(args)
        run.assert_called_once_with(
            ["nft", "-f", "-"],
            input=transaction,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_clear_sets_applies_one_atomic_flush_exactly_once(self) -> None:
        with mock.patch.object(refresher.subprocess, "run") as run:
            refresher.clear_sets("haos_restore")
        run.assert_called_once_with(
            ["nft", "-f", "-"],
            input=(
                "flush set inet haos_restore artifact_v4\n"
                "flush set inet haos_restore ntp_v4\n"
            ),
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_failed_refresh_clears_sets_once(self) -> None:
        with (
            mock.patch.object(
                refresher, "refresh", side_effect=compiler.PolicyError("closed")
            ),
            mock.patch.object(refresher, "clear_sets") as clear,
            mock.patch.object(refresher.sys, "stderr"),
        ):
            result = refresher.main(
                [
                    str(RESTORE_POLICY),
                    "--expected-sha256",
                    sha256(RESTORE_POLICY),
                    "--upstream",
                    "1.1.1.1",
                    "--once",
                ]
            )
        self.assertEqual(result, 78)
        clear.assert_called_once_with("haos_restore", None, None)

    def test_namespace_refresher_resolves_on_host_then_enters_only_for_nft(self) -> None:
        marker = "haos-namespace-haos-restore"
        with mock.patch.object(
            refresher.Path, "read_bytes", return_value=b"python3\0" + marker.encode() + b"\0"
        ):
            command = refresher.nft_command(4321, marker)
        self.assertEqual(command[0], "nsenter")
        self.assertIn("4321", command)
        self.assertEqual(command[-3:], ["nft", "-f", "-"])

    def test_nft_contract_ignores_only_runtime_state(self) -> None:
        base = {
            "nftables": [
                {"metainfo": {"version": "1"}},
                {
                    "set": {
                        "family": "inet",
                        "table": "haos_restore",
                        "name": "artifact_v4",
                        "elem": [{"elem": {"val": "8.8.8.8", "expires": 10}}],
                    }
                },
                {
                    "rule": {
                        "family": "inet",
                        "table": "haos_restore",
                        "chain": "forward",
                        "handle": 7,
                        "expr": [{"counter": {"packets": 3, "bytes": 90}}],
                    }
                },
            ]
        }
        runtime_changed = json.loads(json.dumps(base))
        runtime_changed["nftables"][1]["set"]["elem"] = [
            {"elem": {"val": "1.1.1.1", "expires": 299}}
        ]
        runtime_changed["nftables"][2]["rule"]["handle"] = 99
        runtime_changed["nftables"][2]["rule"]["expr"][0]["counter"] = {
            "packets": 900,
            "bytes": 123456,
        }
        self.assertEqual(
            nft_contract.contract_digest(json.dumps(base)),
            nft_contract.contract_digest(json.dumps(runtime_changed)),
        )

        broadened = json.loads(json.dumps(base))
        broadened["nftables"].append(
            {
                "rule": {
                    "family": "inet",
                    "table": "haos_restore",
                    "chain": "forward",
                    "expr": [{"accept": None}],
                }
            }
        )
        self.assertNotEqual(
            nft_contract.contract_digest(json.dumps(base)),
            nft_contract.contract_digest(json.dumps(broadened)),
        )

    def test_network_is_rootless_two_layer_and_host_loopback_is_disabled(self) -> None:
        for value in (
            "unshare --user --map-root-user --mount --net --mount-proc",
            "--disable-host-loopback",
            "meta skuid $VM_UID",
            "type filter hook output priority -40; policy accept;",
            "type filter hook forward priority filter; policy drop;",
            "ip daddr @artifact_v4 tcp dport 443",
            "ip daddr @ntp_v4 udp dport 123",
            "meta skuid $VM_UID meta nfproto ipv6",
            "net.ipv6.conf.all.disable_ipv6=1",
            "net.ipv6.conf.default.disable_ipv6=1",
            "--timeout-seconds 300 --interval-seconds 120",
        ):
            self.assertIn(value, self.script)
        self.assertNotIn("--enable-ipv6", self.script)
        self.assertNotIn("HAOS_VETH_HOST", self.script)
        self.assertNotIn("ufw ", self.script)

        host = self.script.split("table inet $HOST_NFT_TABLE", 1)[1].split(
            "ns_exec nft -f -", 1
        )[0]
        self.assertIn('ip daddr @artifact_v4 tcp dport 443', host)
        self.assertIn('ip daddr @ntp_v4 udp dport 123', host)
        self.assertNotRegex(host, r'oifname "\$HAOS_UPLINK" tcp dport 443')
        self.assertNotRegex(host, r'oifname "\$HAOS_UPLINK" udp dport 123')
        self.assertNotIn("dport 53", host)
        self.assertIn('--serve-unix "$DNS_UPSTREAM_SOCKET"', self.script)
        self.assertIn('--upstream-unix "$DNS_UPSTREAM_SOCKET"', self.script)

    def test_all_host_lan_tailnet_docker_and_reserved_ranges_are_denied(self) -> None:
        for network in (
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "224.0.0.0/4",
            "240.0.0.0/4",
        ):
            self.assertGreaterEqual(self.script.count(network), 2)
            self.assertTrue(any(ipaddress.ip_network(network) == item for item in refresher.PROHIBITED))
        self.assertIn("meta skuid $VM_UID counter name forbidden_egress drop", self.script)
        self.assertIn('iifname "$HAOS_TAP" counter name forbidden_egress drop', self.script)

    def test_guest_has_no_generic_public_ip_accept_path(self) -> None:
        namespace = self.script.split("table inet $NS_NFT_TABLE", 1)[1].split(
            "setup_network()", 1
        )[0]
        forward = namespace.split("chain forward", 1)[1].split(
            "chain postrouting", 1
        )[0]
        accept_lines = [line.strip() for line in forward.splitlines() if " accept" in line]
        self.assertEqual(
            accept_lines,
            [
                'iifname "$HAOS_EGRESS_TAP" oifname "$HAOS_TAP" ct state established,related accept',
                'iifname "$HAOS_TAP" oifname "$HAOS_EGRESS_TAP" ip daddr @artifact_v4 tcp dport 443 counter name approved_egress accept',
                'iifname "$HAOS_TAP" oifname "$HAOS_EGRESS_TAP" ip daddr @ntp_v4 udp dport 123 counter name approved_egress accept',
            ],
        )

    def test_ui_is_loopback_only_and_report_is_content_free(self) -> None:
        self.assertIn("bind=127.0.0.1", self.script)
        self.assertIn("UI relay escaped loopback", self.script)
        self.assertNotIn("bind=0.0.0.0", self.script)
        report_block = self.script.split("jq -n \\", 1)[1].split(
            "' > \"$temporary\"", 1
        )[0]
        for prohibited in ("coordinate", "utterance", "camera", "person_name", "token", "entity_id"):
            self.assertNotIn(prohibited, report_block.lower())

    def test_bootstrap_has_no_ui_relay_and_restore_creates_it(self) -> None:
        bootstrap = self.script.split("start_bootstrap()", 1)[1].split(
            "pid_is_running()", 1
        )[0]
        self.assertNotIn("start_relays", bootstrap)
        self.assertIn("no host UI relay", bootstrap)
        transition = self.script.split("transition_restore()", 1)[1].split(
            "counter_packets()", 1
        )[0]
        self.assertIn("start_relays", transition)
        self.assertIn("ui=disabled", self.script)

    def test_vm_runtime_is_traversable_without_exposing_root_state(self) -> None:
        self.assertIn("readonly RUN_ROOT=/run/home-agent-haos-restore", self.script)
        self.assertIn(
            "readonly VM_SHARED_ROOT=/run/home-agent-haos-restore-vm", self.script
        )
        self.assertIn(
            "readonly TOOLS_ROOT=/run/home-agent-haos-restore-tools", self.script
        )
        self.assertNotIn('VM_RUN_DIR="$RUN_ROOT/', self.script)
        self.assertIn(
            'install -d -m 0700 -o "$VM_UID" -g "$VM_GID" "$VM_SHARED_ROOT"',
            self.script,
        )
        self.assertIn(
            'install -d -m 0750 -o root -g "$VM_GID" "$TOOLS_ROOT"',
            self.script,
        )
        self.assertIn(
            'install -m 0440 -o root -g "$VM_GID" "$EXACT_DNS_PROXY"',
            self.script,
        )
        self.assertNotIn('"$EXACT_DNS_PROXY" "$VM_RUN_DIR', self.script)
        self.assertIn('"$NFT_CONTRACT" "$TOOL_NFT_CONTRACT"', self.script)
        self.assertIn("verify_tool_copies", self.script)

    def test_execution_requires_a_trusted_root_install_and_canonical_paths(self) -> None:
        for value in (
            "LOCKED_INSTALL_DIR=/usr/local/libexec/home-agent-haos-restore",
            '[[ "$SCRIPT_DIR" != "$LOCKED_INSTALL_DIR"',
            "require_trusted_directory_tree",
            "require_trusted_file_tree",
            "require_trusted_ancestors",
            "installed harness directory contains unexpected entry",
            "PYTHONDONTWRITEBYTECODE=1",
            "unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH PYTHONSTARTUP",
        ):
            self.assertIn(value, self.script)
        self.assertIn("readonly PATH=/usr/sbin:/usr/bin:/sbin:/bin", self.script)
        self.assertIn('require_trusted_file_tree "$HAOS_BACKUP_TAR"', self.script)
        self.assertIn('require_trusted_directory_tree "$HAOS_SCRATCH_PARENT"', self.script)
        self.assertIn('"$(stat -Lc \'%d:%i\' "$HAOS_SCRATCH_FILE")" == "$SCRATCH_DEV_INODE"', self.script)

    def test_developer_checkout_execution_is_rejected_before_preflight(self) -> None:
        bash = shutil.which("bash")
        if os.name == "nt" or not bash:
            self.skipTest("requires a POSIX Bash host")
        completed = subprocess.run(
            [bash, str(SCRIPT), "preflight", "/does/not/exist"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 78)
        self.assertIn("run only the root-installed", completed.stderr)

    def test_operator_lock_cannot_follow_or_truncate_an_untrusted_path(self) -> None:
        lock = self.script.split("acquire_lock()", 1)[1].split(
            "validate_paths_and_values()", 1
        )[0]
        self.assertIn("readonly LOCK_FILE=/run/home-agent-haos-restore.lock", self.script)
        self.assertIn('! -L "$LOCK_FILE"', lock)
        self.assertIn("0:600:1", lock)
        self.assertIn('exec 9>>"$LOCK_FILE"', lock)
        self.assertNotIn('exec 9>"$LOCK_FILE"', lock)
        self.assertIn('"/proc/$$/fd/9"', lock)

    def test_guest_exposed_host_helpers_are_nnp_and_capability_empty(self) -> None:
        setup = self.script.split("setup_network()", 1)[1].split(
            "start_relays()", 1
        )[0]
        slirp = setup.split("slirp4netns --configure", 1)[0].rsplit(
            "setpriv --reuid", 1
        )[1]
        for value in (
            "--no-new-privs",
            "--bounding-set=-all",
            "--inh-caps=-all",
            "--ambient-caps=-all",
        ):
            self.assertIn(value, slirp)
        self.assertIn('require_process_confinement "$SLIRP_PID" slirp4netns', setup)
        relays = self.script.split("start_relays()", 1)[1].split(
            "launch_qemu()", 1
        )[0]
        self.assertGreaterEqual(relays.count("--no-new-privs"), 2)
        self.assertIn('require_process_confinement "$OUTER_RELAY_PID" "outer relay"', relays)
        self.assertIn("dedicated VM user may belong only to its primary group and kvm", self.script)

    def test_validation_requires_exact_ruleset_contract_and_http_200(self) -> None:
        validation = self.script.split("validate_and_report()", 1)[1].split(
            "stop_uid_processes_with_marker()", 1
        )[0]
        self.assertIn(
            '[[ "$current_host_contract" == "$HOST_NFT_CONTRACT_SHA256" ]]',
            validation,
        )
        self.assertIn(
            '[[ "$current_ns_contract" == "$NS_NFT_CONTRACT_SHA256" ]]',
            validation,
        )
        self.assertIn('[[ "$http_code" == 200 ]]', validation)
        self.assertIn("host reviewed endpoint sets are empty", validation)
        self.assertIn("namespace reviewed endpoint sets are empty", validation)

    def test_exact_dns_is_not_dnsmasq_suffix_forwarding(self) -> None:
        self.assertIn('python3 "$TOOL_EXACT_DNS_PROXY"', self.script)
        self.assertNotIn("server=/", compiler.render_dnsmasq(
            compiler.load_policy(RESTORE_POLICY),
            interface="haosdrill0",
            gateway="192.0.2.1",
            guest="192.0.2.10",
            upstream="1.1.1.1",
            lease_file="/run/example/leases",
        ))

    def test_ephemeral_luks_key_never_persists_and_cleanup_is_guarded(self) -> None:
        for value in (
            "readonly RUN_ROOT=/run/home-agent-haos-restore",
            'findmnt -rn -o FSTYPE -T /run',
            'openssl rand -out "$KEY_FILE" 64',
            'cryptsetup luksFormat --batch-mode --type luks2 --label "$SCRATCH_LUKS_LABEL"',
            'cryptsetup open --key-file "$KEY_FILE"',
            'rm -f -- "$KEY_FILE"',
            'cryptsetup isLuks "$HAOS_SCRATCH_FILE"',
            "no SSD overwrite is claimed",
        ):
            self.assertIn(value, self.script)
        self.assertNotIn("--key-file=-", self.script)
        self.assertNotIn("shred", self.script)

    def test_state_binds_config_path_and_luks_identity_before_cleanup(self) -> None:
        for value in (
            "CONFIG_SHA256",
            "SCRATCH_PATH_SHA256",
            "SCRATCH_LUKS_UUID",
            "SCRATCH_LUKS_LABEL",
            "SCRATCH_DEV_INODE",
            '--label "$SCRATCH_LUKS_LABEL"',
            'cryptsetup luksUUID "$HAOS_SCRATCH_FILE"',
            'blkid -p -s LABEL -o value -- "$HAOS_SCRATCH_FILE"',
            "load_durable_receipt",
            "RECEIPT_HMAC",
            ".haos-restore-receipt.key",
        ):
            self.assertIn(value, self.script)
        cleanup = self.script.split("cleanup_all()", 1)[1]
        self.assertLess(cleanup.index("load_durable_receipt"), cleanup.index("rm -f -- \"$HAOS_SCRATCH_FILE\""))
        self.assertIn("verify_state_scratch_identity", cleanup)
        self.assertIn("verify_active_mapper_identity", cleanup)
        self.assertLess(
            cleanup.index("verify_active_mapper_identity"),
            cleanup.index('cryptsetup close "$HAOS_MAPPER"'),
        )

    def test_cleanup_receipt_survives_tmpfs_state_loss_without_private_content(self) -> None:
        receipt = self.script.split("receipt_payload()", 1)[1].split(
            "validate_receipt_key()", 1
        )[0]
        for required in (
            "CONFIG_SHA256",
            "SCRATCH_PATH_SHA256",
            "SCRATCH_LUKS_UUID",
            "SCRATCH_LUKS_LABEL",
            "SCRATCH_DEV_INODE",
        ):
            self.assertIn(required, receipt)
        for prohibited in ("coordinate", "utterance", "camera", "token", "person"):
            self.assertNotIn(prohibited, receipt.lower())
        cleanup = self.script.split("cleanup_all()", 1)[1]
        state_fallback = cleanup.split('if [[ -f "$STATE_FILE" ]]', 1)[1].split(
            'stop_pid "$QEMU_PID"', 1
        )[0]
        self.assertIn("else", state_fallback)
        self.assertIn("load_durable_receipt", state_fallback)

    def test_phases_and_cleanup_order_are_explicit(self) -> None:
        for acknowledgement in (
            "--acknowledge-ephemeral-key-loss",
            "--confirm-sterile-bootstrap",
            "--confirm-pristine-no-production-data",
            "--confirm-destroy-ephemeral-restore",
        ):
            self.assertIn(acknowledgement, self.script)
        transition = self.script.split("transition_restore()", 1)[1].split(
            "counter_packets()", 1
        )[0]
        self.assertIn("shut down the pristine VM", transition)
        cleanup = self.script.split("cleanup_all()", 1)[1]
        self.assertLess(cleanup.index('stop_pid "$QEMU_PID"'), cleanup.index("teardown_network"))
        self.assertLess(cleanup.index('stop_pid "$SLIRP_PID"'), cleanup.index("teardown_network"))
        self.assertIn("unknown dedicated-UID process remains", self.script)

    def test_qemu_has_no_physical_passthrough_or_published_listener(self) -> None:
        qemu = self.script.split("launch_qemu()", 1)[1].split(
            "network_failure_cleanup()", 1
        )[0]
        for value in ("-enable-kvm", "-nodefaults", "-display none", "virtio-scsi-pci"):
            self.assertIn(value, qemu)
        for value in ("-hostdev", "usb-host", "-vnc", "0.0.0.0"):
            self.assertNotIn(value, qemu)
        for value in (
            "--no-new-privs",
            "--bounding-set=-all",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            'ip tuntap add dev "$HAOS_TAP" mode tap user 0 group 0',
            'require_process_confinement "$QEMU_PID"',
            "CapEff",
            "CapBnd",
            "NoNewPrivs",
        ):
            self.assertIn(value, self.script)

    def test_documentation_states_human_gate_and_expected_unavailability(self) -> None:
        normalized = " ".join(self.readme.split())
        for value in (
            "Do **not** upload the production backup",
            "Enter its emergency-kit key only in the local browser",
            "No physical action should succeed",
            "cryptographic erasure",
            "does not make a false SSD overwrite claim",
            "must not run from a developer checkout",
            "IP/port sets cannot authenticate TLS SNI",
        ):
            self.assertIn(value, normalized)


if __name__ == "__main__":
    unittest.main()
