import copy
import io
import ipaddress
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from network_contract import (
    ContractError,
    main,
    read_env,
    validate_bff_inspect,
    validate_candidate_collisions,
    validate_address_contract,
    validate_network_inspect,
    validate_image_id,
    validate_oauth_contract,
    validate_origin_inspect,
)

STACK = Path(__file__).resolve().parents[2]
PUBLIC_ORIGIN = "https://home-app.example.ts.net:8443"
ORIGIN_IMAGE_ID = "sha256:" + ("ab" * 32)
BFF_IMAGE_ID = "sha256:" + ("cd" * 32)


def live_shape(*, internal=True, ip_range="172.23.128.0/17", containers=None):
    return {
        "Name": "home-agent_api-net",
        "Id": "0123456789abcdef",
        "Driver": "bridge",
        "Scope": "local",
        "Internal": internal,
        "EnableIPv6": False,
        "IPAM": {
            "Config": [{
                "Subnet": "172.23.0.0/16",
                "IPRange": ip_range,
                "Gateway": "172.23.0.1",
            }]
        },
        "Containers": containers or {},
    }


def bff_shape():
    return {
        "Name": "/home-agent-bff-1",
        "Image": BFF_IMAGE_ID,
        "Config": {
            "Image": "engineered-lighting/home-agent-bff:local",
            "Labels": {
                "com.docker.compose.project": "home-agent",
                "com.docker.compose.service": "bff",
            },
            "Env": [
                f"HOME_AGENT_ALLOWED_ORIGINS={PUBLIC_ORIGIN}",
                f"HOME_AGENT_OAUTH_CLIENT_ID={PUBLIC_ORIGIN}",
                f"HOME_AGENT_OAUTH_REDIRECT_URI={PUBLIC_ORIGIN}/api/agent/auth/callback",
            ],
        },
    }


def origin_shape():
    return {
        "Name": "/home-agent-origin-origin-1",
        "Image": ORIGIN_IMAGE_ID,
        "Config": {
            "Image": "engineered-lighting/home-agent-origin:local",
            "User": "1000:1000",
            "Labels": {
                "com.docker.compose.project": "home-agent-origin",
                "com.docker.compose.service": "origin",
            },
            "Env": [f"HOME_AGENT_WEB_PUBLIC_ORIGIN={PUBLIC_ORIGIN}"],
        },
        "HostConfig": {
            "NetworkMode": "home-agent_api-net",
            "PortBindings": {},
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64,
            "Memory": 128 * 1024 * 1024,
        },
        "NetworkSettings": {
            "Networks": {
                "home-agent_api-net": {
                    "IPAddress": "172.23.0.10",
                    "GlobalIPv6Address": "",
                }
            },
            "Ports": {"8096/tcp": None},
        },
        "Mounts": [],
        "State": {"Running": True, "Health": {"Status": "healthy"}},
    }


