"""Trace string opens and closes to find mismatched quotes."""
src = open("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read()
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")
limit = i + 60000  # roughly past line 1336

depth = 0
in_str = None
escape = False
stack = []  # (open_byte, open_line, char)
events = []

while i < min(len(src), limit):
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
            line = src[:i].count("\n") + 1
            opener = stack.pop()
            events.append(("close", opener[1], line, opener[2]))
            in_str = None
        i += 1
        continue
    if c == '"' or c == "'":
        line = src[:i].count("\n") + 1
        in_str = c
        stack.append((i, line, c))
        i += 1
        continue
    if c == "[":
        depth += 1
    if c == "]":
        depth -= 1
        if depth == 0:
            line = src[:i].count("\n") + 1
            print(f"closing ] at line {line}")
            break
    i += 1

# Find last 5 string events to see what happens around line 1336
print(f"\nstack at end: {stack[-5:]}")
print(f"events near line 1330-1340:")
for ev in events:
    if 1325 <= ev[1] <= 1345 or 1325 <= ev[2] <= 1345:
        print(f"  {ev}")
print(f"\nlast 5 string events: {events[-5:]}")
print(f"\nfinal depth={depth}, in_str={in_str!r}")
