# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QFileDialog, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal
from core.editing_engine import ProjectState, FFmpegRenderer
from core.timecode import format_timecode
from export.capcut_package import CapCutPackageExporter
import os
import json
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class TimelineExportSnapshot:
    source_path: str
    segments_us: Tuple[Tuple[int, int], ...]
    fps: float
    include_audio: bool

    @property
    def segments_seconds(self):
        return tuple(
            (start_us / 1_000_000, end_us / 1_000_000)
            for start_us, end_us in self.segments_us
        )


class TimelineExportAdapter:
    @staticmethod
    def snapshot(document: ProjectState) -> TimelineExportSnapshot:
        document.validate()
        if not document.media_info.filepath:
            raise ValueError("لا يوجد ملف وسائط مرتبط بالمشروع.")
        tracks = {track.track_id: track for track in document.tracks}
        video_track = tracks.get("video")
        audio_track = tracks.get("audio")
        if video_track is None or not video_track.enabled:
            raise ValueError("يجب تفعيل مسار الفيديو للتصدير.")
        clips = sorted(
            (
                clip for clip in document.clips
                if (
                    not clip.is_cut
                    and clip.track_id == video_track.track_id
                    and clip.video_enabled
                    and clip.duration_us > 0
                )
            ),
            key=lambda clip: clip.timeline_start_us,
        )
        if not clips:
            raise ValueError("لا توجد مقاطع متبقية للتصدير بعد حذف الصمت.")
        if any(clip.source_path != document.media_info.filepath for clip in clips):
            raise ValueError("المحرك الحالي يدعم التصدير من مصدر فيديو واحد فقط.")
        audio_states = {clip.audio_enabled for clip in clips}
        if len(audio_states) > 1:
            raise ValueError("تعطيل الصوت لكل مقطع على حدة غير مدعوم في محرك التصدير الحالي.")
        return TimelineExportSnapshot(
            source_path=document.media_info.filepath,
            segments_us=tuple(
                (clip.source_in_us, clip.source_out_us)
                for clip in clips
            ),
            fps=document.media_info.fps,
            include_audio=bool(audio_track and audio_track.enabled and audio_states == {True}),
        )

class ExportWorker(QThread):
    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(
        self,
        state: ProjectState,
        out_path: str,
        resolution: str = "الدقة الأصلية الكاملة",
        export_type: str = "video",
    ):
        super().__init__()
        self.state = ProjectState.from_dict(state.to_dict())
        self.snapshot = TimelineExportAdapter.snapshot(self.state)
        self.out_path = out_path
        self.resolution = resolution
        self.export_type = export_type

    def run(self):
        try:
            if self.export_type == "capcut":
                success, message = CapCutPackageExporter.export_package_with_video(
                    self.state, self.out_path, self.resolution
                )
                self.finished.emit(success, message)
                return

            success, message = FFmpegRenderer.render_segments(
                self.snapshot.source_path,
                self.snapshot.segments_seconds,
                self.out_path,
                resolution=self.resolution,
                fast_copy=(self.resolution == "الدقة الأصلية الكاملة"),
                include_audio=self.snapshot.include_audio,
            )
            self.finished.emit(success, message)
        except Exception as exc:
            self.finished.emit(False, f"تعذر إكمال التصدير: {exc}")

