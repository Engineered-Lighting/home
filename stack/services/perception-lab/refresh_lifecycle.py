"""Compose refresh resources on the ASGI loop that will use their SQLite journal."""


class RefreshLifecycle:
    def __init__(self, factory):
        self.factory = factory
        self.backend = None
        self.started = False

    async def start(self):
        if self.started:
            raise RuntimeError('refresh lifecycle cannot restart')
        self.started = True
        # Trusted application composition only, never a module name from HTTP.
        self.backend = self.factory()

    async def accept(self, namespace, request_id):
        if self.backend is None:
            raise RuntimeError('refresh unavailable')
        return await self.backend.accept(namespace, request_id)

    async def status(self, namespace, request_id):
        if self.backend is None:
            raise RuntimeError('refresh unavailable')
        return await self.backend.status(namespace, request_id)

    async def close(self):
        if self.backend is not None:
            await self.backend.close()
            self.backend.journal.close()
            self.backend = None

    async def image(self, namespace, request_id):
        if self.backend is None:
            raise RuntimeError('refresh unavailable')
        return await self.backend.image(namespace, request_id)
