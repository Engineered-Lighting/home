#!/usr/bin/env python3
"""N1's one command, run on the owner's PC. Standard library only.

    python -B tools\\den-label\\label.py sessions
    python -B tools\\den-label\\label.py surfaces <take>
    python -B tools\\den-label\\label.py boxes <take>          (or --practice, or --pad 25)
    python -B tools\\den-label\\label.py labels <take>         (or --practice)
    python -B tools\\den-label\\label.py remark
    python -B tools\\den-label\\label.py status
    python -B tools\\den-label\\label.py freeze fixture        (then: freeze labels)
    python -B tools\\den-label\\label.py verify
    python -B tools\\den-label\\label.py aside <take>          (drafts made under another protocol)

The passes open page.html in the owner's browser, served on 127.0.0.1 only with
a one-time token. Frames never leave the PC. Agents read only `status` output
and freeze lines.
"""
import argparse
import hmac
import json
import re
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import n1

HERE = Path(__file__).resolve().parent
DEFAULT_TAKES = r"C:\den-takes"
FREEZES = HERE / "FREEZES.md"
MAX_BODY = 4 * 1024 ** 2
PAGE_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def new_actor(pass_name):
    actor = {"labeller": "owner", "pass": pass_name, "started_unix": n1.now(), "saved_unix": None,
             "tools": n1.tool_sha256s()}
    if pass_name in ("label", "remark"):
        actor["end_to_end"] = None
    return actor


def check_surfaces(surfaces):
    if type(surfaces) is not list:
        raise n1.Refused("surfaces must be a list")
    seen, out = set(), []
    for item in surfaces:
        if type(item) is not dict or set(item) != {"id", "type", "height_cm", "polygon"}:
            raise n1.Refused("each surface has id, type, height_cm and polygon")
        if not n1.SURFACE_ID.fullmatch(str(item["id"])) or item["id"] in seen:
            raise n1.Refused(f"surface id {item['id']!r} is empty, repeated or not lower-case letters and digits")
        seen.add(item["id"])
        if item["type"] not in n1.SURFACE_TYPES:
            raise n1.Refused(f"surface type {item['type']!r} is not in {n1.SURFACE_TYPES}")
        heights = item["height_cm"]
        if item["type"] == "handheld":
            if item["polygon"] is not None or type(heights) is not dict or set(heights) != {"seated", "standing"}:
                raise n1.Refused("handheld has no polygon and a seated and a standing height")
            values = list(heights.values())
        else:
            polygon = item["polygon"]
            if (type(polygon) is not list or len(polygon) < 3
                    or not all(type(p) is list and len(p) == 2 and all(n1.finite(v) for v in p)
                               and 0 <= p[0] <= n1.WIDTH and 0 <= p[1] <= n1.HEIGHT for p in polygon)):
                raise n1.Refused(f"surface {item['id']}: the polygon needs 3 or more corners inside the image")
            values = [heights]
        if not all(n1.finite(v) and type(v) is not bool and 0 < v <= 300 for v in values):
            raise n1.Refused(f"surface {item['id']}: heights are cm from the floor, 0 to 300")
        out.append(item)
    return out


