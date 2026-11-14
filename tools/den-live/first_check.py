#!/usr/bin/env python3
"""Step 4's first check, before anything is installed: what LA serves today. The owner's paste, on this PC.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\first_check.py

Do it with someone in the den: the Lab's LA worker runs only while the den is occupied, and its load is part of what
this measures. It runs read-only commands on LA over one `ssh hav-ubuntu` (--host), with first_check_remote.py's text
on ssh's stdin, and the camera token inside that stdin, never on a command line. Nothing is copied to LA, and no frame
is kept anywhere. It prints:
- LA's Home checkout: its branch, commit and date, whether it's clean, and whether the gateway (home-web-gateway, as
  tools/deploy-home-web.sh and tools/install-home-web-gateway-linux.sh name it; checkout ~/code/home, or the running
  gateway's own folder) is active;
- tailscale serve status: what port 443 forwards to, and whether --proxy-protocol=1 is on (the look-only rule needs it);
- the den's content-pack revisions on LA by folder name, its active one, and whether step 2's pack is there;
- a 60 s pull of /frame.npz from LA, as den-live's source pulls it: frames a second, median and 95th fetch time,
  median bytes;
- LA's cores and 1-minute load, and whether the Lab's LA worker (lab-la-detector-trial.service, a systemctl --user
  unit of LA's main user) is active;
- what the install needs: Python 3.12 with venv, OpenCV's libGL and libglib, port 8111 free.
It ends by saying what to tell an agent, and whether the Jetson relay (plan pick 2) is needed: under 4 frames a second,
or a median fetch over 300 ms. Words and numbers only: never the token, an address or a hostname.
"""
import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install_la  # noqa: E402  the registration and its receipt
import perceive  # noqa: E402
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402

say, Stop = sc.say, sc.Stop
REMOTE = HERE / "first_check_remote.py"
GATEWAY, CHECKOUT = "home-web-gateway", "~/code/home"
LAB_WORKER = "lab-la-detector-trial.service"
SECONDS = 60
RELAY_FPS, RELAY_FETCH_MS = 4.0, 300.0


# ---- reading what LA said ------------------------------------------------------------------------------------------

def out(result):
    return (result or {}).get("out", "").strip() if (result or {}).get("code") == 0 else None


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def checkout_line(facts):
    git, gateway = facts.get("git") or {}, facts.get("gateway") or {}
    head = out(git.get("head"))
    if head:
        status = (git.get("status") or {})
        changed = len((status.get("out") or "").splitlines()) if status.get("code") == 0 else None
        where = "the running gateway's folder" if gateway.get("checkout_from") == "gateway" else CHECKOUT
        text = "%s at %s, %s, %s (%s)" % (out(git.get("branch")) or "a branch", head[:8], out(git.get("date")) or "no date",
                                         "clean" if changed == 0 else "%s files changed" % (changed if changed is not None else "some"), where)
    else:
        text = "not found at %s" % CHECKOUT
    active = out(gateway.get("active")) or ((gateway.get("active") or {}).get("out") or "").strip() or "unknown"
    return "Home checkout:    %s; gateway %s: %s" % (text, gateway.get("unit", GATEWAY), active)


