#!/usr/bin/env python3
"""Step 4 of the live den view: build den-live's release tarball for the LA service. On this PC; standard library only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\release.py

install_la.py runs it as its first step, so you only paste this to look at a release by itself.

What goes in, by the path it has in the tarball:
- tools/den-live, tools/den-detect, tools/den-sim/refs.json and stack/services/den-live (the unit and install.sh): this
  branch at HEAD, by git archive. It refuses while those paths hold changes that aren't committed, because the release
  names its commit. (--worktree takes them as they are on disk instead, for the tests.)
- trees/<key>-<sha>/: the pinned trees the live loop loads, by git archive at refs.json's pins, as run_local.py's
  sofa_check.archive makes them: den-observe and den-take with the Lab's detector files, den-track, den-register. Each
  has the .complete marker archive() writes, so the service finds them without git. Their README.md files stay out:
  the service never reads them.
- models/: yolo26s.pt, rtmpose-m-body7.onnx and the pose model's artifact.json, from the detector venv's models folder,
  where detect_worker.py finds them on this PC, each held to den-detect's requirements.lock (YOLO26s 646f8bc3...,
  RTMPose-m 5c0a4bf6...). The detector won't load without the manifest, so it comes along.
- wheels/rtmlib-0.0.16-py3-none-any.whl, from the pose preparation folder the lock names, held to the lock's sha256:
  the venv's rtmlib came from that wheel, and the pose model's manifest holds rtmlib's source files to it.
- RELEASE.json (the commit, the pins, the counts) and SHA256SUMS (every other file).

The scan, before anything is written: every text file going in is searched for private strings. The kinds are the den's
place names (held here only as sha256 digests of the words; the one that's also a shell builtin's name counts only
as an identifier), the den's site id and this PC's user name (read at run time), tailnet names, private and tailnet
IPv4 addresses, tailnet IPv6 addresses, a user's own folder (C:\\Users\\<name>, /home/<name>), token-looking strings,
and the camera token's own value when its file is there. It reads the final contents, the pose model's manifest too. A hit refuses the release and names the file, the line and the kind, never the string. The pinned trees'
own unedited files hold a few such lines (the Lab's device names, den-take's default camera address); those are allowed
for those exact git blobs only (KNOWN_PINNED), so a changed file or a new hit still refuses.

Out, under <data root>\\private\\release\\: den-live-release.tar.gz and den-live-release.tar.gz.sha256. The tarball is
the same bytes for the same commit. It prints words, counts, those paths and the sha256.
"""
import argparse
import functools
import getpass
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_local as rl  # noqa: E402  den_site, the trees the loop loads
import sofa_check as sc  # noqa: E402  say, Stop, the pins, the data root, the venv

say, Stop = sc.say, sc.Stop
ROOT = HERE.parents[1]
BRANCH_PATHS = ("tools/den-live", "tools/den-detect", "tools/den-sim/refs.json", "stack/services/den-live")
TARBALL = "den-live-release.tar.gz"
MODEL_FILES = ("yolo26s.pt", "rtmpose-m-body7.onnx", "artifact.json")
PLAN_PINS = {"yolo26s.pt": "646f8bc3", "rtmpose-m-body7.onnx": "5c0a4bf6"}   # plan step 4's pins, as the lock holds them
WHEEL = "rtmlib-0.0.16-py3-none-any.whl"
WHEEL_SHA = re.compile(r"The rtmlib wheel's sha256 is ([0-9a-f]{64})")
BINARY = (".pt", ".onnx", ".whl")
TREE_SKIP = ("README.md",)
SCHEMA = "den-live-release-1"

# ---- the scan ----------------------------------------------------------------------------------------------------

# sha256 of the lower-case place words (one, or two with a space) that must not go in: the words are never written here.
PLACE_WORDS = frozenset((
    "ab1cb712f2dca756105160805501f4d6d8657d93d40b16eee4ecb5fd048d26eb",
    "9f2608067816e38c85edfb0c3985feff32def8b5dc17bb522ffc2e877e9b386b"))
# The other home's site id is also an everyday word, a shell builtin's name: it counts only where it stands as an
# identifier (identifier_context).
CONTEXT_WORDS = frozenset(("092c79e8f80e559e404bcf660c48f3522b67aba9ff1484b0367e1a4ddef7431d",))
WORD = re.compile(r"[a-z]+")
TAILNET = re.compile(r"\.ts\.net(?![a-z0-9-])", re.IGNORECASE)
TAILNET_V6 = re.compile(r"fd7a:115c:a1e0[:]", re.IGNORECASE)   # [:] so this line isn't one itself
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
RUN = re.compile(r"[A-Za-z0-9_-]{24,}")
# A user's own folder: C:\Users\<name> (one or two backslashes, or a slash), /Users/<name> or /home/<name>.
PROFILE = re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)|(?<![A-Za-z0-9._-])/(?:Users|home)/)([A-Za-z0-9._-]+)")
PLACEHOLDER_USERS = frozenset(("u", "user", "username", "you", "your", "name", "me", "someone", "owner", "example", "runner",
                               "ubuntu", "public", "default", "shared", "all", "admin", "root", "x", "y", "z"))