class Pass:
    """One pass over one take: what the page may read and what it may save."""

    def __init__(self, ws, mode, take_id=None, practice=False, out=sys.stdout):
        self.ws, self.mode, self.practice, self.out = ws, mode, practice, out
        self.stop_reason, self.stopped, self.lock = None, threading.Event(), threading.Lock()
        self.split = None
        self.refused = None  # the reason the last save was refused, until a save is accepted
        if mode == "remark":
            take_id = ws.remark_take()
        self.take = take_id
        registered = mode in ("boxes", "labels", "remark") and not practice
        if registered:
            self.split = ws.entry(take_id)["split"]
            for line in ws.sync(take_id):
                print(line, file=out)
        self.allow_commissioning = not registered
        self.state = ws.require_ok(take_id, self.allow_commissioning)
        self.frames = n1.read_frames(self.state.folder)
        self.usable = n1.usable_frames(self.state, self.frames)
        if not self.usable:
            raise n1.Refused(f"{take_id} has no usable frames")
        self.by_n = {f["n"]: f for f in self.usable}
        self.t_of = {f["n"]: f["t"] for f in self.frames}
        self.segments = self.state.segments()
        kind = {"boxes": "boxes", "labels": "labels", "remark": "remark"}.get(mode)
        self.path = ws.surfaces_path() if mode == "surfaces" else ws.draft_path(kind, take_id, practice)
        self.draft = ws.load_draft(self.path)
        if mode == "labels" and not practice:
            self.check_unlocked()
            boxes = ws.load_draft(ws.draft_path("boxes", take_id))
            if boxes is None or not n1.audited_all(boxes["audited"], self.usable):
                raise n1.Refused(f"{take_id}'s label pass opens only after its box audit is complete")
        if mode == "remark":
            first = ws.load_draft(ws.draft_path("labels", take_id))
            days = ws.protocol.settings["remark_days"]
            if not (first and first["actor"]["end_to_end"]):
                raise n1.Refused(f"{take_id}'s label pass is not marked end to end yet")
            last_edit = first["actor"]["saved_unix"] or first["actor"]["end_to_end"]
            if n1.now() < last_edit + days * 86400:
                raise n1.Refused(f"the re-mark of {take_id} waits {days:g} days after the last edit of its "
                                 f"label pass, which was on {n1.local_date(last_edit)}")
        if self.draft is None:
            self.draft = self.new_draft()
            n1.write_json(self.path, self.draft)  # a label draft on disk locks the session's boxes

    def new_draft(self):
        common = {"protocol_sha256": self.ws.protocol.sha256}
        if self.mode == "surfaces":
            return dict(common, schema="den-n1-surfaces-draft-1", reference=None, actor=new_actor("surfaces"),
                        surfaces=[{"id": "handheld", "type": "handheld", "polygon": None,
                                   "height_cm": {"seated": n1.DEFAULT_SEATED_CM,
                                                 "standing": n1.DEFAULT_STANDING_CM}}])
        common.update(take=self.take, check=self.state.check, check_sha256=self.state.check_sha256)
        if self.mode == "boxes":
            return dict(common, schema="den-n1-boxes-draft-1", actor=new_actor("boxes"), keyframes=[], audited=[])
        return dict(common, schema="den-n1-labels-draft-1", split=self.split,
                    actor=new_actor("label" if self.mode == "labels" else "remark"), intervals=[], spans=[])

    def check_unlocked(self):
        """A session's first label pass is locked once its re-mark starts: no state, frames or saves."""
        if self.mode == "labels" and not self.practice and self.ws.remark_started(self.take):
            raise n1.Refused(f"the label pass of {self.take} is locked: its re-mark has started")

    def boxes_locked(self):
        return (self.mode == "boxes" and not self.practice
                and self.ws.draft_path("labels", self.take).exists())

    def page_state(self):
        self.check_unlocked()
        state = {"mode": self.mode, "take": self.take, "practice": self.practice,
                 "frames": [[f["n"], f["t"]] for f in self.usable], "segments": self.segments,
                 "lists": self.ws.protocol.lists(), "pause_s": self.ws.protocol.settings["pause_s"]}
        if self.mode == "boxes":
            state.update(keyframes=self.draft["keyframes"], audited=self.draft["audited"],
                         keyframe_times={str(n): self.t_of[n] for n, _ in self.draft["keyframes"]},
                         pad=n1.load_pad(self.ws.root, self.ws.protocol), locked=self.boxes_locked())
        elif self.mode in ("labels", "remark"):  # never boxes, never the first labels
            state.update(intervals=self.draft["intervals"], spans=self.draft["spans"],
                         end_to_end=bool(self.draft["actor"]["end_to_end"]))
        else:
            state.update(surfaces=self.draft["surfaces"],
                         reference_n=(self.draft["reference"] or {}).get("n"),
                         default_lap_cm=n1.DEFAULT_LAP_CM)
        return state

    def frame(self, n):
        if self.stopped.is_set():
            raise n1.Refused("this pass has ended")
        self.check_unlocked()
        frame = self.by_n.get(n)
        return None if frame is None else n1.read_nv12(self.state.folder, frame)

    def save(self, body):
        with self.lock:
            if self.stopped.is_set():
                raise n1.Refused("this pass has ended")
            try:
                reply = self.save_checked(body)
            except n1.Refused as exc:
                self.refused = str(exc)
                raise
            self.refused = None
            return reply

    def save_checked(self, body):
        """Check one save and write it. An unchanged save writes nothing, so it is not an edit."""
        self.check_unlocked()
        if type(body) is not dict:
            raise n1.Refused("a save is a JSON object")
        now_state = n1.take_state(self.state.folder, allow_commissioning=self.allow_commissioning)
        if now_state.check_names != self.state.check_names:
            raise n1.Refused("the take's check files changed since this pass opened; end it and run "
                             "label.py again")
        draft = json.loads(json.dumps(self.draft))
        if self.mode == "boxes":
            if self.boxes_locked():
                raise n1.Refused("boxes are locked: this session's label pass has started")
            allowed = set(self.by_n) | {k[0] for k in draft["keyframes"]}
            draft["keyframes"] = n1.check_keyframes(body.get("keyframes"), allowed)
            draft["audited"] = n1.check_ranges(body.get("audited"))
        elif self.mode in ("labels", "remark"):
            draft["intervals"], draft["spans"] = n1.check_labels(
                body.get("intervals"), body.get("spans"), self.ws.protocol, self.segments, self.t_of)
            if type(body.get("end_to_end")) is not bool:
                raise n1.Refused("end_to_end must be true or false")
            actor = draft["actor"]
            actor["end_to_end"] = (actor["end_to_end"] or n1.now()) if body["end_to_end"] else None
        else:
            n = body.get("reference_n")
            if type(n) is not int or n not in self.by_n:
                raise n1.Refused("the reference frame must be a usable frame")
            draft["surfaces"] = check_surfaces(body.get("surfaces"))
            draft["reference"] = {"take": self.take, "n": n,
                                  "frame_sha256": n1.sha256_bytes(n1.read_nv12(self.state.folder,
                                                                               self.by_n[n]))}
        if draft == self.draft:
            return {"ok": True, "saved_unix": draft["actor"]["saved_unix"]}
        draft["actor"]["saved_unix"] = n1.now()
        n1.write_json(self.path, draft)
        self.draft = draft
        return {"ok": True, "saved_unix": draft["actor"]["saved_unix"]}

    def stop(self, reason):
        """End the pass. "done" waits for an accepted last save; "second_person" always ends it."""
        if reason not in ("done", "second_person"):
            raise n1.Refused("reason must be done or second_person")
        with self.lock:
            if reason == "done" and self.refused:
                raise n1.Refused(f"not ended: the last save was refused ({self.refused}). Fix it on the page, "
                                 "or press Ctrl+C in the PowerShell window to stop without it")
            self.stop_reason = self.stop_reason or reason
            self.stopped.set()


