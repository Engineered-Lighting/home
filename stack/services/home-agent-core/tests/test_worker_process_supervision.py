from __future__ import annotations

import asyncio

import pytest

from app.main import terminate_on_unexpected_worker_exit


@pytest.mark.asyncio
async def test_unexpected_worker_exit_terminates_the_container_process() -> None:
    async def crash() -> None:
        raise RuntimeError("private detail must not be reflected")

    task = asyncio.create_task(crash())
    with pytest.raises(RuntimeError):
        await task
    calls: list[tuple[int, int]] = []

    terminate_on_unexpected_worker_exit(
        task,
        asyncio.Event(),
        terminate=lambda process_id, signal_number: calls.append(
            (process_id, signal_number)
        ),
    )

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_expected_worker_shutdown_does_not_terminate_process() -> None:
    stop = asyncio.Event()
    stop.set()
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    calls: list[tuple[int, int]] = []

    terminate_on_unexpected_worker_exit(
        task,
        stop,
        terminate=lambda process_id, signal_number: calls.append(
            (process_id, signal_number)
        ),
    )

    assert calls == []


@pytest.mark.asyncio
async def test_unexpected_worker_cancellation_terminates_the_process() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    calls: list[tuple[int, int]] = []

    terminate_on_unexpected_worker_exit(
        task,
        asyncio.Event(),
        terminate=lambda process_id, signal_number: calls.append(
            (process_id, signal_number)
        ),
    )

    assert len(calls) == 1
