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
        self.state = state
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

            clips = [clip for clip in self.state.clips if not clip.is_cut and clip.duration > 0]
            if not clips:
                self.finished.emit(False, "لا توجد مقاطع متبقية للتصدير بعد حذف الصمت.")
                return
            segments = [(clip.source_in, clip.source_out) for clip in clips]
            success, message = FFmpegRenderer.render_segments(
                self.state.media_info.filepath,
                segments,
                self.out_path,
                resolution=self.resolution,
                fast_copy=(self.resolution == "الدقة الأصلية الكاملة"),
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
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.btn_start.setEnabled(False)
        self.btn_capcut.setEnabled(False)
        self.worker = ExportWorker(
            self.state,
            out_path,
            self.combo_res.currentText(),
            export_type,
        )
        self.worker.finished.connect(self.on_export_finished)
        self.worker.start()

    def export_cuts(self, out_path: str):
        clips = [clip for clip in self.state.clips if not clip.is_cut]
        base, ext = os.path.splitext(out_path)
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
                    "fps": self.state.media_info.fps,
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
