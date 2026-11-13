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

The Qwen ask (ask.py, inference plan step 2) runs here when the settings file has an asks block and the credentials hold
ha-token.txt: only while Frigate's den occupancy reads on and den-live sees someone seated. Without them, or with a block
that's wrong, the ask is off and one journal line says why; the markers go on (README, "The Qwen ask").

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
import ask  # noqa: E402  the Qwen ask (inference plan, step 2)
import coordinator_live  # noqa: E402  the den lighting coordinator (coordinator plan, step 3)
import ha_link  # noqa: E402  its Home Assistant link
import occupancy  # noqa: E402  Frigate's occupancy, the ask's first gate
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


HA_TOKEN = "ha-token.txt"       # the den home's Home Assistant: the den-live user's long-lived token (a credential)
ENTITY = re.compile(r"^binary_sensor\.[a-z0-9_]{1,80}$")
ZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$")


def asks_settings(config, folder):
    """(the asks block's fixed settings and the token, None) when the settings file sets the Qwen ask up, else (None, why).
    The block: ha_url (the den home's Home Assistant), ha_entity (Frigate's den occupancy sensor), vllm_url (LA's own
    vLLM, on loopback only), and optionally time_zone, backoff_status, ha_ca_file and the switches ask.Settings re-reads.
    A block that's wrong turns the asks off with one sentence; the markers never stop for it."""
    block = config.get("asks")
    if block is None:
        return None, "the settings file doesn't set the Qwen ask up"
    if not isinstance(block, dict):
        return None, "the settings file's asks block isn't one"
    try:
        url, vllm = urlsplit(str(block.get("ha_url") or "")), urlsplit(str(block.get("vllm_url") or ""))
        ha_port, vllm_port = url.port, vllm.port   # urlsplit raises here on a port that isn't one
    except ValueError:
        return None, "its ha_url or vllm_url has a port that isn't one"
    if (url.scheme not in ("http", "https") or not url.hostname or not ha_port or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        return None, "its ha_url must look like https://<address>:<port>"
    if not isinstance(block.get("ha_entity"), str) or not ENTITY.match(block["ha_entity"]):
        return None, "its ha_entity must be a binary_sensor's id"
    if (vllm.scheme != "http" or vllm.hostname not in ("127.0.0.1", "localhost") or not vllm_port or vllm.username
            or vllm.path not in ("", "/")):
        return None, "its vllm_url must be LA's own vLLM, http://127.0.0.1:<port>"
    if block.get("time_zone") is not None and not (isinstance(block["time_zone"], str) and ZONE.match(block["time_zone"])):
        return None, "its time_zone isn't a time zone's name"
    for key in ("backoff_status", "ha_ca_file"):
        if block.get(key) is not None and not (isinstance(block[key], str) and Path(block[key]).is_absolute()):
            return None, "its %s isn't an absolute path" % key
    if block.get("ha_ca_file") is not None and not Path(block["ha_ca_file"]).is_file():
        return None, "its ha_ca_file isn't there"
    try:
        token = (Path(folder) / HA_TOKEN).read_text(encoding="utf-8").strip()
    except OSError:
        return None, "the service's Home Assistant token credential (%s) is missing" % HA_TOKEN
    except ValueError:
        return None, "the service's Home Assistant token credential doesn't read"
    if not token or any(c.isspace() for c in token):
        return None, "the service's Home Assistant token credential doesn't read"
    return dict(block, token=token), None


def asks_starter(fixed, config_path, *, site, state, camera_id):
    """serve()'s asks: Frigate's occupancy read, then the Qwen ask, on the loop's detector."""
    def start(detector):
        """(asker, stop), or None when it can't start: the ask's failure never stops the markers."""
        reader = None
        try:
            reader = occupancy.Occupancy(occupancy.fetcher(fixed["ha_url"], fixed["token"], fixed["ha_entity"],
                                                           cafile=fixed.get("ha_ca_file")))
            asker = ask.Asker(detector=detector, occupancy=reader, post=ask.poster(fixed["vllm_url"]), state=state,
                              site_id=site, camera_id=camera_id, settings=ask.Settings(config_path),
                              backoff=ask.Backoff(fixed.get("backoff_status")), zone=fixed.get("time_zone"))
            reader.start()
        except Exception as exc:  # a type name only: its text could hold the URL
            if reader is not None:
                reader.halt.set()
            say("The Qwen ask is off: it didn't start (%s). The markers go on." % type(exc).__name__)
            return None

        def stop():
            asker.close()
            reader.halt.set()
        say("The Qwen ask is on: only while Frigate sees someone in the den and den-live sees them seated.")
        return asker, stop
    return start


LIGHT = re.compile(r"^light\.[a-z0-9_]{1,80}$")
SENSOR = re.compile(r"^(sensor|switch)\.[a-z0-9_]{1,80}$")


def coordinator_settings(config, fixed):
    """(the coordinator block's settings, None) when the settings file sets the coordinator up, else (None, why). The
    block: lights (den_lighting's room lights, one per socket), and optionally utc_offset_h, status_entity,
    switch_entity, and fixtures_file (an absolute path to the fixtures' rows, for the spot). It uses the asks block's Home
    Assistant (ha_url and the ha-token.txt credential): one den-live user and token for both."""
    block = config.get("coordinator")
    if block is None:
        return None, "the settings file doesn't set the coordinator up"
    if not isinstance(block, dict):
        return None, "the settings file's coordinator block isn't one"
    preview = block.get("preview", False)
    if type(preview) is not bool:
        return None, "its preview setting must be true or false"
    if fixed is None and not preview:
        return None, "it needs the asks block's Home Assistant link (ha_url, and the ha-token.txt credential)"
    lights = [] if preview else block.get("lights")
    if not preview and not (isinstance(lights, list) and lights and all(isinstance(e, str) and LIGHT.match(e) for e in lights)
                            and len(set(lights)) == len(lights)):
        return None, "its lights must be den_lighting's room lights, light.<name>, each once"
    offset = block.get("utc_offset_h", 0)
    if type(offset) not in (int, float) or not -14 <= offset <= 14:
        return None, "its utc_offset_h must be hours from UTC"
    for key in ("status_entity", "switch_entity"):
        if block.get(key) is not None and not (isinstance(block[key], str) and SENSOR.match(block[key])):
            return None, "its %s isn't an entity id" % key
    fixtures = {}
    if block.get("fixtures_file") is not None:
        try:
            fixtures = json.loads(Path(block["fixtures_file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None, "its fixtures_file doesn't read"
        if not isinstance(fixtures, dict):
            return None, "its fixtures_file isn't an object of fixture rows"
    if preview and not fixtures:
        return None, "C12 preview needs registered fixtures"
    seat_mark = block.get("seat_mark")
    if seat_mark is not None and (not isinstance(seat_mark, list) or len(seat_mark) != 3
                                  or not all(coordinator_live.c12_preview.finite(v) for v in seat_mark)):
        return None, "its seat_mark must be a room point"
    return {"lights": lights, "utc_offset_h": float(offset), "fixtures": fixtures, "preview": preview,
            "seat_mark": seat_mark,
            "status_entity": block.get("status_entity") or coordinator_live.STATUS_ENTITY,
            "switch_entity": block.get("switch_entity") or coordinator_live.SWITCH_ENTITY}, None


def coordinator_starter(settings, fixed, *, state, tree):
    """serve()'s coordinator: (service, stop), or None when it can't start. Its failure never stops the markers."""
    def start():
        try:
            core = coordinator_live.load_core(tree)
            preview = settings["preview"]
            ha = None if preview else ha_link.HaClient(fixed["ha_url"], fixed["token"], cafile=fixed.get("ha_ca_file"))
            service = coordinator_live.CoordinatorService(
                core, state_dir=state, lights=settings["lights"], fixtures=settings["fixtures"], ha=ha,
                utc_offset_h=settings["utc_offset_h"], status_entity=settings["status_entity"],
                switch_entity=settings["switch_entity"], seat_mark=settings["seat_mark"], preview=preview)
            service.start()
        except Exception as exc:   # a type name only: its text could hold the URL
            say("The lighting coordinator is off: it didn't start (%s). The markers go on." % type(exc).__name__)
            return None
        if preview:
            say("The lighting coordinator is running offline C12 preview.")
        else:
            say("The lighting coordinator is on: it follows the \"Den lighting coordinator\" switch in Home Assistant.")
        return service, service.close
    return start


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
    fixed, why = asks_settings(config, paths["room_model"].parent)
    if fixed is None:
        say("The Qwen ask is off: %s." % why)
    starter = None if fixed is None else asks_starter(fixed, config_path, site=config["site"], state=state, camera_id=camera_id)
    lighting, why = coordinator_settings(config, fixed)
    if lighting is None:
        say("The lighting coordinator is off: %s." % why)
    coordinator = None if lighting is None else coordinator_starter(lighting, fixed, state=state, tree=trees["coordinator"])
    return rl.serve(trees=trees, take=take, camera=camera, room_model=paths["room_model"], camera_id=camera_id,
                    require_correction=require_correction,
                    floor=None, packs=config["content_packs"], site=config["site"], picked_on=picked_on, state=live,
                    detector_argv=[config["venv_python"], "-B", str(HERE / "detect_worker.py"), "--tools-root",
                                   str(trees["observe"]), "--state", str(state / "detector"), "--models", config["models"]],
                    stream_port=config["stream_port"], busy="is another den-live still running?", until=until,
                    status_every_s=status_every_s, asks=starter, coordinator=coordinator)


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
