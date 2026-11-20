# -*- coding: utf-8 -*-
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QBoxLayout,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.editing_engine import ProjectState, seconds_to_us
from editing.commands import TimelineSnappingEngine


RULER_HEIGHT = 26
VIDEO_Y = 30
VIDEO_HEIGHT = 66
AUDIO_Y = 102
AUDIO_HEIGHT = 52
MARKER_Y = 160
MARKER_HEIGHT = 24
SCENE_HEIGHT = MARKER_Y + MARKER_HEIGHT + 8
BASE_PIXELS_PER_SECOND = 80.0
TRACK_HEADER_WIDTH = 92


class TimelineCoordinateMapper:
    def __init__(self, pixels_per_second: float = BASE_PIXELS_PER_SECOND):
        self.pixels_per_second = max(0.01, float(pixels_per_second))

    def time_us_to_x(self, time_us: int) -> float:
        return time_us * self.pixels_per_second / 1_000_000

    def x_to_time_us(self, x: float) -> int:
        return max(0, int(round(x * 1_000_000 / self.pixels_per_second)))


def _format_ruler_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    total_seconds, milliseconds = divmod(total_ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


class TimelineScene(QGraphicsScene):
    def __init__(self, state: ProjectState):
        super().__init__()
        self.state = state
        self.pixels_per_second = BASE_PIXELS_PER_SECOND

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor("#0b0e14"))
        ruler = QRectF(rect.left(), 0, rect.width(), RULER_HEIGHT)
        painter.fillRect(ruler, QColor("#151a25"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QPen(QColor("#5c6678"), 1))

        minimum_step = 52 / max(self.pixels_per_second, 0.01)
        step = 1.0
        while step < minimum_step:
            step *= 2 if step in (1, 5, 10, 50) else 2.5
        first_tick = max(0, int(rect.left() / self.pixels_per_second / step))
        tick = first_tick * step
        while tick * self.pixels_per_second <= rect.right():
            x = tick * self.pixels_per_second
            painter.drawLine(QPointF(x, RULER_HEIGHT - 7), QPointF(x, RULER_HEIGHT))
            painter.drawText(QPointF(x + 3, 12), _format_ruler_time(tick))
            tick += step

        for y, height in (
            (VIDEO_Y, VIDEO_HEIGHT),
            (AUDIO_Y, AUDIO_HEIGHT),
            (MARKER_Y, MARKER_HEIGHT),
        ):
            painter.fillRect(QRectF(rect.left(), y, rect.width(), height), QColor("#111722"))
            painter.setPen(QPen(QColor("#252d3b"), 1))
            painter.drawLine(QPointF(rect.left(), y + height), QPointF(rect.right(), y + height))


class TimelineClipItem(QGraphicsObject):
    def __init__(
        self,
        clip,
        lane: str,
        width: float,
        waveform_peaks: Optional[np.ndarray],
        media_duration_us: int,
        thumbnail: Optional[QImage],
        selected: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.clip = clip
        self.lane = lane
        self.width = max(2.0, width)
        self.waveform_peaks = waveform_peaks
        self.media_duration_us = max(1, media_duration_us)
        self.thumbnail = thumbnail
        self.selected = selected
        self.setZValue(2)
        self.left_handle = TimelineTrimHandle("start", self)
        self.right_handle = TimelineTrimHandle("end", self)
        self.set_selected(selected)

    def boundingRect(self) -> QRectF:
        height = VIDEO_HEIGHT if self.lane == "video" else AUDIO_HEIGHT
        return QRectF(0, 0, self.width, height)

    def set_preview_geometry(self, x: float, width: float):
        self.prepareGeometryChange()
        self.width = max(2.0, width)
        self.setX(x)
        self.right_handle.setX(max(0.0, self.width - self.right_handle.width))
        self.update()

    def set_selected(self, selected: bool):
        self.selected = selected
        self.left_handle.setVisible(selected)
        self.right_handle.setVisible(selected)
        self.right_handle.setX(max(0.0, self.width - self.right_handle.width))
        self.update()

    def paint(self, painter: QPainter, _option, _widget=None):
        rect = self.boundingRect()
        radius = 4
        if self.lane == "video":
            painter.setPen(QPen(QColor("#34d399") if self.selected else QColor("#1f8f6c"), 2))
            painter.setBrush(QColor("#18392f"))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)
            if self.thumbnail and not self.thumbnail.isNull() and rect.width() > 18:
                painter.save()
                painter.setClipRect(rect.adjusted(3, 3, -3, -3))
                image_rect = rect.adjusted(3, 3, -3, -3)
                painter.setOpacity(0.82)
                painter.drawImage(image_rect, self.thumbnail)
                painter.restore()
            painter.setPen(QColor("#f1f5f9"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            label = f"{self.clip.source_in:.2f}–{self.clip.source_out:.2f}s"
            painter.drawText(rect.adjusted(6, 4, -5, -4), Qt.AlignLeft | Qt.AlignBottom, label)
        else:
            painter.setPen(QPen(QColor("#3b82f6") if self.selected else QColor("#2563eb"), 1))
            painter.setBrush(QColor("#152942"))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)
            if self.waveform_peaks is not None and len(self.waveform_peaks):
                painter.setPen(QPen(QColor("#60a5fa"), 1))
                center_y = rect.center().y()
                source_duration = max(1, self.clip.source_duration_us)
                for x in range(2, max(2, int(self.width) - 1), 3):
                    fraction = min(1.0, max(0.0, x / self.width))
                    source_time = self.clip.source_in_us + int(source_duration * fraction)
                    peak_index = min(
                        len(self.waveform_peaks) - 1,
                        int(source_time * len(self.waveform_peaks) / self.media_duration_us),
                    )
                    amplitude = float(self.waveform_peaks[peak_index])
                    amplitude = min(1.0, max(0.0, amplitude))
                    half_height = max(1.0, amplitude * rect.height() * 0.42)
                    painter.drawLine(
                        QPointF(x, center_y - half_height),
                        QPointF(x, center_y + half_height),
                    )



class TimelineTrimHandle(QGraphicsObject):
    def __init__(self, edge: str, parent: TimelineClipItem):
        super().__init__(parent)
        self.edge = edge
        self.width = 7.0
        self.height = VIDEO_HEIGHT if parent.lane == "video" else AUDIO_HEIGHT
        self.setZValue(5)
        self.setX(0 if edge == "start" else max(0.0, parent.width - self.width))
        self.setAcceptedMouseButtons(Qt.LeftButton)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter: QPainter, _option, _widget=None):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e2e8f0"))
        painter.drawRoundedRect(self.boundingRect().adjusted(1, 2, -1, -2), 2, 2)


class TimelinePlayheadItem(QGraphicsObject):
    def __init__(self):
        super().__init__()
        self.setZValue(20)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        return QRectF(-6, 0, 12, SCENE_HEIGHT)

    def paint(self, painter: QPainter, _option, _widget=None):
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(QPointF(0, 0), QPointF(0, SCENE_HEIGHT))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#38bdf8"))
        painter.drawPolygon(QPolygonF([
            QPointF(-6, 0),
            QPointF(6, 0),
            QPointF(0, 12),
        ]))


class TimelineView(QGraphicsView):
    time_changed = Signal(float)
    clip_move_requested = Signal(str, int)
    trim_requested = Signal(str, str, int)
    clip_selected = Signal(str)
    delete_requested = Signal(str)
    thumbnail_requested = Signal(str, str, int)

    def __init__(self, state: ProjectState, parent=None):
        self.timeline_scene = TimelineScene(state)
        super().__init__(self.timeline_scene, parent)
        self.state = state
        self.coordinate_mapper = TimelineCoordinateMapper()
        self.waveform_peaks: Optional[np.ndarray] = None
        self.thumbnails = {}
        self._thumbnail_requested = set()
        self._duration_us = state.total_edited_duration_us or state.media_info.duration_us
        self._interaction = None
        self._drag_clip_id = None
        self._drag_origin_x = 0.0
        self._drag_item = None
        self._drag_original_x = 0.0
        self._drag_original_width = 0.0
        self._drag_lane = "video"
        self._trim_edge = None
        self._pending_time_us = 0
        self._playhead_item = TimelinePlayheadItem()
        self.timeline_scene.addItem(self._playhead_item)
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setBackgroundBrush(QColor("#0b0e14"))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(190)
        self._snapping = TimelineSnappingEngine()
        self.horizontalScrollBar().valueChanged.connect(self._on_horizontal_scroll)
        self.rebuild()

    @property
    def pixels_per_second(self) -> float:
        return self.timeline_scene.pixels_per_second

    def set_zoom(self, zoom: float, anchor_x: Optional[float] = None):
        zoom = max(0.2, min(32.0, float(zoom)))
        anchor_x = self.viewport().width() / 2 if anchor_x is None else anchor_x
        anchor_time_us = self.coordinate_mapper.x_to_time_us(
            self.mapToScene(int(anchor_x), 0).x()
        )
        self.state.zoom_level = zoom
        self.timeline_scene.pixels_per_second = BASE_PIXELS_PER_SECOND * zoom
        self.coordinate_mapper.pixels_per_second = self.pixels_per_second
        self.rebuild()
        target_x = self.coordinate_mapper.time_us_to_x(anchor_time_us)
        self.horizontalScrollBar().setValue(max(0, int(target_x - anchor_x)))

    def fit_to_view(self):
        duration = max(self._duration_us / 1_000_000, 1.0)
        usable_width = max(1, self.viewport().width() - 24)
        self.set_zoom(usable_width / (duration * BASE_PIXELS_PER_SECOND), 0)

    def set_waveform_data(self, peaks: Optional[np.ndarray]):
        self.waveform_peaks = peaks
        self.rebuild()

    def set_playhead_us(self, time_us: int):
        self.state.current_playhead_us = min(
            max(0, time_us),
            self.state.total_edited_duration_us,
        )
        self._playhead_item.setX(
            self.state.current_playhead_us * self.pixels_per_second / 1_000_000
        )
        self._playhead_item.update()
        playhead_x = self._playhead_item.x()
        view_left = self.horizontalScrollBar().value()
        view_right = view_left + self.viewport().width()
        if playhead_x < view_left:
            self.horizontalScrollBar().setValue(max(0, int(playhead_x - 24)))
        elif playhead_x > view_right - 24:
            self.horizontalScrollBar().setValue(
                max(0, int(playhead_x - self.viewport().width() + 24))
            )

    def set_thumbnail(self, clip_id: str, image_data: bytes):
        image = QImage.fromData(image_data)
        if image.isNull():
            raise ValueError(f"Could not decode thumbnail for clip {clip_id}.")
        self.thumbnails[clip_id] = image
        self.rebuild()

    def clear_media_assets(self):
        self.waveform_peaks = None
        self.thumbnails.clear()
        self._thumbnail_requested.clear()
        self.rebuild()

    def time_to_x(self, seconds: float) -> float:
        return self.coordinate_mapper.time_us_to_x(seconds_to_us(seconds))

    def x_to_time(self, x: float) -> float:
        return self.coordinate_mapper.x_to_time_us(x) / 1_000_000

    def rebuild(self):
        scroll_time_us = self.state.horizontal_scroll_us
        self._duration_us = self.state.total_edited_duration_us or self.state.media_info.duration_us
        self.timeline_scene.pixels_per_second = BASE_PIXELS_PER_SECOND * self.state.zoom_level
        self.coordinate_mapper.pixels_per_second = self.pixels_per_second
        duration_seconds = max(self._duration_us / 1_000_000, 1.0)
        scene_width = max(
            self.viewport().width(),
            int(duration_seconds * self.pixels_per_second) + 32,
        )
        self.timeline_scene.clear()
        self._playhead_item = TimelinePlayheadItem()
        self.timeline_scene.addItem(self._playhead_item)
        self.timeline_scene.setSceneRect(0, 0, scene_width, SCENE_HEIGHT)

        for clip in self.state.clips:
            if clip.is_cut:
                continue
            x = clip.timeline_start_us * self.pixels_per_second / 1_000_000
            width = clip.duration_us * self.pixels_per_second / 1_000_000
            selected = clip.id in self.state.selected_clip_ids
            video_item = TimelineClipItem(
                clip,
                "video",
                width,
                None,
                self.state.media_info.duration_us,
                self.thumbnails.get(clip.id),
                selected,
            )
            video_item.setPos(x, VIDEO_Y)
            video_item.setData(0, clip.id)
            self.timeline_scene.addItem(video_item)
            if clip.audio_enabled:
                audio_item = TimelineClipItem(
                    clip,
                    "audio",
                    width,
                    self.waveform_peaks,
                    self.state.media_info.duration_us,
                    None,
                    selected,
                )
                audio_item.setPos(x, AUDIO_Y)
                audio_item.setData(0, clip.id)
                self.timeline_scene.addItem(audio_item)

            if clip.id not in self.thumbnails and clip.id not in self._thumbnail_requested:
                self._thumbnail_requested.add(clip.id)
                self._request_thumbnail(clip)

        for region in self.state.detected_regions:
            start_us = self.state.nearest_timeline_position_for_source_us(region.start_time_us)
            end_us = self.state.nearest_timeline_position_for_source_us(region.end_time_us)
            x1 = start_us * self.pixels_per_second / 1_000_000
            x2 = end_us * self.pixels_per_second / 1_000_000
            marker = self.timeline_scene.addRect(
                QRectF(x1, MARKER_Y, max(3.0, x2 - x1), MARKER_HEIGHT),
                QPen(QColor("#a78bfa"), 1),
                QColor(139, 92, 246, 85),
            )
            marker.setToolTip(
                f"{region.reason}\n"
                f"{'محذوف' if region.approved_for_cut else 'مُستعاد'}"
            )
            marker.setZValue(3)

        playhead_x = self.state.current_playhead_us * self.pixels_per_second / 1_000_000
        self._playhead_item.setPos(playhead_x, 0)
        self.horizontalScrollBar().setValue(
            max(0, int(scroll_time_us * self.pixels_per_second / 1_000_000))
        )
        self.timeline_scene.update()

    def _on_horizontal_scroll(self, value: int):
        if self.pixels_per_second > 0:
            self.state.horizontal_scroll_us = int(value * 1_000_000 / self.pixels_per_second)

    def _request_thumbnail(self, clip):
        self.thumbnail_requested.emit(
            clip.id,
            clip.source_path,
            clip.source_in_us + clip.source_duration_us // 2,
        )

    def _clip_item_at(self, point):
        item = self.itemAt(point)
        while item is not None and not isinstance(item, TimelineClipItem):
            item = item.parentItem()
        return item

    def _snap_time_us(self, value: int, clip_id: Optional[str] = None) -> int:
        candidates = [0, self.state.current_playhead_us]
        for clip in self.state.clips:
            if clip.is_cut or clip.id == clip_id:
                continue
            candidates.extend((clip.timeline_start_us, clip.timeline_start_us + clip.duration_us))
        for region in self.state.detected_regions:
            candidates.extend((
                self.state.nearest_timeline_position_for_source_us(region.start_time_us),
                self.state.nearest_timeline_position_for_source_us(region.end_time_us),
            ))
        tolerance = int(8 * 1_000_000 / max(self.pixels_per_second, 0.01))
        self._snapping.tolerance_us = tolerance
        return self._snapping.snap(value, candidates)

    def _set_playhead_at_scene_x(self, scene_x: float):
        time_us = min(
            self.coordinate_mapper.x_to_time_us(scene_x),
            self.state.total_edited_duration_us,
        )
        self.state.current_playhead_us = time_us
        self._playhead_item.setX(time_us * self.pixels_per_second / 1_000_000)
        self.time_changed.emit(self.state.current_playhead)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        scene_point = self.mapToScene(event.position().toPoint())
        clip_item = self._clip_item_at(event.position().toPoint())
        if clip_item is None:
            self._interaction = "playhead"
            self._set_playhead_at_scene_x(scene_point.x())
            event.accept()
            return

        clip_id = clip_item.clip.id
        self.state.selected_clip_ids = [clip_id]
        for item in self.timeline_scene.items():
            if isinstance(item, TimelineClipItem):
                item.set_selected(item.clip.id == clip_id)
        self.clip_selected.emit(clip_id)
        local_x = scene_point.x() - clip_item.x()
        self._drag_clip_id = clip_id
        self._drag_item = clip_item
        self._drag_origin_x = scene_point.x()
        self._drag_original_x = clip_item.x()
        self._drag_original_width = clip_item.width
        self._drag_lane = clip_item.lane
        handle_width = min(8.0, max(3.0, clip_item.width / 4))
        if local_x <= handle_width:
            self._interaction = "pending_trim"
            self._trim_edge = "start"
        elif local_x >= clip_item.width - handle_width:
            self._interaction = "pending_trim"
            self._trim_edge = "end"
        else:
            self._interaction = "pending_move"
        event.accept()

    def mouseMoveEvent(self, event):
        scene_x = self.mapToScene(event.position().toPoint()).x()
        if self._interaction == "playhead":
            self._set_playhead_at_scene_x(scene_x)
            event.accept()
            return

        if self._interaction == "pending_move":
            if abs(scene_x - self._drag_origin_x) >= 4:
                self._interaction = "move"
        if self._interaction == "pending_trim":
            if abs(scene_x - self._drag_origin_x) >= 4:
                self._interaction = "trim"
            else:
                super().mouseMoveEvent(event)
                return
        if self._interaction == "move" and self._drag_item is not None:
            delta = scene_x - self._drag_origin_x
            target_x = max(0.0, self._drag_original_x + delta)
            target_time = self.coordinate_mapper.x_to_time_us(target_x)
            target_time = self._snap_time_us(target_time, self._drag_clip_id)
            self._drag_item.setX(target_time * self.pixels_per_second / 1_000_000)
            self._drag_item.update()
            event.accept()
            return
        if self._interaction == "trim" and self._drag_item is not None:
            clip = self._drag_item.clip
            self._pending_time_us = self._snap_time_us(
                self.coordinate_mapper.x_to_time_us(scene_x),
                self._drag_clip_id,
            )
            preview_x = self._pending_time_us * self.pixels_per_second / 1_000_000
            clip_x = self._drag_original_x
            clip_right = clip_x + self._drag_original_width
            if self._trim_edge == "start":
                preview_x = min(max(clip_x, preview_x), clip_right - 2)
                self._drag_item.set_preview_geometry(preview_x, clip_right - preview_x)
            else:
                preview_x = min(max(clip_x + 2, preview_x), clip_right + self._drag_original_width)
                self._drag_item.set_preview_geometry(clip_x, preview_x - clip_x)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        interaction = self._interaction
        if interaction in ("pending_move", "pending_trim") and self._drag_item is not None:
            scene_x = self.mapToScene(event.position().toPoint()).x()
            self._set_playhead_at_scene_x(scene_x)
        elif interaction == "move" and self._drag_item is not None:
            target_time_us = max(
                0,
                self.coordinate_mapper.x_to_time_us(self._drag_item.x()),
            )
            self.clip_move_requested.emit(self._drag_clip_id, target_time_us)
        elif interaction == "trim" and self._drag_item is not None:
            self.trim_requested.emit(
                self._drag_clip_id,
                self._trim_edge,
                self._pending_time_us,
            )
        self._interaction = None
        self._drag_item = None
        self._drag_clip_id = None
        self._trim_edge = None
        self.rebuild()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        scene_point = self.mapToScene(event.position().toPoint())
        self._set_playhead_at_scene_x(scene_point.x())
        event.accept()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.set_zoom(self.state.zoom_level * factor, event.position().x())
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state.selected_clip_ids:
            self.delete_requested.emit(self.state.selected_clip_ids[0])
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.rebuild()


class TimelineTrackHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addSpacing(RULER_HEIGHT + 4)
        for text, height in (
            ("Video", VIDEO_HEIGHT),
            ("Audio", AUDIO_HEIGHT),
            ("Detection", MARKER_HEIGHT),
        ):
            label = QLabel(text)
            label.setFixedSize(TRACK_HEADER_WIDTH, height)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                "background:#141a25;color:#cbd5e1;border:1px solid #252d3b;"
            )
            layout.addWidget(label)


