"""Unit tests for structure-aware chunking."""

from __future__ import annotations

from auditmind_api.ingestion.application.chunking import chunk_text


def test_short_text_produces_a_single_chunk() -> None:
    text = "This is a short paragraph.\n\nAnd a second one."

    chunks = chunk_text(text, target_words=100, min_words=5)

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_paragraphs_are_never_split_when_they_fit_the_budget() -> None:
    para_a = "Word " * 50
    para_b = "Other " * 50
    text = f"{para_a.strip()}\n\n{para_b.strip()}"

    chunks = chunk_text(text, target_words=60, min_words=5)

    # Both paragraphs fit in the first chunk (50 words), but adding the second would exceed 60,
    # so they split into two chunks — each containing exactly one whole, un-split paragraph.
    assert len(chunks) == 2
    assert para_a.strip() in chunks[0].text
    assert para_b.strip() in chunks[1].text


def test_multiple_small_paragraphs_are_combined_into_one_chunk() -> None:
    paragraphs = [f"Paragraph number {i} with a few words in it." for i in range(5)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, target_words=100, min_words=5)

    assert len(chunks) == 1
    for para in paragraphs:
        assert para in chunks[0].text


def test_a_paragraph_exceeding_the_budget_is_split_with_overlap() -> None:
    huge_paragraph = " ".join(f"word{i}" for i in range(1000))

    chunks = chunk_text(huge_paragraph, target_words=100, min_words=10, overlap_ratio=0.1)

    assert len(chunks) > 1
    # Overlap: the tail of one sub-chunk reappears at the head of the next.
    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    overlap_region = first_words[-10:]
    assert overlap_region[0] in second_words[:15]


def test_a_trailing_fragment_below_the_minimum_is_merged_into_its_predecessor() -> None:
    full_paragraph = " ".join(f"w{i}" for i in range(120))
    tiny_trailing = "short tail"
    text = f"{full_paragraph}\n\n{tiny_trailing}"

    chunks = chunk_text(text, target_words=100, min_words=20)

    # Without merging this would be 2 chunks (100 words + a 2-word fragment); merging the
    # under-floor trailing fragment into its predecessor should leave exactly one chunk here
    # since nothing else exceeded the budget enough to force an independent split.
    assert any(tiny_trailing in c.text for c in chunks)
    assert not any(c.text.strip() == tiny_trailing for c in chunks)


def test_char_offsets_are_exact_for_whole_untouched_paragraphs() -> None:
    text = "First paragraph here.\n\nSecond paragraph here."

    chunks = chunk_text(text, target_words=3, min_words=1)

    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text


def test_empty_text_produces_no_chunks() -> None:
    assert chunk_text("") == []


def test_blank_lines_and_whitespace_only_paragraphs_are_ignored() -> None:
    text = "Real paragraph.\n\n   \n\n\n\nAnother real paragraph."

    chunks = chunk_text(text, target_words=100, min_words=1)

    combined = " ".join(c.text for c in chunks)
    assert "Real paragraph." in combined
    assert "Another real paragraph." in combined