# The pinned trees' known lines: (tree key, path in the tree) -> (git blob sha1, the kinds its lines may hold).
_SITE, _PLACE, _HOME = "the den's site id", "place name", "a user's own folder"
KNOWN_PINNED = {
    ("observe", "stack/services/perception-lab/apartment_desk_preview.py"): ("3a215d941ba74e5d5e8397320a5e76b183514169", (_PLACE, _SITE)),
    ("observe", "stack/services/perception-lab/la_detector_app.py"): ("abd20742988faa5dbe3eed7b7de4b139d16e270c", (_PLACE, _SITE, "token-like string", _HOME, "this PC's user name")),
    ("observe", "stack/services/perception-lab/room_mapper.py"): ("51d17ec489b0beb07ad2bba7be2e0932a245b790", (_PLACE, _SITE, "private address")),
    ("observe", "stack/services/perception-lab/host_home.py"): ("6e1688f85f67404e17163bca40912da58b123eb1", (_HOME,)),   # LA's own paths
    ("observe", "tools/den-take/take.py"): ("badd932b9abefb490ffcc884bbf7eeca03a8b19d", ("private address",)),   # its default camera
    ("register", "tools/den-take/take.py"): ("badd932b9abefb490ffcc884bbf7eeca03a8b19d", ("private address",)),
    # The lighting brain's den fixture ids, which the coordinator's aim.py import loads with it (its geometry module)
    ("coordinator", "stack/services/lighting-brain/lighting_brain/geometry.py"): ("cb2795ad40a86d1369c705c63bf79633ce63bd66", (_PLACE, _SITE)),
}


@functools.lru_cache(maxsize=65536)
def _digest(word):
    return hashlib.sha256(word.encode("utf-8")).hexdigest()


def private_address(a, b, c, d):
    octets = [int(x) for x in (a, b, c, d)]
    if any(o > 255 for o in octets):
        return False
    first, second = octets[:2]
    return (first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168)
            or (first == 100 and 64 <= second <= 127))   # the last: carrier-grade NAT, the tailnet's own range


def token_like(run):
    """A run of 24 or more [A-Za-z0-9_-] with a stretch of 16 or more holding upper case, lower case and a digit: how a
    random token looks, and not a hex digest, a wheel name or an identifier."""
    return any(len(part) >= 16 and re.search(r"[A-Z]", part) and re.search(r"[a-z]", part) and re.search(r"\d", part)
               for part in re.split(r"[-_]", run))


def identifier_context(lower, start, end):
    """Whether the word at lower[start:end] stands as an identifier: quoted on its own ("w" or 'w'), assigned (w=), a
    path segment (/w/), or after "site" on the same line. A shell command line using the word does none of these."""
    before, after = lower[start - 1:start], lower[end:end + 1]
    return ((before in ("'", '"') and after == before) or re.match(r"\s*=", lower[end:]) is not None
            or (before == "/" and after in ("/", "")) or "site" in lower[:start])


def placeholder_user(name):
    return len(name) < 2 or name.lower() in PLACEHOLDER_USERS


