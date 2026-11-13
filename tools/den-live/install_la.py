#!/usr/bin/env python3
"""Step 4 of the live den view: install den-live on LA. The owner's paste, on this PC.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\install_la.py

0. It refuses unless this checkout is on claude/den-live with no uncommitted change to a tracked file, the only branch
   LA installs from, and prints the branch and commit it found.
1. It refuses until step 2 has registered the camera: the room model in <data root>\\private\\register\\ and the step 2
   receipt that registered its camera.
2. It builds the release (release.py): this branch at HEAD, the pinned trees, the two models and rtmlib's wheel,
   scanned for private strings.
3. It copies to LA, into /dev/shm/den-live-install/ (memory, never disk, the folder yours alone): the tarball,
   install.sh, and the private inputs: the room model, the camera token, that receipt, and a settings file with the
   camera's address and the den's site (both read at run time on this PC, never written into git). With labels in
   <data root>\\private\\labels\\ that read, it brings them too: den-live takes them once, only while LA has none.
   With --preview-geometry it brings the private Home geometry export as a separate service credential.
4. It runs install.sh there with sudo: ssh -t, so sudo can ask for your password.
5. Whatever happened, it removes /dev/shm/den-live-install/ from LA, and its own copies here.

Its lines and install.sh's are words only: never the token, a coordinate or the camera's address. The last lines are
the undo, for this PowerShell. --host is the ssh host, hav-ubuntu as the den simulation's pastes use; --camera is the
camera as LA reaches it, by default den-take's.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perceive  # noqa: E402
import release  # noqa: E402
import room_labels  # noqa: E402  your labels, brought once
import run_local as rl  # noqa: E402
import run_service as rs  # noqa: E402  the settings file's schema and its camera-address check
import sofa_check as sc  # noqa: E402

say, Stop = sc.say, sc.Stop
INSTALL_BRANCH = "claude/den-live"   # the only branch LA installs from (the den end-to-end plan, section 7)
HOST = "hav-ubuntu"
REMOTE = "/dev/shm/den-live-install"
# The Qwen ask's two private files, saved once beside the camera token: the den-live user's long-lived token (the same
# admin user and token the coordinator's link uses) and the den home's Home Assistant address as LA reaches it.
HA_TOKEN_FILE, HA_URL_FILE = "den-live-ha-token.txt", "den-live-ha-url.txt"
PREVIEW_GEOMETRY = "virtual-fixtures.json"
INSTALL_SH = Path("stack") / "services" / "den-live" / "install.sh"


def registration(root, tools, camera_id=rl.CAMERA_ID):
    """(room model path, the receipt that registered its camera, the transform id): refuses before step 2 has."""
    work = Path(root) / "private" / "register"
    model = work / "room-model.jsonl"
    if not model.is_file():
        raise Stop("step 2 hasn't registered the camera yet: paste register_now.py first")
    try:
        transform_id = tools.roommodel.current_transform_id(tools.roommodel.load(model), camera_id)
    except tools.roommodel.RegistrationRefused as exc:
        raise Stop("step 2's room model has no registration the service could use (%s): paste register_now.py again" % exc) from None
    receipt = rl.registering_receipt(work, transform_id)
    if receipt is None:
        raise Stop("no step 2 receipt registered %s: paste register_now.py again" % transform_id)
    return model, receipt, transform_id


def branch_check(repo=None):
    """(branch, short commit) of the checkout this paste runs from: refuses unless it's claude/den-live with no
    uncommitted change to a tracked file. Untracked files don't count: the release is built from the commit."""
    repo = repo or release.ROOT
    try:
        branch = release.git("rev-parse", "--abbrev-ref", "HEAD", repo=repo).strip()
        commit = release.git("rev-parse", "--short", "HEAD", repo=repo).strip()
        changed = [line for line in release.git("status", "--porcelain", "--untracked-files=no", repo=repo).splitlines() if line.strip()]
    except Stop:
        raise Stop("this folder isn't a git checkout; LA installs from %s only" % INSTALL_BRANCH) from None
    if branch != INSTALL_BRANCH:
        raise Stop("this checkout is on %s; LA installs from %s only" % (branch, INSTALL_BRANCH))
    if changed:
        raise Stop("%s has uncommitted changes to %d file%s; an agent commits them first"
                   % (INSTALL_BRANCH, len(changed), "" if len(changed) == 1 else "s"))
    return branch, commit


