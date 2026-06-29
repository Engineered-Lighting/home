"""Find first line where parser depth doesn't match expected nesting."""
src = open("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read()
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")

depth = 0
in_str = None
escape = False
prev_line = 0
states_at_close = []  # Track state at each `]` close

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
    if c == '"' or c == "'":
        in_str = c
        i += 1
        continue
    if c == "[":
        depth += 1
    if c == "]":
        depth -= 1
        line = src[:i].count("\n") + 1
        states_at_close.append((line, depth))
        if depth == 0:
            print(f"closing ] at line {line}")
            break
        if line > 1336:
            print(f"PAST EXPECTED CLOSE: at line {line}, depth={depth}")
            # Walk back to last open bracket
            break
    i += 1

print(f"\nTotal ] closes: {len(states_at_close)}")
print(f"closes near expected end (lines 1330-1340):")
for line, d in states_at_close:
    if 1330 <= line <= 1340:
        print(f"  line {line}: depth after close = {d}")
# Find any negative depths or zero before 1336
zero_before_1336 = [(l,d) for l,d in states_at_close if l < 1336 and d == 0]
print(f"\nzero depth reached at lines: {zero_before_1336}")
