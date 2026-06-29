#!/usr/bin/env python3
"""Debug the const.py DEFAULT_CONF_FUNCTION_TOOLS parser."""
from pathlib import Path

src = Path("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read_text(encoding="utf-8")
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
print(f"start at byte {start} (line {src[:start].count(chr(10))+1})")

i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")
depth = 0
in_str = None
escape = False
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
        i += 1
        continue
    if c in ('"', "'"):
        in_str = c
        i += 1
        continue
    if c == "[":
        depth += 1
        i += 1
        continue
    if c == "]":
        depth -= 1
        if depth == 0:
            print(f"closing ] at byte {i} (line {src[:i].count(chr(10))+1})")
            break
        i += 1
        continue
    i += 1

print(f"final: depth={depth}, in_str={in_str!r}, last i={i}")
if in_str is not None:
    # Find the line where the unclosed string starts
    print(f"unclosed string detected. char at i={i}: {src[i-30:i+30]!r}")
