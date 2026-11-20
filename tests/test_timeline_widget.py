import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.editing_engine import Clip, MediaInfo, ProjectState
from timeline.timeline_widget import TimelineClipItem, TimelineWidget


_APP = None


def _application():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_clicking_clip_selects_it_and_seeks_in_timeline_time():
    _application()
    document = ProjectState()
    document.media_info = MediaInfo(filepath="source.mp4", duration=3.0)
    document.clips = [
        Clip(source_path="source.mp4", source_in=1.0, source_out=3.0),
    ]
    document.recalculate_timeline_positions()
    widget = TimelineWidget(document)
    widget.resize(900, 240)
    widget.show()
    _application().processEvents()

    click_position = widget.view.mapFromScene(48, 50)
    QTest.mouseClick(widget.view.viewport(), Qt.LeftButton, pos=click_position)
    _application().processEvents()

    assert document.selected_clip_ids == [document.clips[0].id]
    assert document.current_playhead_us == 600_000
    video_item = next(
        item
        for item in widget.view.timeline_scene.items()
        if isinstance(item, TimelineClipItem) and item.lane == "video"
    )
    assert video_item.left_handle.isVisible()
    assert video_item.right_handle.isVisible()
    widget.close()
