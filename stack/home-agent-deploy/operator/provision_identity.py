"""Stage a reviewed HA-account-to-person binding proposal.

This operator tool is deliberately one half of a two-party ceremony.  It can
see an opaque request identifier and review code, but it never receives the
Home Assistant user UUID and it cannot create a principal binding.  The
authenticated subject must confirm the staged proposal in the private Agent
surface.

The tool is interactive by design: private review values are not accepted as
command-line arguments where they would be retained in process listings or
shell history.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.request
import uuid


BASE = "http://127.0.0.1:8104"
MAX_RESPONSE_BYTES = 1024 * 1024
REVIEW_CODE_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{16}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ENDPOINTS = {
    ("GET", "/v1/operator/principal-binding-requests"): 200,
    ("POST", "/v1/people/verify-reviewed"): 200,
    ("POST", "/v1/operator/principal-binding-proposals"): 201,
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


OPERATOR_TOKEN: str | None = None
BOOTSTRAP_TOKEN: str | None = None


def configure_credentials() -> None:
    global OPERATOR_TOKEN, BOOTSTRAP_TOKEN
    OPERATOR_TOKEN = secret("operator_token")
    BOOTSTRAP_TOKEN = secret("bootstrap_token")


def request_json(
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    """Call one exact operator endpoint without subject identity headers."""

    expected_status = ENDPOINTS.get((method, path))
    if expected_status is None:
        raise SystemExit("typed operator endpoint mismatch")
    if (method == "GET") != (body is None):
        raise SystemExit("typed operator request body mismatch")
    if OPERATOR_TOKEN is None or BOOTSTRAP_TOKEN is None:
        raise SystemExit("operator credentials are not initialized")

    headers = {
        "Authorization": f"Bearer {OPERATOR_TOKEN}",
        "X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN,
        "Accept": "application/json",
    }
    encoded = None
    if body is not None:
        encoded = json.dumps(
            body,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE + path,
        data=encoded,
        method=method,
        headers=headers,
    )
    try:
        with OPENER.open(request, timeout=10) as response:
            status_code = getattr(response, "status", None)
            if status_code is None:
                status_code = response.getcode()
            if status_code != expected_status:
                raise SystemExit("operator response status is invalid")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError) as error:
                    raise SystemExit(
                        "operator response length is invalid"
                    ) from error
                if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
                    raise SystemExit("operator response exceeded size limit")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise SystemExit("operator response exceeded size limit")
            try:
                value = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SystemExit("operator response is not valid JSON") from error
            if not isinstance(value, dict):
                raise SystemExit("operator response schema is invalid")
            return value
    except urllib.error.HTTPError as error:
        if error.code == 409 and path.endswith("principal-binding-requests"):
            raise SystemExit(
                "Binding review is unavailable in the current rollout; "
                "no changes were made."
            ) from error
        raise SystemExit(
            f"operator request failed ({error.code}); no binding was created"
        ) from error
    except urllib.error.URLError as error:
        if method == "POST":
            raise SystemExit(
                "operator service response was unavailable; proposal outcome is "
                "unknown, but no binding was created"
            ) from error
        raise SystemExit(
            "operator service is unavailable; no changes were made"
        ) from error


def _exact_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SystemExit(f"{label} response schema is invalid")


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"{label} is invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise SystemExit(f"{label} is invalid") from error
    if str(parsed) != value.lower():
        raise SystemExit(f"{label} is invalid")
    return str(parsed)


def _aware_datetime(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"{label} is invalid")
    return value


def _review_code(value: object) -> str:
    if not isinstance(value, str) or REVIEW_CODE_PATTERN.fullmatch(value) is None:
        raise SystemExit("review code is invalid")
    return value


def list_pending_requests() -> list[dict[str, str]]:
    payload = request_json("GET", "/v1/operator/principal-binding-requests")
    _exact_keys(payload, {"requests"}, "binding request list")
    raw_requests = payload["requests"]
    if not isinstance(raw_requests, list) or len(raw_requests) > 100:
        raise SystemExit("binding request list response schema is invalid")

    requests: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_codes: set[str] = set()
    expected = {"request_id", "review_code", "state", "requested_at", "expires_at"}
    for raw in raw_requests:
        if not isinstance(raw, dict):
            raise SystemExit("binding request response schema is invalid")
        _exact_keys(raw, expected, "binding request")
        request_id = _uuid(raw["request_id"], "request ID")
        review_code = _review_code(raw["review_code"])
        if raw["state"] != "pending":
            raise SystemExit("binding request state is invalid")
        requested_at = _aware_datetime(raw["requested_at"], "request time")
        expires_at = _aware_datetime(raw["expires_at"], "request expiry")
        if request_id in seen_ids or review_code in seen_codes:
            raise SystemExit("binding request list contains a duplicate")
        seen_ids.add(request_id)
        seen_codes.add(review_code)
        requests.append(
            {
                "request_id": request_id,
                "review_code": review_code,
                "requested_at": requested_at,
                "expires_at": expires_at,
            }
        )
    return requests


def select_request(
    requests: list[dict[str, str]], selector: str
) -> dict[str, str]:
    value = selector.strip().upper()
    if REVIEW_CODE_PATTERN.fullmatch(value) is not None:
        matches = [item for item in requests if item["review_code"] == value]
    else:
        try:
            request_id = str(uuid.UUID(selector.strip()))
        except (ValueError, AttributeError) as error:
            raise SystemExit(
                "enter one exact review code or request UUID"
            ) from error
        matches = [item for item in requests if item["request_id"] == request_id]
    if len(matches) != 1:
        raise SystemExit("no unique pending request matched; no changes were made")
    return matches[0]


def reviewed_source_sha256(prompt: str) -> str:
    value = input(prompt).strip().lower()
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise SystemExit("reviewed legacy source SHA-256 is required")
    return value


def reviewed_person_uuid(prompt: str) -> str:
    value = input(prompt).strip()
    return _uuid(value, "reviewed person UUID")


def verify_existing_person(
    person_id: str,
    display_name: str,
    legacy_source_sha256: str,
) -> dict[str, str]:
    if not display_name or display_name != display_name.strip():
        raise SystemExit("exact reviewed display label is required")
    payload = request_json(
        "POST",
        "/v1/people/verify-reviewed",
        {
            "person_id": person_id,
            "display_name": display_name,
            "legacy_source_sha256": legacy_source_sha256,
        },
    )
    _exact_keys(
        payload,
        {"person_id", "display_name", "privacy_scope", "status"},
        "reviewed person",
    )
    verified_id = _uuid(payload["person_id"], "reviewed person UUID")
    if verified_id != person_id or payload["display_name"] != display_name:
        raise SystemExit("reviewed person response did not match the requested record")
    if payload["status"] != "active":
        raise SystemExit("reviewed person is not active")
    if payload["privacy_scope"] not in {"private", "household"}:
        raise SystemExit("reviewed person privacy scope is invalid")
    return {
        "person_id": verified_id,
        "display_name": display_name,
        "privacy_scope": str(payload["privacy_scope"]),
        "status": "active",
    }


def confirm(phrase: str) -> None:
    print(f"Type exactly: {phrase}")
    if input("> ").strip() != phrase:
        raise SystemExit("confirmation did not match; no proposal was staged")


def stage_proposal(
    request: dict[str, str],
    person: dict[str, str],
) -> dict[str, str]:
    operator_request_id = str(uuid.uuid4())
    payload = request_json(
        "POST",
        "/v1/operator/principal-binding-proposals",
        {
            "request_id": request["request_id"],
            "review_code": request["review_code"],
            "person_id": person["person_id"],
            "operator_request_id": operator_request_id,
        },
    )
    expected = {
        "proposal_id",
        "request_id",
        "person_id",
        "reviewed_display_label",
        "state",
        "stage_receipt_digest",
        "staged_at",
        "expires_at",
    }
    _exact_keys(payload, expected, "binding proposal")
    proposal_id = _uuid(payload["proposal_id"], "proposal ID")
    request_id = _uuid(payload["request_id"], "request ID")
    person_id = _uuid(payload["person_id"], "person ID")
    if request_id != request["request_id"] or person_id != person["person_id"]:
        raise SystemExit("binding proposal response did not match the reviewed request")
    if payload["reviewed_display_label"] != person["display_name"]:
        raise SystemExit("binding proposal changed the reviewed display label")
    if payload["state"] != "ready":
        raise SystemExit("binding proposal is not ready for subject confirmation")
    receipt = payload["stage_receipt_digest"]
    if not isinstance(receipt, str) or DIGEST_PATTERN.fullmatch(receipt) is None:
        raise SystemExit("binding proposal receipt is invalid")
    staged_at = _aware_datetime(payload["staged_at"], "proposal stage time")
    expires_at = _aware_datetime(payload["expires_at"], "proposal expiry")
    return {
        "proposal_id": proposal_id,
        "request_id": request_id,
        "person_id": person_id,
        "reviewed_display_label": person["display_name"],
        "stage_receipt_digest": receipt,
        "staged_at": staged_at,
        "expires_at": expires_at,
    }


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit(
            "this interactive tool accepts no command-line arguments"
        )
    configure_credentials()
    print("Home Agent operator binding review (staging only; no opt-ins)")
    requests = list_pending_requests()
    if not requests:
        raise SystemExit(
            "No pending subject binding requests are available; no changes were made."
        )

    print("Pending opaque binding requests:")
    for item in requests:
        print(
            f"- review code {item['review_code']} | request {item['request_id']} "
            f"| expires {item['expires_at']}"
        )
    selected = select_request(
        requests,
        input("Exact review code or request UUID to review: "),
    )

    person_id = reviewed_person_uuid("Existing reviewed person UUID: ")
    display_name = input("Exact reviewed person display label: ")
    source_digest = reviewed_source_sha256(
        "Reviewed legacy source SHA-256 for that person: "
    )
    person = verify_existing_person(person_id, display_name, source_digest)
    phrase = f"STAGE {selected['review_code']} FOR {person['display_name']}"
    confirm(phrase)
    proposal = stage_proposal(selected, person)

    print("Binding proposal staged; no principal was bound by this tool.")
    print(f"Reviewed label: {proposal['reviewed_display_label']}")
    print(f"Stage receipt: {proposal['stage_receipt_digest']}")
    print(f"Proposal expires: {proposal['expires_at']}")
    print(
        "The requesting subject must now open the private Agent surface, review "
        "the exact statement, and confirm it. Location memory and travel "
        "greetings remain off."
    )


if __name__ == "__main__":
    main()
