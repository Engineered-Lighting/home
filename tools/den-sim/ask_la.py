"""The owner's one paste: ask Qwen on LA for every reply the den simulation's clicks still need. Workstation only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-sim-connected\\tools\\den-sim\\ask_la.py

It works from any folder, and refuses to start with no console, before any ssh, so a background run can't. The
bodies are the ones prepare.py --owed filed under <data root>\\prepared\\owed\\, each re-hashed against its name; the
data root is DEN_SIM_HOME, or else %USERPROFILE%\\den-sim. Standard library, plus what it lifts by git archive at
refs.json's pins, each held to its blob: the sofa look's la.py (the LA host, its ssh and scp argument lists) and
ask.py, and qwen_scene.py. The model test's look_la.py, proved on LA, in two visits:

1. Check visit: a fresh /dev/shm/den-sim-ask with umask 077, copy only the tools (ask_one.py, ask.py,
   qwen_scene.py), run the preflight (served name and root, GPU under 77 C with 8 GiB free), remove the folder.
   No image leaves this PC on this visit.
2. Only if that passed: a fresh folder again, copy the tools and the bodies still needed, run ask_one.py under a
   timeout (at most the bodies plus 2 requests, 4 s apart), copy replies back through a .part folder, file each
   well-formed one as <data root>\\prepared\\replies\\<body_sha256>.json, remove the folder.

Before both, a paste cut short may have left replies on LA: they come back first. Any failed step stops the run;
then, if a visit had made the LA folder, it copies back what LA holds and removes the folder, and a failed copy
stops before that removal, so no reply is lost. last-ask.json under prepared\\ says where it stopped and why, what
came back, what is still needed, and each reply's digest, which a click's receipt is held to. One line goes to
prepared\\tries.jsonl per paste. Nothing here prints a body, a reply or exception text (type names only), and the
output is ASCII.
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REMOTE = "/dev/shm/den-sim-ask"
CHECK_TIMEOUT, MARGIN = "2m", 2
REPLY_FIELDS = {"asked_unix", "host", "root", "http_status", "raw", "elapsed_s"}
VLLM_DOC = r"docs\VLLM-QWEN36-SWAP.md"
PREFLIGHT = {10: "vLLM on LA is not answering", 11: "LA's vLLM does not serve qwen3-vl-30b",
             12: "LA's GPU is at 77 C or more", 13: "LA's GPU has under 8 GiB free", 14: "LA's GPU could not be read",
             124: "the preflight timed out", 255: "this PC could not reach LA over ssh"}
ASKED = {0: None, 2: "the LA folder was not private", 20: "two misses in a row", 21: "vLLM refused a request",
         22: "the request cap", 23: "a body's hash changed on the way", 124: "the ask timed out", 255: "the ssh link dropped",
         **{code: PREFLIGHT[code] for code in range(10, 15)}}   # the ask runs the preflight again before any request
AGAIN = "Paste the same line again later."


class Stop(Exception):
    pass


def say(text):
    print(str(text).encode("ascii", "replace").decode("ascii"), flush=True)


def has_console():
    try:
        return sys.stdin is not None and sys.stdout is not None and sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def data_root(env=None):
    """DEN_SIM_HOME when set, or else <home>\\den-sim: the rule data-root.mjs and prepare.py keep."""
    env = os.environ if env is None else env
    return Path(env["DEN_SIM_HOME"]) if env.get("DEN_SIM_HOME") else Path.home() / "den-sim"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def lift(folder):
    """refs.json's lifted files, by git archive at their pins, each held to its pinned blob: {name: path}."""
    pins = json.loads((HERE / "refs.json").read_text(encoding="utf-8"))["lifted"]["files"]
    out = {}
    for path, pin in pins.items():
        tree = Path(folder) / pin["commit"][:12]
        target = tree / path
        if not target.is_file():
            done = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", pin["commit"], path], capture_output=True)
            if done.returncode:
                raise Stop("git archive of %s at %s failed" % (path, pin["commit"][:8]))
            with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
                tar.extractall(tree, filter="data")
        data = target.read_bytes()
        if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != pin["blob"]:
            raise Stop("%s is not the blob refs.json pins" % path)
        out[Path(path).name] = target
    return out


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SSH:
    """The only route to LA: the sofa look's la.py host and its ssh and scp argument lists."""

    def __init__(self, la, runner=subprocess.run):
        self.la, self.runner = la, runner

    def shell(self, text, capture=False, timeout=None):
        return self.runner(self.la.SSH + [text], **(dict(capture_output=True, encoding="ascii", errors="replace") if capture else {}))

    def put(self, paths, remote):
        return self.runner(self.la.SCP + [str(path) for path in paths] + ["%s:%s/" % (self.la.LA, remote)])

    def get(self, remote, local):
        return self.runner(self.la.SCP + ["-r", "%s:%s" % (self.la.LA, remote), str(local)])


