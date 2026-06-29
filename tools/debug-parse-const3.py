#!/usr/bin/env python3
"""Trace parser state at every string opening to find mismatch."""
from pathlib import Path

src = Path("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read_text(encoding="utf-8")
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")
limit = i + (1336 - 413) * 100  # rough byte limit for the list

depth = 0
in_str = None
escape = False
opens = []  # list of (offset, line, quote_char)

while i < len(src) and i < limit:
    c = src[i]
    if escape:
        escape = False
        i += 1
        continue
    if in_str:
        if c == "\\":
            escape = True
            i += 1
            continue
        if c == in_str:
            in_str = None
        i += 1
        continue
    if c in ('"', "'"):
        in_str = c
        opens.append((i, src[:i].count("\n") + 1, c))
        i += 1
        continue
    if c == "[":
        depth += 1
        i += 1
        continue
    if c == "]":
        depth -= 1
        if depth == 0:
            line = src[:i].count("\n") + 1
            print(f"closing ] at byte {i} (line {line}), opens recorded: {len(opens)}")
            break
        i += 1
        continue
    i += 1
else:
    print(f"hit limit without finding close. depth={depth}, in_str={in_str!r}")
    if opens:
        last = opens[-1]
        print(f"last string open: byte {last[0]} line {last[1]} char {last[2]!r}")
        print(f"context: {src[last[0]-100:last[0]+100]!r}")

# Show last 5 string opens to see where things diverged
print(f"\nlast 5 string opens: {opens[-5:]}")
