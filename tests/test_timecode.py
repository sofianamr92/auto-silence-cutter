from core.timecode import format_timecode


def test_format_timecode_uses_nominal_24_fps():
    assert format_timecode(1.5, 23.976) == "00:00:01:12"


def test_format_timecode_uses_nominal_30_fps_for_ntsc_video():
    assert format_timecode(1.5, 29.97) == "00:00:01:15"


def test_format_timecode_falls_back_to_30_fps_when_rate_is_invalid():
    assert format_timecode(1.5, 0) == "00:00:01:15"
