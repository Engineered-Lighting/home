"""Bounded startup admission for private listeners.

Core proves worker maintenance only with a heartbeat newer than its own startup
instant (`maintenance_observed_after`), and the worker heartbeats every ten
seconds. A single admission check made immediately after startup therefore
almost always reports the worker as unobserved. Retry the unchanged admission
for a bounded window instead of weakening what it checks.

Every OptionalWorkSuspendedError is retried within that window, including
schema, restore-replay and rollout suspensions; a condition that does not clear
within about 30 seconds still fails startup, and each attempt re-checks
everything. Any other exception fails startup immediately.
"""
import asyncio

from .errors import OptionalWorkSuspendedError

STARTUP_ADMISSION_SECONDS = 30.0
STARTUP_ADMISSION_INTERVAL = 1.0
ATTEMPT_TIMEOUT_SECONDS = 10.0


async def admit_at_startup(admission):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STARTUP_ADMISSION_SECONDS
    while True:
        try:
            async with asyncio.timeout(ATTEMPT_TIMEOUT_SECONDS):
                return await admission()
        except OptionalWorkSuspendedError:
            if loop.time() + STARTUP_ADMISSION_INTERVAL >= deadline:
                raise
            await asyncio.sleep(STARTUP_ADMISSION_INTERVAL)
