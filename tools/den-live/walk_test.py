#!/usr/bin/env python3
"""Step 4's walk test: your markers in deployed Home, measured from this PC. The owner's pastes.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\walk_test.py --spots
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\walk_test.py --home https://<your Home address>
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\walk_test.py --home https://<your Home address> --empty
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\walk_test.py --home https://<your Home address> --second

--spots, once: your scan opens on a local page (step 2's scan picker, in its spots mode: no camera picture). Click 4
floor spots you can find both in the room and in the scan, such as a rug corner or a sofa foot, and Save. They go to
<data root>\\private\\walk\\spots.json, with the scan revision they were picked on.

--home: the walk. It subscribes to den-live's people stream through your Home's tailnet address with no cookie, as
your phone does in look-only mode (GET /api/den-live/v1/homes/den/stream). It speaks "Stand on spot 1." and so on, 6 s
to get there and 10 s standing still on each. For each spot it takes the marker shown longest in those 10 s, the
median of its floor positions, and how far that is from the spot. The lag is capture to stream: each event's time on
LA after the frame arrived (emitted_ts less capture_ts) plus the camera-to-LA transit on its health line
(transit_s_median); when the Jetson's clock is off and the transit is null, it says so and gives LA's part alone.
--empty: 10 minutes with the den empty: how many markers showed, and for how long. Leave when it says so.
--second: with a helper. Stay in the den and have someone else come in. It says "two" out loud when it has shown two
markers together for 2 s, and counts how long they stayed two.

It prints the plan's block: words and numbers, distances in metres and times in seconds, never a position. The address
you give is never printed or kept.
"""
import argparse
import json
import math
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import register_now as rn  # noqa: E402  the scan page's launcher
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402  say, Stop, the data root, the speech
import stream  # noqa: E402

say, Stop = sc.say, sc.Stop
STREAM_PATH = stream.BASE + "den/stream"
SPOT_COUNT = 4
GOAL_M, GOAL_LAG_S = 0.5, 1.0


@dataclass(frozen=True)
class Timing:
    first_event_s: float = 20.0   # the stream's first people event, before any cue
    lead_s: float = 8.0           # after the first cue, before spot 1's own
    walk_s: float = 6.0           # after "Stand on spot N.", before its window
    window_s: float = 10.0
    empty_lead_s: float = 30.0    # to leave the den
    empty_s: float = 600.0
    second_s: float = 180.0
    together_s: float = 2.0       # two markers this long is "two"
    after_two_s: float = 10.0     # watched after that, for how long they stay two


# ---- the stream, as any tailnet device reads it ---------------------------------------------------------------------

class Subscriber(threading.Thread):
    """GET <home>/api/den-live/v1/homes/den/stream with no cookie. Each people event is kept with this PC's wall-clock
    arrival. A dropped stream reconnects after 1 s and is counted; 401, 403 and 404 won't mend by themselves, so they end
    it and say which."""

    def __init__(self, url, opener=urllib.request.urlopen):
        super().__init__(daemon=True, name="walk-test-stream")
        self.url, self.opener = url, opener
        self.events, self.lock, self.halt = [], threading.Lock(), threading.Event()
        self.refused = None
        self.reconnects = 0

    def run(self):
        while not self.halt.is_set():
            try:
                request = urllib.request.Request(self.url, headers={"Accept": "text/event-stream", "Cache-Control": "no-store"})
                with self.opener(request, timeout=10) as response:
                    self.read(response)
            except urllib.error.HTTPError as exc:
                self.refused = exc.code
                if exc.code in (401, 403, 404):
                    return
            except (OSError, ValueError):
                pass
            if not self.halt.is_set():
                self.reconnects += 1
                self.halt.wait(1.0)

    def read(self, response):
        name, data = None, []
        for raw in response:
            if self.halt.is_set():
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if name == "people" and data:
                    try:
                        event = json.loads("\n".join(data))
                    except ValueError:
                        event = None
                    if isinstance(event, dict):
                        with self.lock:
                            self.events.append((time.time(), event))
                name, data = None, []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)

    def between(self, start, end):
        with self.lock:
            return [(t, e) for t, e in self.events if start <= t <= end]

    def first(self):
        with self.lock:
            return self.events[0][1] if self.events else None


