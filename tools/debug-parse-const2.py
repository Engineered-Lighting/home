#!/usr/bin/env python3
"""Find where parser thinks the unclosed string is."""
from pathlib import Path

src = Path("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read_text(encoding="utf-8")
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")

depth = 0
in_str = None
escape = False
str_open_at = None

while i < len(src):
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
            str_open_at = None
        i += 1
        continue
    if c in ('"', "'"):
        in_str = c
        str_open_at = i
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
            print(f"closing ] at byte {i} (line {line})")
            break
        i += 1
        continue
    i += 1

if depth != 0 or in_str is not None:
    line = src[:str_open_at].count("\n") + 1 if str_open_at else "?"
    print(f"PROBLEM: depth={depth}, in_str={in_str!r}, str opened at byte {str_open_at} (line {line})")
    if str_open_at:
        print(f"context: {src[str_open_at-50:str_open_at+200]!r}")
