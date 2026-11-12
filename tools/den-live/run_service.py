#!/usr/bin/env python3
"""Step 4 of the live den view: den-live as an always-on service on LA. systemd starts it (den-live.service); nobody
pastes it.

    /usr/bin/python3.12 -B /opt/den-live/current/tools/den-live/run_service.py --config /etc/den-live/config.json

It's run_local.py's loop without the page: the camera's newest /frame.npz about 5 a second, the detector in its own
process in the detector's venv with no network, den-observe and den-track unedited at their pins on step 2's
registration and your scan's floor padded 1 m, and the people stream on 127.0.0.1:<stream_port> only. The Home
gateway's den-live route passes that stream through to Home.

Its inputs, all from systemd:
- $CREDENTIALS_DIRECTORY (LoadCredential: root-only files on LA that install_la.py copies from this PC's private
  folder): room-model.jsonl (step 2's registration), camera-token.txt (the camera's bearer token) and
  register-receipt.json (the step 2 receipt that registered it, which says the scan revision its picks were made on).
- $STATE_DIRECTORY (StateDirectory=den-live): live/ holds the tracker's method copy and the detector's log, detector/
  its caches and TEMP. That's all it keeps, as run_local does: no frames, no tracks.
- --config, the settings file install.sh writes (/etc/den-live/config.json): camera_url, content_packs (the gateway's
  content-pack folder), site (the den's site, as the gateway names it), venv_python, models and stream_port.

It refuses to start with one sentence and exit 3 when an input is missing or doesn't match, and systemd doesn't
restart it (RestartPreventExitStatus=3): a missing credential, a receipt that didn't register the room model's camera,
a scan revision other than the one step 2 picked on, or a registration the camera no longer fits. Any other stop exits
2, and systemd starts it again after 10 s. Every 60 s it prints one line of words and counts to the journal, never a
coordinate.

install.sh also runs it once with --write-config, as root, to write that settings file and the unit's drop-in (the
camera's address for IPAddressAllow, and a read-only bind of the content packs), from install_la.py's settings.json
and the running gateway's own environment.
"""
import argparse
import ipaddress
import json
import os
import re
import signal
import stat as stat_bits
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perceive  # noqa: E402
import run_local as rl  # noqa: E402  the loop (serve), the scan's floor, the receipt's revision
import sofa_check as sc  # noqa: E402  say, Stop, the pinned trees' markers
import source  # noqa: E402

say, Stop, Refusal = sc.say, sc.Stop, rl.Refusal
RELEASE = HERE.parents[1]          # /opt/den-live/releases/<id>: tools/, trees/, models/
TREES_DIR = RELEASE / "trees"
CONFIG_SCHEMA, SETTINGS_SCHEMA = "den-live-service-config-1", "den-live-install-settings-1"
CREDENTIALS = {"room_model": "room-model.jsonl", "token": "camera-token.txt", "receipt": "register-receipt.json"}
MODEL_FILES = ("yolo26s.pt", "rtmpose-m-body7.onnx", "artifact.json")
STATUS_EVERY_S = 60.0
LOG_MAX_BYTES = 10 * 1024 * 1024   # the detector's log is rolled to .1 at a start past this: at most two are kept
GATEWAY = "home-web-gateway"       # tools/install-home-web-gateway-linux.sh's service, as deploy-home-web.sh names it
SITE = re.compile(r"^[a-z0-9-]{1,64}$")
EXIT_REFUSED = 3


# ---- the settings file -------------------------------------------------------------------------------------------

def camera_address(url):
    """The camera URL's IP address: it must name one, since the unit allows the service that one address only."""
    parts = urlsplit(url if isinstance(url, str) else "")
    try:
        if parts.scheme != "http" or not parts.port or parts.path not in ("", "/") or parts.query:
            raise ValueError
        return ipaddress.ip_address(parts.hostname)
    except (TypeError, ValueError):
        raise Refusal("the camera URL must look like http://<IP address>:<port>") from None


