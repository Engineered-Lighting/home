"""pytest configuration for the Living Lights simulation.

The Home Assistant test fixture fails a test when timers or tasks are still
pending at teardown. A simulated night always leaves pending timers (the next
5-minute tick, `for:` holds, a pilot mid-ramp), so that check is disabled
here. Nothing else about the fixture is changed.
"""
import pytest


@pytest.fixture
def expected_lingering_tasks() -> bool:
    return True


@pytest.fixture
def expected_lingering_timers() -> bool:
    return True