def scan_text(text, secrets=()):
    """[(line number, kind)] of every private string in text. secrets: (value, kind) pairs read at run time; the den's
    site id and this PC's user name match as whole words, any other value anywhere."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        kinds, lower = set(), line.lower()
        words = list(WORD.finditer(lower))
        for i, match in enumerate(words):
            word = match.group()
            if _digest(word) in PLACE_WORDS or (i + 1 < len(words) and _digest(word + " " + words[i + 1].group()) in PLACE_WORDS):
                kinds.add("place name")
            elif _digest(word) in CONTEXT_WORDS and identifier_context(lower, match.start(), match.end()):
                kinds.add("place name")
        if TAILNET.search(line):
            kinds.add("tailnet name")
        if TAILNET_V6.search(line):
            kinds.add("tailnet address")
        if any(not placeholder_user(m.group(1)) for m in PROFILE.finditer(line)):
            kinds.add("a user's own folder")
        if any(private_address(*m.groups()) for m in IPV4.finditer(line)):
            kinds.add("private address")
        if any(token_like(m.group()) for m in RUN.finditer(line)):
            kinds.add("token-like string")
        for value, kind in secrets:
            if not value:
                continue
            if kind in ("the den's site id", "this PC's user name"):
                if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(value.lower()), lower):
                    kinds.add(kind)
            elif value in line:
                kinds.add(kind)
        hits.extend((number, kind) for kind in sorted(kinds))
    return hits


def blob_sha1(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def scan(files, secrets):
    """files: {path in the tarball: (bytes, tree key or None, path in the tree or None)}. Returns (text files, known
    pinned lines). Stops on the first file that isn't allowed what it holds, naming the file, the line and the kind."""
    texts = known = 0
    for path in sorted(files):
        data, key, inner = files[path]
        if path.endswith(BINARY):
            continue
        try:
            text = data.decode("utf-8")
            if "\0" in text:
                raise UnicodeDecodeError("utf-8", data, 0, 1, "a NUL byte")
        except UnicodeDecodeError:
            raise Stop("%s isn't text, so the private-string scan can't read it" % path) from None
        texts += 1
        hits = scan_text(text, secrets)
        if not hits:
            continue
        allowed = KNOWN_PINNED.get((key, inner)) if key else None
        if allowed and allowed[0] == blob_sha1(data) and {kind for _, kind in hits} <= set(allowed[1]):
            known += len({n for n, _ in hits})
            continue
        line, kind = hits[0]
        raise Stop("the release would carry a private string: %s, line %d, a %s (%d such lines in that file). Nothing was "
                   "written" % (path, line, kind, len({n for n, _ in hits})))
    return texts, known


# ---- what goes in --------------------------------------------------------------------------------------------------

def git(*args, repo=ROOT, text=True):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, **({"encoding": "utf-8"} if text else {}))
    if done.returncode:
        raise Stop("git %s failed" % args[0])
    return done.stdout


def from_tar(data):
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read()
    return out


def branch_files(source, repo=ROOT):
    """This branch's paths: at HEAD (refusing changes not committed), or as they are on disk."""
    if source == "HEAD":
        changed = git("status", "--porcelain", "--untracked-files=all", "--", *BRANCH_PATHS, repo=repo).splitlines()
        if changed:
            raise Stop("%d files under %s aren't committed, and the release names its commit: an agent commits them first"
                       % (len(changed), ", ".join(BRANCH_PATHS)))
        commit = git("rev-parse", "HEAD", repo=repo).strip()
        when = int(git("show", "-s", "--format=%ct", "HEAD", repo=repo).strip())
        return from_tar(git("archive", "--format=tar", commit, "--", *BRANCH_PATHS, repo=repo, text=False)), commit, when
    names = [n for n in git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *BRANCH_PATHS, repo=repo).split("\0") if n]
    out = {}
    for name in names:
        path = Path(repo) / name
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            out[name] = path.read_bytes()
    return out, None, 0


def tree_files(repo=ROOT):
    """{path in the tarball: (bytes, key, path in the tree)} for each pinned tree the loop loads, with its marker."""
    out = {}
    for key in rl.TREES:
        folder, sha, paths, marker = sc.tree_path(Path("trees"), key)
        for inner, data in from_tar(git("archive", "--format=tar", sha, "--", *paths, repo=repo, text=False)).items():
            if Path(inner).name not in TREE_SKIP:
                out[(folder / inner).as_posix()] = (data, key, inner)
        out[(folder / ".complete").as_posix()] = (marker.encode("utf-8"), key, ".complete")
    return out


def lock_pins(lock_text):
    """The weights' sha256 from den-detect's requirements.lock, and rtmlib's wheel's from its note."""
    weights = {name: digest for _, _, name, digest in (line.split() for line in lock_text.splitlines() if line.startswith("# sha256 "))}
    wheel = WHEEL_SHA.search(lock_text)
    if not wheel or any(name not in weights for name in MODEL_FILES):
        raise Stop("den-detect's requirements.lock no longer names the weights and the wheel it did")
    for name, prefix in PLAN_PINS.items():
        if not weights[name].startswith(prefix):
            raise Stop("requirements.lock's %s isn't the plan's pinned %s..." % (name, prefix))
    return weights, wheel.group(1)


def binaries(lock_text, models_dir, wheel_path):
    weights, wheel_sha = lock_pins(lock_text)
    out = {}
    for name in MODEL_FILES:
        path = Path(models_dir) / name
        try:
            data = path.read_bytes()
        except OSError:
            raise Stop("%s isn't in the detector's models folder, %s" % (name, models_dir)) from None
        if hashlib.sha256(data).hexdigest() != weights[name]:
            raise Stop("%s in %s isn't the pinned one" % (name, models_dir))
        out["models/" + name] = data
    try:
        data = Path(wheel_path).read_bytes()
    except OSError:
        raise Stop("rtmlib's wheel isn't at %s" % wheel_path) from None
    if hashlib.sha256(data).hexdigest() != wheel_sha:
        raise Stop("rtmlib's wheel at %s isn't the pinned one" % wheel_path)
    out["wheels/" + WHEEL] = data
    return out


