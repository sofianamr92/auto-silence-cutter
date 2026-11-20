# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMenu, QToolButton
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QFont, QAction

class PlayerWidget(QWidget):
    step_forward = Signal()
    step_backward = Signal()
    workspace_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        
        self.video_widget = QVideoWidget(self)
        self.player.setVideoOutput(self.video_widget)

        self._build_ui()
        self._fps_num = 30
        self._fps_den = 1
        self._variable_frame_rate = False
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.video_widget.setStyleSheet("background-color: #060709; border-radius: 6px;")
        layout.addWidget(self.video_widget, stretch=1)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(10, 4, 10, 6)

        self.lbl_timecode = QLabel("00:00:00:00 / 00:00:00:00")
        self.lbl_timecode.setFont(QFont("JetBrains Mono", 9))
        self.lbl_timecode.setStyleSheet("color: #60a5fa; background: #161a24; padding: 4px 8px; border-radius: 4px;")
        toolbar.addWidget(self.lbl_timecode)

        toolbar.addStretch()

        self.btn_prev_frame = QPushButton("◀")
        self.btn_prev_frame.setFixedSize(32, 30)
        self.btn_prev_frame.clicked.connect(self.step_backward.emit)
        toolbar.addWidget(self.btn_prev_frame)

        self.btn_play = QPushButton("تشغيل")
        self.btn_play.setFixedSize(64, 30)
        self.btn_play.setStyleSheet("background-color: #2563eb; color: #fff; font-weight: bold; border-radius: 4px;")
        self.btn_play.clicked.connect(self.toggle_play)
        toolbar.addWidget(self.btn_play)

        self.btn_next_frame = QPushButton("▶")
        self.btn_next_frame.setFixedSize(32, 30)
        self.btn_next_frame.clicked.connect(self.step_forward.emit)
        toolbar.addWidget(self.btn_next_frame)

        toolbar.addStretch()

        # زر مساحة العمل الدائم في شريط المشغل
        self.btn_workspace = QToolButton(self)
        self.btn_workspace.setText("مساحة العمل ▾")
        self.btn_workspace.setStyleSheet("background: #1e2433; color: #f1f5f9; padding: 4px 10px; border-radius: 4px; font-weight: 600;")
        self.btn_workspace.setPopupMode(QToolButton.InstantPopup)

        ws_menu = QMenu(self.btn_workspace)
        layouts = [
            ("Standard Horizontal (افتراضي)", "Standard Horizontal"),
            ("Vertical Workspace (رأسي)", "Vertical Workspace"),
            ("Timeline Focus (تركيز التايم لاين)", "Timeline Focus"),
            ("Preview Focus (تركيز المعاينة)", "Preview Focus"),
            ("Compact Workspace (مضغوط)", "Compact Workspace"),
            ("Custom Workspace (مخصص)", "Custom Workspace")
        ]
        for label, code in layouts:
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, c=code: self.workspace_changed.emit(c))
            ws_menu.addAction(action)

        self.btn_workspace.setMenu(ws_menu)
        toolbar.addWidget(self.btn_workspace)

        layout.addLayout(toolbar)

    def load_media(self, filepath: str):
        self.player.setSource(QUrl.fromLocalFile(filepath))
        self.lbl_timecode.setText("00:00:00:00 / 00:00:00:00")
        self.btn_play.setText("تشغيل")

    def set_timebase(self, fps_num: int, fps_den: int = 1, frame_index_mapper=None):
        if fps_num <= 0 or fps_den <= 0:
            raise ValueError("Frame rate must be a positive rational value.")
        self._fps_num = fps_num
        self._fps_den = fps_den
        self._variable_frame_rate = bool(
            frame_index_mapper is not None
            and frame_index_mapper.is_variable_frame_rate
        )
        self._on_position_changed(self.player.position())

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("تشغيل")
        else:
            self.player.play()
            self.btn_play.setText("إيقاف")

    def _on_position_changed(self, position_ms: int):
        duration_ms = self.player.duration()
        self.lbl_timecode.setText(
            f"{self._format_timecode(position_ms)} / {self._format_timecode(duration_ms)}"
        )

    def _on_playback_state_changed(self, _state):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.btn_play.setText("إيقاف")
        else:
            self.btn_play.setText("تشغيل")

    def _format_timecode(self, milliseconds: int) -> str:
        milliseconds = max(0, int(milliseconds))
        total_seconds = milliseconds // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if self._variable_frame_rate:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds % 1000:03d}"
        frame_rate = max(1, int(round(self._fps_num / self._fps_den)))
        frame_number = (
            milliseconds * self._fps_num + 500 * self._fps_den
        ) // (1000 * self._fps_den)
        frames_per_hour = frame_rate * 3600
        hours, frame_remainder = divmod(frame_number, frames_per_hour)
        minutes, frame_remainder = divmod(frame_remainder, frame_rate * 60)
        seconds, frames = divmod(frame_remainder, frame_rate)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
