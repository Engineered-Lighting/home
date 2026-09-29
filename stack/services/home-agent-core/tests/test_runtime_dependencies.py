"""Every third-party import in app/ must be a production requirement.

The production image installs only requirements.lock; tests run with the dev
lock. A module imported only at runtime (for example app.lighting_edge_client's
httpx) would pass every test yet fail at container start.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Import name -> distribution name, where they differ.
DISTRIBUTIONS = {"sqlalchemy": "sqlalchemy", "pydantic_settings": "pydantic-settings", "psycopg": "psycopg"}
# Transitive packages imported directly and pinned through a declared requirement.
TRANSITIVE = {"starlette": "fastapi", "anyio": "httpx"}


def declared():
    names = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)", line.strip())
        if match and not line.startswith("-"):
            names.add(match.group(1).lower())
    return names


def imported_top_levels():
    found = {}
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                found.setdefault(module.split(".")[0], set()).add(path.name)
    return found


def test_every_third_party_import_is_a_production_requirement():
    requirements = declared()
    missing = {}
    for name, files in imported_top_levels().items():
        if name in sys.stdlib_module_names or name == "app":
            continue
        distribution = DISTRIBUTIONS.get(name, name.replace("_", "-")).lower()
        if distribution in requirements or TRANSITIVE.get(name) in requirements:
            continue
        missing[name] = sorted(files)
    assert missing == {}, f"imported but not in requirements.txt: {missing}"


def test_httpx_is_pinned_with_hashes_in_the_production_lock():
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    for name in ("httpx==0.28.1", "httpcore==", "certifi=="):
        block = lock.split(name, 1)
        assert len(block) == 2, name
        assert "--hash=sha256:" in block[1].split("\n", 2)[1], name
