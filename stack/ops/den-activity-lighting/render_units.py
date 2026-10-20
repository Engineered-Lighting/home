#!/usr/bin/env python3
"""Render the ``*.in`` unit templates with private values.

    python3 render_units.py --values <private values.json> --host la|jetson --out <dir>

``values.json`` maps each ``@TOKEN@`` (without the @) to its value, per host:
    {"la": {"RELEASE_DIR": "...", ...}, "jetson": {"bridge": {...}, "loop": {...}, "monitor": {...}}}
See the RUNBOOK for the keys. Rendering refuses a template with a token that
has no value, so no placeholder reaches a host. Usernames, paths and URLs stay
in the private values file, never in the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = re.compile(r"@([A-Z][A-Z0-9_]*)@")

# template -> (values section, rendered file name)
TEMPLATES = {
    "la": {
        "units/la/lighting-brain.service.in": ("brain", "lighting-brain.service"),
        "units/la/lighting-brain-health.service.in": ("brain", "lighting-brain-health.service"),
        "units/la/den-ops-monitor.service.in": ("monitor", "den-ops-monitor.service"),
    },
    "jetson": {
        "units/jetson/victoria-ha-bridge.service.in": ("bridge", "victoria-ha-bridge.service"),
        "units/jetson/perception-lab-managed.service.d/50-activity-loop.conf.in":
            ("loop", "perception-lab-managed.service.d/50-activity-loop.conf"),
        "units/jetson/den-ops-monitor.service.in": ("monitor", "den-ops-monitor.service"),
    },
}


def tokens(text):
    return sorted(set(TOKEN.findall(text)))


PLACEHOLDER = re.compile(r"<[^<>]+>")


def render(text, values, allow_placeholders=False):
    missing = [name for name in tokens(text) if name not in values or values[name] in (None, "")]
    if missing:
        raise ValueError("no value for: %s" % ", ".join(missing))
    for name in tokens(text):
        value = str(values[name])
        if "\n" in value:
            raise ValueError("value for %s spans lines" % name)
        if not allow_placeholders and PLACEHOLDER.search(value):
            raise ValueError("value for %s is still a placeholder: %s" % (name, value))
    return TOKEN.sub(lambda m: str(values[m.group(1)]), text)


def render_host(host, values, out_dir, root=HERE, allow_placeholders=False):
    written = []
    for template, (section, name) in TEMPLATES[host].items():
        with open(os.path.join(root, template), encoding="utf-8") as handle:
            text = render(handle.read(), values.get(section, {}), allow_placeholders)
        target = os.path.join(out_dir, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        written.append(target)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--values", required=True)
    parser.add_argument("--host", choices=sorted(TEMPLATES), required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    with open(args.values, encoding="utf-8") as handle:
        values = json.load(handle)
    try:
        for path in render_host(args.host, values.get(args.host, {}), args.out):
            print(path)
    except ValueError as error:
        print("render refused: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
