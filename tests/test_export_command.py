# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import sys
from fractions import Fraction

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.editing_engine import FFmpegRenderer, merge_contiguous_segments  # noqa: E402
from export.export_settings import ExportSettings  # noqa: E402


def _command(segments, script_path=None, include_audio=True):
    return FFmpegRenderer._build_configured_command(
        ffmpeg="ffmpeg",
        source_path="in.mp4",
        output_path="out.mp4",
        segments=segments,
        settings=ExportSettings(),
        source_width=1920,
        source_height=1080,
        include_audio=include_audio,
        output_width=None,
        output_height=None,
        encoder="libx264",
        source_fps=Fraction(30, 1),
        filter_script_path=script_path,
    )


def test_many_segments_do_not_inflate_command_line(tmp_path, monkeypatch):
    monkeypatch.setattr(
        FFmpegRenderer, "_filter_script_option", staticmethod(lambda ffmpeg: "-filter_complex_script")
    )
    segments = [(i * 2.0, i * 2.0 + 1.5) for i in range(600)]
    script = tmp_path / "graph.txt"
    command = _command(segments, str(script))
    assert len(" ".join(command)) < 3000
    assert "-filter_complex_script" in command
    assert command[command.index("-filter_complex_script") + 1] == str(script)
    graph = script.read_text(encoding="utf-8")
    assert "concat=n=600:v=1:a=0[vout]" in graph
    assert "concat=n=600:v=0:a=1[aout]" in graph


def test_inline_filter_still_available_without_script_path():
    command = _command([(0.0, 1.0), (2.0, 3.0)])
    assert "-filter_complex" in command


def test_audio_segments_have_short_fades():
    command = _command([(1.0, 3.0)])
    graph = command[command.index("-filter_complex") + 1]
    assert "afade=t=in:st=0:d=0.0100" in graph
    assert "afade=t=out:st=1.990000:d=0.0100" in graph


def test_fade_is_never_longer_than_a_quarter_of_the_clip():
    command = _command([(0.0, 0.020)])
    graph = command[command.index("-filter_complex") + 1]
    assert "afade=t=in:st=0:d=0.0050" in graph


def test_no_audio_filters_without_audio():
    command = _command([(0.0, 1.0)], include_audio=False)
    graph = command[command.index("-filter_complex") + 1]
    assert "atrim" not in graph and "afade" not in graph


def test_merge_contiguous_segments():
    merged = merge_contiguous_segments([
        {"start": 0.0, "end": 2.0, "id": "a"},
        {"start": 2.0005, "end": 4.0},
        {"start": 5.0, "end": 6.0},
    ])
    assert merged == [
        {"start": 0.0, "end": 4.0, "id": "a"},
        {"start": 5.0, "end": 6.0},
    ]


def test_commit_output_falls_back_when_hard_links_unsupported(tmp_path, monkeypatch):
    temp = tmp_path / "temp.mp4"
    temp.write_bytes(b"video")
    target = tmp_path / "final.mp4"

    def no_link(*args, **kwargs):
        raise OSError("hard links not supported")

    monkeypatch.setattr(os, "link", no_link)
    FFmpegRenderer._commit_output(str(temp), str(target), overwrite=False)
    assert target.read_bytes() == b"video"
    assert not temp.exists()


def test_commit_output_never_overwrites_existing_file(tmp_path, monkeypatch):
    temp = tmp_path / "temp.mp4"
    temp.write_bytes(b"new")
    target = tmp_path / "final.mp4"
    target.write_bytes(b"old")

    def no_link(*args, **kwargs):
        raise OSError("hard links not supported")

    monkeypatch.setattr(os, "link", no_link)
    with pytest.raises(FileExistsError):
        FFmpegRenderer._commit_output(str(temp), str(target), overwrite=False)
    assert target.read_bytes() == b"old"


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg غير مثبت"
)
def test_render_hundreds_of_segments_end_to_end(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source),
        ],
        check=True,
    )
    segments = [(i * 0.5, i * 0.5 + 0.3) for i in range(55)]
    expected = sum(end - start for start, end in segments)
    output = tmp_path / "out.mp4"
    settings = ExportSettings(encoder_preset="ultrafast", hardware_acceleration="software")
    ok, message = FFmpegRenderer.render_segments(
        str(source), segments, str(output), settings=settings
    )
    assert ok, message
    assert output.is_file()
    assert not list(tmp_path.glob(".*.filter.txt"))
    probe = FFmpegRenderer.probe_media(str(output))
    assert abs(float(probe["format"]["duration"]) - expected) < 0.75
