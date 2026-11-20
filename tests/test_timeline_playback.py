from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer

from core.editing_engine import Clip, ProjectState
from timeline.playback_controller import TimelinePlaybackController


class FakePlayer(QObject):
    positionChanged = Signal(int)
    playbackStateChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self._position = 0
        self._state = QMediaPlayer.StoppedState
        self.stopped = False

    def position(self):
        return self._position

    def setPosition(self, position):
        self._position = position
        self.positionChanged.emit(position)

    def playbackState(self):
        return self._state

    def stop(self):
        self.stopped = True
        self._state = QMediaPlayer.StoppedState


def edited_document():
    document = ProjectState()
    document.clips = [
        Clip(source_path="source.mp4", source_in=1, source_out=3),
        Clip(source_path="source.mp4", source_in=5, source_out=8),
    ]
    document.recalculate_timeline_positions()
    return document


def test_timeline_seek_maps_to_source_and_player_position_maps_back():
    document = edited_document()
    player = FakePlayer()
    controller = TimelinePlaybackController(document, player)

    assert controller.seek_to_timeline_us(2_500_000)
    assert player.position() == 5_500
    player.positionChanged.emit(6_000)
    assert document.current_playhead_us == 3_000_000


def test_playback_skips_source_gaps_removed_from_the_timeline():
    document = edited_document()
    player = FakePlayer()
    player._state = QMediaPlayer.PlayingState
    controller = TimelinePlaybackController(document, player)

    player.positionChanged.emit(4_000)

    assert player.position() == 5_000
    assert document.current_playhead_us == 2_000_000


def test_playback_stops_at_end_of_kept_source_ranges():
    document = edited_document()
    player = FakePlayer()
    player._state = QMediaPlayer.PlayingState
    controller = TimelinePlaybackController(document, player)

    player.positionChanged.emit(8_000)

    assert player.stopped
    assert document.current_playhead_us == document.total_edited_duration_us
