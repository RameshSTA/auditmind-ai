"""Structure-aware chunking.

Paragraphs (blank-line-separated blocks) are the primary split boundary and are never split
unless a single paragraph alone exceeds the word budget, in which case it is further split with
overlap. A trailing fragment below the minimum floor is merged into its predecessor rather than
indexed alone.

Chunk size is budgeted in **words**, not tokens: no embedding model's real tokenizer is available
in this environment, and word count is a standard, honestly-labeled approximation (roughly 0.75
tokens per word for English text) rather than a claim of token-exact sizing. A real tokenizer-based
budget could replace this without changing the paragraph-boundary logic.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_TARGET_WORDS = 375  # approximates ~500 tokens at ~0.75 tokens/word
DEFAULT_MIN_WORDS = 40  # approximates ~50 tokens
DEFAULT_OVERLAP_RATIO = 0.12


@dataclass(frozen=True)
class TextChunk:
    text: str
    char_start: int
    char_end: int


def chunk_text(
    text: str,
    *,
    target_words: int = DEFAULT_TARGET_WORDS,
    min_words: int = DEFAULT_MIN_WORDS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[TextChunk]:
    """Splits ``text`` into structure-aware chunks. See module docstring for the policy."""
    paragraphs = _split_paragraphs(text)
    chunks: list[TextChunk] = []
    buffer: list[tuple[str, int, int]] = []
    buffer_words = 0

    def flush() -> None:
        nonlocal buffer, buffer_words
        if not buffer:
            return
        start = buffer[0][1]
        end = buffer[-1][2]
        combined = "\n\n".join(p[0] for p in buffer)
        chunks.append(TextChunk(text=combined, char_start=start, char_end=end))
        buffer = []
        buffer_words = 0

    for para_text, para_start, para_end in paragraphs:
        para_word_count = len(para_text.split())

        if para_word_count > target_words:
            flush()
            chunks.extend(
                _split_large_paragraph(para_text, para_start, target_words, overlap_ratio)
            )
            continue

        if buffer_words + para_word_count > target_words and buffer_words >= min_words:
            flush()

        buffer.append((para_text, para_start, para_end))
        buffer_words += para_word_count

    flush()

    if len(chunks) >= 2 and len(chunks[-1].text.split()) < min_words:
        last = chunks.pop()
        prev = chunks.pop()
        chunks.append(
            TextChunk(
                text=f"{prev.text}\n\n{last.text}",
                char_start=prev.char_start,
                char_end=last.char_end,
            )
        )

    return chunks


def _split_paragraphs(text: str) -> list[tuple[str, int, int]]:
    """Returns ``(paragraph_text, char_start, char_end)`` triples, splitting on blank lines."""
    paragraphs: list[tuple[str, int, int]] = []
    cursor = 0
    for block in text.split("\n\n"):
        start = text.index(block, cursor) if block else cursor
        end = start + len(block)
        stripped = block.strip()
        if stripped:
            paragraphs.append((stripped, start, end))
        cursor = end
    return paragraphs


def _split_large_paragraph(
    para_text: str, para_start: int, target_words: int, overlap_ratio: float
) -> list[TextChunk]:
    """A single paragraph too large to fit in one chunk: split by word count with overlap.

    Character offsets for these sub-chunks are approximate (anchored to the parent paragraph's
    start, not re-scanned per sub-chunk) — an accepted, documented approximation for the rare
    oversized-paragraph case; whole untouched paragraphs (the common case, handled above) retain
    exact offsets.
    """
    words = para_text.split()
    overlap_words = max(1, round(target_words * overlap_ratio))
    step = max(1, target_words - overlap_words)

    sub_chunks: list[TextChunk] = []
    i = 0
    while i < len(words):
        window = words[i : i + target_words]
        sub_text = " ".join(window)
        sub_chunks.append(
            TextChunk(text=sub_text, char_start=para_start, char_end=para_start + len(sub_text))
        )
        if i + target_words >= len(words):
            break
        i += step

    return sub_chunks
