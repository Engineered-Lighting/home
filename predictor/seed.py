"""seed.py — load the warm-start zone-adjacency graph (seed_adjacency.yaml).

A zero-dependency parser for the simple `adjacency:` map — no PyYAML, so the
predictor package stays importable in the minimal add-on runtime and the
offline harness alike.
"""

from __future__ import annotations

import re


def parse_adjacency(text):
    """Parse a seed_adjacency.yaml `adjacency:` block -> {zone: set(neigh)},
    made symmetric. Tolerates line-wrapped [...] lists and `#` comments."""
    no_comments = "\n".join(
        line.split("#", 1)[0] for line in text.splitlines())
    adj = {}
    for zone, body in re.findall(r"([A-Za-z0-9_]+)\s*:\s*\[([^\]]*)\]",
                                 no_comments):
        if zone == "adjacency":
            continue
        adj.setdefault(zone, set())
        for n in (n.strip() for n in body.split(",")):
            if n:
                adj[zone].add(n)
                adj.setdefault(n, set()).add(zone)   # symmetric
    return adj


def load_adjacency(path):
    with open(path, "r", encoding="utf-8") as f:
        return parse_adjacency(f.read())
