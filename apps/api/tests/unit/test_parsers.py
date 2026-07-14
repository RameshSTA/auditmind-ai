"""Unit tests for document parsers (Phase 6 §3)."""

from __future__ import annotations

import pytest
from fpdf import FPDF

from auditmind_api.ingestion.application.exceptions import (
    DocumentParsingError,
    UnsupportedMimeTypeError,
)
from auditmind_api.ingestion.infrastructure.parsers import (
    ParserRouter,
    PlainTextParser,
    PyPdfParser,
    default_parser_router,
)


def _make_real_pdf(text: str) -> bytes:
    """Generates an actual, valid, extractable-text PDF — not synthetic/fake bytes — so the
    parser is tested against real PDF structure, the same class of input it handles in
    production."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text=text)
    return bytes(pdf.output())


class TestPlainTextParser:
    def test_can_parse_text_plain(self) -> None:
        parser = PlainTextParser()
        assert parser.can_parse("text/plain") is True
        assert parser.can_parse("application/pdf") is False

    def test_parses_utf8_content(self) -> None:
        parser = PlainTextParser()
        result = parser.parse("Hello, world — evidence note.".encode())
        assert result == "Hello, world — evidence note."

    def test_raises_on_undecodable_bytes(self) -> None:
        parser = PlainTextParser()
        invalid_utf8 = b"\xff\xfe\x00\x01invalid"
        with pytest.raises(DocumentParsingError):
            parser.parse(invalid_utf8)


class TestPyPdfParser:
    def test_can_parse_application_pdf(self) -> None:
        parser = PyPdfParser()
        assert parser.can_parse("application/pdf") is True
        assert parser.can_parse("text/plain") is False

    def test_extracts_text_from_a_real_pdf(self) -> None:
        parser = PyPdfParser()
        pdf_bytes = _make_real_pdf("Hello from a real test PDF.")

        result = parser.parse(pdf_bytes)

        assert "Hello from a real test PDF." in result

    def test_raises_document_parsing_error_on_malformed_pdf(self) -> None:
        parser = PyPdfParser()
        with pytest.raises(DocumentParsingError):
            parser.parse(b"this is not a pdf at all")

    def test_raises_on_a_pdf_with_no_extractable_text(self) -> None:
        """An empty page (no text drawn at all) — the closest reproducible stand-in for a
        scanned, image-only PDF, which this increment's parser deliberately fails rather than
        silently indexing as an empty, misleadingly "successful" document."""
        parser = PyPdfParser()
        pdf = FPDF()
        pdf.add_page()
        empty_pdf_bytes = bytes(pdf.output())

        with pytest.raises(DocumentParsingError, match="No extractable text"):
            parser.parse(empty_pdf_bytes)


class TestParserRouter:
    def test_routes_to_the_matching_parser(self) -> None:
        router = ParserRouter([PlainTextParser(), PyPdfParser()])

        result = router.parse(mime_type="text/plain", content=b"routed correctly")

        assert result == "routed correctly"

    def test_raises_unsupported_mime_type_when_no_parser_matches(self) -> None:
        router = ParserRouter([PlainTextParser(), PyPdfParser()])

        with pytest.raises(UnsupportedMimeTypeError):
            router.parse(mime_type="image/png", content=b"\x89PNG...")

    def test_default_router_handles_both_supported_types(self) -> None:
        router = default_parser_router()

        plain_result = router.parse(mime_type="text/plain", content=b"plain text works")
        assert plain_result == "plain text works"

        pdf_bytes = _make_real_pdf("PDF via default router.")
        pdf_result = router.parse(mime_type="application/pdf", content=pdf_bytes)
        assert "PDF via default router." in pdf_result