class SSH:
    """ssh and scp as processes of their own, on this PC's console."""

    def __init__(self, host):
        self.host = host

    def run(self, argv, interactive=False):
        return subprocess.run(argv, stdin=None if interactive else subprocess.DEVNULL).returncode

    def shell(self, command, interactive=False):
        return self.run(["ssh", *(["-t"] if interactive else []), self.host, command], interactive)

    def copy(self, paths, remote):
        return self.run(["scp", "-q", *[str(p) for p in paths], "%s:%s/" % (self.host, remote)])


def ha_inputs(token_file, url_file):
    """(token, url) for the Qwen ask, from the two private files you save once beside the camera token (README, "The
    Qwen ask"), or (None, None) when neither is there. Never printed."""
    have = [Path(token_file).is_file(), Path(url_file).is_file()]
    if not any(have):
        return None, None
    if not all(have):
        raise Stop("the Qwen ask needs both %s and %s; one is missing" % (Path(token_file).name, Path(url_file).name))
    try:   # utf-8-sig: PowerShell 5.1's -Encoding utf8 writes a byte-order mark first
        token = Path(token_file).read_text(encoding="utf-8-sig").strip()
        url = Path(url_file).read_text(encoding="utf-8-sig").strip()
    except (OSError, ValueError):
        raise Stop("%s or %s doesn't read" % (Path(token_file).name, Path(url_file).name)) from None
    if not token or not token.isascii() or any(c.isspace() for c in token):
        raise Stop("%s should hold the token alone, on one line" % Path(token_file).name)
    rs.ha_address(url)   # an IP address and a port, or it says so
    return token, url


def labels_seed(root):
    """(path, how many) of this PC's labels file when it reads, else None: an unreadable one stays here, said once."""
    path = Path(root) / "private" / "labels" / room_labels.FILE
    if not path.is_file():
        return None
    try:
        doc, reason = room_labels.validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        doc, reason = None, "unreadable"
    if doc is None:
        say("Your labels on this PC don't read (%s), so they stay here." % reason)
        return None
    return path, len(doc["labels"])


def preview_geometry_export(path):
    """Validate the export envelope and keep the original bytes for the service credential."""
    try:
        payload = Path(path).read_bytes()
        doc = json.loads(payload)
    except (OSError, ValueError):
        raise Stop("the private Home preview geometry export could not be read") from None
    if (not isinstance(doc, dict) or doc.get("schema") != "lighting-brain-geometry-1"
            or doc.get("physical_commands") is not False or doc.get("home") != "victoria" or doc.get("room") != "den"
            or not isinstance(doc.get("fixtures"), list) or not doc["fixtures"]):
        raise Stop("choose the registered Victoria Den preview geometry exported by Home")
    return payload


def undo_lines(host):
    return ['  ssh -t %s "sudo systemctl disable --now den-live"' % host]