def read_config(path):
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refusal("its settings file %s doesn't read: install it again with install_la.py" % path) from None
    if not isinstance(config, dict) or config.get("schema") != CONFIG_SCHEMA:
        raise Refusal("its settings file isn't %s: install it again with install_la.py" % CONFIG_SCHEMA)
    camera_address(config.get("camera_url"))
    for key in ("content_packs", "venv_python", "models"):
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
            raise Refusal("its settings file has no absolute %s: install it again with install_la.py" % key)
    if not isinstance(config.get("site"), str) or not SITE.match(config["site"]):
        raise Refusal("its settings file names no den site: install it again with install_la.py")
    port = config.get("stream_port")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise Refusal("its settings file's stream_port isn't a port number")
    return config


def gateway_packs(pid, proc=Path("/proc")):
    """The content-pack folder the running gateway serves, from its own environment and working folder, as server.mjs
    finds it: HOME_CONTENT_PACKS_DIR, else <HOME_WEB_APARTMENT_ASSETS_DIR's parent>/home-content, else
    <checkout>/app/data/home-content. None when the gateway isn't running or can't be read."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        raw = (proc / str(pid) / "environ").read_bytes()
        cwd = Path(os.readlink(proc / str(pid) / "cwd"))
    except OSError:
        return None
    return packs_from(dict(item.split("=", 1) for item in raw.decode("utf-8", "replace").split("\0") if "=" in item), cwd)


def packs_from(env, cwd):
    """server.mjs's CONTENT_PACKS_DIR, from the gateway's environment and its working folder (the checkout)."""
    if env.get("HOME_CONTENT_PACKS_DIR"):
        return Path(os.path.normpath(cwd / env["HOME_CONTENT_PACKS_DIR"]))
    assets = cwd / env["HOME_WEB_APARTMENT_ASSETS_DIR"] if env.get("HOME_WEB_APARTMENT_ASSETS_DIR") else cwd / "app" / "data" / "apartment"
    return Path(os.path.normpath(assets.parent / "home-content"))


def service_cant_read(packs, site, stat=os.stat):
    """The first folder or file under packs that the service's dynamic user couldn't read through its read-only bind, or
    None. That user is neither owner nor group of the owner's files, so the "other" bits are what count: search (x) on
    each folder from packs down to the floor file, read (r) on each file run_local.scan_floor reads (ACTIVE, the active
    pack's content-pack.json, its floor asset). Folders above packs don't count: the bind brings packs itself into the
    service's view, so a home folder that's 0750 is no matter. Raises Refusal when the pack itself doesn't read."""
    site_dir = Path(packs) / site
    try:
        revision = (site_dir / "ACTIVE").read_text(encoding="utf-8").strip()
        manifest = json.loads((site_dir / revision / "content-pack.json").read_text(encoding="utf-8"))
        asset = Path(manifest["assets"]["floor.json"]["path"])
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal("the den's active pack has no floor.json in its manifest: deploy Home with the den's pack first") from None
    if not revision or asset.is_absolute() or ".." in asset.parts or ".." in Path(revision).parts:
        raise Refusal("the den's active pack names a floor file outside itself")
    pack = site_dir / revision
    folders = [Path(packs), site_dir, pack, *[pack / Path(*asset.parts[:n]) for n in range(1, len(asset.parts))]]
    files = [site_dir / "ACTIVE", pack / "content-pack.json", pack / asset]
    for path, bit in [*((f, stat_bits.S_IXOTH) for f in folders), *((f, stat_bits.S_IROTH) for f in files)]:
        try:
            mode = stat(path).st_mode
        except OSError:
            return path
        if not mode & bit:
            return path
    return None


def write_config(*, settings_path, gateway_pid, venv_python, models, out, dropin, stream_port=rl.STREAM_PORT, proc=Path("/proc"),
                 stat=os.stat):
    """install.sh's step: the service's settings file and its unit drop-in, from install_la.py's settings.json."""
    try:
        settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refusal("settings.json beside install.sh doesn't read") from None
    if not isinstance(settings, dict) or settings.get("schema") != SETTINGS_SCHEMA:
        raise Refusal("settings.json isn't %s" % SETTINGS_SCHEMA)
    address = camera_address(settings.get("camera_url"))
    site = settings.get("site")
    if not isinstance(site, str) or not SITE.match(site):
        raise Refusal("settings.json names no den site")
    packs = Path(settings["content_packs"]) if settings.get("content_packs") else gateway_packs(gateway_pid, proc)
    if packs is None:
        raise Refusal("the Home gateway (%s) isn't running, so its content-pack folder isn't known: start it, or deploy "
                      "Home first" % GATEWAY)
    if not packs.is_absolute() or re.search(r"\s", str(packs)):
        raise Refusal("the gateway's content-pack folder must be an absolute path without spaces, for the unit's bind")
    if not (packs / site / "ACTIVE").is_file():
        raise Refusal("the gateway's content-pack folder holds no active den pack: deploy Home with the den's pack first")
    blocked = service_cant_read(packs, site, stat)
    if blocked is not None:
        raise Refusal("the den-live service's own user couldn't read %s through its read-only bind: give other users read "
                      "access to it (chmod o+rX %s); the installer leaves your files as they are" % (blocked, blocked))
    config = {"schema": CONFIG_SCHEMA, "camera_url": settings["camera_url"], "content_packs": str(packs), "site": site,
              "venv_python": str(venv_python), "models": str(models), "stream_port": int(stream_port)}
    for path, text in ((Path(out), json.dumps(config, indent=1) + "\n"), (Path(dropin), dropin_text(address, packs))):
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(text, encoding="utf-8", newline="\n")
        os.chmod(part, 0o644)
        os.replace(part, path)
    return config


def dropin_text(address, packs):
    """The unit's local settings: the one camera address it may reach, and the content packs, read only. With the packs
    under /home or /root, ProtectHome=tmpfs hides the homes as ProtectHome=yes does, but lets that one bind through."""
    lines = ["# Written by den-live's install.sh from this host's settings. Not in git.", "[Service]",
             "IPAddressAllow=%s" % address, "BindReadOnlyPaths=%s" % packs]
    if packs.parts[1:2] in (("home",), ("root",)):
        lines.append("ProtectHome=tmpfs")
    return "\n".join(lines) + "\n"


# ---- the service -------------------------------------------------------------------------------------------------

def release_trees(trees_dir=None):
    """The pinned trees in the unpacked release, found by their .complete markers, never by git."""
    out = {}
    for key in rl.TREES:
        tree = sc.archived(trees_dir or TREES_DIR, key)
        if tree is None:
            raise Refusal("the release has no whole %s tree at its pin: install it again with install_la.py" % key)
        out[key] = tree
    return out


def credentials(env):
    # systemd lists several state folders with ":" between them; there's one here. (os.pathsep: a Windows test's path
    # has a drive letter's colon in it.)
    folder, state = env.get("CREDENTIALS_DIRECTORY"), (env.get("STATE_DIRECTORY") or "").split(os.pathsep)[0]
    if not folder or not state:
        raise Refusal("run it as the den-live service: systemd gives it $CREDENTIALS_DIRECTORY and $STATE_DIRECTORY. On "
                      "this PC, paste run_local.py instead")
    if not Path(folder).is_absolute() or not Path(state).is_absolute():
        raise Refusal("$CREDENTIALS_DIRECTORY and $STATE_DIRECTORY must be absolute folders, as systemd gives them")
    paths = {key: Path(folder) / name for key, name in CREDENTIALS.items()}
    for key, path in paths.items():
        if not path.is_file():
            raise Refusal("the service's %s credential (%s) is missing: paste install_la.py again" % (key.replace("_", " "), path.name))
    return paths, Path(state)


def roll(log):
    try:
        if log.stat().st_size > LOG_MAX_BYTES:
            os.replace(log, log.with_name(log.name + ".1"))
    except OSError:
        pass


def run(*, config_path, env=None, until=None, status_every_s=STATUS_EVERY_S, trees=None, camera_id=rl.CAMERA_ID,
        require_correction=None):
    """camera_id and require_correction are the tests' (the den simulation's renders are camera den-virtual, with no
    depth_correction record); the service uses den-oak and the method file's rule."""
    env = os.environ if env is None else env
    paths, state = credentials(env)
    config = read_config(config_path)
    trees = trees or release_trees()
    if not Path(config["venv_python"]).is_file():
        raise Refusal("the detector's venv isn't at %s: install it again with install_la.py" % config["venv_python"])
    if not all((Path(config["models"]) / name).is_file() for name in MODEL_FILES):
        raise Refusal("the detector's models aren't all in %s: install it again with install_la.py" % config["models"])
    try:
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refusal("the service's receipt credential doesn't read: paste install_la.py again") from None
    take = rl.den_take(trees["observe"])
    try:
        token = take.read_token(paths["token"])
    except take.Refused:
        raise Refusal("the service's camera token credential doesn't read: paste install_la.py again") from None
    try:
        camera = source.camera_client(take, config["camera_url"], token)
    except take.Refused as exc:
        raise Refusal(str(exc)) from None

    def picked_on(transform_id):
        registered, revision = rl.revision_of(receipt, transform_id)
        if not registered:
            raise Refusal("step 2's receipt in the service's credentials didn't register %s, the room model's camera: paste "
                          "install_la.py again" % transform_id)
        if revision is None:
            say("Note: step 2's receipt doesn't say which scan revision the registration was picked on.")
        return revision

    live = state / "live"
    live.mkdir(parents=True, exist_ok=True)
    roll(live / "detector.log")
    # The den's labels: kept in the service's own state, read and saved through the gateway's den-live route.
    (state / "labels").mkdir(parents=True, exist_ok=True)
    return rl.serve(trees=trees, take=take, camera=camera, room_model=paths["room_model"], camera_id=camera_id,
                    require_correction=require_correction,
                    floor=None, packs=config["content_packs"], site=config["site"], picked_on=picked_on, state=live,
                    detector_argv=[config["venv_python"], "-B", str(HERE / "detect_worker.py"), "--tools-root",
                                   str(trees["observe"]), "--state", str(state / "detector"), "--models", config["models"]],
                    stream_port=config["stream_port"], busy="is another den-live still running?", until=until,
                    status_every_s=status_every_s, labels=rl.room_labels.Store(state / "labels", say=say))


def stopping(_signum, _frame):
    raise KeyboardInterrupt   # systemd's stop: unwind, so the detector, the stream and the source close in order


def main(argv=None, *, env=None, until=None, status_every_s=STATUS_EVERY_S, trees=None, **test):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="/etc/den-live/config.json")
    parser.add_argument("--write-config", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--settings", help=argparse.SUPPRESS)
    parser.add_argument("--gateway-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--venv-python", help=argparse.SUPPRESS)
    parser.add_argument("--models", help=argparse.SUPPRESS)
    parser.add_argument("--dropin", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.write_config:
            config = write_config(settings_path=args.settings, gateway_pid=args.gateway_pid, venv_python=args.venv_python,
                                  models=args.models, out=args.config, dropin=args.dropin)
            say("Settings written: the camera, the den's content packs, the venv, the models, stream port %d." % config["stream_port"])
            return 0
        signal.signal(signal.SIGTERM, stopping)
        run(config_path=args.config, env=env, until=until, status_every_s=status_every_s, trees=trees, **test)
        return 0
    except Refusal as exc:
        say("den-live: refused: %s." % str(exc).rstrip("."))
        return EXIT_REFUSED
    except (Stop, perceive.Stop) as exc:
        say("den-live: stopped: %s. systemd starts it again in 10 s." % str(exc).rstrip("."))
        return 2
    except KeyboardInterrupt:
        say("den-live: stopped.")
        return 0
    except Exception as exc:  # a type name only: its text could quote what it read
        say("den-live: stopped (%s). systemd starts it again in 10 s." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
