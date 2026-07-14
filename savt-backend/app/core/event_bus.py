import asyncio


class EventBus:
    """In-memory pub/sub для SSE-стримов чата.

    Работает только в рамках одного процесса — этого достаточно, т.к. api
    запускается одним воркером uvicorn (см. Dockerfile, без --workers).
    Если когда-нибудь понадобится несколько воркеров/инстансов api,
    придётся переехать на Redis pub/sub — до тех пор это ненужное усложнение.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.setdefault(channel, set()).add(queue)
        return queue

    async def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subscribers.get(channel)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(channel, None)

    async def publish(self, channel: str, event: dict) -> None:
        async with self._lock:
            subs = list(self._subscribers.get(channel, ()))
        for queue in subs:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Медленный подписчик — теряем одно событие, а не копим
                # бэклог и не блокируем публикацию для остальных.
                pass


event_bus = EventBus()
