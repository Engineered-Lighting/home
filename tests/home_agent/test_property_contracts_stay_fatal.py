"""The assertions that protect people must stay fatal, not become warnings.

The Phase 3 activation contracts were retired -- made non-fatal -- because they
verify the shape a completed migration left behind, and could not be satisfied
and extended at the same time: adding an RLS policy so a new relationship kernel
can write its own receipts necessarily changes a digest describing the finished
migration.

A different set of assertions in the same file protects the property the system
exists for: an agent cannot invent, infer, or silently widen what it knows about
people. Those must keep the power to stop a deploy. They are listed here by name
so that retiring one is a deliberate edit to this file, rather than a side
effect of a bulk change to apply-grants.sh.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"

# Assertions whose removal would let a fact about a person be fabricated,
# widened, or leaked -- or would let a privilege appear that nobody granted.
MUST_STAY_FATAL = (
    # An erased person stays erased.
    "identity erasure kernel ownership/membership invalid",
    "partial identity erasure E2 object set",
    "identity erasure E2 function ownership invalid",
    # A kernel cannot be swapped, re-owned, or bypassed.
    "identity finalizer E3 dormant role contract mismatch",
    "identity finalizer E3 ownership dependency mismatch",
    "identity finalizer E3 function contract mismatch",
    "identity finalizer E3 write-fence contract mismatch",
    "identity finalizer E3 evidence policy set mismatch",
    # No privilege appears that nobody granted, and nothing leaks to PUBLIC.
    "identity finalizer E3 grant option detected",
    "identity finalizer E3 default ACL mismatch",
    "identity finalizer E3 PUBLIC ACL mismatch",
    "identity finalizer E3 column ACL mismatch",
    "identity finalizer E3 function ACL mismatch",
    "identity finalizer E3 effective function ACL mismatch",
    "identity finalizer E3 control policy set mismatch",
    # The binding that authenticates a caller.
    "principal-binding E5b dormant role contract mismatch",
    "principal-binding E5b ownership contract mismatch",
    "principal-binding E5b function contract mismatch",
    "principal-binding E5b support graph contract mismatch",
    "principal-binding E5b fence trigger contract mismatch",
    "principal-binding E5b receipt quarantine mismatch",
    "principal-binding E5b broad quarantine mismatch",
    "partial or revision-mismatched principal-binding E5b object set",
    "identity principal-binding E5b catalog admission digest mismatch",
    # Roles that must stay dormant, and callers that must stay pinned.
    "identity cutover E4 dormant role contract mismatch",
    "current-authority E5 caller role contract mismatch",
    "current-authority E5 reviewed E5b policy overlay mismatch",
    # The kernels actually in use.
    "authenticated binding E5c active ACL contract mismatch",
    "parent relationship E5e active ACL contract mismatch",
    "parent relationship E5f active ACL contract mismatch",
    "parent relationship E5h active ACL contract mismatch",
)


def _raised_at(source: str, level: str, message: str) -> bool:
    return bool(re.search(rf"RAISE {level}\s+'{re.escape(message)}'", source))


def test_property_protecting_assertions_can_still_stop_a_deploy() -> None:
    source = GRANTS.read_text(encoding="utf-8")

    problems = []
    for message in MUST_STAY_FATAL:
        if _raised_at(source, "EXCEPTION", message):
            continue
        if _raised_at(source, "WARNING", message):
            problems.append(
                f"{message!r} was downgraded to a warning. It protects the "
                "property that a fact about a person cannot be fabricated, "
                "widened, or leaked, so it must be able to stop a deploy."
            )
        else:
            problems.append(
                f"{message!r} is no longer raised at all. If that was "
                "deliberate, remove it from this list in the same commit."
            )
    assert not problems, "\n".join(problems)


def test_the_rule_detects_a_downgrade() -> None:
    """The check above is only worth having if it would actually fire.

    Verified on a copy in memory rather than by editing the real script: a
    temporary downgrade-and-restore of a live security assertion is exactly the
    edit this file exists to prevent.
    """

    sample = "    RAISE EXCEPTION 'identity finalizer E3 grant option detected'\n"
    assert _raised_at(sample, "EXCEPTION", "identity finalizer E3 grant option detected")
    downgraded = sample.replace("RAISE EXCEPTION", "RAISE WARNING")
    assert not _raised_at(
        downgraded, "EXCEPTION", "identity finalizer E3 grant option detected"
    )
    assert _raised_at(
        downgraded, "WARNING", "identity finalizer E3 grant option detected"
    )


def test_enforcement_statements_are_present() -> None:
    """Retiring assertions must never remove the enforcement itself.

    The contracts verify; the GRANT, REVOKE and CREATE POLICY statements are
    what actually decide who may read and write. A change that removed those
    would be silent -- every remaining assertion would still pass.
    """

    source = GRANTS.read_text(encoding="utf-8")
    assert len(re.findall(r"^\s*GRANT ", source, re.M)) >= 140
    assert len(re.findall(r"^\s*REVOKE ", source, re.M)) >= 70
    assert len(re.findall(r"^\s*CREATE POLICY", source, re.M)) >= 6
    assert "REVOKE ALL PRIVILEGES ON TABLES FROM home_agent_api" in source
    assert "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC" in source
