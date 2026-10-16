"""Shared start/stop sequencing for the CLI and the GUI controller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import Protocol

logger = logging.getLogger(__name__)


class Stoppable(Protocol):
    async def stop(self) -> None: ...


async def stop_servers(servers: Sequence[Stoppable]) -> None:
    """Stop *servers* in reverse start order; one failing ``stop()`` never skips the rest."""
    cancelled: asyncio.CancelledError | None = None
    for srv in reversed(servers):
        try:
            await srv.stop()
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception:
            logger.exception("Error while stopping %s", type(srv).__name__)
    if cancelled is not None:
        raise cancelled


async def run_startup(start: Awaitable[None], stop_event: asyncio.Event) -> bool:
    """Await *start*, abandoning it if *stop_event* is set first.

    Returns False when startup was interrupted. Exceptions raised by *start* propagate.
    """
    startup = asyncio.ensure_future(start)
    stopper = asyncio.ensure_future(stop_event.wait())
    try:
        await asyncio.wait({startup, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if startup.done():
            startup.result()
            return True
        return False
    finally:
        for task in (startup, stopper):
            if not task.done():
                task.cancel()
        await asyncio.gather(startup, stopper, return_exceptions=True)
