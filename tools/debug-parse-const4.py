"""Quick bracket-count diagnostic."""
src = open("C:/Claude/home/ha-config/extended_openai_conversation/const.py").read()
# slice from DEFAULT_CONF_FUNCTION_TOOLS [ to its supposed close
start = src.find("DEFAULT_CONF_FUNCTION_TOOLS = [")
# walk forward
i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")
depth = 0
in_str = None
escape = False
trace = []
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
        line = src[:i].count("\n") + 1
        trace.append(("open", i, line, depth))
    elif c == "]":
        depth -= 1
        line = src[:i].count("\n") + 1
        trace.append(("close", i, line, depth))
        if depth == 0:
            print(f"depth-zero close at byte {i} line {line}")
            break
    i += 1
# print first/last few brackets
print(f"\nTotal bracket events: {len(trace)}")
if depth != 0:
    print(f"final depth: {depth}, parser is at: {src[:i].count(chr(10))+1}")
print("\nLast 10 bracket events:")
for ev in trace[-10:]:
    print(f"  {ev}")
print("\nFirst 10 bracket events:")
for ev in trace[:10]:
    print(f"  {ev}")
