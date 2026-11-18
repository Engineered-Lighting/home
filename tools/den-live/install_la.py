#!/usr/bin/env python3
"""Step 4 of the live den view: install den-live on LA. The owner's paste, on this PC.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\install_la.py

0. It refuses unless this checkout is on claude/den-live with no uncommitted change to a tracked file, the only branch
   LA installs from, and prints the branch and commit it found.
1. It refuses until step 2 has registered and qualified the camera: the room model in
   <data root>\\private\\register\\ and the step 2 receipt that registered its camera and says qualified: true.
2. It builds the release (release.py): this branch at HEAD, the pinned trees, the two models and rtmlib's wheel,
   scanned for private strings.
3. It copies to LA, into /dev/shm/den-live-install/ (memory, never disk, the folder yours alone): the tarball,
   install.sh, and the private inputs: the room model, the camera token, that receipt, and a settings file with the
   camera's address and the den's site (both read at run time on this PC, never written into git). With labels in
   <data root>\\private\\labels\\ that read, it brings them too: den-live takes them once, only while LA has none.
   With --preview-geometry it brings the private Home geometry export as a separate service credential. With --desk
   (plan 05, the desk stage) it brings desk.json, the desk switch: one of that geometry's fixtures, its board's Home
   Assistant device, its measured profile and light true or false. It's checked here first, and needs the geometry and
   the Qwen ask's Home Assistant files. It's brought only when given, each time: an install without --desk removes
   LA's old one, so den-live is virtual only again.
4. It runs install.sh there with sudo: ssh -t, so sudo can ask for your password.
5. Whatever happened, it removes /dev/shm/den-live-install/ from LA, and its own copies here.

Its lines and install.sh's are words only: never the token, a coordinate or the camera's address. The last lines are
the undo, for this PowerShell. --host is the ssh host, hav-ubuntu as the den simulation's pastes use; --camera is the
camera as LA reaches it, by default den-take's.
"""
import argparse
import json
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import body_profiles  # noqa: E402  the figure's profile and the record of its passing score
import desk  # noqa: E402  the desk switch's file and its checks
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
BODY_PROFILE_FILE = "den-live-body-rgb.json"
PREVIEW_GEOMETRY = "virtual-fixtures.json"
INSTALL_SH = Path("stack") / "services" / "den-live" / "install.sh"


def _private_json(path):
    """Read an optional private packet without putting paths or values in errors."""
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024:
            raise ValueError
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError
                value[key] = item
            return value
        def reject(_):
            raise ValueError
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs, parse_constant=reject)
    except (OSError, TypeError, ValueError):
        raise Stop("the optional private JSON packet could not be validated") from None


def enrichment_block(block):
    """Target Linux paths stay POSIX paths even when this paste runs on Windows."""
    keys = {"enabled", "port", "profile_file", "frame_root", "duration_s", "frame_access", "frame_group", "dependencies_file"}
    if not isinstance(block, dict) or set(block) - keys:
        raise Stop("the optional model settings have unsupported fields")
    if block == {"enabled": False}:
        return block
    if (block.get("enabled") is not True or type(block.get("port")) is not int or not 1024 <= block["port"] <= 65535
            or type(block.get("duration_s", 4)) is not int or block.get("duration_s", 4) not in (4, 8)
            or block.get("frame_access") != "private_group" or type(block.get("frame_group")) is not int
            or not 0 < block["frame_group"] <= 2147483647):
        raise Stop("the optional model settings need an explicit port, window and private group")
    for key in ("profile_file", "frame_root", "dependencies_file"):
        value = block.get(key)
        if (not isinstance(value, str) or not PurePosixPath(value).is_absolute()
                or str(PurePosixPath(value)) != value):
            raise Stop("the optional model settings need canonical absolute Linux paths")
    frames, source = PurePosixPath(block["frame_root"]), PurePosixPath(block["dependencies_file"]).parent
    if frames == source or frames in source.parents or source in frames.parents:
        raise Stop("the optional model inputs need separate scoped directories")
    protected = tuple(PurePosixPath(p) for p in ("/var/lib/den-live", "/var/lib/private/den-live", "/run/den-live",
                                                 "/etc/den-live", "/opt/den-live"))
    if any(root == path or root in path.parents for root in protected for path in (frames, source)):
        raise Stop("optional model inputs must remain outside protected Den service state")
    try:
        rs.enrichment_dropin(block)
    except (rs.Refusal, TypeError, ValueError, KeyError):
        raise Stop("the optional model settings do not preserve the service boundary") from None
    return dict(block)


