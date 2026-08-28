"""A kernel must be able to execute, and to write what its body writes.

``identity.commit_owner_partner_relationship_e5k`` shipped unable to run at
all, for four separate reasons. Every one was found by executing it; none was
visible in any single diff, and the static suite that shipped with it was
entirely green. These tests encode the four as rules, so the next one fails at
edit time instead of on the first real call.

The rules, in the order the faults appear in a call:

1. A created function must be given an explicit owner. SECURITY DEFINER runs as
   the owner, and these kernels guard on ``current_user``, so a function that
   keeps the migration runner's ownership fails its own opening guard.
2. A function recreated with a changed signature must drop the superseded
   overload, or calls become ambiguous rather than resolving to the new one.
3. A kernel role must actually be granted something, somewhere.
4. A grant is not a policy. On a table that forces RLS, both gates must open.

Scoped to 0022 onward. Earlier migrations distribute grants across
apply-grants.sh in ways this cannot follow, and a rule that flags working code
is worse than no rule.
"""

from __future__ import annotations

import ast
import pathlib
import re

CORE = pathlib.Path(__file__).resolve().parents[1]
VERSIONS = CORE / "alembic" / "versions"
APPLY_GRANTS = CORE.parents[2] / "stack" / "home-agent-deploy" / "apply-grants.sh"

SQL_TYPES = r"uuid|text|timestamptz|jsonb|boolean|integer|numeric"
KERNEL_ROLE = "home_agent_partner_relationship_kernel"


def _new_migrations() -> list[pathlib.Path]:
    return sorted(p for p in VERSIONS.glob("*.py") if p.name >= "0022")


def _constants(source: str) -> dict[str, str]:
    """Module-level string constants, including multi-line concatenations.

    Parsed with ast rather than regex: KERNEL_PREDICATE is a parenthesised
    concatenation across several lines, and a regex for single-line assignments
    silently misses it. That miss made an earlier version of this test skip the
    very policies it exists to check -- an assertion that matches nothing passes
    for the wrong reason.
    """

    values: dict[str, str] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            # f-strings are not literals. Fall back to the assignment's own
            # source text: it still carries the SQL the policy will contain,
            # which is what these assertions inspect.
            segment = ast.get_source_segment(source, node.value)
            if segment:
                values[target.id] = segment
            continue
        if isinstance(value, str):
            values[target.id] = value
    return values


def _resolved(source: str) -> str:
    """Source as the SQL it becomes, so regexes see real statements.

    Two transformations, both of which a naive regex over raw source gets
    wrong. Adjacent string literals are concatenated: a statement written as
    three f-string fragments is one SQL string at run time, but in source it
    carries quotes and newlines a pattern cannot cross. Then module constants
    are substituted for their placeholders.
    """

    joined = re.sub(r'"\s*\n\s*f?"', "", source)
    for name, value in _constants(source).items():
        joined = joined.replace("{" + name + "}", value)
    return joined


def _loop_tables(source: str) -> list[str]:
    """Table names appearing as the first element of a tuple constant.

    Policies and grants written once inside a ``for table, columns in ...``
    loop apply to every table in that tuple. A regex over the loop body sees
    only the literal ``{table}``, so each candidate is substituted in turn and
    the expansions are searched together.
    """

    return re.findall(r'\(\s*"([a-z_]+(?:\.[a-z_]+)?)"\s*,', source)


def _expansions(source: str) -> str:
    """Resolved source, plus one loop expansion per candidate table."""

    resolved = _resolved(source)
    parts = [resolved]
    for table in _loop_tables(source):
        stem = table.split(".", 1)[-1]
        parts.append(resolved.replace("{table}", table).replace("{stem}", stem))
    return "\n".join(parts)


def _created_functions(source: str) -> dict[str, int]:
    """Function name -> declared argument count, from CREATE statements."""

    created: dict[str, int] = {}
    for match in re.finditer(
        r"CREATE (?:OR REPLACE )?FUNCTION\s+([\w.]+)\s*\((.*?)\)\s*RETURNS",
        source,
        re.S,
    ):
        name, params = match.group(1), match.group(2)
        created[name] = len(
            re.findall(rf"^\s*\w+\s+(?:{SQL_TYPES})\b", params, re.M)
        )
    return created


def _upgrade_body(source: str) -> str:
    """Just the upgrade() half, resolved.

    A DROP in downgrade() drops nothing during an upgrade. 0024's downgrade
    names the superseded overload, and counting it let this file pass while the
    overload was still live in the database.
    """

    resolved = _resolved(source)
    start = resolved.find("def upgrade(")
    if start < 0:
        return ""
    end = resolved.find("def downgrade(", start)
    return resolved[start:] if end < 0 else resolved[start:end]


def _owned_signatures(source: str) -> set[tuple[str, int]]:
    """(function name, argument count) pairs given an explicit owner.

    Keyed on the signature, not the name. 0024 sets an owner for its
    fifteen-argument overload; that says nothing about the seventeen-argument
    one 0026 created, and keying on the name alone credited it anyway.
    """

    owned: set[tuple[str, int]] = set()
    for match in re.finditer(
        rf"ALTER FUNCTION\s+([\w.]+)\(([^)]*)\)\s*OWNER TO", _resolved(source), re.S
    ):
        name, params = match.group(1), match.group(2)
        owned.add((name, len(re.findall(rf"\b(?:{SQL_TYPES})\b", params))))
    return owned


def _kernel_roles(source: str) -> set[str]:
    """Kernel roles this migration names in a constant."""

    return {
        value
        for value in _constants(source).values()
        if re.fullmatch(r"home_agent_[a-z_]+_kernel", value)
    }


def _granted_roles(source: str) -> set[str]:
    """Roles this migration grants anything to."""

    return set(re.findall(r"GRANT\s+[^;]*?\bTO\s+([a-z_]+)\s*;", _resolved(source), re.S))


def test_a_created_function_is_given_an_explicit_owner() -> None:
    """Fault 1: 0026 used CREATE OR REPLACE with a changed signature.

    For a changed signature that is a plain CREATE, so the function is owned by
    whoever ran alembic rather than by the kernel role. SECURITY DEFINER then
    sets current_user to that owner and the function's own first guard rejects
    every call. 0024 does this correctly; 0026 did not.
    """

    owned: set[tuple[str, int]] = set()
    for path in _new_migrations():
        owned |= _owned_signatures(path.read_text())

    problems = []
    for path in _new_migrations():
        source = path.read_text()
        # Only functions that guard on current_user are at risk. A helper with
        # no such guard runs correctly whoever owns it, and CREATE OR REPLACE
        # on an unchanged signature preserves the existing owner anyway --
        # 0022's suppression predicate is both, and flagging it would be noise.
        if "current_user" not in source:
            continue
        for name, arity in _created_functions(source).items():
            if (name, arity) not in owned:
                problems.append(
                    f"{path.name} creates {name} with {arity} arguments but "
                    "no migration sets that signature's owner; "
                    "SECURITY DEFINER would run it as the migration runner "
                    "and its own current_user guard would reject it"
                )
    assert not problems, "\n".join(problems)


def test_a_changed_signature_drops_the_superseded_overload() -> None:
    """Fault 2: 0024's 15 arguments and 0026's 17 were both live.

    0026's last two arguments carry defaults, so the adapter's fifteen-argument
    call matched both and failed to resolve at all -- SQLSTATE 42725, not a
    stale caller reaching an older contract but a caller that cannot execute.
    """

    arities: dict[str, list[tuple[str, int]]] = {}
    for path in _new_migrations():
        for name, count in _created_functions(path.read_text()).items():
            arities.setdefault(name, []).append((path.name, count))

    combined = "\n".join(_upgrade_body(p.read_text()) for p in _new_migrations())

    problems = []
    for name, entries in arities.items():
        counts = {count for _, count in entries}
        if len(counts) < 2:
            continue
        superseded = sorted(counts)[:-1]
        for count in superseded:
            dropped = any(
                len(re.findall(rf"\b(?:{SQL_TYPES})\b", match.group(1))) == count
                for match in re.finditer(
                    rf"DROP FUNCTION IF EXISTS\s+{re.escape(name)}\((.*?)\)",
                    combined,
                    re.S,
                )
            )
            if not dropped:
                problems.append(
                    f"{name} is created with {sorted(counts)} arguments across "
                    f"{[m for m, _ in entries]}, but the {count}-argument "
                    "overload is never dropped; a call matching both resolves "
                    "to neither"
                )
    assert not problems, "\n".join(problems)


def test_every_kernel_role_is_granted_something_somewhere() -> None:
    """Fault 3: the partner kernel held zero privileges in the database.

    ``home_agent_partner_relationship_kernel`` is a *different role* from E5f's
    ``home_agent_parent_relationship_kernel`` -- partner, not parent -- and
    that one-word difference is why it went unnoticed: every privilege check
    run against the parent role passes. apply-grants.sh grants only to the
    parent, and grep over the whole tree found the partner role in nothing but
    its own three migrations.

    A role granted nothing anywhere cannot be a mistake worth tolerating, so
    this needs no allowance list -- which is the point. The list this test used
    to carry named tables globally, but a grant is held by a *role*; excusing
    a table because some other kernel holds it is what hid this fault.
    """

    grants_sh = APPLY_GRANTS.read_text() if APPLY_GRANTS.exists() else ""
    granted_in_migrations: set[str] = set()
    for path in _new_migrations():
        granted_in_migrations |= _granted_roles(path.read_text())

    problems = []
    for path in _new_migrations():
        for role in _kernel_roles(path.read_text()):
            if role in granted_in_migrations:
                continue
            if re.search(rf"\bTO\s+{role}\b", grants_sh):
                continue
            problems.append(
                f"{path.name} names kernel role {role}, but no migration and "
                "no line of apply-grants.sh grants it anything; it would fail "
                "with permission denied on its first statement"
            )
    assert not problems, "\n".join(problems)


# Tables that FORCE row-level security and are written by a kernel, with the
# role that writes them. A grant is not enough for these: without a policy
# admitting the role, the table denies its own writer. This cannot be derived
# from migration source -- it is database state -- so it is stated explicitly.
FORCED_RLS_WRITTEN_BY_KERNELS = {
    ("privacy.artifact_registry", "home_agent_identity_finalizer_kernel"),
    ("privacy.artifact_registry", "home_agent_partner_relationship_kernel"),
    ("knowledge.fact_versions", "home_agent_partner_relationship_kernel"),
    ("knowledge.fact_support", "home_agent_partner_relationship_kernel"),
    ("knowledge.memory_transactions", "home_agent_partner_relationship_kernel"),
    (
        "operations.partner_relationship_authority_receipts",
        "home_agent_partner_relationship_kernel",
    ),
    (
        "operations.partner_relationship_authority_receipt_edges",
        "home_agent_partner_relationship_kernel",
    ),
}


def test_forced_rls_tables_have_a_policy_for_each_kernel_that_writes_them() -> None:
    """Fault 4: a grant and a row policy are different gates.

    0024 forced RLS on its receipt tables with only
    ``session_user = 'home_agent_owner'`` -- but SECURITY DEFINER changes
    current_user, not session_user, so the kernel was denied by its own table.
    The same shape recurred on the person kernel, which 0027 granted columns on
    privacy.artifact_registry without adding any policy.

    Permissive policies are OR'd, so the invariant is not "no owner policy";
    it is "some policy admits this role".
    """

    resolved = "\n".join(_expansions(p.read_text()) for p in _new_migrations())

    problems = []
    for table, role in sorted(FORCED_RLS_WRITTEN_BY_KERNELS):
        schema, name = table.split(".")
        # Exact qualification: a loop expansion can place a bare table name
        # under the wrong schema, and an optional-schema pattern would credit
        # a table for a policy written for a different one.
        pattern = (
            rf"CREATE POLICY\s+\S+\s+ON\s+{schema}\.{name}\b[^;]*?"
            rf"TO\s+{role}\b"
        )
        if not re.search(pattern, resolved, re.S):
            problems.append(
                f"{table} forces RLS and is written by {role}, but no "
                "migration creates a policy admitting it; the write would be "
                "denied even though the grant is held"
            )
    assert not problems, "\n".join(problems)


def test_kernel_policies_refuse_a_caller_that_can_set_role() -> None:
    """The row policy must not be weaker than the function's own guard.

    Each kernel refuses a caller able to SET ROLE into it. A policy admitting
    the kernel role without that clause would let the row layer accept what the
    function layer rejects.
    """

    source = (VERSIONS / "0028_owner_partner_receipt_access.py").read_text()
    assert "pg_has_role(session_user" in source
    assert "'SET'" in source
    assert "current_user = '" in source
    assert "session_user = '" in source


def test_the_replay_branch_can_read_what_it_replays() -> None:
    """Replay returns an existing receipt without writing, which needs SELECT.

    Granting only INSERT would make the happy path work and the replay path
    fail -- the worse failure, because it appears only on a retry. Both gates
    are checked: the grant in apply-grants.sh and the row policy in 0028.
    """

    grants = APPLY_GRANTS.read_text()
    migration = (VERSIONS / "0028_owner_partner_receipt_access.py").read_text()
    for table in (
        "operations.partner_relationship_authority_receipts",
        "operations.partner_relationship_authority_receipt_edges",
    ):
        assert re.search(
            rf"GRANT SELECT[^;]*ON {re.escape(table)}\s*\n?\s*TO {KERNEL_ROLE}",
            grants,
            re.S,
        ), f"{table} has no SELECT grant, so replay would fail on a retry"
    assert "FOR SELECT" in migration


def test_the_kernel_can_execute_the_functions_its_body_calls() -> None:
    """The write fence and the block check are function calls, not tables.

    Without EXECUTE on privacy.lock_identity_semantic_write_fence() the kernel
    aborts at its first write barrier, after passing every table check.

    These must be granted in apply-grants.sh, not in a migration. The erasure
    quarantine block revokes ALL PRIVILEGES on identity_person_is_blocked from
    every role in pg_roles, and that script runs after alembic -- so a grant
    made by a migration is removed on the next deploy.
    """

    grants = APPLY_GRANTS.read_text()
    match = re.search(
        rf"GRANT EXECUTE ON FUNCTION(.*?)TO {KERNEL_ROLE}\s*;", grants, re.S
    )
    assert match, f"{KERNEL_ROLE} is granted EXECUTE on nothing"
    granted = match.group(1)
    assert "lock_identity_semantic_write_fence" in granted
    assert "identity_person_is_blocked" in granted


def test_the_kernel_can_reach_the_schemas_it_reads() -> None:
    """Without USAGE the kernel cannot name an object at all.

    This role held no schema USAGE on any of the four schemas its body
    references -- the most basic gap of the five, and the one a table-level
    privilege check never reveals.
    """

    grants = APPLY_GRANTS.read_text()
    match = re.search(
        rf"GRANT USAGE ON SCHEMA([^;]*)TO {KERNEL_ROLE}\s*;", grants, re.S
    )
    assert match, f"{KERNEL_ROLE} holds USAGE on no schema"
    granted = match.group(1)
    for schema in ("identity", "knowledge", "operations", "privacy"):
        assert schema in granted, f"{KERNEL_ROLE} cannot reach schema {schema}"
