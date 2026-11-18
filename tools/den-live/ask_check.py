#!/usr/bin/env python3
"""The inference plan's step 4: the ask check, from this PC. The owner's paste, once den-live asks Qwen on LA.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\ask_check.py --home https://<your Home address>

It follows den-live's people stream through your Home's tailnet address with no cookie, as the walk test does, and
speaks three cues:
1. "Sit down in your usual seat and read your book." For 2 minutes.
2. "Now put the book down and use your phone." For 2 minutes.
3. "Now get up and leave the den." For 30 seconds.

From the stream alone (its health.qwen line, and each track's activity), it prints when, after the first cue, Frigate
had seen you, den-live had you seated and asking, the first answer came, and the first close-up answer came. For each
cue it prints how many answers said close-up work, and the words they used. After the last cue, it prints when the
asks stopped, and Qwen's median answer time as den-live reports it. Times are this PC's, from each event's arrival,
so LA's clock never matters.

Words and numbers only: never a position, a picture or your Home's address. Do it with the room lit and nobody else in
the den. An answer shows on a marker only when den-live was sure whose it was, so a missed answer can mean that too.
"""
import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402  say, Stop, the data root, the speech
import cue_light  # noqa: E402  the cue-to-light instrument: the subscriber that keeps the spot snapshots too
import walk_test as wt  # noqa: E402  the stream's address and its refusals

say, Stop = sc.say, sc.Stop
CUES = (("read", "Sit down in your usual seat and read your book. Stay until the next cue."),
        ("phone", "Now put the book down and use your phone."),
        ("leave", "Now get up and leave the den, until you hear done."))


@dataclass(frozen=True)
class Timing:
    first_event_s: float = 20.0
    lead_s: float = 15.0          # the countdown before the first cue
    read_s: float = 120.0
    phone_s: float = 120.0
    leave_s: float = 30.0


def qwen(event):
    return ((event.get("health") or {}).get("qwen")) or {}


def answers(samples, skip=frozenset()):
    """[(arrival, word, close_up, key)] for each new answer the stream showed: one per key, (track, answer time), the
    first time it showed. Keys in skip (answers already shown before) are left out."""
    seen, out = set(skip), []
    for t, event in samples:
        for track in event.get("tracks") or []:
            activity = track.get("activity")
            if not isinstance(activity, dict):
                continue
            key = (track.get("track_id"), activity.get("at"))
            if key not in seen:
                seen.add(key)
                out.append((t, str(activity.get("word")), bool(activity.get("close_up")), key))
    return out


def first(samples, start, test):
    """Seconds after start of the first sample meeting test, or None."""
    for t, event in samples:
        if test(event):
            return round(max(0.0, t - start), 1)
    return None


def frigate_on(event):
    q = qwen(event)
    return q.get("state") in ("asking", "unavailable") or q.get("why") == "nobody seated"


def phase(samples, start, *, earlier=()):
    """One cue's numbers: answers, close-up count and words; and for the first cue, when each hop first held. An answer
    already seen before the cue (earlier) isn't counted again."""
    got = answers(samples, skip={a[3] for a in answers(earlier)})
    return {"answers": len(got), "close_up": sum(1 for a in got if a[2]), "words": Counter(a[1] for a in got),
            "frigate_s": first(samples, start, frigate_on), "asking_s": first(samples, start, lambda e: qwen(e).get("state") == "asking"),
            "first_answer_s": round(got[0][0] - start, 1) if got else None,
            "first_close_up_s": next((round(a[0] - start, 1) for a in got if a[2]), None)}


def stopped_s(samples, start):
    """Seconds after start until the stream stopped saying asking for good: the first event after its last asking one.
    0.0 when it never said asking after start; None when it still did at the end. A single quiet frame between asking
    ones doesn't count."""
    asking = lambda e: qwen(e).get("state") in ("asking", "unavailable")
    last = max((i for i, (_, e) in enumerate(samples) if asking(e)), default=None)
    if last is None:
        return 0.0
    if last == len(samples) - 1:
        return None
    return round(max(0.0, samples[last + 1][0] - start), 1)


def words(counter):
    return ", ".join("%s %d" % (w, n) for w, n in counter.most_common()) or "none"


def at(value):
    return "-" if value is None else "+%.1f s" % value


