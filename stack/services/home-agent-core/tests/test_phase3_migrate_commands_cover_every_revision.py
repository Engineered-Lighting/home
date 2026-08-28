"""Every deployable revision needs a command that can reach it.

A migration in the image with no entrypoint command is unreachable: `migrate`
refuses any target other than the baseline, and the Phase 3 paths take fixed
targets chosen by their own command. Adding `0022`-`0027` without adding a
command left them undeployable, and the failure surfaced as
"HOME_AGENT_EXPECTED_DB_REVISION is not deployable by this image" -- which reads
as a configuration error rather than a missing command.
"""

from __future__ import annotations

import pathlib
import re

ENTRYPOINT = pathlib.Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _entrypoint() -> str:
    return ENTRYPOINT.read_text()


def _phase3_targets() -> set[str]:
    """Revisions some phase3-migrate-* command can actually reach."""

    source = _entrypoint()
    constants = dict(re.findall(r'^([A-Z0-9_]+)="([^"]+)"$', source, re.M))
    reached = set()
    for match in re.finditer(r'run_phase3_migration "\$([A-Z0-9_]+)"', source):
        value = constants.get(match.group(1))
        if value:
            reached.add(value)
    return reached


def _head_revision() -> str:
    """The latest revision in the chain: the one a deployment ends at."""

    chain = {}
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        rev = re.search(r'^revision(?:\s*:\s*[^=]+)? = "([^"]+)"', text, re.M)
        down = re.search(r'^down_revision(?:\s*:\s*[^=]+)? = "([^"]+)"', text, re.M)
        if rev:
            chain[rev.group(1)] = down.group(1) if down else None
    downs = {d for d in chain.values() if d}
    heads = [r for r in chain if r not in downs]
    assert len(heads) == 1, f"expected one head, found {sorted(heads)}"
    return heads[0]


def test_the_head_revision_has_a_command_that_reaches_it() -> None:
    head = _head_revision()
    assert head in _phase3_targets(), (
        f"{head} is the head of the migration chain but no "
        "phase3-migrate-* command targets it, so it cannot be deployed"
    )


def test_the_readiness_pin_has_a_command_that_reaches_it() -> None:
    """The readiness value the services will run at must be reachable, or the
    database can never get to the revision the app insists on."""

    config = (
        ENTRYPOINT.parent / "app" / "config.py"
    ).read_text()
    members = re.search(r"ReadinessMigration = Literal\[(.*?)\]", config, re.S).group(1)
    latest = re.findall(r'"([^"]+)"', members)[-1]
    assert latest in _phase3_targets() or latest.startswith("0006a"), (
        f"the newest ReadinessMigration member {latest} has no migrate command"
    )


def test_each_phase3_command_targets_a_real_revision() -> None:
    """A command pointing at a revision no migration produces would fail only
    when someone ran it."""

    revisions = set()
    for path in VERSIONS.glob("*.py"):
        for line in path.read_text().splitlines():
            if re.match(r"^revision(\s*:\s*[^=]+)? = ", line):
                revisions.add(line.split('"')[1])
    missing = sorted(_phase3_targets() - revisions)
    assert not missing, f"commands target revisions with no migration: {missing}"


def test_commands_reject_extra_arguments() -> None:
    """Each takes a fixed target; an argument would suggest it is selectable."""

    source = _entrypoint()
    for command in re.findall(r"^  (phase3-migrate-[a-z-]+)\)", source, re.M):
        block = source[source.index(f"  {command})"):]
        block = block[: block.index(";;")]
        assert '[ "$#" -eq 1 ]' in block, f"{command} accepts arguments"