def needed(prepared):
    """The owed bodies with no reply yet, each re-hashed against its name, and how many are owed in all."""
    owed = prepared / "owed"
    try:
        names = json.loads((owed / "owed.json").read_text(encoding="utf-8"))["bodies"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Stop("nothing is filed as owed: an agent runs prepare.py --owed first") from None
    paths = []
    for name in names:
        if (prepared / "replies" / (name + ".json")).exists():
            continue
        path = owed / (name + ".body")
        if not re.fullmatch(r"[0-9a-f]{64}", name) or not path.is_file() or sha256(path.read_bytes()) != name:
            raise Stop("an owed body changed or is gone since prepare.py --owed filed it; an agent runs it again")
        paths.append(path)
    return paths, len(names)


def accept(part, folder, names, filed):
    """Move each well-formed reply for a body we sent from the .part folder into replies/, noting its digest."""
    folder.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in sorted(part.glob("*.json")):
        name = path.name[:-5]
        try:
            ok = name in names and re.fullmatch(r"[0-9a-f]{64}", name) and set(json.loads(path.read_text(encoding="utf-8"))) == REPLY_FIELDS
        except ValueError:
            ok = False
        if ok and not (folder / path.name).exists():   # a reply already here is never replaced or counted twice
            filed[name] = {"sha256": sha256(path.read_bytes())}
            os.replace(path, folder / path.name)
            moved += 1
    shutil.rmtree(part)
    return moved


def run(prepared, transport, tools, record):
    replies = prepared / "replies"

    def step(name, call, ok=(0,)):
        record["step"] = name
        result = call()
        if result.returncode not in ok:
            raise Stop("%s failed (exit %d)" % (name, result.returncode))
        return result

    def fresh_folder_with_tools():
        record["la_folder"] = "made"
        step("making the LA folder", lambda: transport.shell("rm -rf %s && umask 077 && mkdir %s" % (REMOTE, REMOTE)))
        step("copying the tools", lambda: transport.put([HERE / "ask_one.py", tools["ask.py"], tools["qwen_scene.py"]], REMOTE))

    def copy_back(label="looking for replies"):
        """LA's replies/ comes back through replies.part; the well-formed ones still owed move into replies/."""
        if names and step(label, lambda: transport.shell("test -d %s/replies" % REMOTE), ok=(0, 1)).returncode == 0:
            part = prepared / "replies.part"
            if part.exists():
                shutil.rmtree(part)
            step("copying replies back", lambda: transport.get(REMOTE + "/replies", part))
            record["answered"] += accept(part, replies, names, record["filed"])

    def remove():
        step("removing the LA folder", lambda: transport.shell("rm -rf %s" % REMOTE))
        record["la_folder"] = "removed"

    paths, total = needed(prepared)
    record["needed"] = len(paths)
    names = {path.stem for path in paths}
    try:
        copy_back("looking for replies left on LA")   # a paste cut short may have left some: back before any folder goes
        paths = [path for path in paths if not (replies / (path.stem + ".json")).exists()]
        if not paths:   # every reply is here: only remove a folder an earlier paste may have left
            try:
                remove()
            except Stop:
                record["la_folder"] = "not removed; the next paste removes it"
            if not names:
                say("ask: all %d replies are already here; nothing to ask." % total)
            return 0
        fresh_folder_with_tools()
        record["step"] = "preflight"
        check = transport.shell("timeout %s python3 -B %s/ask_one.py --check" % (CHECK_TIMEOUT, REMOTE), capture=True)
        for line in (check.stdout or "").splitlines():
            if line.startswith("preflight ok "):
                try:
                    record["root"] = json.loads(line[len("preflight ok "):]).get("root")
                except (ValueError, AttributeError):
                    record["root"] = "unreadable"
            say("LA: " + line)
        remove()
        if check.returncode != 0:
            record.update(stopped_at="preflight", reason=PREFLIGHT.get(check.returncode, "preflight exit %d" % check.returncode))
            if check.returncode == 10:
                record["vllm"] = "down"
            say("ask: %s, so no image was sent." % record["reason"])
            return 3
        fresh_folder_with_tools()
        step("making the bodies folder", lambda: transport.shell("umask 077 && mkdir %s/bodies" % REMOTE))
        step("copying the bodies still needed", lambda: transport.put(paths, REMOTE + "/bodies"))
        record.update(step="asking", sent=sorted(path.stem for path in paths), cap=len(paths) + MARGIN)
        say("ask: %d bodies, at most %d requests" % (len(paths), len(paths) + MARGIN))
        code = transport.shell("timeout %dm python3 -B %s/ask_one.py" % (3 + len(paths), REMOTE)).returncode
        record["ask_exit"] = code
        copy_back()
        remove()
    except BaseException:
        if record["la_folder"] == "made":   # a visit stopped partway: copy back what LA holds, then remove the folder
            failed_at = record["step"]
            try:
                copy_back()
                remove()
            except BaseException:   # best effort; a failed copy stops before the removal, and the next paste copies back first
                record["la_folder"] = "left; the next paste copies back what it holds, then removes it"
            record["step"] = failed_at
        raise
    if code != 0:
        record.update(stopped_at="asking", reason=ASKED.get(code, "ask_one exit %d" % code))
        if code == 10:
            record["vllm"] = "down"
    return 0 if code == 0 else 4


def came_back(record):
    return "; %d of %d replies came back" % (record["answered"], record["needed"]) if record["needed"] else ""


def still_needed(prepared):
    try:
        return len(needed(prepared)[0])
    except Stop:
        return None


def try_line(prepared, record, clock):
    """One line per paste, on the record of how the picture was chosen. It asks about a set already chosen, so it
    has no single body: the page's count of Qwen tries leaves it out."""
    set_id = None
    try:
        set_id = json.loads((prepared / "owed" / "owed.json").read_text(encoding="utf-8"))["set"]
        info = json.loads((prepared / set_id / "set.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        info = {}
    line = {"schema": "den-sim-try-1", "by": "ask_la.py", "kind": "asked", "set": set_id, "figure": info.get("figure"), "way": info.get("way"),
            "seed": info.get("seed"), "root": record["root"], "body_sha256": None, "bodies": record.get("sent", []), "answered": record["answered"],
            "still_needed": record["still_needed"], "facts": None, "invalid_reason": None, "outcome": "asked" if record["answered"] else "not asked",
            "written_unix": round(clock(), 3)}
    prepared.mkdir(parents=True, exist_ok=True)
    with open(prepared / "tries.jsonl", "a", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(line, sort_keys=True) + "\n")


def main(argv=None, *, root=None, transport=None, console=has_console, clock=time.time):
    root = Path(root) if root else data_root()
    prepared = root / "prepared"
    record = {"schema": "den-sim-last-ask-1", "started_unix": round(clock(), 3), "stopped_at": None, "reason": None, "step": None,
              "needed": None, "answered": 0, "still_needed": None, "cap": None, "root": None, "ask_exit": None, "vllm": None,
              "la_folder": None, "filed": {}}
    finished, started = False, False
    if not console():
        record.update(stopped_at="start", reason="no console: run it yourself in PowerShell")
        code = 2
        say("ask: refused, no console. Paste the line into PowerShell yourself.")
    else:
        started = True
        part = prepared / "replies.part"
        if part.exists():
            shutil.rmtree(part)
        try:
            tools = lift(root / "tools" / "lifted")
            code = run(prepared, transport or SSH(load(tools["la.py"], "den_sim_la")), tools, record)
            finished = True
        except Stop as exc:
            record.update(stopped_at=record["step"] or "start", reason=str(exc))
            say("ask: stopped: %s%s. %s" % (exc, came_back(record), AGAIN))
            code = 2
        except KeyboardInterrupt:
            record.update(stopped_at=record["step"] or "start", reason="interrupted")
            say("ask: interrupted%s. %s" % (came_back(record), AGAIN))
            code = 130
        except Exception as exc:  # type name only
            record.update(stopped_at=record["step"] or "start", reason=type(exc).__name__)
            say("ask: stopped at %s (%s)%s. %s" % (record["step"] or "start", type(exc).__name__, came_back(record), AGAIN))
            code = 2
    record.update(finished_unix=round(clock(), 3), still_needed=still_needed(prepared))
    try:   # every reply on file keeps the digest the paste that filed it took
        record["filed"] = {**json.loads((prepared / "last-ask.json").read_text(encoding="utf-8")).get("filed", {}), **record["filed"]}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    prepared.mkdir(parents=True, exist_ok=True)
    (prepared / "last-ask.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    if started:
        try_line(prepared, record, clock)
    if finished and record["stopped_at"] in (None, "asking") and record["needed"]:
        say("ask: %d of %d replies came back%s. %s" % (record["answered"], record["needed"],
                                                      "" if not record["stopped_at"] else " (stopped: %s)" % record["reason"],
                                                      "Reload the page and press the scenes." if record["answered"] else AGAIN))
    if record["vllm"] == "down":   # on either visit
        say("Starting vLLM on LA is your call: its start line is in %s (allow about 3 minutes to load), then paste this line again." % VLLM_DOC)
    return code


if __name__ == "__main__":
    sys.exit(main())
