# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QMessageBox, QProgressBar, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from core.editing_engine import ProjectState, Clip, seconds_to_us
from core.media_probe import FrameIndexMapper, MediaProbe
from core.silence_detection import SilenceDetector
from core.breath_detection import BreathDetector
from editing.commands import TimelineController
from editing.undo_redo import UndoRedoStack
from timeline.timeline_widget import TimelineWidget
from timeline.playback_controller import TimelinePlaybackController
from ui.player_widget import PlayerWidget
from export.export_dialog import ExportDialog
import hashlib
import numpy as np
import os
import shutil
import subprocess
import tempfile

class AnalysisWorker(QThread):
    analysis_succeeded = Signal(int, object, object, object, str)
    analysis_failed = Signal(int, str)

    def __init__(
        self,
        filepath: str,
        threshold: float,
        request_id: int,
        fps_num: int = 30,
        fps_den: int = 1,
        duration_us: int = 0,
    ):
        super().__init__()
        self.filepath = filepath
        self.threshold = threshold
        self.request_id = request_id
        self.fps_num = fps_num
        self.fps_den = fps_den
        self.duration_us = duration_us

    def run(self):
        try:
            audio, audio_duration = SilenceDetector.extract_pcm_float(self.filepath, 16000)
            detector = SilenceDetector(threshold_db=self.threshold)
            regions = detector.analyze_audio(audio, 16000, audio_duration)
            breath_engine = BreathDetector()
            breaths = breath_engine.detect_breaths(audio, 16000, regions)
            frame_index = None
            frame_index_error = ""
            try:
                frame_index = FrameIndexMapper.from_media(
                    self.filepath,
                    self.fps_num,
                    self.fps_den,
                    self.duration_us,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                frame_index_error = str(exc)
            self.analysis_succeeded.emit(
                self.request_id,
                regions + breaths,
                audio,
                frame_index,
                frame_index_error,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
            self.analysis_failed.emit(self.request_id, str(e))


class ThumbnailWorker(QThread):
    image_ready = Signal(str, bytes)
    image_failed = Signal(str, str)

    def __init__(self, clip_id: str, filepath: str, timestamp_us: int):
        super().__init__()
        self.clip_id = clip_id
        self.filepath = filepath
        self.timestamp_us = max(0, timestamp_us)

    def run(self):
        try:
            source = os.path.abspath(self.filepath)
            stat = os.stat(source)
            cache_key = hashlib.sha256(
                f"{os.path.normcase(source)}|{stat.st_size}|{stat.st_mtime_ns}|{self.timestamp_us}".encode("utf-8")
            ).hexdigest()
            cache_root = os.path.join(
                os.getenv("LOCALAPPDATA") or tempfile.gettempdir(),
                "AutoSilenceCutter",
                "thumbnails",
            )
            os.makedirs(cache_root, exist_ok=True)
            cache_path = os.path.join(cache_root, cache_key + ".png")
            if os.path.isfile(cache_path):
                with open(cache_path, "rb") as cached:
                    image_data = cached.read()
            else:
                ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
                result = subprocess.run(
                    [
                        ffmpeg, "-v", "error", "-ss",
                        f"{self.timestamp_us / 1_000_000:.6f}",
                        "-i", source,
                        "-frames:v", "1",
                        "-vf", "scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2",
                        "-f", "image2pipe", "-vcodec", "png", "pipe:1",
                    ],
                    capture_output=True,
                )
                if result.returncode != 0 or not result.stdout:
                    detail = result.stderr.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(detail or "FFmpeg could not create a clip thumbnail.")
                image_data = result.stdout
                temp_path = cache_path + f".{os.getpid()}.tmp"
                with open(temp_path, "wb") as output:
                    output.write(image_data)
                os.replace(temp_path, cache_path)
            self.image_ready.emit(self.clip_id, image_data)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self.image_failed.emit(self.clip_id, str(exc))


class FrameIndexWorker(QThread):
    index_ready = Signal(object)
    index_failed = Signal(str)

    def __init__(self, filepath: str, fps_num: int, fps_den: int, duration_us: int):
        super().__init__()
        self.filepath = filepath
        self.fps_num = fps_num
        self.fps_den = fps_den
        self.duration_us = duration_us

    def run(self):
        try:
            mapper = FrameIndexMapper.from_media(
                self.filepath,
                self.fps_num,
                self.fps_den,
                self.duration_us,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.index_failed.emit(str(exc))
            return
        self.index_ready.emit(mapper)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AUTO SILENCE CUTTER — محرر الفيديو الذكي")
        self.resize(1366, 768)
        self.setLayoutDirection(Qt.RightToLeft)

        self.state = ProjectState()
        self.undo_stack = UndoRedoStack()
        self.timeline_controller = TimelineController(self.state, self.undo_stack)
        self.playback_controller = None
        self._frame_index_mapper = None
        self._project_path = None
        self._analysis_generation = 0
        self._analysis_workers = set()
        self._analysis_worker = None
        self._thumbnail_workers = set()
        self._frame_index_workers = set()

        self._build_ui()
        self.playback_controller = TimelinePlaybackController(
            self.state,
            self.player_widget.player,
            self,
        )
        self.timeline_widget.time_changed.connect(self._seek_timeline)
        self.timeline_widget.clip_move_requested.connect(self._move_clip)
        self.timeline_widget.trim_requested.connect(self._trim_clip)
        self.timeline_widget.delete_requested.connect(self._delete_clip)
        self.timeline_widget.thumbnail_requested.connect(self._request_thumbnail)
        self.playback_controller.playhead_changed.connect(self.timeline_widget.set_playhead_us)
        self.player_widget.step_forward.connect(lambda: self.playback_controller.step_frame(1))
        self.player_widget.step_backward.connect(lambda: self.playback_controller.step_frame(-1))
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

        self.btn_open_project = QPushButton("فتح مشروع")
        self.btn_open_project.clicked.connect(self.open_project)
        header.addWidget(self.btn_open_project)

        self.btn_save_project = QPushButton("حفظ مشروع")
        self.btn_save_project.clicked.connect(self.save_project)
        header.addWidget(self.btn_save_project)

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
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self.open_project)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.on_export_clicked)
        QShortcut(QKeySequence("Delete"), self, activated=self._delete_selected_clip)

    def on_import_clicked(self):
        file, _ = QFileDialog.getOpenFileName(self, "اختر ملف فيديو", "", "فيديوهات (*.mp4 *.mov *.mkv *.webm *.avi)")
        if file:
            info = MediaProbe.probe_file(file)
            if not info:
                QMessageBox.critical(self, "خطأ", "تعذر قراءة بيانات الفيديو عبر FFprobe!")
                return
            self.state.reset()
            self.undo_stack.clear()
            self.state.media_info = info
            self.state.clips = [Clip(
                source_path=file,
                source_in=0.0,
                source_out=info.duration,
                timeline_in=0.0
            )]
            self.state.recalculate_timeline_positions()
            self.state.current_playhead_us = 0
            self.state.is_modified = True
            self._project_path = None
            self.player_widget.set_timebase(info.fps_num, info.fps_den)
            self._frame_index_mapper = FrameIndexMapper.from_frame_rate(
                info.fps_num,
                info.fps_den,
                info.duration_us,
            )
            self.playback_controller.set_frame_index_mapper(self._frame_index_mapper)
            self.player_widget.load_media(file)
            self.timeline_widget.set_waveform_data(None)
            self.timeline_widget.refresh()

            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            self._analysis_generation += 1
            request_id = self._analysis_generation
            worker = AnalysisWorker(
                file,
                -38.0,
                request_id,
                info.fps_num,
                info.fps_den,
                info.duration_us,
            )
            self._analysis_workers.add(worker)
            self._analysis_worker = worker
            worker.analysis_succeeded.connect(self.on_analysis_finished)
            worker.analysis_failed.connect(self.on_analysis_error)
            worker.finished.connect(self._release_analysis_worker)
            worker.start()

    def _release_analysis_worker(self):
        worker = self.sender()
        if worker is not None:
            self._analysis_workers.discard(worker)
            if self._analysis_worker is worker:
                self._analysis_worker = None

    def _seek_timeline(self, time_seconds: float):
        self.playback_controller.seek_to_timeline_us(seconds_to_us(time_seconds))

    def _frame_aligned_playhead(self) -> int:
        playhead_us = self.state.current_playhead_us
        if self._frame_index_mapper is None:
            return playhead_us
        source_time_us = self.state.timeline_to_source_us(playhead_us)
        if source_time_us is None:
            return playhead_us
        snapped_source_us = self._frame_index_mapper.nearest_frame_time_us(source_time_us)
        mapped_time_us = self.state.source_to_timeline_us(snapped_source_us)
        if mapped_time_us is not None:
            self.state.current_playhead_us = mapped_time_us
        return self.state.current_playhead_us

    def _move_clip(self, clip_id: str, target_time_us: int):
        if self.timeline_controller.move_clip(clip_id, target_time_us):
            self._refresh_timeline_playback()

    def _trim_clip(self, clip_id: str, edge: str, target_time_us: int):
        clip = next((item for item in self.state.clips if item.id == clip_id), None)
        if clip is None:
            return
        target_time_us = max(
            clip.timeline_start_us,
            min(clip.timeline_start_us + clip.duration_us, target_time_us),
        )
        self.state.current_playhead_us = target_time_us
        self._frame_aligned_playhead()
        if edge == "start":
            changed = self.timeline_controller.trim_start_at(self.state.current_playhead_us)
        else:
            changed = self.timeline_controller.trim_end_at(self.state.current_playhead_us)
        if changed:
            self._refresh_timeline_playback()

    def _delete_clip(self, clip_id: str):
        if self.timeline_controller.delete_clip(clip_id):
            self._refresh_timeline_playback()

    def _refresh_timeline_playback(self):
        self.timeline_widget.refresh()
        if self.state.clips and self.playback_controller.seek_to_timeline_us(
            self.state.current_playhead_us
        ):
            return
        self.player_widget.player.stop()

    def _delete_selected_clip(self):
        if self.state.selected_clip_ids:
            self._delete_clip(self.state.selected_clip_ids[0])

    def _request_thumbnail(self, clip_id: str, filepath: str, timestamp_us: int):
        if not filepath or not os.path.isfile(filepath):
            return
        worker = ThumbnailWorker(clip_id, filepath, timestamp_us)
        self._thumbnail_workers.add(worker)
        worker.image_ready.connect(self._on_thumbnail_ready)
        worker.image_failed.connect(self._on_thumbnail_failed)
        worker.finished.connect(self._release_thumbnail_worker)
        worker.start()

    def _on_thumbnail_ready(self, clip_id: str, image_data: bytes):
        if any(clip.id == clip_id for clip in self.state.clips):
            self.timeline_widget.set_thumbnail(clip_id, image_data)

    def _on_thumbnail_failed(self, clip_id: str, error: str):
        if any(clip.id == clip_id for clip in self.state.clips):
            QMessageBox.warning(
                self,
                "تعذر إنشاء الصورة المصغّرة",
                f"المقطع {clip_id}: {error}",
            )

    def _release_thumbnail_worker(self):
        worker = self.sender()
        if worker is not None:
            self._thumbnail_workers.discard(worker)

    def on_analysis_error(self, request_id, error):
        if request_id != self._analysis_generation:
            return
        self.progress_bar.setVisible(False)
        QMessageBox.warning(self, "تنبيه", f"خطأ في التحليل: {error}")

    def on_analysis_finished(
        self,
        request_id,
        regions,
        audio,
        frame_index_mapper=None,
        frame_index_error="",
    ):
        if request_id != self._analysis_generation:
            return
        self.progress_bar.setVisible(False)
        self.state.detected_regions = regions
        if frame_index_mapper is not None:
            self._frame_index_mapper = frame_index_mapper
            self.playback_controller.set_frame_index_mapper(frame_index_mapper)
            info = self.state.media_info
            self.player_widget.set_timebase(
                info.fps_num,
                info.fps_den,
                frame_index_mapper,
            )

        approved_regions = sorted(
            (r for r in regions if r.approved_for_cut),
            key=lambda r: r.start_time_us,
        )
        merged_regions = []
        for region in approved_regions:
            start = max(0, region.start_time_us)
            end = min(self.state.media_info.duration_us, region.end_time_us)
            if end <= start:
                continue
            if merged_regions and start <= merged_regions[-1][1]:
                merged_regions[-1] = (merged_regions[-1][0], max(merged_regions[-1][1], end))
            else:
                merged_regions.append((start, end))

        kept_clips = []
        cursor_us = 0
        source_path = self.state.media_info.filepath
        for start, end in merged_regions:
            if start > cursor_us:
                kept_clips.append(Clip(
                    source_path=source_path,
                    source_in_us=cursor_us,
                    source_out_us=start,
                    timeline_start_us=sum(c.duration_us for c in kept_clips),
                    origin_type="silence_detection",
                ))
            cursor_us = max(cursor_us, end)
        if cursor_us < self.state.media_info.duration_us:
            kept_clips.append(Clip(
                source_path=source_path,
                source_in_us=cursor_us,
                source_out_us=self.state.media_info.duration_us,
                timeline_start_us=sum(c.duration_us for c in kept_clips),
                origin_type="silence_detection",
            ))
        self.state.clips = kept_clips
        self.state.recalculate_timeline_positions()
        self.state.is_modified = len(kept_clips) > 0
        self.state.current_playhead_us = 0

        if len(audio) == 0:
            peaks = np.array([], dtype=np.float32)
        else:
            peak_count = min(1200, len(audio))
            sample_size = max(1, int(np.ceil(len(audio) / peak_count)))
            trimmed = audio[:peak_count * sample_size]
            if len(trimmed) < peak_count * sample_size:
                trimmed = np.pad(trimmed, (0, peak_count * sample_size - len(trimmed)))
            peaks = np.abs(trimmed.reshape(peak_count, sample_size)).max(axis=1)
        self.state.waveform_peaks = [float(value) for value in peaks]
        self.timeline_widget.set_waveform_data(peaks)
        self.playback_controller.seek_to_timeline_us(self.state.current_playhead_us)
        if frame_index_error:
            QMessageBox.warning(
                self,
                "تنبيه",
                f"تعذر إنشاء فهرس إطارات دقيق؛ سيُستخدم معدل الإطارات المبلغ عنه:\n{frame_index_error}",
            )
        QMessageBox.information(self, "نجاح", f"تم اكتشاف {len(regions)} مقطع صمت ونفس بنجاح!")

    def on_remove_clicked(self):
        if not self.state.media_info.filepath:
            return
        res = QMessageBox.question(self, "تأكيد", "هل أنت متأكد من رغبتك بإزالة الفيديو الحالي من مساحة العمل؟\n(لن يتم حذف ملفك الأصلي من الحاسوب)")
        if res == QMessageBox.Yes:
            self._analysis_generation += 1
            self.progress_bar.setVisible(False)
            self.player_widget.player.stop()
            self.player_widget.player.setSource("")
            self.state.reset()
            self.undo_stack.clear()
            self.timeline_widget.clear_media_assets()
            self._frame_index_mapper = None
            self.playback_controller.set_frame_index_mapper(None)
            self._project_path = None

    def on_split(self):
        if self.timeline_controller.split_at(self._frame_aligned_playhead()):
            self._refresh_timeline_playback()

    def on_trim_start(self):
        if self.timeline_controller.trim_start_at(self._frame_aligned_playhead()):
            self._refresh_timeline_playback()

    def on_trim_end(self):
        if self.timeline_controller.trim_end_at(self._frame_aligned_playhead()):
            self._refresh_timeline_playback()

    def on_undo(self):
        if self.undo_stack.undo():
            self._refresh_timeline_playback()

    def on_redo(self):
        if self.undo_stack.redo():
            self._refresh_timeline_playback()

    def on_export_clicked(self):
        dlg = ExportDialog(self.state, self)
        dlg.exec()

    def save_project(self):
        filepath = self._project_path
        if not filepath:
            filepath, _ = QFileDialog.getSaveFileName(
                self,
                "حفظ مشروع Timeline",
                self.state.project_name + ".asctimeline",
                "Auto Silence Timeline (*.asctimeline)",
            )
            if not filepath:
                return
            if not filepath.lower().endswith(".asctimeline"):
                filepath += ".asctimeline"
        try:
            self.state.save_to_file(filepath)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر حفظ المشروع:\n{exc}")
            return
        self._project_path = filepath
        self.setWindowTitle(f"AUTO SILENCE CUTTER — {os.path.basename(filepath)}")

    def open_project(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "فتح مشروع Timeline",
            self._project_path or "",
            "Auto Silence Timeline (*.asctimeline)",
        )
        if not filepath:
            return
        try:
            loaded = ProjectState.load_from_file(filepath)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر فتح المشروع:\n{exc}")
            return

        relinked = False
        old_source = loaded.media_info.filepath
        if old_source and not os.path.isfile(old_source):
            replacement, _ = QFileDialog.getOpenFileName(
                self,
                "إعادة ربط ملف الفيديو",
                "",
                "فيديوهات (*.mp4 *.mov *.mkv *.webm *.avi)",
            )
            if replacement:
                replacement_info = MediaProbe.probe_file(replacement)
                required_duration = max(
                    (clip.source_out_us for clip in loaded.clips),
                    default=0,
                )
                if replacement_info is None or replacement_info.duration_us < required_duration:
                    QMessageBox.warning(
                        self,
                        "تعذر ربط الوسائط",
                        "الملف المحدد غير صالح أو أقصر من المقاطع المحفوظة.",
                    )
                else:
                    loaded.media_info = replacement_info
                    new_media_id = Clip._media_id_for_path(replacement)
                    for clip in loaded.clips:
                        clip.source_path = replacement
                        clip.media_id = new_media_id
                    loaded.recalculate_timeline_positions()
                    relinked = True

        self._analysis_generation += 1
        self.progress_bar.setVisible(False)
        self.state.replace_from(loaded)
        self.undo_stack.clear()
        self._project_path = filepath
        self.timeline_widget.clear_media_assets()
        peaks = np.asarray(self.state.waveform_peaks, dtype=np.float32)
        self.timeline_widget.set_waveform_data(peaks)
        self.timeline_widget.refresh()
        self._frame_index_mapper = FrameIndexMapper.from_frame_rate(
            self.state.media_info.fps_num,
            self.state.media_info.fps_den,
            self.state.media_info.duration_us,
        )
        self.playback_controller.set_frame_index_mapper(self._frame_index_mapper)
        self.player_widget.set_timebase(
            self.state.media_info.fps_num,
            self.state.media_info.fps_den,
        )
        source_path = self.state.media_info.filepath
        if source_path and os.path.isfile(source_path):
            current_info = MediaProbe.probe_file(source_path)
            if current_info is None:
                QMessageBox.warning(
                    self,
                    "تعذر قراءة الوسائط",
                    "تم فتح المشروع، لكن تعذر قراءة بيانات ملف الفيديو المرتبط.",
                )
            else:
                self.player_widget.load_media(source_path)
                self._start_frame_index_worker(current_info)
                self.playback_controller.seek_to_timeline_us(self.state.current_playhead_us)
        else:
            self.player_widget.player.stop()
            self.player_widget.player.setSource("")
            QMessageBox.warning(
                self,
                "الوسائط غير موجودة",
                "تم فتح بيانات المشروع، لكن ملف الفيديو غير موجود في مساره المحفوظ.",
            )
        self.change_workspace_layout(self.state.active_layout)
        self.state.is_modified = relinked
        self.setWindowTitle(f"AUTO SILENCE CUTTER — {os.path.basename(filepath)}")

    def _start_frame_index_worker(self, media_info):
        worker = FrameIndexWorker(
            media_info.filepath,
            media_info.fps_num,
            media_info.fps_den,
            media_info.duration_us,
        )
        self._frame_index_workers.add(worker)
        worker.index_ready.connect(self._on_frame_index_ready)
        worker.index_failed.connect(self._on_frame_index_failed)
        worker.finished.connect(self._release_frame_index_worker)
        worker.start()

    def _on_frame_index_ready(self, mapper):
        if os.path.normcase(mapper.source_path) != os.path.normcase(
            os.path.abspath(self.state.media_info.filepath)
        ):
            return
        self._frame_index_mapper = mapper
        self.playback_controller.set_frame_index_mapper(mapper)
        info = self.state.media_info
        self.player_widget.set_timebase(info.fps_num, info.fps_den, mapper)

    def _on_frame_index_failed(self, error: str):
        QMessageBox.warning(
            self,
            "تنبيه",
            f"تعذر إنشاء فهرس إطارات دقيق؛ سيُستخدم معدل الإطارات المبلغ عنه:\n{error}",
        )

    def _release_frame_index_worker(self):
        worker = self.sender()
        if worker is not None:
            self._frame_index_workers.discard(worker)

    def change_workspace_layout(self, layout_name: str):
        self.state.active_layout = layout_name
        self.state.is_modified = True
        if layout_name == "Vertical Workspace":
            self.splitter.setOrientation(Qt.Horizontal)
            self.splitter.setSizes([int(self.width() * 0.6), int(self.width() * 0.4)])
            return
        self.splitter.setOrientation(Qt.Vertical)
        h = self.height()
        if layout_name == "Timeline Focus":
            self.splitter.setSizes([int(h * 0.45), int(h * 0.55)])
        elif layout_name == "Preview Focus":
            self.splitter.setSizes([int(h * 0.75), int(h * 0.25)])
        elif layout_name == "Compact Workspace":
            self.splitter.setSizes([int(h * 0.58), int(h * 0.42)])
        else:
            self.splitter.setSizes([int(h * 0.65), int(h * 0.35)])