def run(*, root, host, camera_url, token_file, transport, models_dir=None, wheel=None, source="HEAD", camera_id=rl.CAMERA_ID,
        ha_token_file=None, ha_url_file=None, preview_geometry=None):
    geometry = None if preview_geometry is None else preview_geometry_export(preview_geometry)
    tools_dir = root / "tools"
    observe, track = sc.archive(tools_dir, "observe"), sc.archive(tools_dir, "track")
    model, receipt, transform_id = registration(root, perceive.Tools(observe, track), camera_id)
    take = rl.den_take(observe)
    try:
        token = take.read_token(token_file)
    except take.Refused as exc:
        raise Stop(sc.token_refusal(exc, token_file, observe)) from None
    rs.camera_address(camera_url)
    private = Path(token_file).parent
    ha_token, ha_url = ha_inputs(ha_token_file or private / HA_TOKEN_FILE, ha_url_file or private / HA_URL_FILE)
    say("Step 2's registration %s, from %s." % (transform_id, receipt.name))
    say("The Qwen ask: %s" % ("on, with Home Assistant's token and address." if ha_token else
                              "off. To turn it on, save %s and %s first (README, \"The Qwen ask\")." % (HA_TOKEN_FILE, HA_URL_FILE)))
    seed = labels_seed(root)
    say("Your labels: %s" % ("%d from this PC; LA takes them only if it has none yet." % seed[1] if seed else "none from this PC."))
    if geometry is not None:
        say("Virtual preview: bringing the private Home geometry export with Hue overlay disabled.")
    say("Building the release...")
    out_dir = root / "private" / "release"
    built = release.build(out_dir=out_dir, source=source, token=token, models_dir=models_dir, wheel=wheel)
    release.report(built)
    outbox = out_dir / "outbox"
    shutil.rmtree(outbox, ignore_errors=True)
    outbox.mkdir(parents=True)
    copied = False
    try:
        shutil.copyfile(model, outbox / "room-model.jsonl")
        shutil.copyfile(token_file, outbox / "camera-token.txt")
        shutil.copyfile(receipt, outbox / "register-receipt.json")
        settings = {"schema": rs.SETTINGS_SCHEMA, "camera_url": camera_url, "site": rl.den_site()}
        if ha_token:
            settings["asks"] = {"ha_url": ha_url, "ha_entity": rs.FRIGATE_ENTITY}
            (outbox / rs.HA_TOKEN).write_text(ha_token + "\n", encoding="utf-8", newline="\n")
        if seed:
            settings["labels_seed"] = True
            shutil.copyfile(seed[0], outbox / rs.LABELS_SEED)
        if geometry is not None:
            settings["coordinator"] = {"preview": True, "transform_id": transform_id, "hue_overlay": False}
            (outbox / PREVIEW_GEOMETRY).write_bytes(geometry)
        (outbox / "settings.json").write_text(json.dumps(settings) + "\n", encoding="utf-8", newline="\n")
        (outbox / "install.sh").write_bytes(built["contents"][INSTALL_SH.as_posix()])   # the release's own, byte for byte
        say("Copying the release and the private inputs to %s:%s ..." % (host, REMOTE))
        if transport.shell("rm -rf %s && mkdir -m 700 %s" % (REMOTE, REMOTE)):
            raise Stop("ssh %s didn't answer; is it reachable from this PC?" % host)
        copied = True
        files = [built["tarball"], *(outbox / name for name in ("install.sh", "room-model.jsonl", "camera-token.txt",
                                                                "register-receipt.json", "settings.json")),
                 *([outbox / rs.HA_TOKEN] if ha_token else []), *([outbox / rs.LABELS_SEED] if seed else []),
                 *([outbox / PREVIEW_GEOMETRY] if geometry is not None else [])]
        if transport.copy(files, REMOTE):
            raise Stop("scp to %s stopped part way" % host)
        say("Installing on %s (sudo asks for your password there)..." % host)
        code = transport.shell("sudo bash %s/install.sh %s %s" % (REMOTE, built["sha256"], host), interactive=True)
        if code:
            raise Stop("install.sh stopped (exit %d); its own lines above say why" % code)
        say("Installed. Next: deploy Home (step 4's pull request), then the walk test.")
        return 0
    finally:
        shutil.rmtree(outbox, ignore_errors=True)
        if copied:
            if transport.shell("rm -rf %s" % REMOTE):
                say("Note: LA may still hold the copies in %s. To remove them, paste:" % REMOTE)
                say('  ssh %s "rm -rf %s"' % (host, REMOTE))
            else:
                say("Removed the copies from %s on %s." % (REMOTE, host))


def main(argv=None, *, root=None, transport=None, console=sc.has_console, check_branch=branch_check, **kw):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=HOST, help="the ssh host for LA (default %(default)s)")
    parser.add_argument("--camera", help="the camera's URL as LA reaches it (default: den-take's)")
    parser.add_argument("--preview-geometry", type=Path, help="private registered Victoria Den geometry export from Home")
    args = parser.parse_args(argv)
    if not console():   # a background run is refused before anything: no build, no ssh
        say("install: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else sc.data_root()
    try:
        branch, commit = check_branch()   # before anything is built or copied
        say("Installing from %s at %s." % (branch, commit))
        take = rl.den_take(sc.archive(root / "tools", "observe"))
        if args.preview_geometry is not None:
            kw["preview_geometry"] = args.preview_geometry
        code = run(root=root, host=args.host, camera_url=args.camera or take.DEFAULT_CAMERA,
                   token_file=kw.pop("token_file", None) or take.DEFAULT_TOKEN_FILE, transport=transport or SSH(args.host), **kw)
    except Stop as exc:
        say("install: stopped: %s" % exc)
        code = 2
    except KeyboardInterrupt:
        say("install: interrupted.")
        code = 130
    except Exception as exc:  # a type name only
        say("install: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        code = 2
    say("Undo, to stop den-live on %s and keep it from starting:" % args.host)
    for line in undo_lines(args.host):
        say(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