def no_stream_reason(code):
    return {401: "your Home asked for sign-in: it doesn't see this PC as a tailnet device. Is --proxy-protocol=1 on in "
                 "Tailscale Serve, and is the address your Home's tailnet one?",
            403: "your Home refused the stream (403)",
            404: "your Home has no den-live route yet: deploy step 4's Home first",
            502: "your Home's den-live route answered 502: is den-live running on LA?",
            503: "your Home's den-live route answered 503: is den-live running on LA?"}.get(
        code, "no people event came from your Home's den-live stream%s" % (" (it answered %s)" % code if code else ""))


# ---- the numbers ----------------------------------------------------------------------------------------------------

def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def spot_result(samples, spot_xy):
    """The marker shown in the most events of the window, its per-axis median floor position, and how far that is from
    the spot: {distance_m or None, markers}."""
    tracks = {}
    for _, event in samples:
        for track in event.get("tracks") or []:
            tracks.setdefault(track["track_id"], []).append(track["xy_m"])
    if not tracks:
        return {"distance_m": None, "markers": 0}
    points = max(tracks.values(), key=len)
    x, y = statistics.median(p[0] for p in points), statistics.median(p[1] for p in points)
    return {"distance_m": math.hypot(x - spot_xy[0], y - spot_xy[1]), "markers": len(tracks)}


def lag_result(samples):
    """Capture to stream per event: (emitted_ts - capture_ts) on LA, plus the transit on its health line."""
    on_la, transit, total = [], [], []
    for _, event in samples:
        if event.get("capture_ts") is None or event.get("emitted_ts") is None:
            continue
        here = event["emitted_ts"] - event["capture_ts"]
        on_la.append(here)
        crossing = (event.get("health") or {}).get("transit_s_median")
        if crossing is not None:
            transit.append(crossing)
            total.append(here + crossing)
    return {"median": statistics.median(total) if total else None, "p95": percentile(total, 0.95),
            "on_la": statistics.median(on_la) if on_la else None, "on_la_p95": percentile(on_la, 0.95),
            "transit": statistics.median(transit) if transit else None, "events": len(on_la)}


def shown_seconds(samples, test, gap_cap_s=2.0):
    """Seconds during which test(event) held: each event counts until the next, at most gap_cap_s."""
    total = 0.0
    for (t, event), (t_next, _) in zip(samples, samples[1:]):
        if test(event):
            total += min(gap_cap_s, max(0.0, t_next - t))
    return total


def empty_result(samples):
    ids = {track["track_id"] for _, event in samples for track in event.get("tracks") or []}
    return {"markers": len(ids), "shown_s": shown_seconds(samples, lambda e: bool(e.get("tracks"))),
            "offline_s": shown_seconds(samples, lambda e: (e.get("camera") or {}).get("status") != "ok"), "events": len(samples)}


def two_shown(event):
    return sum(1 for track in event.get("tracks") or [] if track.get("state") == "confirmed") >= 2


def second_result(samples, together_s):
    """When two confirmed markers first showed together for together_s (seconds after the first sample), and the
    longest they stayed two."""
    start = samples[0][0] if samples else 0.0
    first = run_start = None
    longest = 0.0
    for t, event in samples:
        if two_shown(event):
            run_start = t if run_start is None else run_start
            longest = max(longest, t - run_start)
            if first is None and t - run_start >= together_s:
                first = run_start - start
        else:
            run_start = None
    return {"after_s": first, "together_s": longest}


# ---- the lines ------------------------------------------------------------------------------------------------------

