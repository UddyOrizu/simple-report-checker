import asyncio
import uuid
from collections import defaultdict


class Broadcaster:
    """One asyncio.Queue per document — in-process, no external dependency. Publishers push
    events; subscribers (the SSE endpoint, in Phase 7.2) drain their own queue independently."""

    def __init__(self) -> None:
        self._queues: dict[uuid.UUID, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, document_id: uuid.UUID) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[document_id].append(queue)
        return queue

    def unsubscribe(self, document_id: uuid.UUID, queue: asyncio.Queue) -> None:
        subscribers = self._queues.get(document_id)
        if subscribers and queue in subscribers:
            subscribers.remove(queue)
        if subscribers is not None and not subscribers:
            self._queues.pop(document_id, None)

    async def publish(self, document_id: uuid.UUID, event: dict) -> None:
        for queue in self._queues.get(document_id, []):
            await queue.put(event)


broadcaster = Broadcaster()
