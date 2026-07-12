from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .config import Settings
from .db import Database
from .ledger import EncryptedErasureLedger
from .models import RolloutAuthorizationRequest
from .restore import RestoreQuarantineGate, RestoreReplay
from .resources import inspect_disk_budget
from .rollout import RolloutAuthorizationService


MAX_ROLLOUT_REQUEST_BYTES = 4096


def ledger_init() -> int:
    raw_key = os.environ.get("HOME_AGENT_ERASURE_LEDGER_KEY", "")
    if not raw_key:
        raise SystemExit("ledger init requires HOME_AGENT_ERASURE_LEDGER_KEY")
    try:
        key = base64.urlsafe_b64decode(raw_key + "=" * (-len(raw_key) % 4))
    except Exception as exc:
        raise SystemExit("invalid erasure ledger key") from exc
    if len(key) != 32:
        raise SystemExit("erasure ledger key must decode to 32 bytes")
    path = Path(
        os.environ.get("HOME_AGENT_ERASURE_LEDGER_PATH", "/erasure-ledger/ledger.jsonl")
    )
    head_path = Path(
        os.environ.get(
            "HOME_AGENT_ERASURE_LEDGER_HEAD_PATH",
            "/erasure-ledger/ledger.head.json",
        )
    )
    if path.exists() or head_path.exists():
        raise SystemExit("erasure ledger init refuses existing ledger state")
    EncryptedErasureLedger(path, key, head_path=head_path, initialize=True)
    print('{"ledger_epoch":0,"status":"initialized"}')
    return 0


async def restore_replay(settings: Settings) -> int:
    database = Database(settings.async_database_url())
    try:
        ledger = EncryptedErasureLedger(
            settings.erasure_ledger_path,
            settings.decoded_erasure_ledger_key(),
            head_path=settings.erasure_ledger_head_path,
        )
        count = await RestoreReplay(database, ledger).replay()
        status = await RestoreQuarantineGate(
            database, settings.erasure_ledger_head_path, cache_seconds=0
        ).status(force=True)
        if not status.current:
            raise RuntimeError(f"restore quarantine remains active: {status.code}")
        print(
            json.dumps(
                {
                    "status": "current",
                    "replayed_entries": count,
                    "ledger_epoch": status.ledger_epoch,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


async def restore_verify(settings: Settings) -> int:
    database = Database(settings.async_database_url())
    try:
        status = await RestoreQuarantineGate(
            database, settings.erasure_ledger_head_path, cache_seconds=0
        ).status(force=True)
        print(
            json.dumps(
                {
                    "status": status.code,
                    "current": status.current,
                    "ledger_epoch": status.ledger_epoch,
                    "database_epoch": status.database_epoch,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0 if status.current else 78
    finally:
        await database.close()


def rollout_request_from_stdin() -> RolloutAuthorizationRequest:
    raw = sys.stdin.buffer.read(MAX_ROLLOUT_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_ROLLOUT_REQUEST_BYTES:
        raise SystemExit("shadow authorization request is missing or oversized")

    def exact_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    try:
        payload = json.loads(raw, object_pairs_hook=exact_object)
        if not isinstance(payload, dict):
            raise ValueError("request must be an object")
        return RolloutAuthorizationRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        raise SystemExit("shadow authorization request is invalid") from exc


async def authorize_shadow(
    settings: Settings, request: RolloutAuthorizationRequest
) -> int:
    database = Database(settings.async_database_url())
    try:
        revision = await database.migration_revision()
        if revision != settings.readiness_migration:
            raise SystemExit("shadow authorization database revision is not current")
        restore = await RestoreQuarantineGate(
            database,
            settings.erasure_ledger_head_path,
            cache_seconds=0,
        ).status(force=True)
        if not restore.current:
            raise SystemExit(
                f"shadow authorization restore gate is not current: {restore.code}"
            )
        disk = inspect_disk_budget(settings.storage_monitor_path)
        if not disk.optional_work_allowed:
            raise SystemExit(
                f"shadow authorization storage gate rejected: {disk.state}"
            )
        receipt = await RolloutAuthorizationService(
            database, settings
        ).authorize_shadow(request)
        print(
            json.dumps(
                receipt.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="home-agent-core")
    parser.add_argument(
        "command",
        choices=(
            "ledger-init",
            "restore-replay",
            "restore-verify",
            "authorize-shadow",
        ),
    )
    args = parser.parse_args()
    if args.command == "ledger-init":
        return ledger_init()
    settings = Settings()
    if args.command == "authorize-shadow":
        if settings.role != "rollout":
            raise SystemExit("shadow authorization requires HOME_AGENT_ROLE=rollout")
        return asyncio.run(authorize_shadow(settings, rollout_request_from_stdin()))
    if settings.role != "restore":
        raise SystemExit("restore commands require HOME_AGENT_ROLE=restore")
    if args.command == "restore-replay":
        return asyncio.run(restore_replay(settings))
    return asyncio.run(restore_verify(settings))


if __name__ == "__main__":
    raise SystemExit(main())