def model_worker_token(path):
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256:
            raise ValueError
        value = path.read_text(encoding="utf-8-sig").strip()
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError
        return value
    except (OSError, TypeError, ValueError):
        raise Stop("the optional model worker needs its own private 64-hex token") from None


def enrichment_packet(settings_file, token_file, *, other_tokens=()):
    block = None if settings_file is None else enrichment_block(_private_json(settings_file))
    enabled = block is not None and block.get("enabled") is True
    if not enabled:
        if token_file is not None:
            raise Stop("a model worker token needs explicitly enabled optional model settings")
        return block, None
    if token_file is None:
        raise Stop("enabled optional model settings require the model worker's own token")
    if any(Path(token_file).resolve() == Path(path).resolve() for path in other_tokens):
        raise Stop("the optional model worker cannot reuse another service's credential")
    return block, model_worker_token(token_file)


def walking_packet(path, geometry):
    if path is None:
        return None
    if geometry is None:
        raise Stop("walking settings require this install's private preview geometry")
    value = _private_json(path)
    try:
        import walking
        if not isinstance(value, dict) or value.get("mode", "off") not in ("off", "shadow", "virtual"):
            raise ValueError
        walking._config(value)
    except (ImportError, TypeError, ValueError, KeyError):
        raise Stop("the optional walking settings are invalid for this release") from None
    return value


def walking_registration(value, geometry, receipt, transform_id):
    """An optional floor belongs to this install's registration and scan only."""
    if value is None or value.get("mode", "off") == "off":
        return
    try:
        exported = geometry if isinstance(geometry, dict) else json.loads(geometry)
        registered = _private_json(receipt)
        floor = value["floor"]
        if (floor["transform_id"] != transform_id or floor["scan_revision"] != exported["binding"]["content_revision"]
                or floor["scan_revision"] != registered["scan_revision"]
                or floor["depth_calibration_sha256"] not in registered["source_correction"]["calibrations"]):
            raise ValueError
    except (OSError, TypeError, ValueError, KeyError):
        raise Stop("the optional walking floor does not match this install's registration") from None


def target_enrichment(block, *, inspect=None, group_lookup=None, profile_reader=None):
    """Read-only Linux preflight. Never creates groups or reconciles permissions."""
    block = enrichment_block(block)
    if block.get("enabled") is not True:
        return
    inspect = inspect or (lambda path: Path(str(path)).lstat())
    if group_lookup is None:
        import grp
        group_lookup = grp.getgrgid
    try:
        group = group_lookup(block["frame_group"])
        if group.gr_name in {"root", "sudo", "adm", "docker", "users", "video", "render", "audio", "wheel", "systemd-journal"}:
            raise ValueError
        paths = [PurePosixPath(block[k]) for k in ("frame_root", "dependencies_file", "profile_file")]
        for path in paths:
            for entry in (path, *path.parents):
                try:
                    metadata = inspect(entry)
                except FileNotFoundError:
                    if entry == paths[1]:  # the source receipt is created by Den, its parent must already exist
                        continue
                    raise
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError
        for directory in (paths[0], paths[1].parent):
            metadata = inspect(directory)
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
                    or metadata.st_gid != block["frame_group"] or stat.S_IMODE(metadata.st_mode) != 0o2770):
                raise ValueError
        profile = inspect(paths[2])
        parent = inspect(paths[2].parent)
        if (not stat.S_ISREG(profile.st_mode) or profile.st_uid != 0 or profile.st_mode & 0o022
                or not (profile.st_mode & 0o004 or (profile.st_gid == block["frame_group"] and profile.st_mode & 0o040))
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022):
            raise ValueError
        try:
            source = inspect(paths[1])
        except FileNotFoundError:
            source = None
        if source is not None and (not stat.S_ISREG(source.st_mode) or source.st_gid != block["frame_group"]
                                   or stat.S_IMODE(source.st_mode) != 0o640):
            raise ValueError
        profile_reader = profile_reader or (lambda path: _private_json(Path(str(path))))
        model_profile = profile_reader(paths[2])
        if (not isinstance(model_profile, dict) or model_profile.get("schema") != "den.model-profile/v1"
                or model_profile.get("id") not in ("vjepa-lm-qwen3-8b", "vjepa-lm-qwen3-4b")):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError):
        raise Stop("the optional model target inputs or private group have not been safely provisioned") from None


