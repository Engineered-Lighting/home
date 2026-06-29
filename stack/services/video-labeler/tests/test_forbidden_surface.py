"""The recovered gesture timeline UI is a sanctioned UI reference ONLY — its
runtime surface must never leak into this service. Greps the whole service
tree for the banned strings (built by concatenation so this file cannot match
itself; it is excluded from the scan anyway)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".pytest_cache", "__pycache__", ".git", "data"}

FORBIDDEN = [
    "gesture" + "-control",
    "Home" + "Gesture",
    "call_" + "service",
    "spea" + "ker",
    "annou" + "nce",
]


def _scannable_files():
    for p in SERVICE_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(SERVICE_ROOT).parts):
            continue
        if p.resolve() == Path(__file__).resolve():
            continue
        yield p


def test_no_forbidden_surface():
    hits = []
    for p in _scannable_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        for needle in FORBIDDEN:
            if needle.lower() in text:
                hits.append(f"{p.relative_to(SERVICE_ROOT)}: contains '{needle}'")
    assert not hits, "forbidden surface leaked into the service:\n" + "\n".join(hits)


def test_scan_actually_sees_the_tree():
    names = {p.name for p in _scannable_files()}
    assert "main.py" in names and "0001_init.sql" in names
