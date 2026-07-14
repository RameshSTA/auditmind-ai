"""Document parsers, simplified to what's implementable with pure-Python libraries — no Azure
Document Intelligence or Docling network dependency. The ``ParserRouter`` shape is deliberately
generic, so replacing a parser with a real Document Intelligence/Docling adapter later is a swap,
not a redesign."""

from __future__ import annotations

from io import BytesIO
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from auditmind_api.ingestion.application.exceptions import (
    DocumentParsingError,
    UnsupportedMimeTypeError,
)


class _FormatParser(Protocol):
    """Per-format parser shape — an infrastructure-internal detail. The application layer
    depends only on ``domain.ports.ParserRouter``'s ``parse(mime_type=..., content=...)``
    signature and never needs to know a router is composed of several of these."""

    def can_parse(self, mime_type: str) -> bool: ...
    def parse(self, content: bytes) -> str: ...


class PlainTextParser:
    """Handles ``text/plain`` uploads — the trivial case, included because it costs nothing and
    is exactly the common case for exported email bodies and policy text dumps."""

    _SUPPORTED_MIME_TYPES = frozenset({"text/plain"})

    def can_parse(self, mime_type: str) -> bool:
        return mime_type in self._SUPPORTED_MIME_TYPES

    def parse(self, content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentParsingError(f"Could not decode text content as UTF-8: {exc}") from exc


class PyPdfParser:
    """Handles ``application/pdf`` uploads with native text layers.

    Extracts only native PDF text — no OCR or table-structure extraction. A scanned, image-only
    PDF will parse as empty text and is treated as a parsing failure below rather than silently
    producing an empty, misleadingly "successful" document.
    """

    _SUPPORTED_MIME_TYPES = frozenset({"application/pdf"})

    def can_parse(self, mime_type: str) -> bool:
        return mime_type in self._SUPPORTED_MIME_TYPES

    def parse(self, content: bytes) -> str:
        try:
            reader = PdfReader(BytesIO(content))
            pages_text = [page.extract_text() or "" for page in reader.pages]
        except PdfReadError as exc:
            raise DocumentParsingError(f"PDF could not be read: {exc}") from exc

        combined = "\n\n".join(p for p in pages_text if p.strip())
        if not combined.strip():
            raise DocumentParsingError(
                "No extractable text found — this may be a scanned, image-only PDF, which "
                "requires OCR (not currently supported)."
            )
        return combined


class ParserRouter:
    """Routes to the first registered parser able to handle the given mime type.

    Satisfies ``domain.ports.ParserRouter`` — the interface the application layer actually
    depends on.
    """

    def __init__(self, parsers: list[_FormatParser]) -> None:
        self._parsers = parsers

    def parse(self, *, mime_type: str, content: bytes) -> str:
        for parser in self._parsers:
            if parser.can_parse(mime_type):
                return parser.parse(content)
        raise UnsupportedMimeTypeError(f"No parser available for mime type '{mime_type}'.")


def default_parser_router() -> ParserRouter:
    return ParserRouter([PlainTextParser(), PyPdfParser()])
