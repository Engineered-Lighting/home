"""Print parser state at every line between 413 and 1336 to find anomaly."""
src = open("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read()
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")

depth = 0
in_str = None
escape = False
last_line = 0
events_per_line = {}

while i < len(src):
    c = src[i]
    line = src[:i].count("\n") + 1
    if line > 1336:
        break
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
            events_per_line.setdefault(line, []).append(f"close{c}")
        i += 1
        continue
    if c == '"' or c == "'":
        in_str = c
        events_per_line.setdefault(line, []).append(f"open{c}")
        i += 1
        continue
    if c == "[":
        depth += 1
        events_per_line.setdefault(line, []).append(f"[d{depth}")
    if c == "]":
        depth -= 1
        events_per_line.setdefault(line, []).append(f"]d{depth}")
        if depth == 0:
            print(f"closing ] at line {line}")
            break
    i += 1

# Find lines where in_str is left set across a newline (indicates broken parse)
print(f"Final state at line 1336: depth={depth}, in_str={in_str!r}")
# Find lines with odd number of quote events
problem_lines = []
for line, events in events_per_line.items():
    opens = sum(1 for e in events if e.startswith("open"))
    closes = sum(1 for e in events if e.startswith("close"))
    if opens != closes:
        problem_lines.append((line, opens, closes, events))
print(f"\nLines with odd quote counts (first 5):")
for line, opens, closes, events in problem_lines[:5]:
    print(f"  line {line}: open={opens}, close={closes}: {events[:10]}")
print(f"\nTotal problem lines: {len(problem_lines)}")
