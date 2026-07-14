"""Local filesystem storage for exported report PDFs, behind the ``ReportFileStorage`` port.

Deliberately its own small adapter rather than an import of ``ingestion``'s
``LocalFilesystemBlobStorage`` — same reasoning as ``chunk_lookup.py``'s cross-context read port:
each bounded context owns the shape of what it needs, never another context's infrastructure
class. The two classes look similar by convention, not by shared code.
"""

from __future__ import annotations

import functools
from pathlib import Path

import anyio


class LocalFilesystemReportStorage:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    async def put(self, *, engagement_id: str, report_id: str, content: bytes) -> str:
        engagement_dir = self._root / engagement_id
        await anyio.to_thread.run_sync(
            functools.partial(engagement_dir.mkdir, parents=True, exist_ok=True)
        )
        path = engagement_dir / f"{report_id}.pdf"
        await anyio.to_thread.run_sync(path.write_bytes, content)
        return str(path.relative_to(self._root))

    async def get(self, storage_uri: str) -> bytes:
        path = self._root / storage_uri
        result: bytes = await anyio.to_thread.run_sync(path.read_bytes)
        return result