class NetworkContractTests(unittest.TestCase):
    def contract(self):
        return validate_address_contract(
            "172.23.0.0/16",
            "172.23.128.0/17",
            "172.23.0.1",
            "172.23.0.10",
            "http://172.23.0.10:8096",
        )

    def test_reviewed_contract_is_disjoint_and_live_shape_matches(self):
        subnet, dynamic, gateway, origin = self.contract()
        validate_network_inspect(
            live_shape(), subnet=subnet, dynamic=dynamic, gateway=gateway, origin=origin
        )

    def test_oauth_configuration_and_running_bff_must_exactly_align(self):
        base = {
            "HOME_AGENT_ALLOWED_ORIGINS": PUBLIC_ORIGIN,
            "HOME_AGENT_OAUTH_CLIENT_ID": PUBLIC_ORIGIN,
            "HOME_AGENT_OAUTH_REDIRECT_URI": f"{PUBLIC_ORIGIN}/api/agent/auth/callback",
        }
        origin = {"HOME_AGENT_WEB_PUBLIC_ORIGIN": PUBLIC_ORIGIN}
        self.assertEqual(validate_oauth_contract(base, origin), PUBLIC_ORIGIN)
        validate_bff_inspect(bff_shape(), PUBLIC_ORIGIN, BFF_IMAGE_ID)
        for key in tuple(base):
            invalid = dict(base)
            invalid[key] = "https://legacy.example.invalid"
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_oauth_contract(invalid, origin)
        for public in (
            "http://home-app.example.ts.net:8443",
            "https://HOME-APP.example.ts.net:8443",
            f"{PUBLIC_ORIGIN}/",
        ):
            with self.subTest(public=public), self.assertRaises(ContractError):
                validate_oauth_contract(base, {"HOME_AGENT_WEB_PUBLIC_ORIGIN": public})
        stale_bff = bff_shape()
        stale_bff["Config"]["Env"][0] = "HOME_AGENT_ALLOWED_ORIGINS=https://legacy.invalid"
        with self.assertRaises(ContractError):
            validate_bff_inspect(stale_bff, PUBLIC_ORIGIN, BFF_IMAGE_ID)

    def test_origin_cannot_be_dynamic_gateway_or_mismatched_serve_target(self):
        invalid = (
            ("172.23.128.10", "http://172.23.128.10:8096"),
            ("172.23.0.1", "http://172.23.0.1:8096"),
            ("172.23.0.10", "http://172.23.0.11:8096"),
            ("172.23.0.10", "https://172.23.0.10:8096"),
        )
        for address, target in invalid:
            with self.subTest(address=address, target=target):
                with self.assertRaises(ContractError):
                    validate_address_contract(
                        "172.23.0.0/16",
                        "172.23.128.0/17",
                        "172.23.0.1",
                        address,
                        target,
                    )

    def test_network_must_be_internal_and_exactly_pinned(self):
        subnet, dynamic, gateway, origin = self.contract()
        for inspect in (
            {**live_shape(), "Driver": "overlay"},
            {**live_shape(), "Scope": "swarm"},
            live_shape(internal=False),
            {**live_shape(), "EnableIPv6": True},
            live_shape(ip_range=""),
            {**live_shape(), "IPAM": {"Config": []}},
        ):
            with self.assertRaises(ContractError):
                validate_network_inspect(
                    inspect, subnet=subnet, dynamic=dynamic, gateway=gateway, origin=origin
                )

    def test_candidate_subnet_rejects_host_tailscale_and_docker_collisions(self):
        subnet, _dynamic, _gateway, _origin = self.contract()
        valid_routes = [
            {"dst": "172.23.0.0/16", "dev": "br-0123456789ab"},
            {"dst": "172.23.0.1/32", "dev": "br-0123456789ab"},
            {"dst": "192.168.0.0/24", "dev": "enp4s0"},
            {"dst": "default", "dev": "enp4s0"},
        ]
        validate_candidate_collisions(
            subnet,
            ipaddress.ip_address("172.23.0.1"),
            "home-agent_api-net",
            docker_networks=[live_shape()],
            host_routes=valid_routes,
            tailscale_routes=["100.64.0.0/10"],
        )
        cases = (
            {
                "docker_networks": [{**live_shape(), "Driver": "overlay"}],
                "host_routes": valid_routes,
                "tailscale_routes": [],
            },
            {
                "docker_networks": [
                    live_shape(),
                    {
                        "Name": "other",
                        "IPAM": {"Config": [{"Subnet": "172.23.1.0/24"}]},
                    },
                ],
                "host_routes": valid_routes,
                "tailscale_routes": [],
            },
            {
                "docker_networks": [live_shape()],
                "host_routes": valid_routes + [{"dst": "172.23.44.0/24", "dev": "tailscale0"}],
                "tailscale_routes": [],
            },
            {
                "docker_networks": [live_shape()],
                "host_routes": valid_routes,
                "tailscale_routes": ["172.23.64.0/18"],
            },
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ContractError):
                validate_candidate_collisions(
                    subnet,
                    ipaddress.ip_address("172.23.0.1"),
                    "home-agent_api-net",
                    **values,
                )

    def test_collision_and_missing_required_origin_fail(self):
        subnet, dynamic, gateway, origin = self.contract()
        collision = live_shape(containers={
            "id": {"Name": "unrelated", "IPv4Address": "172.23.0.10/16"}
        })
        with self.assertRaises(ContractError):
            validate_network_inspect(
                collision, subnet=subnet, dynamic=dynamic, gateway=gateway, origin=origin
            )
        with self.assertRaises(ContractError):
            validate_network_inspect(
                live_shape(),
                subnet=subnet,
                dynamic=dynamic,
                gateway=gateway,
                origin=origin,
                require_origin=True,
            )
        attached = live_shape(containers={
            "id": {
                "Name": "home-agent-origin-origin-1",
                "IPv4Address": "172.23.0.10/16",
            }
        })
        validate_network_inspect(
            attached,
            subnet=subnet,
            dynamic=dynamic,
            gateway=gateway,
            origin=origin,
            require_origin=True,
        )

    def test_running_origin_must_have_only_internal_network_and_no_publication(self):
        origin_ip = ipaddress.ip_address("172.23.0.10")
        validate_origin_inspect(
            origin_shape(),
            network_name="home-agent_api-net",
            origin=origin_ip,
            public_origin=PUBLIC_ORIGIN,
            image_id=ORIGIN_IMAGE_ID,
        )
        mutations = []
        extra_network = origin_shape()
        extra_network["NetworkSettings"]["Networks"]["egress"] = {
            "IPAddress": "172.30.0.2", "GlobalIPv6Address": ""
        }
        mutations.append(extra_network)
        binding = origin_shape()
        binding["HostConfig"]["PortBindings"] = {
            "8096/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8096"}]
        }
        mutations.append(binding)
        published = origin_shape()
        published["NetworkSettings"]["Ports"]["8096/tcp"] = [
            {"HostIp": "127.0.0.1", "HostPort": "8096"}
        ]
        mutations.append(published)
        for inspect in mutations:
            with self.subTest(kind="network_or_port"), self.assertRaises(ContractError):
                validate_origin_inspect(
                    inspect,
                    network_name="home-agent_api-net",
                    origin=origin_ip,
                    public_origin=PUBLIC_ORIGIN,
                    image_id=ORIGIN_IMAGE_ID,
                )

    def test_running_origin_identity_and_hardening_cannot_drift(self):
        origin_ip = ipaddress.ip_address("172.23.0.10")
        mutations = []
        for path, value in (
            (("", "Image"), "sha256:" + ("cd" * 32)),
            (("Config", "Image"), "unreviewed:latest"),
            (("Config", "User"), "0:0"),
            (("HostConfig", "ReadonlyRootfs"), False),
            (("HostConfig", "Privileged"), True),
            (("HostConfig", "CapDrop"), []),
            (("HostConfig", "SecurityOpt"), []),
            (("State", "Running"), False),
        ):
            inspect = copy.deepcopy(origin_shape())
            if path[0]:
                inspect[path[0]][path[1]] = value
            else:
                inspect[path[1]] = value
            mutations.append((path, inspect))
        wrong_label = origin_shape()
        wrong_label["Config"]["Labels"]["com.docker.compose.service"] = "legacy"
        mutations.append((("Config", "Labels"), wrong_label))
        for path, inspect in mutations:
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate_origin_inspect(
                    inspect,
                    network_name="home-agent_api-net",
                    origin=origin_ip,
                    public_origin=PUBLIC_ORIGIN,
                    image_id=ORIGIN_IMAGE_ID,
                )

    def test_running_bff_image_identity_cannot_drift(self):
        for path, value in (
            (("Image",), "sha256:" + ("ef" * 32)),
            (("Config", "Image"), "unreviewed:latest"),
        ):
            inspect = copy.deepcopy(bff_shape())
            if len(path) == 1:
                inspect[path[0]] = value
            else:
                inspect[path[0]][path[1]] = value
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate_bff_inspect(inspect, PUBLIC_ORIGIN, BFF_IMAGE_ID)

        with self.assertRaises(ContractError):
            validate_bff_inspect(
                bff_shape(),
                PUBLIC_ORIGIN,
                "sha256:" + ("ef" * 32),
            )

    def test_candidate_bff_check_allows_previous_content_but_not_tag_drift(self):
        previous = bff_shape()
        previous["Image"] = "sha256:" + ("ef" * 32)
        validate_bff_inspect(
            previous,
            PUBLIC_ORIGIN,
            BFF_IMAGE_ID,
            require_image_match=False,
        )

        previous["Config"]["Image"] = "unreviewed:latest"
        with self.assertRaises(ContractError):
            validate_bff_inspect(
                previous,
                PUBLIC_ORIGIN,
                BFF_IMAGE_ID,
                require_image_match=False,
            )

    def test_image_id_must_be_one_exact_lowercase_sha256_digest(self):
        self.assertEqual(validate_image_id(ORIGIN_IMAGE_ID), ORIGIN_IMAGE_ID)
        self.assertEqual(
            validate_image_id(BFF_IMAGE_ID, component="BFF"),
            BFF_IMAGE_ID,
        )
        for value in (
            "",
            "sha256:" + ("a" * 63),
            "sha256:" + ("a" * 65),
            "sha256:" + ("AB" * 32),
            "sha256: " + ("ab" * 32),
            "sha512:" + ("ab" * 32),
            f"{ORIGIN_IMAGE_ID}\n",
        ):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_image_id(value)
            with (
                self.subTest(component="BFF", value=value),
                self.assertRaisesRegex(ContractError, "^BFF image ID must"),
            ):
                validate_image_id(value, component="BFF")

    def test_candidate_preflight_rejects_missing_or_malformed_image_ids(self):
        base_lines = (
            f"HOME_AGENT_ALLOWED_ORIGINS={PUBLIC_ORIGIN}",
            f"HOME_AGENT_OAUTH_CLIENT_ID={PUBLIC_ORIGIN}",
            f"HOME_AGENT_OAUTH_REDIRECT_URI={PUBLIC_ORIGIN}/api/agent/auth/callback",
        )
        origin_lines = (
            f"HOME_AGENT_WEB_PUBLIC_ORIGIN={PUBLIC_ORIGIN}",
            "HOME_AGENT_WEB_INTERNAL_IP=172.23.0.10",
            "HOME_AGENT_WEB_SERVE_TARGET=http://172.23.0.10:8096",
        )

        def run_candidate(
            bff_image_line: str,
            origin_image_line: str,
        ) -> tuple[int, str]:
            base_text = "\n".join((*base_lines, bff_image_line))
            origin_text = "\n".join((*origin_lines, origin_image_line))
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                base_path = root / "base.env"
                origin_path = root / "origin.env"
                base_path.write_text(f"{base_text}\n", encoding="utf-8")
                origin_path.write_text(f"{origin_text}\n", encoding="utf-8")
                error = io.StringIO()
                with redirect_stderr(error):
                    result = main(
                        [
                            "--base-env",
                            str(base_path),
                            "--origin-env",
                            str(origin_path),
                            "--candidate-only",
                        ]
                    )
                return result, error.getvalue()

        for bff_image_line in (
            "",
            f"HOME_AGENT_BFF_IMAGE_ID=sha256:{'AB' * 32}",
            f"HOME_AGENT_BFF_IMAGE_ID=sha256:{'a' * 63}",
        ):
            with self.subTest(bff_image_line=bff_image_line):
                result, error = run_candidate(
                    bff_image_line,
                    f"HOME_AGENT_WEB_IMAGE_ID={ORIGIN_IMAGE_ID}",
                )
                self.assertEqual(result, 1)
                self.assertIn(
                    "BFF image ID must be one exact lowercase sha256 digest",
                    error,
                )

        for origin_image_line in (
            "",
            f"HOME_AGENT_WEB_IMAGE_ID=sha256:{'AB' * 32}",
            f"HOME_AGENT_WEB_IMAGE_ID=sha256:{'a' * 63}",
        ):
            with self.subTest(origin_image_line=origin_image_line):
                result, error = run_candidate(
                    f"HOME_AGENT_BFF_IMAGE_ID={BFF_IMAGE_ID}",
                    origin_image_line,
                )
                self.assertEqual(result, 1)
                self.assertIn(
                    "Agent origin image ID must be one exact lowercase sha256 digest",
                    error,
                )

    def test_compose_has_no_host_port_or_noninternal_origin_network(self):
        origin_compose = (STACK / "home-agent-deploy/agent-origin/compose.yml").read_text()
        base_compose = (STACK / "home-agent-compose.yml").read_text()
        base_env = (STACK / "home-agent.env.example").read_text()
        origin_env = (STACK / "home-agent-deploy/agent-origin/home-agent-origin.env.example").read_text()
        self.assertNotIn("ports:", origin_compose)
        self.assertNotIn("origin-public", origin_compose)
        self.assertIn('ipv4_address: "${HOME_AGENT_WEB_INTERNAL_IP:', origin_compose)
        self.assertIn(
            "api-net:\n    internal: true\n    enable_ipv4: true\n    enable_ipv6: false\n    ipam:",
            base_compose,
        )
        self.assertIn("HOME_AGENT_API_DYNAMIC_RANGE:-172.23.128.0/17", base_compose)
        self.assertIn(
            "HOME_AGENT_BFF_IMAGE_ID=sha256:REPLACE_WITH_LOCAL_IMAGE_ID",
            base_env,
        )
        self.assertIn("HOME_AGENT_WEB_INTERNAL_IP=172.23.0.10", origin_env)
        self.assertIn("HOME_AGENT_WEB_SERVE_TARGET=http://172.23.0.10:8096", origin_env)
        self.assertIn(
            "HOME_AGENT_WEB_IMAGE_ID=sha256:REPLACE_WITH_LOCAL_IMAGE_ID",
            origin_env,
        )
        with self.assertRaises(ContractError):
            validate_image_id(
                read_env(
                    STACK
                    / "home-agent-deploy/agent-origin/home-agent-origin.env.example"
                )["HOME_AGENT_WEB_IMAGE_ID"]
            )
        with self.assertRaisesRegex(ContractError, "^BFF image ID must"):
            validate_image_id(
                read_env(STACK / "home-agent.env.example")[
                    "HOME_AGENT_BFF_IMAGE_ID"
                ],
                component="BFF",
            )
        validate_oauth_contract(
            read_env(STACK / "home-agent.env.example"),
            read_env(STACK / "home-agent-deploy/agent-origin/home-agent-origin.env.example"),
        )


if __name__ == "__main__":
    unittest.main()
