import pytest

from core.editing_engine import Clip, MediaInfo, ProjectState
from core.media_probe import FrameIndexMapper, MediaProbe
from editing.commands import TimelineController
from editing.undo_redo import UndoRedoStack
from export.export_dialog import ExportWorker, TimelineExportAdapter


def test_clip_stores_integer_microseconds_and_maps_both_time_domains():
    document = ProjectState()
    document.media_info = MediaInfo(filepath="source.mp4", duration=8.0)
    document.clips = [
        Clip(source_path="source.mp4", source_in=1.25, source_out=3.0),
        Clip(source_path="source.mp4", source_in=5.0, source_out=7.0),
    ]
    document.recalculate_timeline_positions()

    assert document.clips[0].source_in_us == 1_250_000
    assert document.clips[1].timeline_start_us == 1_750_000
    assert document.timeline_to_source_us(2_000_000) == 5_250_000
    assert document.source_to_timeline_us(5_500_000) == 2_250_000
    assert document.source_to_timeline_us(4_000_000) is None
    assert document.nearest_timeline_position_for_source_us(4_000_000) == 1_750_000


def test_timeline_document_round_trips_native_project_state():
    document = ProjectState()
    document.media_info = MediaInfo(
        filepath="source.mp4",
        duration=4.0,
        fps=29.97,
    )
    document.clips = [Clip(source_path="source.mp4", source_in=0.125, source_out=3.875)]
    document.detected_regions = []
    document.current_playhead_us = 500_000
    document.zoom_level = 2.5
    document.active_layout = "Timeline Focus"
    document.recalculate_timeline_positions()

    restored = ProjectState.from_dict(document.to_dict())

    assert restored.media_info.fps_num == 2997
    assert restored.media_info.fps_den == 100
    assert restored.clips[0].source_in_us == 125_000
    assert restored.current_playhead_us == 500_000
    assert restored.zoom_level == 2.5
    assert restored.active_layout == "Timeline Focus"
    assert restored.is_modified is False


def test_native_project_rejects_unsupported_schema_and_invalid_clips():
    with pytest.raises(ValueError, match="Unsupported"):
        ProjectState.from_dict({"schema_version": 2})

    document = ProjectState()
    data = document.to_dict()
    data["clips"] = [{
        "source_in_us": 4,
        "source_out_us": 4,
        "source_path": "source.mp4",
    }]
    with pytest.raises(ValueError, match="no source duration"):
        ProjectState.from_dict(data)


def test_native_project_file_round_trips_without_copying_media(tmp_path):
    source_path = tmp_path / "source.mp4"
    project_path = tmp_path / "edit.asctimeline"
    document = ProjectState()
    document.media_info = MediaInfo(filepath=str(source_path), duration=2.0)
    document.clips = [Clip(source_path=str(source_path), source_in=0.25, source_out=1.75)]
    document.waveform_peaks = [0.1, 0.4]
    document.recalculate_timeline_positions()

    document.save_to_file(str(project_path))
    restored = ProjectState.load_from_file(str(project_path))

    assert restored.media_info.filepath == str(source_path)
    assert restored.clips[0].source_in_us == 250_000
    assert restored.waveform_peaks == [0.1, 0.4]
    assert not source_path.exists()


def test_export_adapter_uses_document_order_and_integer_source_ranges():
    document = ProjectState()
    document.media_info = MediaInfo(filepath="source.mp4", duration=5.0)
    document.clips = [
        Clip(source_path="source.mp4", source_in=2, source_out=5),
        Clip(source_path="source.mp4", source_in=0, source_out=2),
    ]
    document.recalculate_timeline_positions()

    snapshot = TimelineExportAdapter.snapshot(document)
    document.clips[0].source_in_us = 3_000_000

    assert snapshot.segments_us == ((2_000_000, 5_000_000), (0, 2_000_000))
    assert snapshot.include_audio


def test_export_worker_uses_a_snapshot_not_live_document_mutations():
    document = ProjectState()
    document.media_info = MediaInfo(filepath="source.mp4", duration=3.0)
    document.clips = [Clip(source_path="source.mp4", source_in=0.25, source_out=2.75)]
    document.recalculate_timeline_positions()

    worker = ExportWorker(document, "output.mp4")
    document.clips[0].source_in_us = 1_000_000

    assert worker.snapshot.segments_us == ((250_000, 2_750_000),)


def test_cfr_and_vfr_frame_mapping():
    cfr = FrameIndexMapper.from_frame_rate(30_000, 1_001, 2_000_000)
    assert cfr.nearest_frame_time_us(40_000) == 33_366
    assert cfr.next_frame_time_us(0) == 33_366
    assert cfr.previous_frame_time_us(33_366) == 0

    vfr = FrameIndexMapper.from_timestamps(
        [0, 40_000, 85_000, 125_000],
        fps_num=25,
        fps_den=1,
    )
    assert vfr.is_variable_frame_rate
    assert vfr.nearest_frame_index(60_000) == 1
    assert vfr.nearest_frame_time_us(70_000) == 85_000
    assert vfr.previous_frame_time_us(85_000) == 40_000
    assert vfr.next_frame_time_us(40_000) == 85_000


def test_media_probe_parses_rational_frame_rate():
    assert MediaProbe._parse_frame_rate("30000/1001") == (30000, 1001)
    assert MediaProbe._parse_frame_rate("0/0") is None


def test_controller_snaps_and_moves_clips_with_undo():
    document = ProjectState()
    document.clips = [
        Clip(source_path="source.mp4", source_in=0, source_out=1),
        Clip(source_path="source.mp4", source_in=1, source_out=2),
    ]
    document.recalculate_timeline_positions()
    first, second = document.clips
    stack = UndoRedoStack()
    controller = TimelineController(document, stack)

    assert controller.snap_time(1_050_000) == 1_000_000
    assert controller.move_clip(first.id, 1_500_000)
    assert [clip.id for clip in document.clips] == [second.id, first.id]
    assert stack.undo()
    assert [clip.id for clip in document.clips] == [first.id, second.id]