def default_wheel(repo=ROOT):
    """The pose preparation folder in the main checkout, as requirements.lock's install lines name it."""
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", repo=repo).strip())
    return common.parent / ".tmp" / "pose-preparation" / WHEEL


def inside_checkout(path):
    resolved = Path(path).resolve()
    return any((p / ".git").exists() for p in (resolved, *resolved.parents))


def tarball_bytes(files, mtime):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(files[name]), mtime, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(files[name]))
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=6) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def secrets_of(site, token, user=None):
    """The private values read at run time: the den's site id, the camera token, and this PC's user name (when it's
    no placeholder: a common word would refuse every release)."""
    if user is None:
        try:
            user = getpass.getuser()
        except (OSError, KeyError, ImportError):
            user = None
    out = [(site, "the den's site id"), (token, "the camera token")]
    if user and len(user) >= 4 and not placeholder_user(user):
        out.append((user, "this PC's user name"))
    return out


def build(*, out_dir, source="HEAD", token=None, site=None, models_dir=None, wheel=None, repo=ROOT, extra=None):
    """Write the tarball and its .sha256 into out_dir. extra: {path: bytes} added to the branch files (tests)."""
    out_dir = Path(out_dir)
    if inside_checkout(out_dir):
        raise Stop("the release folder is inside a git checkout; private inputs sit beside it")
    branch, commit, when = branch_files(source, repo)
    branch.update(extra or {})
    files = {path: (data, None, None) for path, data in branch.items()}
    files.update(tree_files(repo))
    lock_text = branch["tools/den-detect/requirements.lock"].decode("utf-8")
    added = binaries(lock_text, models_dir or sc.VENV_PY.parents[1] / "models", wheel or default_wheel(repo))
    files.update({path: (data, None, None) for path, data in added.items()})
    # Every text file that goes in, the pose model's manifest among them; the weights and the wheel are held to their
    # sha256 instead.
    texts, known = scan(files, secrets_of(site or rl.den_site(repo), token))
    contents = {path: data for path, (data, _key, _inner) in files.items()}
    pins = {key: sc.tree_path(Path("trees"), key)[1] for key in rl.TREES}
    contents["RELEASE.json"] = (json.dumps({
        "schema": SCHEMA, "source": source, "commit": commit, "pins": pins, "files": len(contents) + 2,   # with this and SHA256SUMS
        "models": {name: hashlib.sha256(contents["models/" + name]).hexdigest() for name in MODEL_FILES},
        "wheel": {WHEEL: hashlib.sha256(contents["wheels/" + WHEEL]).hexdigest()},
        "scan": {"text_files": texts, "known_pinned_lines": known}}, indent=1, sort_keys=True) + "\n").encode("utf-8")
    contents["SHA256SUMS"] = "".join("%s  %s\n" % (hashlib.sha256(contents[name]).hexdigest(), name)
                                     for name in sorted(contents)).encode("utf-8")
    data = tarball_bytes(contents, when)
    digest = hashlib.sha256(data).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / TARBALL
    part = target.with_name(TARBALL + ".part")
    part.write_bytes(data)
    part.replace(target)
    (out_dir / (TARBALL + ".sha256")).write_text("%s  %s\n" % (digest, TARBALL), encoding="ascii", newline="\n")
    return {"tarball": target, "sha256": digest, "files": len(contents), "text_files": texts, "known": known,
            "bytes": len(data), "commit": commit, "contents": contents}


def report(built):
    say("Release: %s, %d files (3 model files, 1 wheel), %.1f MB."
        % ("commit %s" % built["commit"][:8] if built["commit"] else "this worktree, as it is on disk", built["files"],
           built["bytes"] / 1e6))
    say("Scan: %d text files, no private strings (%d known lines in the pinned trees' unedited files)."
        % (built["text_files"], built["known"]))
    say("Written: %s" % built["tarball"])
    say("sha256: %s" % built["sha256"])


def main(argv=None, *, root=None, **kw):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worktree", action="store_true", help="this branch's files as they are on disk, not HEAD")
    args = parser.parse_args(argv)
    root = Path(root) if root else sc.data_root()
    try:
        take = rl.den_take(sc.archive(root / "tools", "observe"))
        token = None
        try:
            token = take.read_token(take.DEFAULT_TOKEN_FILE)
        except take.Refused:
            pass   # no token file here: the scan can't look for its value, and says nothing of it
        report(build(out_dir=root / "private" / "release", source="worktree" if args.worktree else "HEAD", token=token, **kw))
        return 0
    except Stop as exc:
        say("release: stopped: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
