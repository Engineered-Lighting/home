#!/usr/bin/env python3
"""Merge probe: in a tree holding both claude/rung-7-photometry and claude/rung-8-stage1, does
den-photometry import its own synthetic.py or den-light's?

Usage: python3 probe_merge_synthetic_shadow.py <merged-tree-root>
den-photometry/card.py inserts tools/den-light at sys.path[0]; Stage 1 adds tools/den-light/synthetic.py,
so `import synthetic` in den-photometry's tests resolves to den-light's module. Prints RESULT lines.
"""
import subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
code = ("import sys; sys.path.insert(0, 'tools/den-photometry'); import card, synthetic; "
        "print(synthetic.__file__); print(hasattr(synthetic, 'linear_parameters'))")
out = subprocess.run([sys.executable, "-B", "-c", code], cwd=root, capture_output=True, text=True)
lines = out.stdout.split()
path, has = (lines + ["?", "?"])[:2]
print("RESULT synthetic resolves to:", path)
print("RESULT has den-photometry's linear_parameters:", has)
suite = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-photometry", "-p", "test*.py"],
                       cwd=root, capture_output=True, text=True)
tail = [l for l in suite.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
print("RESULT den-photometry suite in this tree:", " ".join(tail))
print("FAIL (shadowed)" if "den-light" in path else "PASS (own module)")
