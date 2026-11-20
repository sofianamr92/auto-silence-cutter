# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QMouseEvent, QWheelEvent
from PySide6.QtCore import Qt, Signal, QRectF, QPoint
from core.editing_engine import ProjectState
import numpy as np
from typing import Optional

class TimelineWidget(QWidget):
    time_changed = Signal(float)

    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.zoom_factor = 1.0
        self.pixels_per_second = 80.0
        self.waveform_peaks: Optional[np.ndarray] = None
        self.setMinimumHeight(160)
        self.setMouseTracking(True)
        self.is_dragging_playhead = False

    def set_waveform_data(self, peaks: np.ndarray):
        self.waveform_peaks = peaks
        self.update()

    def set_zoom(self, zoom: float):
        self.zoom_factor = max(0.2, min(8.0, zoom))
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            t = self.x_to_time(event.position().x())
            self.state.current_playhead = max(0.0, min(self.state.media_info.duration, t))
            self.is_dragging_playhead = True
            self.time_changed.emit(self.state.current_playhead)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_dragging_playhead:
            t = self.x_to_time(event.position().x())
            self.state.current_playhead = max(0.0, min(self.state.media_info.duration, t))
            self.time_changed.emit(self.state.current_playhead)
            self.update()
        else:
            t = self.x_to_time(event.position().x())
            for reg in self.state.detected_regions:
                if reg.start_time <= t <= reg.end_time:
                    QToolTip.showText(event.globalPosition().toPoint(),
                                      f"{reg.reason}\nالحالة: {'محذوف' if reg.approved_for_cut else 'مُستعاد'}", self)
                    return
            QToolTip.hideText()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.is_dragging_playhead = False

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.set_zoom(self.zoom_factor * 1.15)
            else:
                self.set_zoom(self.zoom_factor / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    def time_to_x(self, t: float) -> float:
        return t * self.pixels_per_second * self.zoom_factor

    def x_to_time(self, x: float) -> float:
        pps = self.pixels_per_second * self.zoom_factor
        return x / pps if pps > 0 else 0.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        painter.fillRect(0, 0, width, height, QColor("#0d0e12"))

        # Time Ruler
        ruler_h = 24
        painter.fillRect(0, 0, width, ruler_h, QColor("#141720"))
        painter.setPen(QColor("#475569"))
        painter.setFont(QFont("JetBrains Mono", 8))

        step_sec = 1.0 if self.zoom_factor > 1.5 else (5.0 if self.zoom_factor > 0.5 else 10.0)
        total_dur = self.state.media_info.duration or 60.0

        t = 0.0
        while t <= total_dur:
            x = self.time_to_x(t)
            if 0 <= x <= width:
                painter.drawLine(int(x), ruler_h - 6, int(x), ruler_h)
                m = int(t // 60)
                s = int(t % 60)
                painter.drawText(int(x) + 3, ruler_h - 8, f"{m:02d}:{s:02d}")
            t += step_sec

        track_y = ruler_h + 8
        track_h = height - track_y - 8
        painter.fillRect(0, track_y, int(self.time_to_x(total_dur)), track_h, QColor("#161a24"))

        # رسم المناطق المحذوفة بالأحمر
        for reg in self.state.detected_regions:
            rx = self.time_to_x(reg.start_time)
            rw = self.time_to_x(reg.duration)
            if reg.approved_for_cut:
                color = QColor(239, 68, 68, 80) if reg.region_type == "silence" else QColor(139, 92, 246, 80)
                painter.fillRect(QRectF(rx, track_y, rw, track_h), color)
                painter.setPen(QColor("#ef4444") if reg.region_type == "silence" else QColor("#8b5cf6"))
                painter.drawRect(QRectF(rx, track_y, rw, track_h))

        # رسم الموجة الصوتية
        if self.waveform_peaks is not None and len(self.waveform_peaks) > 0:
            painter.setPen(QPen(QColor("#60a5fa"), 1))
            center_y = track_y + (track_h / 2)
            wave_len = len(self.waveform_peaks)
            for i in range(min(width, int(self.time_to_x(total_dur)))):
                rel_time = self.x_to_time(i)
                idx = int((rel_time / max(1.0, total_dur)) * wave_len)
                if 0 <= idx < wave_len:
                    val = self.waveform_peaks[idx]
                    h = val * (track_h * 0.42)
                    painter.drawLine(i, int(center_y - h), i, int(center_y + h))

        # خطوط قص المقاطع
        for clip in self.state.clips:
            if not clip.is_cut:
                cx = self.time_to_x(clip.timeline_in)
                painter.setPen(QPen(QColor("#10b981"), 2))
                painter.drawLine(int(cx), track_y, int(cx), track_y + track_h)

        # المؤشر (Playhead)
        px = self.time_to_x(self.state.current_playhead)
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(int(px), 0, int(px), height)
        painter.setBrush(QBrush(QColor("#38bdf8")))
        poly = [QPoint(int(px) - 6, 0), QPoint(int(px) + 6, 0), QPoint(int(px), 12)]
        painter.drawPolygon(poly)
