import pytest

from app.chunking.fixed_chunker import chunk_fixed


def test_windows_overlap_and_cover_the_text() -> None:
    text = "".join(chr(ord("a") + i % 26) for i in range(2500))
    chunks = chunk_fixed(text, "pkg/mod.py", "acme/widget", size=1000, overlap=200)

    # step = 800 -> windows start at 0, 800, 1600; the last one already reaches the end,
    # so there is no 4th window that would only repeat text.
    assert [c.id.rsplit(":", 1)[1] for c in chunks] == ["0", "800", "1600"]
    assert [len(c.content) for c in chunks] == [1000, 1000, 900]
    assert chunks[0].content[-200:] == chunks[1].content[:200]  # the overlap
    assert chunks[0].content == text[:1000]
    assert chunks[-1].content == text[1600:]  # last window ends exactly at the end


def test_line_numbers_are_one_based_and_inclusive() -> None:
    text = "aaaa\nbbbb\ncccc\ndddd\n"  # 20 chars; lines start at offsets 0, 5, 10, 15
    chunks = chunk_fixed(text, "x.py", "r", size=10, overlap=0)

    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 2), (3, 4)]
    assert chunks[0].content == "aaaa\nbbbb\n"


def test_window_starting_mid_line_reports_that_line() -> None:
    text = "aaaa\nbbbb\ncccc\n"
    chunks = chunk_fixed(text, "x.py", "r", size=8, overlap=2)
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
    assert (chunks[1].start_line, chunks[1].end_line) == (2, 3)  # starts at offset 6, in "bbbb"


def test_short_text_is_a_single_chunk_and_blank_text_is_dropped() -> None:
    (only,) = chunk_fixed("x = 1\n", "x.py", "r")
    assert only.content == "x = 1\n"
    assert chunk_fixed("", "x.py", "r") == []
    assert chunk_fixed("  \n\n   \n", "x.py", "r") == []


def test_category_is_set_from_the_path() -> None:
    assert chunk_fixed("x = 1", "tests/test_a.py", "r")[0].category == "test"
    assert chunk_fixed("x = 1", "app/a.py", "r")[0].category == "source"


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, 11), (10, -1)])
def test_invalid_parameters(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_fixed("abc", "x.py", "r", size=size, overlap=overlap)
