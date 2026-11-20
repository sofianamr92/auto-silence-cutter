import pytest

from core.media_http import parse_byte_range, video_content_type


def test_parse_byte_range_with_start_and_end():
    assert parse_byte_range("bytes=2-5", 10) == (2, 5)


def test_parse_byte_range_with_open_end_and_suffix():
    assert parse_byte_range("bytes=7-", 10) == (7, 9)
    assert parse_byte_range("bytes=-3", 10) == (7, 9)


def test_parse_byte_range_without_header():
    assert parse_byte_range(None, 10) is None


@pytest.mark.parametrize(
    "header",
    ["bytes=-0", "bytes=10-", "bytes=5-3", "bytes=0-1,4-5", "bytes=abc-def", "items=0-1"],
)
def test_parse_byte_range_rejects_invalid_or_unsupported_ranges(header):
    with pytest.raises(ValueError):
        parse_byte_range(header, 10)


def test_video_content_type_matches_video_extension():
    assert video_content_type("clip.mkv") == "video/x-matroska"
    assert video_content_type("clip.mov") == "video/quicktime"