def lines(read, phone, leave_stopped, reply_s, frigate_before, now, counts=None):
    out = ["Ask check, %s" % time.strftime("%H:%M", time.localtime(now))]
    out.append("Read (2 min):   Frigate %s, seated and asking %s, first answer %s, first close-up %s"
               % ("on before the cue" if frigate_before else "on " + at(read["frigate_s"]), at(read["asking_s"]),
                  at(read["first_answer_s"]), at(read["first_close_up_s"])))
    out.append("                close-up on %d of %d answers (%s)" % (read["close_up"], read["answers"], words(read["words"])))
    out.append("Phone (2 min):  close-up on %d of %d answers (%s)" % (phone["close_up"], phone["answers"], words(phone["words"])))
    counts = counts or {}
    if counts.get("leave") == 0:
        leave = "no events came after the cue: the stream stopped"
    elif leave_stopped is None:
        leave = "still asking 30 s after the cue: tell an agent"
    else:
        leave = "asks stopped %.1f s after the cue" % leave_stopped
    out.append("Leave:          %s" % leave)
    out.append("Answer time:    %s" % ("median %.2f s, as den-live reports it" % reply_s if reply_s is not None else "no answers came"))
    if counts:
        out.append("Stream:         %d, %d and %d events in the three parts, %d reconnects"
                   % (counts.get("read", 0), counts.get("phone", 0), counts.get("leave", 0), counts.get("reconnects", 0)))
    return out


def check(*, home, timing, speak, wait, opener=None, clock=time.time, numbers=None):
    """numbers: a folder for the cue-to-light instrument's numbers (cues.json, spots.jsonl), or None."""
    sub = cue_light.connect(home, timing.first_event_s, opener)
    try:
        speak("The ask check starts in %d seconds. Have your book and phone with you." % timing.lead_s)
        wait(timing.lead_s)
        marks = {}
        for (name, cue), length in zip(CUES, (timing.read_s, timing.phone_s, timing.leave_s)):
            marks[name] = clock()
            speak(cue)
            wait(length)
        end = clock()
        speak("Done. Thank you.")
    finally:
        sub.halt.set()
    if sub.refused and not sub.first():
        raise Stop(wt.no_stream_reason(sub.refused))
    before = sub.between(0, marks["read"])
    read_samples, phone_samples = sub.between(marks["read"], marks["phone"]), sub.between(marks["phone"], marks["leave"])
    leave_samples = sub.between(marks["leave"], end)
    read = phase(read_samples, marks["read"], earlier=before)
    phone = phase(phone_samples, marks["phone"], earlier=before + read_samples)
    replies = [qwen(e).get("reply_s_median") for _, e in sub.between(marks["read"], end) if qwen(e).get("reply_s_median") is not None]
    frigate_before = bool(before) and frigate_on(before[-1][1])
    counts = {"read": len(read_samples), "phone": len(phone_samples), "leave": len(leave_samples), "reconnects": sub.reconnects}
    out = lines(read, phone, stopped_s(leave_samples, marks["leave"]), replies[-1] if replies else None, frigate_before, end,
                counts)
    spots = sub.spot_rows()
    cues = [(name, marks[name]) for name, _ in CUES]
    out += cue_light.report_lines(cues, spots)[1:]
    if numbers is not None:
        folder = cue_light.save(numbers, cues, spots)
        out.append("Light numbers: %s (cue_light.py report it, with --paint for Home's stamps)" % folder)
    return out


def main(argv=None, *, root=None, console=sc.has_console, timing=Timing(), speak=None, wait=time.sleep, opener=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", help="your Home's tailnet address, https://...")
    args = parser.parse_args(argv)
    if not console():
        say("ask check: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    try:
        if not args.home or not args.home.startswith("https://"):
            raise Stop("give your Home's address: --home https://<your Home's tailnet address>")
        if speak is None:
            root = Path(root) if root else sc.data_root()
            speak = sc.speaker(rl.den_take(sc.archive(root / "tools", "observe")))
        root = Path(root) if root else sc.data_root()
        numbers = root / "cue-light" / time.strftime("%Y%m%d-%H%M%S")   # numbers only: outside private, agents may read it
        out = check(home=args.home, timing=timing, speak=speak, wait=wait, opener=opener, numbers=numbers)
        say("")
        for line in out:
            say(line)
        say("Tell an agent these lines: they're words and numbers only.")
        return 0
    except Stop as exc:
        say("ask check: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        say("ask check: interrupted.")
        return 130
    except Exception as exc:  # a type name only
        say("ask check: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
