import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncGenerator


@dataclass(order=True)
class _Waiter:
    priority: int
    count: int
    event: asyncio.Event = field(compare=False)
    cancelled: bool = field(compare=False, default=False)


class PrioritySemaphore:
    def __init__(self, value: int = 5):
        if value < 0:
            raise ValueError("Semaphore value must be >= 0")
        self._value = value
        self._waiters: list[_Waiter] = []
        self._counter = itertools.count()

    async def acquire(self, priority: int) -> bool:
        if self._value > 0 and not self._waiters:
            self._value -= 1
            return True

        event = asyncio.Event()
        waiter_entry = _Waiter(priority, next(self._counter), event)
        heapq.heappush(self._waiters, waiter_entry)

        try:
            await event.wait()
        except asyncio.CancelledError:
            waiter_entry.cancelled = True
            if event.is_set():
                self.release()
            raise

        return False

    def release(self) -> None:
        while self._waiters:
            waiter_entry = heapq.heappop(self._waiters)
            if not waiter_entry.cancelled:
                waiter_entry.event.set()
                return
        self._value += 1

    @asynccontextmanager
    async def request(self, priority: int) -> AsyncGenerator["PrioritySemaphore"]:
        await self.acquire(priority)
        try:
            yield self
        finally:
            self.release()
