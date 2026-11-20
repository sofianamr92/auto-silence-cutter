# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QMessageBox, QProgressBar, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from core.editing_engine import ProjectState, Clip
from core.media_probe import MediaProbe
from core.silence_detection import SilenceDetector
from core.breath_detection import BreathDetector
from editing.commands import SplitCommand, TrimStartCommand, TrimEndCommand
from editing.undo_redo import UndoRedoStack
from timeline.timeline_widget import TimelineWidget
from ui.player_widget import PlayerWidget
from export.export_dialog import ExportDialog
import numpy as np

class AnalysisWorker(QThread):
    finished = Signal(object, object)
    error = Signal(str)

    def __init__(self, filepath: str, threshold: float):
        super().__init__()
        self.filepath = filepath
        self.threshold = threshold

    def run(self):
        try:
            detector = SilenceDetector(threshold_db=self.threshold)
            regions = detector.analyze(self.filepath)
            audio, _ = SilenceDetector.extract_pcm_float(self.filepath, 16000)
            breath_engine = BreathDetector()
            breaths = breath_engine.detect_breaths(audio, 16000, regions)
            self.finished.emit(regions + breaths, audio)
        except Exception as e:
            self.error.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AUTO SILENCE CUTTER — محرر الفيديو الذكي")
        self.resize(1366, 768)
        self.setLayoutDirection(Qt.RightToLeft)

        self.state = ProjectState()
        self.undo_stack = UndoRedoStack()

        self._build_ui()
        self._setup_shortcuts()

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        header = QHBoxLayout()
        self.btn_import = QPushButton("استيراد فيديو (Ctrl+O)")
        self.btn_import.setStyleSheet("background: #2563eb; color: #fff; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
        self.btn_import.clicked.connect(self.on_import_clicked)
        header.addWidget(self.btn_import)

        self.btn_remove = QPushButton("حذف الفيديو الحالي")
        self.btn_remove.setStyleSheet("background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); border-radius: 4px; padding: 6px 12px;")
        self.btn_remove.clicked.connect(self.on_remove_clicked)
        header.addWidget(self.btn_remove)

        self.btn_undo = QPushButton("تراجع (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.on_undo)
        header.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("إعادة (Ctrl+Y)")
        self.btn_redo.clicked.connect(self.on_redo)
        header.addWidget(self.btn_redo)

        header.addStretch()

        self.btn_export = QPushButton("تصدير الفيديو (Ctrl+E)")
        self.btn_export.setStyleSheet("background: #059669; color: #fff; font-weight: bold; padding: 6px 18px; border-radius: 4px;")
        self.btn_export.clicked.connect(self.on_export_clicked)
        header.addWidget(self.btn_export)

        main_layout.addLayout(header)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)

        self.player_widget = PlayerWidget(self)
        self.player_widget.workspace_changed.connect(self.change_workspace_layout)
        self.splitter.addWidget(self.player_widget)

        self.timeline_widget = TimelineWidget(self.state, self)
        self.timeline_widget.time_changed.connect(lambda t: self.player_widget.player.setPosition(int(t * 1000)))
        self.splitter.addWidget(self.timeline_widget)

        self.splitter.setSizes([480, 220])
        main_layout.addWidget(self.splitter, stretch=1)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(8)
        main_layout.addWidget(self.progress_bar)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("C"), self, activated=self.on_split)
        QShortcut(QKeySequence("Q"), self, activated=self.on_trim_start)
        QShortcut(QKeySequence("W"), self, activated=self.on_trim_end)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.on_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.on_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.on_redo)
        QShortcut(QKeySequence("Space"), self, activated=self.player_widget.toggle_play)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.on_import_clicked)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.on_export_clicked)

    def on_import_clicked(self):
        file, _ = QFileDialog.getOpenFileName(self, "اختر ملف فيديو", "", "فيديوهات (*.mp4 *.mov *.mkv *.webm *.avi)")
        if file:
            info = MediaProbe.probe_file(file)
            if not info:
                QMessageBox.critical(self, "خطأ", "تعذر قراءة بيانات الفيديو عبر FFprobe!")
                return
            self.state.reset()
            self.state.media_info = info
            from core.editing_engine import Clip
            self.state.clips = [Clip(
                source_path=file,
                source_in=0.0,
                source_out=info.duration,
                timeline_in=0.0
            )]
            self.player_widget.load_media(file)

            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            self.worker = AnalysisWorker(file, -38.0)
            self.worker.finished.connect(self.on_analysis_finished)
            self.worker.error.connect(lambda err: QMessageBox.warning(self, "تنبيه", f"خطأ في التحليل: {err}"))
            self.worker.start()

    def on_analysis_finished(self, regions, audio):
        self.progress_bar.setVisible(False)
        self.state.detected_regions = regions

        approved_regions = sorted(
            (r for r in regions if r.approved_for_cut),
            key=lambda r: r.start_time
        )
        merged_regions = []
        for region in approved_regions:
            start = max(0.0, region.start_time)
            end = min(self.state.media_info.duration, region.end_time)
            if end <= start:
                continue
            if merged_regions and start <= merged_regions[-1][1]:
                merged_regions[-1] = (merged_regions[-1][0], max(merged_regions[-1][1], end))
            else:
                merged_regions.append((start, end))

        kept_clips = []
        cursor = 0.0
        source_path = self.state.media_info.filepath
        for start, end in merged_regions:
            if start > cursor:
                kept_clips.append(Clip(
                    source_path=source_path,
                    source_in=cursor,
                    source_out=start,
                    timeline_in=sum(c.duration for c in kept_clips)
                ))
            cursor = max(cursor, end)
        if cursor < self.state.media_info.duration:
            kept_clips.append(Clip(
                source_path=source_path,
                source_in=cursor,
                source_out=self.state.media_info.duration,
                timeline_in=sum(c.duration for c in kept_clips)
            ))
        self.state.clips = kept_clips
        self.state.recalculate_timeline_positions()
        self.state.is_modified = len(kept_clips) > 0

        if len(audio) == 0:
            peaks = np.array([], dtype=np.float32)
        else:
            peak_count = min(1200, len(audio))
            sample_size = max(1, int(np.ceil(len(audio) / peak_count)))
            trimmed = audio[:peak_count * sample_size]
            if len(trimmed) < peak_count * sample_size:
                trimmed = np.pad(trimmed, (0, peak_count * sample_size - len(trimmed)))
            peaks = np.abs(trimmed.reshape(peak_count, sample_size)).max(axis=1)
        self.timeline_widget.set_waveform_data(peaks)
        self.timeline_widget.update()
        QMessageBox.information(self, "نجاح", f"تم اكتشاف {len(regions)} مقطع صمت ونفس بنجاح!")

    def on_remove_clicked(self):
        if not self.state.media_info.filepath:
            return
        res = QMessageBox.question(self, "تأكيد", "هل أنت متأكد من رغبتك بإزالة الفيديو الحالي من مساحة العمل؟\n(لن يتم حذف ملفك الأصلي من الحاسوب)")
        if res == QMessageBox.Yes:
            self.player_widget.player.stop()
            self.player_widget.player.setSource("")
            self.state.reset()
            self.undo_stack.clear()
            self.timeline_widget.waveform_peaks = None
            self.timeline_widget.update()

    def on_split(self):
        cmd = SplitCommand(self.state, self.state.current_playhead)
        self.undo_stack.push_and_execute(cmd)
        self.timeline_widget.update()

    def on_trim_start(self):
        cmd = TrimStartCommand(self.state, self.state.current_playhead)
        self.undo_stack.push_and_execute(cmd)
        self.timeline_widget.update()

    def on_trim_end(self):
        cmd = TrimEndCommand(self.state, self.state.current_playhead)
        self.undo_stack.push_and_execute(cmd)
        self.timeline_widget.update()

    def on_undo(self):
        if self.undo_stack.undo():
            self.timeline_widget.update()

    def on_redo(self):
        if self.undo_stack.redo():
            self.timeline_widget.update()

    def on_export_clicked(self):
        dlg = ExportDialog(self.state, self)
        dlg.exec()

    def change_workspace_layout(self, layout_name: str):
        self.state.active_layout = layout_name
        h = self.height()
        if layout_name == "Timeline Focus":
            self.splitter.setSizes([int(h * 0.45), int(h * 0.55)])
        elif layout_name == "Preview Focus":
            self.splitter.setSizes([int(h * 0.75), int(h * 0.25)])
        else:
            self.splitter.setSizes([int(h * 0.65), int(h * 0.35)])
