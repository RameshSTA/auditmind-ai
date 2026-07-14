"""Local filesystem blob storage — stands in for Azure Blob Storage behind the ``BlobStorage``
port, so a real Azure adapter later is a new file, not a design change to the application layer
that calls it.

File I/O is offloaded to a worker thread via ``anyio.to_thread.run_sync`` rather than called
directly — a synchronous ``Path.write_bytes`` call inside an ``async def`` would block the event
loop for the duration of the write, defeating the async-first design every other I/O boundary in
this codebase follows.
"""

from __future__ import annotations

import functools
from pathlib import Path

import anyio


class LocalFilesystemBlobStorage:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    async def put(
        self, *, engagement_id: str, document_id: str, filename: str, content: bytes
    ) -> str:
        engagement_dir = self._root / engagement_id
        await anyio.to_thread.run_sync(
            functools.partial(engagement_dir.mkdir, parents=True, exist_ok=True)
        )

        # document_id (already a UUID) prefixes the stored filename so two uploads of the same
        # original filename in the same engagement never collide.
        safe_name = f"{document_id}__{filename}"
        path = engagement_dir / safe_name
        await anyio.to_thread.run_sync(path.write_bytes, content)

        return str(path.relative_to(self._root))

    async def get(self, storage_uri: str) -> bytes:
        path = self._root / storage_uri
        result: bytes = await anyio.to_thread.run_sync(path.read_bytes)
        return result
