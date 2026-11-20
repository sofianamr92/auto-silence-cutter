# -*- coding: utf-8 -*-
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer

from core.editing_engine import ProjectState


class TimelinePlaybackController(QObject):
    playhead_changed = Signal(int)

    def __init__(self, document: ProjectState, player, parent=None):
        super().__init__(parent)
        self.document = document
        self.player = player
        self.frame_index_mapper = None
        self._position_set_by_controller = False
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def set_frame_index_mapper(self, mapper):
        self.frame_index_mapper = mapper

    def seek_to_timeline_us(self, timeline_time_us: int):
        timeline_time_us = min(
            max(0, timeline_time_us),
            self.document.total_edited_duration_us,
        )
        source_time_us = self.document.timeline_to_source_us(timeline_time_us)
        if source_time_us is None:
            return False
        if self.frame_index_mapper is not None:
            source_time_us = self.frame_index_mapper.nearest_frame_time_us(source_time_us)
            mapped_timeline_us = self.document.source_to_timeline_us(source_time_us)
            if mapped_timeline_us is not None:
                timeline_time_us = mapped_timeline_us
        self.document.current_playhead_us = timeline_time_us
        self._set_source_position(source_time_us)
        self.playhead_changed.emit(timeline_time_us)
        return True

    def step_frame(self, direction: int):
        if direction not in (-1, 1):
            raise ValueError("Frame-step direction must be -1 or 1.")
        source_time_us = self.document.timeline_to_source_us(
            self.document.current_playhead_us
        )
        if source_time_us is None:
            source_time_us = max(0, int(self.player.position()) * 1000)
        mapper = self.frame_index_mapper
        if mapper is not None and mapper.frame_count:
            target_source_us = (
                mapper.next_frame_time_us(source_time_us)
                if direction > 0
                else mapper.previous_frame_time_us(source_time_us)
            )
        else:
            info = self.document.media_info
            frame_us = max(1, 1_000_000 * info.fps_den // max(1, info.fps_num))
            target_source_us = max(0, source_time_us + direction * frame_us)

        timeline_time_us = self.document.source_to_timeline_us(target_source_us)
        if timeline_time_us is None:
            timeline_time_us = self._next_kept_timeline_position(
                target_source_us,
                direction,
            )
        if timeline_time_us is None:
            return False
        return self.seek_to_timeline_us(timeline_time_us)

    def _on_player_position_changed(self, position_ms: int):
        if self._position_set_by_controller:
            return
        source_time_us = max(0, int(position_ms) * 1000)
        timeline_time_us = self.document.source_to_timeline_us(source_time_us)
        if timeline_time_us is None:
            if self._is_playing():
                next_clip = next(
                    (
                        clip for clip in self.document.clips
                        if not clip.is_cut and clip.source_in_us >= source_time_us
                    ),
                    None,
                )
                if next_clip is None:
                    self.player.stop()
                    self.document.current_playhead_us = self.document.total_edited_duration_us
                    self.playhead_changed.emit(self.document.current_playhead_us)
                    return
                timeline_time_us = next_clip.timeline_start_us
                self._set_source_position(next_clip.source_in_us)
            else:
                timeline_time_us = self.document.nearest_timeline_position_for_source_us(source_time_us)
        self.document.current_playhead_us = min(
            timeline_time_us,
            self.document.total_edited_duration_us,
        )
        self.playhead_changed.emit(self.document.current_playhead_us)

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self._on_player_position_changed(self.player.position())

    def _next_kept_timeline_position(self, source_time_us: int, direction: int):
        clips = [clip for clip in self.document.clips if not clip.is_cut]
        if direction > 0:
            following = next(
                (clip for clip in clips if clip.source_in_us > source_time_us),
                None,
            )
            return following.timeline_start_us if following else None
        preceding = next(
            (
                clip for clip in reversed(clips)
                if clip.source_out_us <= source_time_us
            ),
            None,
        )
        return preceding.timeline_start_us + preceding.duration_us if preceding else None

    def _set_source_position(self, source_time_us: int):
        self._position_set_by_controller = True
        try:
            self.player.setPosition(max(0, int(source_time_us // 1000)))
        finally:
            self._position_set_by_controller = False

    def _is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState
