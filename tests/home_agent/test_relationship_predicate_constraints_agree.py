"""A migration that replaces a CHECK must name the constraint that exists.

0030 widened the relationship vocabulary in three places: the kernel's own
whitelist, the symmetry decision, and the receipt table's CHECK. The first two
were correct. The third named ``partner_relationship_receipt_predicate_known``
-- a constraint no migration has ever created. ``DROP CONSTRAINT IF EXISTS``
matched nothing, the ``ADD`` installed a second and permissive CHECK, and
0026's ``partner_receipt_predicate`` stayed in force restricting the column to
``partner_of`` and ``parent_of``.

Both CHECKs must pass for a row to be written, so the narrower one decides. The
migration would have applied cleanly, reported success, and left every predicate
it claimed to admit still failing at INSERT.

Nothing caught it because the suites around this assert that a migration
*applies*, which this one does. The defect lives in the seam between the name a
migration writes and the name the database carries, so this replays the
constraint names across the whole revision chain and asserts what survives.

The SQL is rendered through ``ast`` rather than read as text: the migrations
build statements from implicitly concatenated literals with the table and
constraint names interpolated, and a reader that misses either sees nothing at
all -- which is how a name matching no constraint looked fine.
"""

from __future__ import annotations

import ast
import pathlib
import re

VERSIONS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "stack/services/home-agent-core/alembic/versions"
)

RECEIPTS = "operations.partner_relationship_authority_receipts"
FACTS = "knowledge.fact_versions"

# Every predicate the kernel admits after 0030.
VOCABULARY = {
    "colleague_of",
    "friend_of",
    "neighbor_of",
    "parent_of",
    "partner_of",
    "roommate_of",
    "sibling_of",
}

_REVISION = re.compile(r'^revision(?::\s*str)?\s*=\s*"([^"]+)"', re.M)
_DOWN = re.compile(r'^down_revision(?::\s*str\s*\|\s*None)?\s*=\s*"([^"]+)"', re.M)
_ADD = re.compile(
    r"ALTER TABLE\s+(?P<table>[\w.]+)\s+ADD CONSTRAINT\s+(?P<name>\w+)"
    r"\s+CHECK\s*\((?P<body>.*?)\);",
    re.S | re.I,
)
_DROP = re.compile(
    r"ALTER TABLE\s+(?P<table>[\w.]+)\s+DROP CONSTRAINT\s+(?:IF EXISTS\s+)?"
    r"(?P<name>\w+)\s*[;,]",
    re.S | re.I,
)


def _constants(tree: ast.Module) -> dict[str, str]:
    """Module-level string constants, so ``{RECEIPT_CHECK}`` resolves."""

    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found[target.id] = node.value.value
    return found


def _render(node: ast.AST, consts: dict[str, str]) -> str:
    """SQL text of a literal expression, resolving f-string placeholders."""

    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        out: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                inner = part.value
                if isinstance(inner, ast.Name) and inner.id in consts:
                    out.append(consts[inner.id])
                else:
                    # Unresolvable: a placeholder word keeps the SQL parseable.
                    out.append("UNRESOLVED")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render(node.left, consts) + _render(node.right, consts)
    return ""


def _statements(text: str) -> list[str]:
    """Every SQL string ``upgrade()`` hands to ``op.execute``, rendered."""

    tree = ast.parse(text)
    consts = _constants(tree)
    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if upgrade is None:
        return []
    out: list[str] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        for arg in node.args:
            sql = _render(arg, consts)
            if sql:
                out.append(sql)
    return out


def _chain() -> list[tuple[str, str]]:
    """Migration bodies in revision order, oldest first."""

    by_revision: dict[str, tuple[str, str]] = {}
    parent: dict[str, str | None] = {}
    for path in VERSIONS.glob("0*.py"):
        text = path.read_text(encoding="utf-8")
        rev = _REVISION.search(text)
        if rev is None:
            continue
        by_revision[rev.group(1)] = (path.name, text)
        down = _DOWN.search(text)
        parent[rev.group(1)] = down.group(1) if down else None

    children = {down: rev for rev, down in parent.items() if down is not None}
    head = next(rev for rev, down in parent.items() if down is None)
    ordered: list[tuple[str, str]] = []
    cursor: str | None = head
    while cursor is not None:
        ordered.append(by_revision[cursor])
        cursor = children.get(cursor)
    return ordered


def _final_checks() -> dict[str, dict[str, str]]:
    """Replay every ADD/DROP CONSTRAINT to get the surviving CHECK bodies."""

    live: dict[str, dict[str, str]] = {}
    for _name, text in _chain():
        for sql in _statements(text):
            for match in _DROP.finditer(sql):
                live.get(match.group("table"), {}).pop(match.group("name"), None)
            for match in _ADD.finditer(sql):
                live.setdefault(match.group("table"), {})[match.group("name")] = (
                    match.group("body")
                )
    return live


def _admitted(check_body: str) -> set[str]:
    return set(re.findall(r"'(\w+_of)'", check_body))


def test_the_chain_is_readable() -> None:
    """A guard on the reader itself: silence here would pass everything."""

    chain = _chain()
    assert len(chain) > 20, f"only {len(chain)} migrations walked"
    checks = _final_checks()
    assert RECEIPTS in checks, f"no CHECK constraints found on {RECEIPTS}"
    assert FACTS in checks, f"no CHECK constraints found on {FACTS}"


def test_exactly_one_reflexive_check_survives() -> None:
    """0023's partner-only form is subsumed; leaving both is dead weight."""

    checks = _final_checks().get(FACTS, {})
    reflexive = {n: b for n, b in checks.items() if "reflexive" in n}
    assert len(reflexive) == 1, (
        f"{len(reflexive)} reflexive CHECK constraints on {FACTS}: {sorted(reflexive)}"
    )
    admitted = _admitted(next(iter(reflexive.values())))
    assert admitted == VOCABULARY, (
        f"the surviving reflexive CHECK covers {sorted(admitted)}, "
        f"not {sorted(VOCABULARY)}"
    )


def test_every_receipt_check_admits_the_whole_vocabulary() -> None:
    """``partner_receipt_contract`` pinned the vocabulary a second time.

    It binds each predicate to the edge count the kernel writes for it, so it
    rejected new predicates outright rather than as unlisted names -- a stricter
    block than the list constraint, and invisible to a check that only looks for
    a predicate whitelist.
    """

    for name, body in _final_checks().get(RECEIPTS, {}).items():
        admitted = _admitted(body)
        if not admitted:
            continue
        assert admitted == VOCABULARY, (
            f"{name} admits {sorted(admitted)}, not the full vocabulary. "
            f"Missing: {sorted(VOCABULARY - admitted)}"
        )


def test_receipt_edge_counts_follow_the_kernels_symmetry() -> None:
    """One edge for the asymmetric predicate, two for every symmetric one.

    0030 inverted the kernel's symmetry decision -- ``parent_of`` is named as the
    asymmetric predicate and everything else is symmetric -- so a predicate can
    never be admitted to the vocabulary and silently stored one-way. The receipt
    contract has to state the same rule or it contradicts the kernel it records.
    """

    checks = _final_checks().get(RECEIPTS, {})
    contract = next(
        (b for n, b in checks.items() if "edge_count" in b),
        None,
    )
    assert contract is not None, "no receipt CHECK constrains edge_count"

    one_edge = set(re.findall(r"predicate = '(\w+_of)' AND edge_count = 1", contract))
    assert one_edge == {"parent_of"}, (
        f"predicates written as a single edge: {sorted(one_edge)}; "
        f"only parent_of is asymmetric"
    )

    two_edge = set(re.findall(r"'(\w+_of)'", contract)) - one_edge
    assert two_edge == VOCABULARY - {"parent_of"}, (
        f"symmetric predicates in the contract: {sorted(two_edge)}, "
        f"expected {sorted(VOCABULARY - {'parent_of'})}"
    )


_INDEX = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+(?P<name>\w+)\s+"
    r"ON\s+(?P<table>[\w.]+)\s*\((?P<key>.*?)\)\s*WHERE\s+(?P<where>.*?);",
    re.S | re.I,
)
_DROP_INDEX = re.compile(
    r"DROP\s+INDEX(?:\s+IF\s+EXISTS)?\s+(?P<name>[\w.]+)\s*;", re.S | re.I
)


def _final_unique_indexes() -> dict[str, tuple[str, str]]:
    """Surviving partial unique indexes, as ``name -> (key, where)``."""

    live: dict[str, tuple[str, str]] = {}
    for _name, text in _chain():
        for sql in _statements(text):
            for match in _DROP_INDEX.finditer(sql):
                live.pop(match.group("name").split(".")[-1], None)
            for match in _INDEX.finditer(sql):
                live[match.group("name")] = (match.group("key"), match.group("where"))
    return live


def test_no_unique_index_is_scoped_to_a_single_predicate() -> None:
    """A per-predicate index goes stale silently every time the vocabulary moves.

    0023 and 0026 each scoped one to a single predicate. 0030 widened the
    vocabulary and left both, so the five new predicates had no uniqueness guard
    at all -- the same friendship could be recorded twice with nothing to reject
    the second. An index keyed on ``predicate`` instead cannot go stale.
    """

    stale = {}
    for name, (key, where) in _final_unique_indexes().items():
        scoped = set(re.findall(r"predicate\s*=\s*'(\w+_of)'", where))
        if scoped:
            stale[name] = sorted(scoped)
    assert not stale, (
        f"unique indexes scoped to one predicate: {stale}. Put predicate in the "
        f"index key and admit the whole vocabulary in WHERE instead."
    )


def test_the_relationship_uniqueness_index_covers_the_vocabulary() -> None:
    indexes = _final_unique_indexes()
    covering = {
        name: (key, where)
        for name, (key, where) in indexes.items()
        if "person_id" in key and _admitted(where)
    }
    assert covering, "no unique index guards relationship uniqueness"
    for name, (key, where) in covering.items():
        assert "predicate" in key, (
            f"{name} does not key on predicate, so it cannot tell two "
            f"predicates apart between the same pair of people"
        )
        admitted = _admitted(where)
        assert admitted == VOCABULARY, (
            f"{name} guards {sorted(admitted)}, missing "
            f"{sorted(VOCABULARY - admitted)}"
        )
