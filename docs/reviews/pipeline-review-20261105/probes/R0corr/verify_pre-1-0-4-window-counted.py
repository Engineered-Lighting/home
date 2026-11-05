"""verify_pre-1-0-4-window-counted: re-runs the reporter's probe_pre_1_0_4_window_counted.py scenario and also
prints phi_hat and the per-block counts, to see what the install-day block does to rung 0's planning value of phi,
the one number the ladder asks this rung for. Also checks the status sensor source for a version attribute.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_pre-1-0-4-window-counted.py <root of the claude/correction-capture checkout>
"""
import contextlib
import io
import runpy
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent
sys.argv = [str(HERE / "probe_pre_1_0_4_window_counted.py"), str(ROOT)]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(str(HERE / "probe_pre_1_0_4_window_counted.py"))
record = g["record"]
per = record["exposure"]["per_session"]
print("RESULT per_session (block, h, people)=%s" % [(s["session_id"], s["occupied_h"], s["corrections"]) for s in per])
print("RESULT phi_hat with the install day=%s" % record["exposure"]["phi_hat"])
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
after = [s for s in per if s["session_id"] >= "2026-10-28"]
print("RESULT phi_hat from 1.0.4 blocks only=%s" % (capture.phi_hat(after)[0],))
print("RESULT verdict no_game=%s" % record["planning"]["strata"]["no_game"]["verdict"])
src = (ROOT / "ha-config" / "custom_components" / "den_lighting" / "__init__.py").read_text("utf-8")
print("RESULT status() mentions a software version: %s" % ("manifest" in src and "version" in src.split(
    "def status", 1)[-1].split("def ", 1)[0]))