def target_optional_inputs(settings_file, staging):
    """Called from install.sh before any detector build, configuration write or switch."""
    settings = _private_json(settings_file)
    if not isinstance(settings, dict):
        raise Stop("the optional install settings are invalid")
    block = settings.get("enrichment")
    token_path = Path(staging) / rs.MODEL_TOKEN
    if block is None or block == {"enabled": False}:
        if token_path.exists():
            raise Stop("an optional model token was supplied without enabled settings")
    else:
        target_enrichment(block)
        token = model_worker_token(token_path)
        for name in ("camera-token.txt", rs.HA_TOKEN):
            other = Path(staging) / name
            try:
                if other.is_file() and token == other.read_text(encoding="utf-8-sig").strip():
                    raise Stop("the optional model worker cannot reuse another service's credential")
            except (OSError, ValueError):
                raise Stop("the private credential separation could not be validated") from None
    coordinator = settings.get("coordinator") or {}
    if isinstance(coordinator, dict) and "walking" in coordinator:
        if coordinator.get("preview") is not True or not (Path(staging) / PREVIEW_GEOMETRY).is_file():
            raise Stop("walking settings require this install's preview geometry")
        try:
            import walking
            walking._config(coordinator["walking"])
        except (ImportError, TypeError, ValueError, KeyError):
            raise Stop("the optional walking settings are invalid for this release") from None
        walking_registration(coordinator["walking"], _private_json(Path(staging) / PREVIEW_GEOMETRY),
                             Path(staging) / "register-receipt.json", coordinator.get("transform_id"))


def registration(root, tools, camera_id=rl.CAMERA_ID):
    """(room model path, the receipt that registered and qualified its camera, the transform id): refuses before step 2
    has, and when step 2's receipt didn't qualify the registration."""
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
    try:
        rl.qualified_registration(json.loads(receipt.read_text(encoding="utf-8")), transform_id)
    except (OSError, ValueError) as exc:
        raise Stop("%s: paste register_now.py again" % exc) from None
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


BODY_QUALIFICATION_FILE = body_profiles.QUALIFICATION_FILE   # body_profiles.py writes it beside the profile


