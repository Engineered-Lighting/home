"""Contract 2's person state on disk: den-person-state-1, the serialised TrackingSnapshot. Standard library.

In <state>/days/<UTC date>/person-state.jsonl, beside the ask's asks.jsonl and kept as long (ask.RETENTION_DAYS):
every published snapshot with someone in view (a track, or presence present), at about 5 a second, and with nobody
in view at most one a second (IDLE_EVERY_S), plus one at once on any change of status, presence, epoch or run, or a
track coming or going. A line is about 0.6 KB with nobody in view and 1.7 KB with one person: about 50 MB for an empty
den's day, and about 25 MB more for each person-hour. The file has its own cap, CAP_BYTES (300 MB) a UTC day, apart
from the ask's 50 MB: past it, the day's lines are skipped and counted, and the status line says so, naming the file.
The LA service writes it; run_local.py keeps nothing.

Numbers only, by the contract: the snapshot's fields, with times, positions and joints as numbers, ids as opaque strings
and states as closed words. No image, no name and no free text, so two fields stay out: the snapshot's reason (words),
and each track's near phrase (the labels' words). Room metres are kept to the millimetre and seconds to the millisecond.
near can be had again offline: room_labels.Store.put writes every save, with its revision and its saved_at (to the
second), to room-labels-history.jsonl beside the labels, which holds the last 100 saves (HISTORY_KEEP). The save in
force at a line's frame capture_ts, its confirmed labels (when placed on the frame's scan_revision) and the track's
xy_m give room_labels.near_phrase again, right to about a second around each save.

The anchor: before a run's first snapshot in each day's file it writes one line
    {"schema": "den-person-state-1", "service_run_id": <run>,
     "anchor": {"mono": <time.monotonic()>, "ts": <time.time()>}}
so that run's *_mono times join the *_ts times of the ask log and the trace (contract 0's clocks).
"""
import time

from ask import AskLog

SCHEMA = "den-person-state-1"
FILE = "person-state.jsonl"
CAP_BYTES = 300 * 1024 * 1024   # a UTC day's person-state.jsonl; the ask's asks.jsonl has its own (ask.AskLog)
IDLE_EVERY_S = 1.0   # with nobody in view, one line in each second of the monotonic clock at most


def _number(value, places=3):
    return None if value is None else round(float(value), places)


def frame_record(frame):
    """The FrameRef's contract fields, or None for a snapshot with no frame."""
    if frame is None:
        return None
    return {"service_run_id": frame.service_run_id, "camera_epoch": frame.camera_epoch, "frame_id": frame.frame_id,
            "capture_mono": _number(frame.capture_mono), "arrival_mono": _number(frame.arrival_mono),
            "capture_ts": _number(frame.capture_ts), "transform_id": frame.transform_id,
            "scan_revision": frame.scan_revision, "depth_calibration_sha256": frame.depth_calibration_sha256}


def track_record(track):
    """One TrackEvidence's contract fields, near left out (words)."""
    seated = track.seated
    return {"track_id": track.track_id, "state": track.state, "xy_m": [_number(v) for v in track.xy_m],
            "sigma_m": _number(track.sigma_m), "position_basis": track.position_basis,
            "current_obs_ids": list(track.current_obs_ids),
            "association": {"status": track.association_status, "revision": track.association_revision,
                            "since_mono": _number(track.association_since_mono)},
            "supported_pose": {name: [[_number(v) for v in xyz], status]
                               for name, (xyz, status) in sorted(track.supported_pose.items())},
            "seated": None if seated is None else {"state": seated.state, "since_mono": _number(seated.since_mono),
                                                   "basis": seated.basis}}


def serialise(snapshot):
    """A TrackingSnapshot as one den-person-state-1 line, reason left out (words)."""
    return {"schema": SCHEMA, "service_run_id": snapshot.service_run_id, "camera_epoch": snapshot.camera_epoch,
            "seq": snapshot.seq, "frame": frame_record(snapshot.frame), "status": snapshot.status,
            "presence": snapshot.presence, "tracks": [track_record(track) for track in snapshot.tracks],
            "eligible_track_id": snapshot.eligible_track_id, "eligibility_revision": snapshot.eligibility_revision}


def nobody(snapshot):
    """Nobody in view: no track out, and presence not present."""
    return not snapshot.tracks and snapshot.presence != "present"


class Log:
    """Appends the snapshots it's given: each with someone in view; with nobody in view, the first in each second of
    the monotonic clock, and any whose status, presence, epoch, run or having tracks differs from the last line's
    (thinned counts the rest). A write that fails is counted, never raised, so the loop goes on. Past the day's cap
    (CAP_BYTES, this file's own) a line is skipped and counted (skipped, today's)."""

    name = FILE

    def __init__(self, state, *, clock=time.monotonic, wall=time.time, cap=CAP_BYTES):
        self.out = AskLog(state, name=FILE, cap=cap, wall=wall)
        self.clock, self.wall = clock, wall
        self.anchored = set()   # (UTC day, service_run_id): that day's file has this run's anchor
        self.failed = self.thinned = 0
        self.last = None   # the last line's (state, second): what an empty den's next line is held to

    @property
    def skipped(self):
        return self.out.skipped

    def due(self, snapshot, mono):
        """Whether this snapshot gets a line; noted as the last line's when it does."""
        state = (snapshot.service_run_id, snapshot.camera_epoch, snapshot.status, snapshot.presence, bool(snapshot.tracks))
        second = mono // IDLE_EVERY_S
        if nobody(snapshot) and self.last is not None and self.last == (state, second):
            self.thinned += 1
            return False
        self.last = state, second
        return True

    def write(self, snapshot):
        try:
            line = serialise(snapshot)
            mono, wall = self.clock(), self.wall()
            if not self.due(snapshot, mono):
                return
            key = (time.strftime("%Y-%m-%d", time.gmtime(wall)), snapshot.service_run_id)
            if key not in self.anchored:
                if not self.out.write({"schema": SCHEMA, "service_run_id": snapshot.service_run_id,
                                       "anchor": {"mono": _number(mono), "ts": _number(wall)}}, wall):
                    return   # the day's file is at its cap (counted): never a line without its anchor
                self.anchored.add(key)
            self.out.write(line, wall)
        except (OSError, ValueError, TypeError, AttributeError):
            self.failed += 1
