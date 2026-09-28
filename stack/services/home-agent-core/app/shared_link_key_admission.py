"""Admit the linking coordinator's commitment key for shared-link issuance.

Reviewed operator maintenance only, run once in a SERIALIZABLE owner
transaction at exactly schema 0047. Every identity kernel that issues,
inspects or confirms a shared-link ceremony refuses with
``shared_link_key_not_admitted`` until ``privacy.shared_link_key_admission``
holds the coordinator's key reference and fingerprint. Migrations create that
table empty and no runtime role may write it.

``admit`` records the exact key the coordinator profile names. Repeating it for
the same key is a no-op. A different or blocked key is refused: changing the
commitment key requires a reviewed history migration (see
``SharedLinkCommitments``), never a quiet replacement. The key never leaves
this process; only its fingerprint is stored, and neither is printed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

from .shared_link_commitments import SharedLinkCommitments
from .shared_preference_roles import _engine, _require_owner_transaction

SCOPE = "shared-link-v1"
TABLE = "privacy.shared_link_key_admission"


def read_commitments(key_file, key_id):
    """Load the key exactly as the coordinator profile does: one hex line."""
    path = Path(key_file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 65:
        raise ValueError("mounted coordinator commitment key required")
    raw = path.read_bytes().removesuffix(b"\n")
    if re.fullmatch(b"[a-f0-9]{64}", raw) is None:
        raise ValueError("dedicated coordinator secret required")
    return SharedLinkCommitments(bytes.fromhex(raw.decode("ascii")), key_id=key_id)


def _current(connection, *, lock=False):
    return connection.execute(text(f"SELECT key_id,key_fingerprint,revision,state FROM {TABLE} WHERE scope=:scope"
                                   + (" FOR UPDATE" if lock else "")), {"scope": SCOPE}).one_or_none()


def admit(connection, commitments):
    _require_owner_transaction(connection)
    # Serialize concurrent admissions; the primary key allows one row per scope.
    connection.execute(text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    row = _current(connection, lock=True)
    if row is None:
        connection.execute(text(f"INSERT INTO {TABLE} (scope,key_id,key_fingerprint,revision,state) "
                                "VALUES (:scope,:key_id,:fingerprint,1,'active')"),
                           {"scope": SCOPE, "key_id": commitments.key_id, "fingerprint": commitments.fingerprint})
        return "admitted"
    if (row.key_id, row.key_fingerprint, row.state) == (commitments.key_id, commitments.fingerprint, "active"):
        return "unchanged"
    raise ValueError("a different or blocked shared-link key is already recorded")


def status(connection, commitments=None):
    row = _current(connection)
    result = {"admitted": row is not None and row.state == "active"}
    if row is not None:
        result.update(key_id=row.key_id, revision=row.revision, state=row.state)
        if commitments is not None:
            result["matches"] = (row.key_id, row.key_fingerprint) == (commitments.key_id, commitments.fingerprint)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("admit", "status"))
    parser.add_argument("--database-url-file", required=True)
    parser.add_argument("--key-file")
    parser.add_argument("--key-id")
    args = parser.parse_args()
    if (args.key_file is None) != (args.key_id is None) or (args.mode == "admit" and args.key_file is None):
        raise ValueError("key file and key id are required together, and for admission")
    commitments = read_commitments(args.key_file, args.key_id) if args.key_file else None
    engine = _engine(args.database_url_file)
    try:
        with engine.begin() as connection:
            if args.mode == "status":
                print(json.dumps(status(connection, commitments), sort_keys=True))
                return
            outcome = admit(connection, commitments)
    finally:
        engine.dispose()
    print(f"Shared-link key {outcome}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Shared-link key admission failed", file=sys.stderr)
        raise SystemExit(78)