def body_profile(path, receipt):
    """Read the optional owner-qualified profile before copying any release files. It must be the very candidate a
    passing head-cut score used: the qualification record beside it names its sha256 and its pass. A profile in which
    the owner shows the head ("head": "shown") has no head cut to score, and needs no record."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
        block = json.loads(data.decode("utf-8-sig"))
        registration = json.loads(Path(receipt).read_text(encoding="utf-8"))
        rs.RgbBodyProfile.from_config(block, registration)
    except (OSError, TypeError, KeyError, ValueError, OverflowError):
        raise Stop("the RGB figure profile does not match the camera registration") from None
    if not body_profiles.qualified_bytes(data, path.parent / BODY_QUALIFICATION_FILE):
        raise Stop("the RGB figure profile isn't the one a passing head-cut score used (%s beside it)"
                   % BODY_QUALIFICATION_FILE)
    return block


def preview_geometry_export(path, site):
    """Validate the export envelope and keep the original bytes for the service credential."""
    try:
        payload = Path(path).read_bytes()
        doc = json.loads(payload)
    except (OSError, ValueError):
        raise Stop("the private Home preview geometry export could not be read") from None
    if (not isinstance(doc, dict) or doc.get("schema") != "lighting-brain-geometry-1"
            or doc.get("physical_commands") is not False or doc.get("home") != site or doc.get("room") != "den"
            or not isinstance(doc.get("fixtures"), list) or not doc["fixtures"]):
        raise Stop("choose the registered Den preview geometry exported by Home")
    return payload


def desk_mapping(path, geometry):
    """(the checked mapping, desk.json's own bytes), before anything is built or copied. The desk maps one fixture of
    the preview's own geometry export. Never printed: a device reference, a profile."""
    try:
        payload = Path(path).read_bytes()
        data = json.loads(payload.decode("utf-8-sig"))
    except (OSError, ValueError):
        raise Stop("the desk file (--desk) doesn't read") from None
    fixtures = {row.get("fixture_id"): row for row in json.loads(geometry)["fixtures"] if isinstance(row, dict)}
    try:
        return desk.parse(data, fixtures), payload
    except desk.DeskError as exc:
        raise Stop(str(exc)) from None


def undo_lines(host):
    return ['  ssh -t %s "sudo systemctl disable --now den-live"' % host]


def run(*, root, host, camera_url, token_file, transport, models_dir=None, wheel=None, source="HEAD", camera_id=rl.CAMERA_ID,
        ha_token_file=None, ha_url_file=None, preview_geometry=None, body_profile_file=None, desk_file=None,
        enrichment_settings_file=None, model_worker_token_file=None, walking_config_file=None):
    site = rl.den_site()
    geometry = None if preview_geometry is None else preview_geometry_export(preview_geometry, site)
    private = Path(token_file).parent
    enrichment, model_token = enrichment_packet(enrichment_settings_file, model_worker_token_file,
        other_tokens=(token_file, ha_token_file or private / HA_TOKEN_FILE))
    walking = walking_packet(walking_config_file, geometry)
    mapping = desk_bytes = None
    if desk_file is not None:
        if geometry is None:
            raise Stop("the desk needs --preview-geometry: it maps one of the preview's registered fixtures")
        mapping, desk_bytes = desk_mapping(desk_file, geometry)
    tools_dir = root / "tools"
    observe, track = sc.archive(tools_dir, "observe"), sc.archive(tools_dir, "track")
    model, receipt, transform_id = registration(root, perceive.Tools(observe, track), camera_id)
    walking_registration(walking, geometry, receipt, transform_id)
    take = rl.den_take(observe)
    try:
        token = take.read_token(token_file)
    except take.Refused as exc:
        raise Stop(sc.token_refusal(exc, token_file, observe)) from None
    rs.camera_address(camera_url)
    ha_token, ha_url = ha_inputs(ha_token_file or private / HA_TOKEN_FILE, ha_url_file or private / HA_URL_FILE)
    if model_token is not None and model_token in (token, ha_token):
        raise Stop("the optional model worker cannot reuse another service's credential")
    figure = body_profile(body_profile_file or private / BODY_PROFILE_FILE, receipt)
    if mapping is not None and not ha_token:
        raise Stop("the desk's commands go through Home Assistant: save %s and %s first (README, \"The Qwen ask\")"
                   % (HA_TOKEN_FILE, HA_URL_FILE))
    say("Step 2's registration %s, from %s." % (transform_id, receipt.name))
    say("The Qwen ask: %s" % ("on, with Home Assistant's token and address." if ha_token else
                              "off. To turn it on, save %s and %s first (README, \"The Qwen ask\")." % (HA_TOKEN_FILE, HA_URL_FILE)))
    seed = labels_seed(root)
    say("Your labels: %s" % ("%d from this PC; LA takes them only if it has none yet." % seed[1] if seed else "none from this PC."))
    if geometry is not None:
        say("Virtual preview: bringing the private Home geometry export with Hue overlay disabled.")
    if mapping is not None:
        say("The desk: %s's commands go to its board through Home Assistant, %s." % (
            mapping["fixture_id"], "lit" if mapping["light"] else "dark (light false: the spot stays at level 0)"))
    else:
        say("The desk: off. den-live stays virtual, and LA's old desk file, if any, goes.")
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
        settings = {"schema": rs.SETTINGS_SCHEMA, "camera_url": camera_url, "site": site}
        if figure is not None:
            settings["body_rgb"] = figure
        if ha_token:
            settings["asks"] = {"ha_url": ha_url, "ha_entity": rs.FRIGATE_ENTITY}
            (outbox / rs.HA_TOKEN).write_text(ha_token + "\n", encoding="utf-8", newline="\n")
        if seed:
            settings["labels_seed"] = True
            shutil.copyfile(seed[0], outbox / rs.LABELS_SEED)
        if geometry is not None:
            settings["coordinator"] = {"preview": True, "transform_id": transform_id, "hue_overlay": False}
            if walking is not None:
                settings["coordinator"]["walking"] = walking
            (outbox / PREVIEW_GEOMETRY).write_bytes(geometry)
        if enrichment is not None:
            settings["enrichment"] = enrichment
        if model_token is not None:
            (outbox / rs.MODEL_TOKEN).write_text(model_token + "\n", encoding="utf-8", newline="\n")
        if desk_bytes is not None:
            settings["desk"] = True
            (outbox / rs.DESK_FILE).write_bytes(desk_bytes)
        (outbox / "settings.json").write_text(json.dumps(settings) + "\n", encoding="utf-8", newline="\n")
        (outbox / "install.sh").write_bytes(built["contents"][INSTALL_SH.as_posix()])   # the release's own, byte for byte
        say("Copying the release and the private inputs to %s:%s ..." % (host, REMOTE))
        if transport.shell("rm -rf %s && mkdir -m 700 %s" % (REMOTE, REMOTE)):
            raise Stop("ssh %s didn't answer; is it reachable from this PC?" % host)
        copied = True
        files = [built["tarball"], *(outbox / name for name in ("install.sh", "room-model.jsonl", "camera-token.txt",
                                                                "register-receipt.json", "settings.json")),
                 *([outbox / rs.HA_TOKEN] if ha_token else []), *([outbox / rs.LABELS_SEED] if seed else []),
                 *([outbox / PREVIEW_GEOMETRY] if geometry is not None else []),
                 *([outbox / rs.MODEL_TOKEN] if model_token is not None else []),
                 *([outbox / rs.DESK_FILE] if desk_bytes is not None else [])]
        if transport.copy(files, REMOTE):
            raise Stop("scp to %s stopped part way" % host)
        say("Installing on %s (sudo asks for your password there)..." % host)
        code = transport.shell("sudo bash %s/install.sh %s %s" % (REMOTE, built["sha256"], host), interactive=True)
        if code:
            raise Stop("install.sh stopped (exit %d); its own lines above say why" % code)
        say("Installed. Next: deploy Home (step 4's pull request), then the walk test.")
        if mapping is not None:
            say("To turn the desk off: paste install_la.py again without --desk.")
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
    parser.add_argument("--preview-geometry", type=Path, help="private registered Den geometry export from Home")
    parser.add_argument("--enrichment-settings", type=Path, help="private optional model input settings with target Linux paths")
    parser.add_argument("--model-worker-token", type=Path, help="the optional model worker's own private 64-hex token")
    parser.add_argument("--walking-config", type=Path, help="optional off/shadow/virtual walking configuration; needs --preview-geometry")
    parser.add_argument("--desk", type=Path, help="private desk.json (den-c12-desk-1): one fixture's commands to its "
                                                  "board, for the desk stage; needs --preview-geometry")
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
        if args.enrichment_settings is not None:
            kw["enrichment_settings_file"] = args.enrichment_settings
        if args.model_worker_token is not None:
            kw["model_worker_token_file"] = args.model_worker_token
        if args.walking_config is not None:
            kw["walking_config_file"] = args.walking_config
        if args.desk is not None:
            kw["desk_file"] = args.desk
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