class TimelineWidget(QWidget):
    time_changed = Signal(float)
    clip_move_requested = Signal(str, int)
    trim_requested = Signal(str, str, int)
    clip_selected = Signal(str)
    delete_requested = Signal(str)
    thumbnail_requested = Signal(str, str, int)

    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.view = TimelineView(state, self)
        self.view.time_changed.connect(self.time_changed)
        self.view.clip_move_requested.connect(self.clip_move_requested)
        self.view.trim_requested.connect(self.trim_requested)
        self.view.clip_selected.connect(self.clip_selected)
        self.view.delete_requested.connect(self.delete_requested)
        self.view.thumbnail_requested.connect(self.thumbnail_requested)
        self.setMinimumHeight(225)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(QLabel("Timeline"))
        toolbar.addStretch()
        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.setFixedWidth(30)
        self.btn_zoom_out.clicked.connect(lambda: self.set_zoom(self.state.zoom_level / 1.25))
        toolbar.addWidget(self.btn_zoom_out)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(20, 3200)
        self.zoom_slider.setValue(int(self.state.zoom_level * 100))
        self.zoom_slider.setMaximumWidth(180)
        self.zoom_slider.valueChanged.connect(lambda value: self.set_zoom(value / 100))
        toolbar.addWidget(self.zoom_slider)
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFixedWidth(30)
        self.btn_zoom_in.clicked.connect(lambda: self.set_zoom(self.state.zoom_level * 1.25))
        toolbar.addWidget(self.btn_zoom_in)
        self.btn_fit = QPushButton("ملاءمة")
        self.btn_fit.clicked.connect(self.view.fit_to_view)
        toolbar.addWidget(self.btn_fit)
        outer.addLayout(toolbar)

        body = QHBoxLayout()
        body.setDirection(QBoxLayout.LeftToRight)
        body.setSpacing(0)
        body.addWidget(TimelineTrackHeader(self))
        body.addWidget(self.view, stretch=1)
        outer.addLayout(body, stretch=1)

    def set_waveform_data(self, peaks: Optional[np.ndarray]):
        self.view.set_waveform_data(peaks)

    def set_playhead_us(self, time_us: int):
        self.view.set_playhead_us(time_us)

    def set_thumbnail(self, clip_id: str, image_data: bytes):
        self.view.set_thumbnail(clip_id, image_data)

    def clear_media_assets(self):
        self.view.clear_media_assets()

    def set_zoom(self, zoom: float):
        self.view.set_zoom(zoom)
        slider_value = int(self.state.zoom_level * 100)
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(slider_value)
        self.zoom_slider.blockSignals(False)

    def refresh(self):
        self.view.rebuild()

    def time_to_x(self, seconds: float) -> float:
        return self.view.time_to_x(seconds)

    def x_to_time(self, x: float) -> float:
        return self.view.x_to_time(x)