def walk_lines(results, lag, timing, now):
    out = ["Walk test, %s: %d floor spots, %g s each" % (now, len(results), timing.window_s),
           "Distance from spot: %s m   (goal: within %g m)" % (", ".join("none" if r["distance_m"] is None else "%.2f" % r["distance_m"]
                                                                  for r in results), GOAL_M)]
    if lag["median"] is not None:
        out.append("Lag, capture to stream: median %.2f s, 95th %.2f s   (goal: about %g s; camera to LA %.2f s, then %.2f s on LA)"
                   % (lag["median"], lag["p95"], GOAL_LAG_S, lag["transit"], lag["on_la"]))
    elif lag["on_la"] is not None:
        out.append("Lag, capture to stream: unknown; on LA alone, median %.2f s, 95th %.2f s. The camera-to-LA transit was null "
                   "on the stream's health line, so the Jetson's clock is off again" % (lag["on_la"], lag["on_la_p95"]))
    else:
        out.append("Lag, capture to stream: no frame's times came through the stream")
    for n, r in enumerate(results, 1):
        if r["markers"] > 1:
            out.append("Note: spot %d showed %d markers; the one shown longest is measured." % (n, r["markers"]))
        elif not r["markers"]:
            out.append("Note: spot %d showed no marker in its %g s." % (n, timing.window_s))
    return out


def empty_line(result, timing):
    minutes = "%g minutes" % (timing.empty_s / 60) if timing.empty_s >= 60 else "%g s" % timing.empty_s
    text = ("0 markers" if not result["markers"] else "%d marker%s, shown %.0f s in all"
            % (result["markers"], "" if result["markers"] == 1 else "s", result["shown_s"]))
    camera = "; the camera wasn't ok for %.0f s of it" % result["offline_s"] if result["offline_s"] >= 1 else ""
    return "Empty den, %s: %s   (goal: 0)%s" % (minutes, text, camera)


def second_line(result, timing):
    if result["after_s"] is None:
        return "Second person: never two markers together for %g s in %g minutes   (goal: two)" % (timing.together_s, timing.second_s / 60)
    return "Second person: two markers after %.0f s, together for %.0f s   (goal: two)" % (result["after_s"], result["together_s"])


# ---- the runs -------------------------------------------------------------------------------------------------------

def stream_url(home):
    parts = urlsplit(home or "")
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise Stop("--home must be your Home's address, like https://<name>.<tailnet>, with no path")
    return "%s://%s%s" % (parts.scheme, parts.netloc, STREAM_PATH)


def connect(home, timing, opener):
    sub = Subscriber(stream_url(home), opener)
    sub.start()
    end = time.monotonic() + timing.first_event_s
    while sub.first() is None and time.monotonic() < end and sub.is_alive():
        time.sleep(0.1)
    if sub.first() is None:
        sub.halt.set()
        raise Stop(no_stream_reason(sub.refused))
    return sub


def load_spots(root):
    path = Path(root) / "private" / "walk" / "spots.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        spots = doc["spots"]
        if len(spots) != SPOT_COUNT or not all(len(s["xy_m"]) == 2 and all(math.isfinite(v) for v in s["xy_m"]) for s in spots):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise Stop("no floor spots yet: paste walk_test.py --spots first") from None
    return doc


def walk(*, root, home, timing, speak, wait, opener=urllib.request.urlopen, now=None):
    doc = load_spots(root)
    sub = connect(home, timing, opener)
    try:
        first = sub.first()
        if doc.get("content_revision") and first.get("content_revision") and doc["content_revision"] != first["content_revision"]:
            raise Stop("the spots were picked on scan revision %s, and den-live draws on %s: paste walk_test.py --spots again"
                       % (doc["content_revision"], first["content_revision"]))
        if (first.get("camera") or {}).get("status") != "ok":
            say("Note: den-live says the camera is %s; the walk goes on." % ((first.get("camera") or {}).get("status") or "unknown"))
        began = time.time()
        speak("Walk test. %d spots, %g seconds each. Go to spot 1." % (SPOT_COUNT, timing.window_s))
        wait(timing.lead_s)
        results = []
        for n, spot in enumerate(doc["spots"], 1):
            speak("Stand on spot %d." % n)
            wait(timing.walk_s)
            start = time.time()
            wait(timing.window_s)
            results.append(spot_result(sub.between(start, time.time()), spot["xy_m"]))
        speak("Done. Thank you.")
        lag = lag_result(sub.between(began, time.time()))
    finally:
        sub.halt.set()
    out = walk_lines(results, lag, timing, now or time.strftime("%Y-%m-%d %H:%M"))
    if sub.reconnects:
        out.append("Note: the stream dropped and came back %d times." % sub.reconnects)
    return out, results, lag


