"""Interactive, container-local authority bootstrap.

Run only after obtaining the HA UUID from the authenticated whoami view. The
script never prints secrets or coordinates and intentionally has no flags that
would place private values in process listings or shell history.
"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import stat
import urllib.error
import urllib.request
import uuid


BASE = "http://127.0.0.1:8104"
MAX_RESPONSE_BYTES = 1024 * 1024
ENDPOINT_AUDIENCES = {
    "/v1/people": "operator",
    "/v1/people/verify-reviewed": "operator",
    "/v1/principal-bindings": "service",
    "/v1/source-entity-bindings": "service",
    "/v1/relationships/parent-confirmations": "service",
    "/v1/places": "service",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)


def secret(name: str) -> str:
    path = Path("/run/secrets", name)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"invalid secret file: {name}")
    if os.name == "posix" and metadata.st_mode & 0o077:
        raise SystemExit(f"secret file permissions are too broad: {name}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit(f"missing secret file: {name}")
    return value


SERVICE_TOKEN: str | None = None
OPERATOR_TOKEN: str | None = None
BOOTSTRAP_TOKEN: str | None = None


def configure_credentials() -> None:
    global SERVICE_TOKEN, OPERATOR_TOKEN, BOOTSTRAP_TOKEN
    SERVICE_TOKEN = secret("service_token")
    OPERATOR_TOKEN = secret("operator_token")
    BOOTSTRAP_TOKEN = secret("bootstrap_token")


def post(path: str, body: dict, ha_user_id: str, *, audience: str) -> dict:
    expected_audience = ENDPOINT_AUDIENCES.get(path)
    if expected_audience is None or audience != expected_audience:
        raise SystemExit("typed endpoint audience mismatch")
    if SERVICE_TOKEN is None or OPERATOR_TOKEN is None or BOOTSTRAP_TOKEN is None:
        raise SystemExit("operator credentials are not initialized")
    bearer = OPERATOR_TOKEN if audience == "operator" else SERVICE_TOKEN
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN,
            "X-Authenticated-HA-User": ha_user_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with OPENER.open(request, timeout=10) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                raise SystemExit("provisioning response exceeded size limit")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise SystemExit("provisioning response exceeded size limit")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise SystemExit("provisioning response schema is invalid")
            return value
    except urllib.error.HTTPError as error:
        raise SystemExit(f"provisioning request failed ({error.code})") from error


def confirm(phrase: str) -> None:
    print(f"Type exactly: {phrase}")
    if input("> ").strip() != phrase:
        raise SystemExit("confirmation did not match; nothing further was changed")


def optional_uuid(prompt: str) -> str | None:
    value = input(prompt).strip()
    if not value:
        return None
    return str(uuid.UUID(value))


def reviewed_source_sha256(prompt: str) -> str:
    value = input(prompt).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SystemExit("reviewed legacy source SHA-256 is required")
    return value


def create_person(
    ha_user_id: str,
    name: str,
    stable_id: str | None = None,
    legacy_source_sha256: str | None = None,
) -> str:
    if stable_id is not None:
        if legacy_source_sha256 is None:
            raise SystemExit(
                "a reviewed source digest is required to reuse a stable person UUID"
            )
        result = post(
            "/v1/people/verify-reviewed",
            {
                "person_id": stable_id,
                "display_name": name,
                "legacy_source_sha256": legacy_source_sha256,
            },
            ha_user_id,
            audience="operator",
        )
        return str(result["person_id"])
    body: dict[str, object] = {"display_name": name, "privacy_scope": "private"}
    return str(
        post("/v1/people", body, ha_user_id, audience="operator")["person_id"]
    )


def main() -> None:
    configure_credentials()
    print("Home Agent operator bootstrap (no opt-ins are enabled by this tool)")
    ha_user_id = input("Authenticated HA user UUID from whoami: ").strip()
    display_name = input("Person display name: ").strip()
    if not ha_user_id or not display_name:
        raise SystemExit("HA UUID and display name are required")
    stable_id = optional_uuid("Reviewed legacy person UUID (blank for UUIDv7): ")
    stable_digest = (
        reviewed_source_sha256("Reviewed legacy source SHA-256 from migration: ")
        if stable_id
        else None
    )
    confirm(f"BIND HA USER {ha_user_id} TO {display_name}")

    person_id = create_person(
        ha_user_id, display_name, stable_id, stable_digest
    )
    binding = post(
        "/v1/principal-bindings",
        {
            "ha_user_id": ha_user_id,
            "person_id": person_id,
            "display_label": display_name,
            "confirmation_artifact_id": str(uuid.uuid4()),
        },
        ha_user_id,
        audience="service",
    )
    print(f"Bound principal {binding['principal_id']} to person {person_id}")

    tracker = input("Reviewed device_tracker entity (blank to defer): ").strip()
    if tracker:
        confirm(f"BIND {tracker} TO {display_name}")
        result = post(
            "/v1/source-entity-bindings",
            {
                "source_system": "home_assistant",
                "entity_id": tracker,
                "confirmation_artifact_id": str(uuid.uuid4()),
            },
            ha_user_id,
            audience="service",
        )
        print(f"Created source binding {result['binding_id']}")

    parent_names = [value.strip() for value in input(
        "Parent display names, comma-separated (blank to defer): "
    ).split(",") if value.strip()]
    for parent_name in parent_names:
        parent_stable_id = optional_uuid(
            f"Reviewed legacy UUID for {parent_name} (blank for UUIDv7): "
        )
        parent_stable_digest = (
            reviewed_source_sha256(
                f"Reviewed legacy source SHA-256 for {parent_name}: "
            )
            if parent_stable_id
            else None
        )
        confirm(f"I CONFIRM {parent_name} IS A PARENT OF {display_name}")
        parent_id = create_person(
            ha_user_id,
            parent_name,
            parent_stable_id,
            parent_stable_digest,
        )
        post(
            "/v1/relationships/parent-confirmations",
            {
                "parent_person_id": parent_id,
                "child_person_id": person_id,
                "confirmation_artifact_id": str(uuid.uuid4()),
            },
            ha_user_id,
            audience="service",
        )
        print(f"Confirmed parent edge for {parent_name} ({parent_id})")

    if input("Approve the private Itaipava locality now? [y/N]: ").strip().lower() == "y":
        confirm("I APPROVE THE PRIVATE ITAIPAVA LOCALITY")
        latitude = float(getpass.getpass("Locality latitude (hidden): "))
        longitude = float(getpass.getpass("Locality longitude (hidden): "))
        radius_m = int(input("Reviewed locality radius in metres: ").strip())
        place = post(
            "/v1/places",
            {
                "canonical_name": "Itaipava",
                "place_type": "locality",
                "privacy_scope": "private",
                "travel_greeting_eligible": True,
                "locator": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius_m": radius_m,
                    "confirmation_artifact_id": str(uuid.uuid4()),
                },
            },
            ha_user_id,
            audience="service",
        )
        print(f"Created encrypted private locality {place['place_id']}")

    print(
        "Bootstrap complete. Location memory and greetings remain off until "
        "enabled separately in the Agent UI."
    )


if __name__ == "__main__":
    main()
