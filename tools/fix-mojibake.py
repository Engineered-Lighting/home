"""Repair mojibake in home-people.jsx caused by a PowerShell -replace that
read UTF-8 bytes as cp1252. Common mojibake replacements + a sanity check."""
import re
import sys

PATH = r"C:\Claude\home\app\src\home-people.jsx"

with open(PATH, encoding="utf-8", errors="replace") as f:
    s = f.read()

# Mojibake → correct UTF-8 char map. These are the bytes UTF-8 produces
# when its 2-byte chars get re-read as cp1252.
fixes = {
    "â€”": "—",   # em-dash U+2014
    "â€“": "–",   # en-dash U+2013
    "Â·": "·",   # middle dot U+00B7
    "â€˜": "‘",   # left single quote
    "â€™": "’",   # right single quote
    "â€œ": "“",   # left double quote
    "â€\x9d": "”",   # right double quote
    "â”€": "─",   # box-drawing horizontal
    "â”€": "─",   # safety dup
    "Ã©": "é",
    "Ã¨": "è",
    "Ã±": "ñ",
}

before_len = len(s)
for bad, good in fixes.items():
    s = s.replace(bad, good)

with open(PATH, "w", encoding="utf-8", newline="") as f:
    f.write(s)

# Quick sanity: count any remaining suspicious sequences
suspicious = re.findall(r"â[€\x80-\xbf]", s)
print(f"Fixed {before_len - len(s)} bytes; {len(suspicious)} suspicious sequences remain")
