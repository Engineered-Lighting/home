"""apply-grants.sh must admit every revision the database can be sitting at.

The script gates its ACL contracts on allowlists of alembic revisions. Each
enumerates the revisions at which a contract is known to hold, and each was a
contiguous run ending at 0021 -- while the deployed database had moved on to
0027. At any revision not on the list the script raises:

    ERROR:  partial identity finalizer E3 object set

and since apply-grants.sh runs as a compose service during deploy, the grant
stage of the next deploy fails.

Nothing caught it because the script had never once run against a database at
0022 or later. The hosted gate stopped migrating at 0021, and the Phase 3
activation ran its grant stage before the 0022-0027 migrations applied. Adding
a migration is therefore not enough on its own: every one of these lists has to
learn about it, or the deploy tooling stops working at exactly the revision the
deployment now reports.

These lists are contiguous by construction -- a run from the revision that
introduces the contract through to the head -- so the rule is that they may not
end early and may not have holes.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"
VERSIONS = ROOT / "stack/services/home-agent-core/alembic/versions"


def _revision_chain() -> list[str]:
    """Every alembic revision, ordered by its down_revision links."""

    down: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        source = path.read_text()
        revision = re.search(r'^revision: str = "([^"]+)"', source, re.M)
        parent = re.search(r"^down_revision: str \| None = (.+)$", source, re.M)
        if not revision:
            continue
        value = parent.group(1).strip() if parent else "None"
        down[revision.group(1)] = (
            None if value == "None" else value.strip("\"'")
        )

    children = {parent: child for child, parent in down.items()}
    chain: list[str] = []
    node = next(rev for rev, parent in down.items() if parent is None)
    while node is not None:
        chain.append(node)
        node = children.get(node)
    return chain


def _allowlists() -> dict[str, list[str]]:
    source = GRANTS.read_text()
    lists: dict[str, list[str]] = {}
    for match in re.finditer(
        r"(reviewed_\w+) constant text\[\] := ARRAY\[(.*?)\]::text\[\];",
        source,
        re.S,
    ):
        revisions = re.findall(r"'(\d[0-9a-z_]+)'", match.group(2))
        if revisions:
            lists[match.group(1)] = revisions
    return lists


def test_every_reviewed_allowlist_reaches_the_head_revision() -> None:
    """A list that stops short makes the deploy fail at the current revision."""

    chain = _revision_chain()
    head = chain[-1]

    problems = []
    for name, revisions in _allowlists().items():
        if revisions[-1] != head:
            missing = chain[chain.index(revisions[-1]) + 1:]
            problems.append(
                f"{name} ends at {revisions[-1]} but the head is {head}; a "
                f"database at any of {missing} raises instead of applying "
                "grants"
            )
    assert not problems, "\n".join(problems)


def test_reviewed_allowlists_have_no_holes() -> None:
    """Each list is a contiguous run, so a gap is an omission, not a policy.

    A deliberate exclusion would mean a revision at which the contract does not
    hold -- which is a reason to fix the contract or the migration, not to skip
    a revision the database can legitimately be sitting at.
    """

    chain = _revision_chain()
    problems = []
    for name, revisions in _allowlists().items():
        expected = chain[chain.index(revisions[0]): chain.index(revisions[-1]) + 1]
        if revisions != expected:
            problems.append(
                f"{name} skips {sorted(set(expected) - set(revisions))}"
            )
    assert not problems, "\n".join(problems)


def test_every_committer_execute_grant_is_restored_after_the_revoke() -> None:
    """A migration's EXECUTE grant to the committer does not survive a deploy.

    apply-grants.sh revokes ALL PRIVILEGES ON ALL FUNCTIONS from
    home_agent_binding_committer near the top, then restores EXECUTE per kernel
    in its own block. A migration that grants EXECUTE to the committer with no
    matching restore block works exactly until the next run of this script, and
    then the path that calls that kernel stops working -- with no error at
    deploy time, only a permission denied the next time somebody uses the
    feature.

    0027 granted E5n and apply-grants.sh had no block for it, so adding a
    person would have broken on the next deploy.
    """

    grants = GRANTS.read_text()
    assert "FROM home_agent_binding_committer" in grants, (
        "the blanket revoke this rule depends on has moved or gone"
    )

    problems = []
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name < "0013":
            continue
        # Normalise the python string plumbing so a statement split by
        # adjacent literals reads as one piece of SQL.
        raw = path.read_text()
        # Resolve module constants: 0027 writes ``TO {CALLER_ROLE}``, so the
        # role never appears literally and a scan for it finds nothing.
        source = re.sub(r"[\"\']\s*\n\s*f?[\"\']", "", raw)
        for name, value in re.findall(
            r'^([A-Z][A-Z0-9_]*) = "([^"]+)"$', raw, re.M
        ):
            source = source.replace("{" + name + "}", value)
        for statement in source.split(";"):
            if "GRANT EXECUTE ON FUNCTION" not in statement:
                continue
            if "binding_committer" not in statement.rsplit("TO", 1)[-1]:
                continue
            named = re.search(
                r"GRANT EXECUTE ON FUNCTION\s+([a-z0-9_]+\.[a-z0-9_]+)", statement
            )
            if not named:
                continue
            function = named.group(1)
            # Accept either form: a literal GRANT naming the signature, or a
            # DO block that resolves the target at run time. E5k needs the
            # latter, because its owner changes across revisions and a
            # hardcoded SET ROLE would be wrong on one side of that change.
            restored = False
            # Match the bare name: a DO block that resolves the target at
            # run time names it via proname, without the schema prefix.
            bare = function.split('.', 1)[1]
            for hit in re.finditer(re.escape(bare), grants):
                window = grants[max(0, hit.start() - 1500): hit.end() + 1500]
                if "home_agent_binding_committer" in window:
                    restored = True
                    break
            if not restored:
                problems.append(
                    f"{path.name} grants EXECUTE on {function} to the "
                    "committer, but apply-grants.sh has no block restoring it "
                    "after the blanket revoke; the grant is erased on the next "
                    "deploy"
                )
    assert not problems, "\n".join(sorted(set(problems)))
