from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class NativeAttestationDeploymentContractTests(unittest.TestCase):
    def test_bff_receives_only_a_read_only_materialized_registry(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        self.assertIn(
            "HOME_AGENT_NATIVE_INSTALLATIONS_FILE: /run/secrets/native_installations",
            compose,
        )
        self.assertIn("source: native_installations_bff", compose)
        self.assertIn("target: native_installations", compose)
        self.assertIn(
            'native_installations_bff: {file: "${HOME_AGENT_SECRETS_DIR}/runtime/bff/native_installations"}',
            compose,
        )
        self.assertNotIn("HOME_AGENT_NATIVE_INSTALLATIONS_FILE: ${", compose)

    def test_native_proof_audience_is_explicit_and_separate_from_browser_oauth(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        example = read("stack/home-agent.env.example")
        preflight = read("stack/home-agent-deploy/preflight.sh")
        runbook = read("docs/HOME-AGENT-RUNBOOK.md")
        self.assertIn(
            'HOME_AGENT_NATIVE_PUBLIC_ORIGIN: "${HOME_AGENT_NATIVE_PUBLIC_ORIGIN:?',
            compose,
        )
        self.assertIn(
            "HOME_AGENT_NATIVE_PUBLIC_ORIGIN=https://native-agent.home.example.internal",
            example,
        )
        self.assertIn(
            ': "${HOME_AGENT_NATIVE_PUBLIC_ORIGIN:?missing HOME_AGENT_NATIVE_PUBLIC_ORIGIN}"',
            preflight,
        )
        self.assertIn("native and browser OAuth origins must remain separate", preflight)
        self.assertIn(
            "native origin may not appear in the browser allowed-origins set",
            preflight,
        )
        self.assertIn("HOME_NATIVE_AGENT_BFF_URL", runbook)
        self.assertIn("HOME_WEB_NATIVE_AGENT_ORIGIN", runbook)
        self.assertIn("HOME_WEB_AGENT_ORIGINS", runbook)
        self.assertIn("HOME_AGENT_NATIVE_PUBLIC_ORIGIN", runbook)

    def test_registry_is_root_managed_encrypted_and_least_privilege(self) -> None:
        preflight = read("stack/home-agent-deploy/preflight.sh")
        materializer = read("stack/home-agent-deploy/materialize-secrets.sh")
        bootstrap = read("stack/home-agent-deploy/bootstrap-secrets.sh")
        self.assertIn(
            "write_new native_installations.json "
            "'{\"installations\":[],\"schema_version\":1}'",
            bootstrap,
        )
        self.assertIn(
            'native_installations_master="$HOME_AGENT_SECRETS_DIR/master/native_installations.json"',
            preflight,
        )
        self.assertIn(
            'chown root:root "$native_installations_master"', preflight
        )
        self.assertIn('chmod 0600 "$native_installations_master"', preflight)
        self.assertIn(
            "$native_installations_master is not on a verified /dev/mapper encrypted mount",
            preflight,
        )
        self.assertIn(
            "install_secret bff native_installations.json native_installations 1000 1000",
            materializer,
        )
        self.assertIn(
            'verify_secret 1000:1000:400 "$HOME_AGENT_SECRETS_DIR/runtime/bff/native_installations"',
            preflight,
        )
        self.assertIn(
            "manage_native_installations.py", preflight
        )
        self.assertIn("'{\"action\":\"validate\"}'", preflight)

    def test_gateway_forwards_proof_only_on_exact_contained_native_routes(self) -> None:
        gateway = read("web-gateway/server.mjs")
        native_block = gateway.split("const NATIVE_AGENT_ROUTES", 1)[1].split(
            "]);", 1
        )[0]
        self.assertIn('/^\\/native\\/v1\\/attestation\\/challenge$/', native_block)
        self.assertNotIn("initiatives", native_block)
        header_block = gateway.split(
            'if (route.prefix === "/api/agent" && suffix.startsWith("/native/"))',
            1,
        )[1].split("return headers;", 1)[0]
        self.assertIn('"dpop"', header_block)
        for forbidden in (
            "cookie",
            "x-home-agent-channel",
            "x-home-agent-installation",
            "x-authenticated-ha-user",
            "x-principal-id",
        ):
            self.assertNotIn(f'headers["{forbidden}"]', header_block)
        self.assertIn("HOME_WEB_NATIVE_AGENT_ORIGIN", gateway)
        self.assertIn("if (nativeAgentContext.agentHost)", gateway)
        self.assertIn('error: "native_agent_origin_required"', gateway)
        self.assertIn("NATIVE_AGENT_MAX_BODY_BYTES = 64 * 1024", gateway)
        self.assertIn("headersTimeout: GATEWAY_HEADER_TIMEOUT_MS", gateway)
        self.assertIn("server.maxRequestsPerSocket = 100", gateway)

    def test_core_rejects_the_unversioned_native_channel(self) -> None:
        auth = read("stack/services/home-agent-core/app/auth.py")
        main = read("stack/services/home-agent-core/app/main.py")
        api = read("stack/services/home-agent-core/app/api.py")
        store = read("stack/services/home-agent-core/app/store.py")
        self.assertIn(
            'ATTESTED_NATIVE_CHANNEL = "private_tauri_attested_v1"', auth
        )
        self.assertIn("native_installation_id_valid", auth)
        self.assertIn("x_home_agent_installation", auth)
        self.assertNotIn('x_home_agent_channel,\n        "private_tauri",', auth)
        self.assertIn("ATTESTED_NATIVE_CHANNEL", main)
        self.assertEqual(
            api.count(
                "private initiative presentation requires a separate reviewed gate"
            ),
            2,
        )
        self.assertIn('"private_initiatives": "disabled"', store)


if __name__ == "__main__":
    unittest.main()