class ExportDialog(QDialog):
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("تصدير الفيديو النهائي")
        self.resize(480, 320)
        self.setLayoutDirection(Qt.RightToLeft)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("نوع التصدير:"))
        self.combo_type = QComboBox()
        self.combo_type.addItems([
            "فيديو ممنتج كامل (MP4)",
            "حزمة CapCut كاملة",
            "بيانات القص EDL / JSON",
        ])
        layout.addWidget(self.combo_type)

        layout.addWidget(QLabel("دقة الفيديو:"))
        self.combo_res = QComboBox()
        self.combo_res.addItems(["الدقة الأصلية الكاملة", "1080p Full HD", "720p HD", "4K Ultra HD"])
        layout.addWidget(self.combo_res)

        layout.addWidget(QLabel("مسار الحفظ واسم الملف:"))
        path_box = QHBoxLayout()
        self.txt_path = QLineEdit()
        default_name = os.path.splitext(self.state.media_info.filepath)[0] + "_Edited.mp4" if self.state.media_info.filepath else "output.mp4"
        self.txt_path.setText(default_name)
        path_box.addWidget(self.txt_path)

        self.btn_browse = QPushButton("تحديد...")
        self.btn_browse.clicked.connect(self.on_browse)
        path_box.addWidget(self.btn_browse)
        layout.addLayout(path_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        btn_box = QHBoxLayout()
        self.btn_capcut = QPushButton("تصدير حزمة CapCut")
        self.btn_capcut.clicked.connect(self.on_export_capcut)
        btn_box.addWidget(self.btn_capcut)

        self.btn_start = QPushButton("بدء التصدير الآن")
        self.btn_start.setStyleSheet("background: #059669; color: #fff; font-weight: bold; padding: 8px;")
        self.btn_start.clicked.connect(self.on_start_export)
        btn_box.addWidget(self.btn_start)
        layout.addLayout(btn_box)

    def on_browse(self):
        file, _ = QFileDialog.getSaveFileName(self, "حفظ الفيديو", self.txt_path.text(), "فيديو MP4 (*.mp4)")
        if file:
            self.txt_path.setText(file)

    def on_export_capcut(self):
        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد الحفظ لحزمة CapCut")
        if folder:
            self.start_export_worker(folder, "capcut")

    def on_start_export(self):
        out_path = self.txt_path.text().strip()
        if not self.state.media_info.filepath:
            QMessageBox.warning(self, "تنبيه", "يرجى استيراد فيديو أولاً.")
            return
        if not out_path:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد مسار الحفظ.")
            return
        export_type = self.combo_type.currentText()
        if export_type == "حزمة CapCut كاملة":
            folder = QFileDialog.getExistingDirectory(self, "اختر مجلد الحفظ لحزمة CapCut")
            if not folder:
                return
            self.start_export_worker(folder, "capcut")
            return
        if export_type == "بيانات القص EDL / JSON":
            self.export_cuts(out_path)
            return

        self.start_export_worker(out_path, "video")

    def start_export_worker(self, out_path: str, export_type: str):
        try:
            worker = ExportWorker(
                self.state,
                out_path,
                self.combo_res.currentText(),
                export_type,
            )
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "تعذر بدء التصدير", str(exc))
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.btn_start.setEnabled(False)
        self.btn_capcut.setEnabled(False)
        self.worker = worker
        self.worker.finished.connect(self.on_export_finished)
        self.worker.start()

    def export_cuts(self, out_path: str):
        try:
            snapshot = TimelineExportAdapter.snapshot(self.state)
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "تعذر تصدير بيانات القص", str(exc))
            return
        clips = sorted(
            (clip for clip in self.state.clips if not clip.is_cut and clip.video_enabled),
            key=lambda clip: clip.timeline_start_us,
        )
        base, _ext = os.path.splitext(out_path)
        json_path = base + ".json"
        edl_path = base + ".edl"
        cuts = [
            {
                "clip_id": clip.id,
                "source_in": round(clip.source_in, 6),
                "source_out": round(clip.source_out, 6),
                "timeline_in": round(clip.timeline_in, 6),
                "duration": round(clip.duration, 6),
            }
            for clip in clips
        ]
        try:
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "source": self.state.media_info.filepath,
                    "fps": snapshot.fps,
                    "cuts": cuts,
                }, handle, ensure_ascii=False, indent=2)
            with open(edl_path, "w", encoding="utf-8") as handle:
                handle.write("TITLE: AUTO SILENCE CUTTER\nFCM: NON-DROP FRAME\n\n")
                for index, clip in enumerate(clips, start=1):
                    handle.write(
                        f"{index:03d}  AX       V     C        "
                        f"{self._timecode(clip.source_in)} {self._timecode(clip.source_out)} "
                        f"{self._timecode(clip.timeline_in)} "
                        f"{self._timecode(clip.timeline_in + clip.duration)}\n"
                    )
                    handle.write(f"* FROM CLIP: {os.path.basename(clip.source_path)}\n\n")
            QMessageBox.information(
                self,
                "اكتمل التصدير",
                f"تم حفظ بيانات التقطيع:\n{json_path}\n{edl_path}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر كتابة بيانات التقطيع:\n{exc}")

    def _timecode(self, seconds: float) -> str:
        return format_timecode(seconds, self.state.media_info.fps)

    def on_export_finished(self, success: bool, msg: str):
        self.progress_bar.setVisible(False)
        self.btn_start.setEnabled(True)
        self.btn_capcut.setEnabled(True)
        if success:
            QMessageBox.information(self, "اكتمل التصدير", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "خطأ", msg)