def make_server(p, token=None):
    token = token or secrets.token_urlsafe(24)
    page = (HERE / "page.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "den-label", ""

        def log_message(self, *args):
            pass

        def send(self, status, content_type, body, extra=()):
            self.send_response(status)
            for name, value in (("Content-Type", content_type), ("Content-Length", str(len(body))),
                                ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                                ("Referrer-Policy", "no-referrer"), *extra):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, value):
            self.send(status, "application/json", json.dumps(value).encode("ascii"))

        def allowed(self):
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            supplied = self.headers.get("X-N1-Token") or parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            ok = (self.headers.get("Host") == f"127.0.0.1:{port}"
                  and origin in (None, f"http://127.0.0.1:{port}")
                  and hmac.compare_digest(supplied.encode("utf-8", "replace"), token.encode("ascii")))
            if not ok:
                self.send(403, "text/plain", b"Forbidden\n")
            return ok

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            match = re.fullmatch(r"/api/frame/([0-9]{1,7})", path)
            try:
                if path == "/":
                    self.send(200, "text/html; charset=utf-8", page, (("Content-Security-Policy", PAGE_CSP),))
                elif path == "/api/state":
                    self.send_json(200, p.page_state())
                elif match:
                    data = p.frame(int(match[1]))
                    if data is None:
                        self.send(404, "text/plain", b"Not a usable frame\n")
                    else:
                        self.send(200, "application/octet-stream", data)
                else:
                    self.send(404, "text/plain", b"Not found\n")
            except n1.Refused as exc:
                self.send_json(410, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    raise n1.Refused("empty or too large")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == "/api/save":
                    self.send_json(200, p.save(body))
                elif path == "/api/stop":
                    p.stop((body or {}).get("reason"))
                    self.send_json(200, {"ok": True})
                else:
                    self.send(404, "text/plain", b"Not found\n")
            except (ValueError, n1.Refused) as exc:
                self.send_json(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server, token


def serve(p, *, open_browser=True, ready=None, out=sys.stdout):
    """Serve one pass until the page ends it (or Ctrl+C). Returns the stop reason."""
    server, token = make_server(p)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Page for {p.mode} on {p.take}: {url}", file=out, flush=True)
    print("This PC only. Leave this window open; the page ends the pass.", file=out, flush=True)
    if ready:
        ready(url)
    if open_browser:
        webbrowser.open(url)
    try:
        while not p.stopped.wait(0.2):
            pass
    except KeyboardInterrupt:
        p.stop_reason = p.stop_reason or "interrupted"
    finally:
        server.shutdown()
        server.server_close()
    return p.stop_reason


def after_pass(ws, p, reason, out):
    not_saved = (f"The last change was not saved ({p.refused}); the draft holds the last accepted save."
                 if p.refused else None)
    if reason == "second_person":
        union = n1.take_state(p.state.folder, allow_commissioning=p.allow_commissioning).union
        print(f"Second person seen in {p.take}. The pass ended" + ("." if not_saved else " and the draft is saved."),
              file=out)
        if not_saved:
            print(not_saved, file=out)
        print(f"Re-run: python -B tools\\den-take\\check.py {ws.takes / p.take}", file=out)
        if union == "(no earlier spans)":
            print("Earlier spans: (no earlier spans). At check.py's prompt type only the new spans.", file=out)
        else:
            print(f"Earlier spans: {union}", file=out)
            print("At check.py's prompt type those spans plus the new ones.", file=out)
    elif reason == "done":
        print(f"Saved. {p.mode} pass on {p.take} ended.", file=out)
    else:
        print(f"Stopped ({reason}). Everything saved so far is kept.", file=out)
        if not_saved:
            print(not_saved, file=out)


def run_pass(ws, mode, take_id, practice, args, out, ready):
    p = Pass(ws, mode, take_id, practice, out=out)
    reason = serve(p, open_browser=not args.no_browser, ready=ready, out=out)
    after_pass(ws, p, reason, out)
    return 0


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--takes", default=DEFAULT_TAKES, help="the takes folder, default %(default)s")
    common.add_argument("--n1", help=r"the N1 folder, default <takes>\n1")
    common.add_argument("--no-browser", action="store_true", help="print the page address only")
    top = argparse.ArgumentParser(description="N1: boxes and labels for den takes, on this PC only.")
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("sessions", parents=[common], help="register new retained staged takes and their split")
    for name in ("surfaces", "labels"):
        command = sub.add_parser(name, parents=[common])
        command.add_argument("take")
        if name == "labels":
            command.add_argument("--practice", action="store_true")
    boxes = sub.add_parser("boxes", parents=[common])
    boxes.add_argument("take", nargs="?")
    boxes.add_argument("--practice", action="store_true")
    boxes.add_argument("--pad", type=float, help="raise the pad to this percent of the box (only up)")
    boxes.add_argument("--pad-min", type=float, help="raise the pad's minimum, in px (only up)")
    sub.add_parser("remark", parents=[common])
    sub.add_parser("status", parents=[common])
    freeze = sub.add_parser("freeze", parents=[common])
    freeze.add_argument("what", choices=("fixture", "labels"))
    sub.add_parser("verify", parents=[common])
    aside = sub.add_parser("aside", parents=[common],
                           help="move a take's drafts made under another protocol aside (never deleted)")
    aside.add_argument("take")
    return top


def freezes_text():
    return FREEZES.read_text(encoding="utf-8") if FREEZES.exists() else ""


def main(argv=None, out=None, ready=None, protocol=None):
    out = out or sys.stdout
    args = parser().parse_args(argv)
    try:
        ws = n1.Workspace(args.takes, args.n1 or str(Path(args.takes) / "n1"), protocol=protocol)
        if args.command == "sessions":
            added, notes = ws.scan()
            for entry in added:
                print(f"registered {entry['session']} ({entry['script']} #{entry['order']}): {entry['split']}",
                      file=out)
            for note in notes:
                print(note, file=out)
            for line in ws.sync_all():
                print(line, file=out)
            print(f"{len(ws.entries())} sessions registered.", file=out)
            return 0
        if args.command == "boxes" and (args.pad is not None or args.pad_min is not None):
            current = n1.load_pad(ws.root, ws.protocol)
            fraction = current["fraction"] if args.pad is None else round(args.pad / 100, 6)
            minimum = current["min_px"] if args.pad_min is None else args.pad_min
            pad = n1.set_pad(ws.root, ws.protocol, fraction, minimum)
            print(f"pad is now {pad['fraction'] * 100:g}% and at least {pad['min_px']:g} px, for every session",
                  file=out)
            return 0
        if args.command in ("surfaces", "boxes", "labels", "remark"):
            if args.command in ("boxes",) and not args.take:
                raise n1.Refused("name a take, or give --pad")
            return run_pass(ws, args.command, getattr(args, "take", None), getattr(args, "practice", False),
                            args, out, ready)
        if args.command == "status":
            for line in ws.sync_all():
                print(line, file=out)
            print("\n".join(ws.status()), file=out)
            return 0
        if args.command == "freeze":
            if args.what == "fixture":
                line, changes = ws.freeze_fixture(freezes_text())
                record = None
            else:
                line, record, changes = ws.freeze_labels(freezes_text())
            for change in changes:
                print(change, file=out)
            print("Paste this line to the agent for FREEZES.md:", file=out)
            print(line, file=out)
            if record:
                print("EvalRecord fields N1 fills:", file=out)
                print(json.dumps(record, sort_keys=True), file=out)
            return 0
        if args.command == "verify":
            ok, lines = ws.verify(freezes_text())
            print("\n".join(lines), file=out)
            return 0 if ok else 1
        if args.command == "aside":
            for line in ws.aside(args.take):
                print(line, file=out)
            print(f"Nothing was deleted. Redo {args.take}'s passes with label.py as usual.", file=out)
            return 0
    except n1.Refused as exc:
        print(f"Refused: {exc}", file=out if out is not sys.stdout else sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
