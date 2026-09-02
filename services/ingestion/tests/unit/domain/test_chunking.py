import pytest
from ingestion.domain.chunking import chunk_text


def test_short_record_is_one_whole_chunk() -> None:
    text = "A short report.\n\nWith two paragraphs."
    (chunk,) = chunk_text(text, window_chars=4000, overlap_chars=200)
    assert (chunk.char_start, chunk.char_end) == (0, len(text))
    assert chunk.slices(text)


def test_long_record_splits_into_verifiable_overlapping_windows() -> None:
    sentences = [f"Sentence number {i} describes an event at the marina." for i in range(40)]
    text = "\n\n".join(" ".join(sentences[i : i + 4]) for i in range(0, 40, 4))
    chunks = chunk_text(text, window_chars=300, overlap_chars=60)

    assert len(chunks) > 1
    assert chunks[0].char_start == 0 and chunks[-1].char_end == len(text)
    assert all(chunk.slices(text) for chunk in chunks)
    assert all(len(chunk.text) <= 300 for chunk in chunks)
    assert all(
        later.char_start < earlier.char_end
        for earlier, later in zip(chunks, chunks[1:], strict=False)
    )
    assert all(
        later.char_start > earlier.char_start
        for earlier, later in zip(chunks, chunks[1:], strict=False)
    )
    assert "".join(chunk.text for chunk in chunks).count("Sentence number 39") >= 1


def test_text_without_separators_still_progresses() -> None:
    text = "x" * 1000
    chunks = chunk_text(text, window_chars=100, overlap_chars=10)
    assert all(chunk.slices(text) for chunk in chunks)
    assert chunks[-1].char_end == 1000


def test_empty_text_yields_no_chunk_and_bad_overlap_is_rejected() -> None:
    assert chunk_text("", window_chars=10, overlap_chars=2) == []
    with pytest.raises(ValueError):
        chunk_text("abc", window_chars=10, overlap_chars=10)
