import asyncio

import pytest

from app import startup_admission
from app.errors import OptionalWorkSuspendedError
from app.startup_admission import admit_at_startup


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(startup_admission, "STARTUP_ADMISSION_SECONDS", 0.5)
    monkeypatch.setattr(startup_admission, "STARTUP_ADMISSION_INTERVAL", 0.01)


class Heartbeat:
    """Mirror Core's rule: maintenance is current only once the worker has
    heartbeated after the listener's startup instant."""

    def __init__(self, beats_until_observed):
        self.remaining = beats_until_observed
        self.calls = 0

    async def admission(self):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise OptionalWorkSuspendedError("retention maintenance unavailable")
        return "admitted"


@pytest.mark.asyncio
async def test_waits_for_a_heartbeat_newer_than_startup():
    heartbeat = Heartbeat(beats_until_observed=3)
    assert await admit_at_startup(heartbeat.admission) == "admitted"
    assert heartbeat.calls == 4


@pytest.mark.asyncio
async def test_suspension_that_never_clears_still_fails_startup():
    heartbeat = Heartbeat(beats_until_observed=10**6)
    with pytest.raises(OptionalWorkSuspendedError):
        await admit_at_startup(heartbeat.admission)
    assert heartbeat.calls > 1


@pytest.mark.asyncio
async def test_other_failures_are_not_retried():
    calls = 0

    async def restore_pending():
        nonlocal calls
        calls += 1
        raise RuntimeError("restore replay required")

    with pytest.raises(RuntimeError):
        await admit_at_startup(restore_pending)
    assert calls == 1


@pytest.mark.asyncio
async def test_each_attempt_is_time_bounded(monkeypatch):
    monkeypatch.setattr(startup_admission, "ATTEMPT_TIMEOUT_SECONDS", 0.05)

    async def hangs():
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        await admit_at_startup(hangs)
