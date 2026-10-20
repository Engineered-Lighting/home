"""Per-call stage timings appended to the LA pilot's backend stderr lines.

Log lines only, never a proof field: Victoria rejects unknown proof keys. A
backend times its model_health preflight and its docker exec on the worker
thread that calls it; the line that reports the call reads them back on that
same thread. A stage that did not run reports 0.
"""
import contextlib
import math
import threading
import time

_calls = threading.local()


def start():
    """Forget the previous call's stages on this thread."""
    _calls.stages = {}


@contextlib.contextmanager
def stage(name):
    started = time.monotonic()
    try:
        yield
    finally:
        stages = getattr(_calls, 'stages', None)
        if stages is None:
            stages = _calls.stages = {}
        stages[name] = time.monotonic() - started


def _ms(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return 0
    return int(round(seconds * 1000))


def suffix(total_seconds):
    """' health_ms=<int> exec_ms=<int> total_ms=<int>' for this thread's call."""
    stages = getattr(_calls, 'stages', None) or {}
    return ' health_ms=%d exec_ms=%d total_ms=%d' % (_ms(stages.get('health')), _ms(stages.get('exec')),
                                                     _ms(total_seconds))
