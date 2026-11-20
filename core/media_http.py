import mimetypes
import os


_VIDEO_CONTENT_TYPES = {
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
}


def parse_byte_range(range_header, size):
    if not range_header:
        return None

    unit, separator, value = range_header.partition("=")
    if not separator or unit.strip().lower() != "bytes" or "," in value:
        raise ValueError("Invalid byte range.")

    first, separator, last = value.strip().partition("-")
    if not separator or (not first and not last):
        raise ValueError("Invalid byte range.")
    if any(not part.isascii() or not part.isdigit() for part in (first, last) if part):
        raise ValueError("Invalid byte range.")
    if size <= 0:
        raise ValueError("Byte ranges are unavailable for an empty file.")

    if not first:
        suffix_length = int(last)
        if suffix_length <= 0:
            raise ValueError("Invalid byte range.")
        start = max(size - suffix_length, 0)
        end = size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1

    if start < 0 or start >= size or end < start:
        raise ValueError("Invalid byte range.")
    return start, end


def video_content_type(file_path):
    extension = os.path.splitext(file_path)[1].lower()
    return _VIDEO_CONTENT_TYPES.get(extension) or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
