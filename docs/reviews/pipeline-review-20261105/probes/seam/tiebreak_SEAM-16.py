#!/usr/bin/env python3
"""tiebreak_SEAM-16: den-core's --facts adapter refuses a la-fact-log-1 file re-saved as UTF-16 (PowerShell 5.1 '>').
Is that reachable from a documented owner step, is the refusal clean, and which encodings does it accept?

    python3 -B tiebreak_SEAM-16.py <den-core-live checkout root>

Synthetic data and temp dirs only; no network or hardware.
 1. makes a synthetic la-fact-log-1 day file with den-core's own run.py --synthetic evening;
 2. re-encodes it as: ASCII LF (byte copy, control), ASCII CRLF (what `ssh ... | Set-Content -Encoding ascii`
    writes, the README's journal pattern), UTF-8 with BOM (PowerShell 5.1 `-Encoding utf8`), UTF-16LE+BOM CRLF
    (PowerShell 5.1 `>` / Out-File), and a torn byte copy (last line cut, no final newline);
 3. runs run.py --facts <file> --assume-occupied --policies need incumbent decide on each: exit code, traceback,
    last line, and whether the input file is left byte-identical;
 4. reads how the writer (stack/services/perception-lab/la_fact_log.py) encodes lines, how each sources.py
    adapter decodes, and what the README tells the owner to do with fact files and with UTF-16.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
core = root / "tools" / "den-core"
tmp = Path(tempfile.mkdtemp(prefix="tb16-"))
subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / "syn"), "--synthetic", "evening",
                "--policies", "incumbent"], capture_output=True, text=True, cwd=str(tmp), check=True)
src = sorted((tmp / "syn").glob("la-fact-log-1-*.jsonl"))[0]
raw = src.read_bytes()
text = raw.decode("ascii")
print("RESULT synthetic fact file: %s, %d lines, ascii=%s, crlf=%s" % (src.name, text.count("\n"), raw.isascii(),
                                                                      b"\r\n" in raw))
variants = {
    "ascii LF (byte copy)": raw,
    "ascii CRLF (| Set-Content -Encoding ascii)": text.replace("\n", "\r\n").encode("ascii"),
    "utf-8 BOM (-Encoding utf8, PS 5.1)": b"\xef\xbb\xbf" + raw,
    "utf-16le BOM CRLF (> / Out-File, PS 5.1)": b"\xff\xfe" + text.replace("\n", "\r\n").encode("utf-16-le"),
    "torn byte copy (last line cut)": raw[:-40],
}
for n, (label, data) in enumerate(variants.items()):
    folder = tmp / ("in%d" % n)
    folder.mkdir()
    path = folder / src.name
    path.write_bytes(data)
    q = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / ("out%d" % n)), "--facts",
                        str(path), "--assume-occupied", "--policies", "need", "incumbent", "decide"],
                       capture_output=True, text=True, cwd=str(tmp))
    last = ((q.stdout + q.stderr).strip().splitlines() or [""])[-1]
    print("RESULT %-44s exit %d traceback=%s input_unchanged=%s | %s" % (
        label, q.returncode, "Traceback" in q.stderr, path.read_bytes() == data, last.replace(str(tmp), "<tmp>")[:150]))

writer = (root / "stack/services/perception-lab/la_fact_log.py").read_text(encoding="utf-8")
print("RESULT writer: ensure_ascii=True in json.dumps: %s; lines encoded ascii + LF: %s" % (
    "ensure_ascii=True" in writer, '(text + "\\n").encode("ascii")' in writer))
src_py = (core / "sources.py").read_text(encoding="utf-8")
for fn in ("fact_log", "_fact", "poll_log", "mqtt_timeline", "occupancy_journal", "recorder_commands",
           "recorder_lights", "den_lighting_status", "correction_capture"):
    m = re.search(r"^def %s\(.*?(?=^def |\Z)" % fn, src_py, re.S | re.M)
    body = m.group(0) if m else ""
    how = []
    if re.search(r"(?<![A-Za-z])_text\(", body):
        how.append("_text() (BOM-aware UTF-16/UTF-8)")
    if "read_bytes()" in body or 'decode("ascii")' in body:
        how.append("raw bytes / ascii")
    if 'read_text("ascii")' in body:
        how.append("read_text ascii")
    print("RESULT adapter %-20s decodes via: %s" % (fn, ", ".join(how) or "(delegates)"))
readme = (core / "README.md").read_text(encoding="utf-8")
for pat in (r"Copy the whole folder[^.]*\.", r"All three exports are ASCII\.[^.]*\."):
    m = re.search(pat, readme.replace("\n", " "))
    print("RESULT README: %s" % (m.group(0) if m else "(not found: %s)" % pat))
print("tmp:", tmp)
