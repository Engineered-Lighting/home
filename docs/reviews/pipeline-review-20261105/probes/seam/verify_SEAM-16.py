#!/usr/bin/env python3
"""verify_SEAM-16: does den-core's --facts adapter read a la-fact-log-1 file re-saved as UTF-16LE + BOM + CRLF
(what PowerShell 5.1's '>' / Out-File writes), as its _text()-based adapters do?

    python3 -B verify_SEAM-16.py <wt-root containing den-core-live/>

Makes a synthetic fact day file with den-core's own synthetic evening, writes a UTF-16 copy, and runs run.py
--facts on each (--assume-occupied --policies need incumbent decide). Temp dirs only; no network or hardware."""
import subprocess
import sys
import tempfile
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
core = wt / "den-core-live" / "tools" / "den-core"
tmp = Path(tempfile.mkdtemp(prefix="v16-"))
subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / "syn"), "--synthetic", "evening",
                "--policies", "incumbent"], capture_output=True, text=True, cwd=str(tmp), check=True)
src = sorted((tmp / "syn").glob("la-fact-log-1-*.jsonl"))[0]
utf8 = tmp / "in8" / src.name
utf8.parent.mkdir()
utf8.write_bytes(src.read_bytes())
utf16 = tmp / "in16" / src.name
utf16.parent.mkdir()
utf16.write_bytes(b"\xff\xfe" + src.read_bytes().decode("ascii").replace("\n", "\r\n").encode("utf-16-le"))
codes = {}
for label, path in (("UTF-8 (control)", utf8), ("UTF-16LE+BOM, CRLF", utf16)):
    p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / ("out-" + path.parent.name)),
                        "--facts", str(path), "--assume-occupied", "--policies", "need", "incumbent", "decide"],
                       capture_output=True, text=True, cwd=str(tmp))
    codes[label] = p.returncode
    last = ((p.stdout + p.stderr).strip().splitlines() or [""])[-1]
    print("RESULT --facts %s: exit %d, traceback=%s: %s" % (label, p.returncode, "Traceback" in p.stderr, last[:220]))
sys.path.insert(0, str(core))
import sources  # noqa: E402
print("RESULT sources._text() on the same UTF-16 file decodes:", sources._text(utf16).splitlines()[0][:60], "...")
ok = codes["UTF-8 (control)"] == 0 and codes["UTF-16LE+BOM, CRLF"] != 0
print("PASS (defect reproduced)" if ok else "FAIL (defect not reproduced)")
print("tmp:", tmp)
