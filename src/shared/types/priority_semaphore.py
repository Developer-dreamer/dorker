import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager
from typing import AsyncGenerator


class PrioritySemaphore:
    def __init__(self, value: int = 5):
        if value < 0:
            raise ValueError("Semaphore value must be >= 0")
        self._value = value
        self._waiters = []
        self._counter = itertools.count()

    async def acquire(self, priority: int) -> bool:
        if self._value > 0 and not self._waiters:
            self._value -= 1
            return True

        event = asyncio.Event()
        waiter_entry = [priority, next(self._counter), event, False]
        heapq.heappush(self._waiters, waiter_entry)

        try:
            await event.wait()
        except asyncio.CancelledError:
            waiter_entry[3] = True
            if event.is_set():
                self.release()
            raise

        return False

    def release(self) -> None:
        while self._waiters:
            waiter_entry = heapq.heappop(self._waiters)
            if not waiter_entry[3]:
                waiter_entry[2].set()
                return
        self._value += 1

    @asynccontextmanager
    async def request(self, priority: int) -> AsyncGenerator["PrioritySemaphore"]:
        await self.acquire(priority)
        try:
            yield self
        finally:
            self.release()