def empty(*, home, timing, speak, wait, opener=urllib.request.urlopen):
    sub = connect(home, timing, opener)
    try:
        speak("Empty den test. Please leave the den now, and stay out for %g minutes, until you hear done." % round(timing.empty_s / 60, 1))
        wait(timing.empty_lead_s)
        start = time.time()
        wait(timing.empty_s)
        result = empty_result(sub.between(start, time.time()))
        speak("Done. You can come back.")
    finally:
        sub.halt.set()
    return [empty_line(result, timing)], result


def second(*, home, timing, speak, wait, opener=urllib.request.urlopen):
    sub = connect(home, timing, opener)
    try:
        speak("Second person test. Stay in the den, and have someone else come in.")
        start = time.time()
        end, said = start + timing.second_s, None
        while time.time() < end:
            wait(0.25)
            found = second_result(sub.between(start, time.time()), timing.together_s)
            if found["after_s"] is not None and said is None:
                speak("Two.")
                said = time.time()
                end = min(end, said + timing.after_two_s)
        result = second_result(sub.between(start, time.time()), timing.together_s)
        speak("Done. Thank you.")
    finally:
        sub.halt.set()
    return [second_line(result, timing)], result


def inside_checkout(path):
    resolved = Path(path).resolve()
    return any((p / ".git").exists() for p in (resolved, *resolved.parents))


def pick_spots(root, page=rn.scan_page):
    folder = (Path(root) / "private" / "walk").resolve()
    if inside_checkout(folder):
        raise Stop("the data root is inside a git checkout; the spots stay private")
    folder.mkdir(parents=True, exist_ok=True)
    say("Your scan opens in the browser. Click %d floor spots you can find both in the room and in the scan, such as a rug "
        "corner or a sofa foot, spread over the floor the camera sees. Note which is which: the walk test says \"stand on "
        "spot 1\" and so on. Then Save." % SPOT_COUNT)
    if page(folder, None, "spots") != 0:
        raise Stop("the scan page closed without saving the spots")
    say("Saved %d floor spots, private, in %s. Next: the walk test, with your Home's address." % (SPOT_COUNT, folder))


def main(argv=None, *, root=None, console=sc.has_console, timing=Timing(), speak=None, wait=time.sleep,
         opener=urllib.request.urlopen, page=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spots", action="store_true", help="pick the 4 floor spots in your scan, once")
    parser.add_argument("--home", help="your Home's tailnet address, https://...")
    parser.add_argument("--empty", action="store_true", help="10 minutes with the den empty")
    parser.add_argument("--second", action="store_true", help="with a helper: a second person comes in")
    args = parser.parse_args(argv)
    if not console():
        say("walk test: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else sc.data_root()
    try:
        if args.spots:
            pick_spots(root, page or rn.scan_page)
            return 0
        if not args.home:
            raise Stop("give your Home's address: --home https://<your Home's tailnet address>")
        if not args.home.startswith("https://"):
            raise Stop("--home must start with https://, your Home's tailnet address")
        if speak is None:
            speak = sc.speaker(rl.den_take(sc.archive(root / "tools", "observe")))
        if args.empty:
            lines = empty(home=args.home, timing=timing, speak=speak, wait=wait, opener=opener)[0]
        elif args.second:
            lines = second(home=args.home, timing=timing, speak=speak, wait=wait, opener=opener)[0]
        else:
            lines = walk(root=root, home=args.home, timing=timing, speak=speak, wait=wait, opener=opener)[0]
        say("")
        for line in lines:
            say(line)
        say("Tell an agent these lines: they're words and numbers only.")
        return 0
    except Stop as exc:
        say("walk test: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        say("walk test: interrupted.")
        return 130
    except Exception as exc:  # a type name only
        say("walk test: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