def handlers(node):
    """Every TCP port handler in tailscale serve status --json: (port, handler), wherever it sits."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "TCP" and isinstance(value, dict):
                for port, handler in value.items():
                    if isinstance(handler, dict):
                        yield str(port), handler
            else:
                yield from handlers(value)
    elif isinstance(node, list):
        for value in node:
            yield from handlers(value)


def loopback(target):
    host = str(target).rsplit(":", 1)[0].strip("[]")
    return host in ("127.0.0.1", "localhost", "::1")


def serve_facts(serve):
    """{read, ports: [(port, forward to loopback or None, TLS terminated)], proxy: True/False/None}."""
    text = out((serve or {}).get("json"))
    if text is None:
        plain = out((serve or {}).get("text"))
        if plain is None:
            return {"read": False, "ports": [], "proxy": None}
        lower = plain.lower()
        return {"read": True, "ports": [], "proxy": ("proxy" in lower) if plain else None, "plain": True,
                "off": "no serve config" in lower or not plain}
    try:
        config = json.loads(text) if text else {}
    except ValueError:
        return {"read": False, "ports": [], "proxy": None}
    ports, proxy = [], False
    for port, handler in handlers(config):
        forward = handler.get("TCPForward")
        ports.append((port, forward if forward and loopback(forward) else ("elsewhere" if forward else None), bool(handler.get("TerminateTLS"))))
        if handler.get("ProxyProtocol"):
            proxy = True
    return {"read": True, "ports": sorted(set(ports)), "proxy": proxy if ports else None, "off": not ports}


def serve_line(serve):
    found = serve_facts(serve)
    if not found["read"]:
        return "Tailscale Serve:  couldn't read tailscale serve status (it may need the operator setting or sudo)"
    if found.get("off"):
        return "Tailscale Serve:  off: nothing is served on the tailnet"
    if found.get("plain"):
        return "Tailscale Serve:  on; proxy protocol: %s (from its text; its JSON didn't read)" % ("on" if found["proxy"] else "not seen")
    ports = "; ".join("port %s to %s%s" % (port, target or "a web handler", ", TLS terminated" if tls else "") for port, target, tls in found["ports"])
    return "Tailscale Serve:  %s; proxy protocol: %s" % (ports, "on (--proxy-protocol=1)" if found["proxy"] else "off")


def packs_line(packs, wanted):
    packs = packs or {}
    if not packs.get("found"):
        return "Content packs:    the gateway's content-pack folder isn't on LA"
    if not packs.get("site"):
        return "Content packs:    the folder is there, but holds no den pack"
    names = packs.get("revisions") or []
    shown = ", ".join(names[:6]) + (", and %d more" % (len(names) - 6) if len(names) > 6 else "")
    text = "%d den revisions (%s); active: %s" % (packs.get("count", len(names)), shown or "none", packs.get("active") or "none")
    if wanted is None:
        return "Content packs:    %s. Step 2 hasn't registered yet, so its pack isn't known" % text
    if wanted not in names:
        return "Content packs:    %s. Step 2's pack %s: missing on LA" % (text, wanted)
    return "Content packs:    %s. Step 2's pack %s: there, %s" % (text, wanted, "and active" if packs.get("active") == wanted else "but not the active one")


def pull_numbers(pull):
    pull = pull or {}
    seconds = pull.get("seconds") or 0
    fetch = pull.get("fetch_ms") or []
    sizes = pull.get("bytes") or []
    return {"fps": (pull.get("new_frames") or 0) / seconds if seconds else 0.0,
            "median_ms": statistics.median(fetch) if fetch else None, "p95_ms": percentile(fetch, 0.95),
            "median_mb": statistics.median(sizes) / 1e6 if sizes else None, "seconds": seconds,
            "frames": pull.get("new_frames") or 0,
            "other": {k: v for k, v in (pull.get("statuses") or {}).items() if k != "200"}, "errors": pull.get("errors") or {}}


def pull_line(pull):
    n = pull_numbers(pull)
    trouble = ["%d answered %s" % (v, k) for k, v in sorted(n["other"].items())] + ["%d %s" % (v, k) for k, v in sorted(n["errors"].items())]
    if not n["frames"]:
        return "Frames from LA:   none in %d s%s" % (round(n["seconds"]), (": " + ", ".join(trouble)) if trouble else "")
    return ("Frames from LA:   %.1f a second over %d s; fetch median %.0f ms, 95th %.0f ms; median %.2f MB; %s"
            % (n["fps"], round(n["seconds"]), n["median_ms"], n["p95_ms"], n["median_mb"], ", ".join(trouble) or "no errors"))


def relay_line(pull):
    n = pull_numbers(pull)
    if not n["frames"]:
        why = "401 or 403 is the token" if any(k in n["other"] for k in ("401", "403")) else "LA didn't reach the camera"
        return "Jetson relay (plan pick 2): can't say yet: no frame came to LA (%s)." % why
    slow = []
    if n["fps"] < RELAY_FPS:
        slow.append("%.1f frames a second, under %g" % (n["fps"], RELAY_FPS))
    if n["median_ms"] > RELAY_FETCH_MS:
        slow.append("a median fetch of %.0f ms, over %g" % (n["median_ms"], RELAY_FETCH_MS))
    if slow:
        return "Jetson relay (plan pick 2): needed: %s." % " and ".join(slow)
    return ("Jetson relay (plan pick 2): not needed: %.1f frames a second and a median fetch of %.0f ms (it's needed under "
            "%g a second or over %g ms)." % (n["fps"], n["median_ms"], RELAY_FPS, RELAY_FETCH_MS))


def cpu_line(cpu, lab):
    cpu = cpu or {}
    try:
        load = "%.2f" % float((cpu.get("loadavg") or "").split()[0])
    except (IndexError, ValueError):
        load = "unknown"
    state = out(lab) or ((lab or {}).get("out") or "").strip() or ("unknown (systemctl --user didn't answer)" if (lab or {}).get("code") is None else "unknown")
    return "LA's CPU:         %s cores, 1-minute load %s; the Lab's LA worker (%s): %s" % (cpu.get("cores") or "unknown", load, LAB_WORKER.rsplit(".", 1)[0], state)


def install_line(install):
    install = install or {}
    python = out(install.get("python312"))
    libs = install.get("libraries") or {}
    yes = lambda value: "yes" if value else "no"
    den_live = out(install.get("den_live")) or ((install.get("den_live") or {}).get("out") or "").strip()
    return ("For the install:  %s; libGL %s, libglib %s%s; port 8111 %s; den-live %s"
            % ("Python %s with venv" % python if python else "no Python 3.12 with venv (python3.12, python3.12-venv)",
               yes(libs.get("libGL")), yes(libs.get("libglib")), "" if libs.get("listed") else " (ldconfig didn't list)",
               "in use" if install.get("port_8111") else "free",
               "is %s" % den_live if den_live in ("active", "activating", "failed") else "not installed"))


def lines(facts, wanted, host, now):
    if facts.get("failed"):
        return ["First check on LA (%s), %s: the check stopped on LA (%s). Tell an agent this line." % (host, now, facts["failed"])]
    return ["First check on LA (%s), %s: read only; nothing was installed or kept." % (host, now),
            checkout_line(facts), serve_line(facts.get("serve")), packs_line(facts.get("packs"), wanted),
            pull_line(facts.get("pull")), cpu_line(facts.get("cpu"), facts.get("lab_worker")), install_line(facts.get("install")),
            relay_line(facts.get("pull")),
            "Tell an agent the lines above, or paste them: they're words and numbers only."]


# ---- the paste -----------------------------------------------------------------------------------------------------

def stdin_text(payload):
    """What ssh's stdin carries: one line that sets PAYLOAD, then the remote half's own text."""
    return "import json\nPAYLOAD = json.loads(%r)\n" % json.dumps(payload) + REMOTE.read_text(encoding="utf-8")


def ssh_runner(host, text, timeout):
    try:
        done = subprocess.run(["ssh", host, "python3", "-"], input=text, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Stop("LA didn't finish the check in %d s" % timeout) from None
    if done.returncode == 255:
        raise Stop("ssh %s didn't answer; is it reachable from this PC?" % host)
    return done.stdout


def wanted_pack(root):
    tools_dir = root / "tools"
    try:
        _model, receipt, transform_id = install_la.registration(root, perceive.Tools(sc.archive(tools_dir, "observe"), sc.archive(tools_dir, "track")))
    except Stop:
        return None
    return rl.revision_of(json.loads(receipt.read_text(encoding="utf-8")), transform_id)[1]


def run(*, root, host, camera_url, token_file, runner=ssh_runner, seconds=SECONDS, now=None):
    observe = sc.archive(root / "tools", "observe")
    take = rl.den_take(observe)
    try:
        token = take.read_token(token_file)
    except take.Refused as exc:
        raise Stop(sc.token_refusal(exc, token_file, observe)) from None
    wanted = wanted_pack(root)
    say("Checking LA over ssh %s: about %d s, of which %d s pull the camera's frames..." % (host, seconds + 15, seconds))
    payload = {"gateway": GATEWAY, "checkout": CHECKOUT, "site": rl.den_site(), "camera_url": camera_url, "token": token,
               "seconds": seconds, "lab_worker": LAB_WORKER}
    reply = runner(host, stdin_text(payload), seconds + 120)
    found = None
    for line in reversed((reply or "").splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and candidate.get("schema") == "den-live-first-check-1":
            found = candidate
            break
    if found is None:
        raise Stop("LA's half of the check said nothing it could read (is python3 on LA?)")
    for line in lines(found, wanted, host, now or time.strftime("%Y-%m-%d %H:%M")):
        say(line)
    return found


def main(argv=None, *, root=None, console=sc.has_console, **kw):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=install_la.HOST, help="the ssh host for LA (default %(default)s)")
    parser.add_argument("--camera", help="the camera's URL as LA reaches it (default: den-take's)")
    args = parser.parse_args(argv)
    if not console():
        say("first check: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else sc.data_root()
    try:
        take = rl.den_take(sc.archive(root / "tools", "observe"))
        run(root=root, host=args.host, camera_url=args.camera or take.DEFAULT_CAMERA,
            token_file=kw.pop("token_file", None) or take.DEFAULT_TOKEN_FILE, **kw)
        return 0
    except Stop as exc:
        say("first check: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        say("first check: interrupted. Nothing was changed on LA.")
        return 130
    except Exception as exc:  # a type name only
        say("first check: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
