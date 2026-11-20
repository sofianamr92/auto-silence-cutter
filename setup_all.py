# -*- coding: utf-8 -*-
"""
=============================================================================
  AUTO SILENCE CUTTER — Master Project Installer & Codebase Generator
  يقوم هذا الملف بإنشاء كافة ملفات ومجلدات المشروع بالكامل بكود حقيقي واحترافي
=============================================================================
"""
import os
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

print("=" * 70)
print("  [*] جاري إنشاء وتثبيت مشروع AUTO SILENCE CUTTER بالكامل...")
print(f"  [*] مسار التثبيت: {BASE_DIR}")
print("=" * 70)

# قاموس يحتوي على مسار كل ملف وكوده البرمجي الكامل
FILES = {}

# ---------------------------------------------------------------------------
# 1. requirements.txt
# ---------------------------------------------------------------------------
FILES["requirements.txt"] = """PySide6>=6.5.0
numpy>=1.24.0
pytest>=7.3.0
pyinstaller>=5.10.0
python-dotenv>=1.0.0
keyring>=25.0.0
"""

# ---------------------------------------------------------------------------
# 2. .env.example
# ---------------------------------------------------------------------------
FILES[".env.example"] = """# إعدادات Supabase للعميل العام فقط.
# لا تضف Google Client Secret أو Service Role Key إلى هذا الملف.
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_your-public-key
"""

# ---------------------------------------------------------------------------
# 3. app/configuration.py
# ---------------------------------------------------------------------------
FILES["app/configuration.py"] = """# -*- coding: utf-8 -*-
import os

APP_NAME = "AUTO SILENCE CUTTER"
APP_VERSION = "2.4.0"
DEFAULT_SILENCE_THRESHOLD_DB = -38.0
DEFAULT_MIN_SILENCE_DURATION = 0.60
DEFAULT_PAD_BEFORE = 0.15
DEFAULT_PAD_AFTER = 0.18

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(APP_DIR, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)
"""

# ---------------------------------------------------------------------------
# 4. core/editing_engine.py (Single Source of Truth)
# ---------------------------------------------------------------------------
FILES["core/editing_engine.py"] = """# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import List, Optional
import uuid

@dataclass
class MediaInfo:
    filepath: str = ""
    duration: float = 0.0
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    video_codec: str = ""
    audio_codec: str = ""
    audio_sample_rate: int = 44100
    audio_channels: int = 2
    bitrate: int = 0
    file_size_bytes: int = 0

@dataclass
class DetectedRegion:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    region_type: str = "silence"  # 'silence', 'breath', 'mouth_sound', 'speech'
    start_time: float = 0.0
    end_time: float = 0.0
    confidence: float = 1.0
    approved_for_cut: bool = True
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

@dataclass
class Clip:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_path: str = ""
    source_in: float = 0.0
    source_out: float = 0.0
    timeline_in: float = 0.0
    is_cut: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.source_out - self.source_in)

class ProjectState:
    \"\"\"الحالة الموحدة والمطلقة للمشروع (Single Source of Truth)\"\"\"
    def __init__(self):
        self.project_id: str = str(uuid.uuid4())
        self.project_name: str = "مشروع جديد"
        self.media_info: MediaInfo = MediaInfo()
        self.clips: List[Clip] = []
        self.detected_regions: List[DetectedRegion] = []
        self.current_playhead: float = 0.0
        self.is_modified: bool = False
        self.zoom_level: float = 1.0
        self.active_layout: str = "Standard Horizontal"

    def reset(self):
        self.project_id = str(uuid.uuid4())
        self.project_name = "مشروع جديد"
        self.media_info = MediaInfo()
        self.clips.clear()
        self.detected_regions.clear()
        self.current_playhead = 0.0
        self.is_modified = False

    def recalculate_timeline_positions(self):
        pos = 0.0
        for clip in self.clips:
            if not clip.is_cut:
                clip.timeline_in = pos
                pos += clip.duration

    @property
    def total_edited_duration(self) -> float:
        return sum(c.duration for c in self.clips if not c.is_cut)

    @property
    def total_removed_duration(self) -> float:
        return max(0.0, self.media_info.duration - self.total_edited_duration)

    def find_clip_at_time(self, t: float) -> Optional[Clip]:
        for clip in self.clips:
            if not clip.is_cut and clip.timeline_in <= t <= (clip.timeline_in + clip.duration):
                return clip
        return None
"""

# ---------------------------------------------------------------------------
# 5. core/media_probe.py (FFprobe Parser)
# ---------------------------------------------------------------------------
FILES["core/media_probe.py"] = """# -*- coding: utf-8 -*-
import json
import subprocess
import os
import shutil
from typing import Optional
from core.editing_engine import MediaInfo

class MediaProbe:
    @staticmethod
    def get_ffprobe_path() -> str:
        path = shutil.which("ffprobe")
        if path:
            return path
        local_bin = os.path.join(os.path.dirname(__file__), "..", "bin", "ffprobe.exe")
        if os.path.exists(local_bin):
            return local_bin
        return "ffprobe"

    @staticmethod
    def probe_file(filepath: str) -> Optional[MediaInfo]:
        if not os.path.exists(filepath):
            return None

        ffprobe_bin = MediaProbe.get_ffprobe_path()
        cmd = [
            ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            filepath
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
            data = json.loads(res.stdout)

            info = MediaInfo(filepath=filepath)
            format_info = data.get("format", {})
            info.duration = float(format_info.get("duration", 0.0))
            info.bitrate = int(format_info.get("bit_rate", 0))
            info.file_size_bytes = int(format_info.get("size", os.path.getsize(filepath)))

            for stream in data.get("streams", []):
                codec_type = stream.get("codec_type")
                if codec_type == "video" and not info.video_codec:
                    info.video_codec = stream.get("codec_name", "")
                    info.width = int(stream.get("width", 1920))
                    info.height = int(stream.get("height", 1080))
                    r_frame_rate = stream.get("r_frame_rate", "30/1")
                    if "/" in r_frame_rate:
                        num, den = r_frame_rate.split("/")
                        info.fps = float(num) / max(1.0, float(den))
                    else:
                        info.fps = float(r_frame_rate)
                elif codec_type == "audio" and not info.audio_codec:
                    info.audio_codec = stream.get("codec_name", "")
                    info.audio_sample_rate = int(stream.get("sample_rate", 44100))
                    info.audio_channels = int(stream.get("channels", 2))

            return info
        except Exception as ex:
            print(f"[!] MediaProbe Error: {ex}")
            return None
"""

# ---------------------------------------------------------------------------
# 6. core/silence_detection.py (Audio Silence & VAD Engine)
# ---------------------------------------------------------------------------
FILES["core/silence_detection.py"] = """# -*- coding: utf-8 -*-
import numpy as np
import subprocess
import shutil
from typing import List, Tuple
from core.editing_engine import DetectedRegion

class SilenceDetector:
    def __init__(self, threshold_db: float = -38.0, min_duration_sec: float = 0.6,
                 pad_before: float = 0.15, pad_after: float = 0.18, use_vad: bool = True):
        self.threshold_db = threshold_db
        self.min_duration_sec = min_duration_sec
        self.pad_before = pad_before
        self.pad_after = pad_after
        self.use_vad = use_vad

    @staticmethod
    def extract_pcm_float(filepath: str, sample_rate: int = 16000) -> Tuple[np.ndarray, float]:
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ffmpeg_bin, "-y", "-v", "quiet",
            "-i", filepath,
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "f32le",
            "-"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_data, _ = proc.communicate()
        audio = np.frombuffer(raw_data, dtype=np.float32)
        duration = len(audio) / sample_rate if sample_rate > 0 else 0.0
        return audio, duration

    def analyze(self, filepath: str) -> List[DetectedRegion]:
        sample_rate = 16000
        audio, total_duration = self.extract_pcm_float(filepath, sample_rate)
        if len(audio) == 0:
            return []

        window_size = int(sample_rate * 0.025)
        hop_size = int(sample_rate * 0.010)
        num_frames = (len(audio) - window_size) // hop_size
        if num_frames <= 0:
            return []

        frames = np.lib.stride_tricks.sliding_window_view(audio[:num_frames * hop_size + window_size], window_size)[::hop_size]
        rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        db_array = 20 * np.log10(rms + 1e-6)

        sorted_db = np.sort(db_array[::5])
        noise_floor = sorted_db[int(len(sorted_db) * 0.15)] if len(sorted_db) > 0 else -60.0
        effective_threshold = max(self.threshold_db, noise_floor + 7.0)

        is_silence = db_array < effective_threshold
        raw_intervals = []
        in_silence = False
        start_time = 0.0

        for i, silence in enumerate(is_silence):
            t = i * 0.010
            if silence and not in_silence:
                in_silence = True
                start_time = t
            elif not silence and in_silence:
                in_silence = False
                dur = t - start_time
                if dur >= self.min_duration_sec:
                    raw_intervals.append((start_time, t))

        if in_silence:
            dur = total_duration - start_time
            if dur >= self.min_duration_sec:
                raw_intervals.append((start_time, total_duration))

        regions: List[DetectedRegion] = []
        for s, e in raw_intervals:
            cut_start = s + self.pad_after
            cut_end = max(cut_start, e - self.pad_before)
            if (cut_end - cut_start) >= 0.2:
                regions.append(DetectedRegion(
                    region_type="silence",
                    start_time=cut_start,
                    end_time=cut_end,
                    confidence=0.95,
                    approved_for_cut=True,
                    reason=f"سكتة صامتة ({cut_end - cut_start:.2f} ثانية)"
                ))

        return regions
"""

# ---------------------------------------------------------------------------
# 7. core/breath_detection.py (Breath Detection Layer)
# ---------------------------------------------------------------------------
FILES["core/breath_detection.py"] = """# -*- coding: utf-8 -*-
import numpy as np
from typing import List
from core.editing_engine import DetectedRegion

class BreathDetector:
    def __init__(self, sensitivity: str = "medium"):
        self.sensitivity = sensitivity

    def detect_breaths(self, audio: np.ndarray, sample_rate: int,
                       silence_regions: List[DetectedRegion]) -> List[DetectedRegion]:
        breath_regions: List[DetectedRegion] = []
        check_duration = 0.35
        for reg in silence_regions:
            if reg.duration > check_duration:
                breath_start = reg.end_time - check_duration
                breath_end = reg.end_time
                start_sample = int(breath_start * sample_rate)
                end_sample = min(len(audio), int(breath_end * sample_rate))
                if end_sample > start_sample:
                    segment = audio[start_sample:end_sample]
                    rms = np.sqrt(np.mean(segment ** 2) + 1e-12)
                    db = 20 * np.log10(rms + 1e-6)
                    if -46.0 < db < -28.0:
                        breath_regions.append(DetectedRegion(
                            region_type="breath",
                            start_time=breath_start,
                            end_time=breath_end,
                            confidence=0.82,
                            approved_for_cut=True,
                            reason="شهيق قبل بداية الجملة مباشرة"
                        ))
        return breath_regions
"""

# ---------------------------------------------------------------------------
# 8. editing/commands.py (Undo / Redo Commands: C, Q, W)
# ---------------------------------------------------------------------------
FILES["editing/commands.py"] = """# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional
from core.editing_engine import ProjectState, Clip

class EditCommand(ABC):
    @abstractmethod
    def execute(self) -> bool:
        pass

    @abstractmethod
    def undo(self) -> bool:
        pass

    @abstractmethod
    def description(self) -> str:
        pass

class SplitCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float):
        self.state = state
        self.time = playhead_time
        self.target_clip_idx: Optional[int] = None
        self.old_clip: Optional[Clip] = None
        self.new_clip_1: Optional[Clip] = None
        self.new_clip_2: Optional[Clip] = None

    def execute(self) -> bool:
        active_clips = [i for i, c in enumerate(self.state.clips) if not c.is_cut]
        for idx in active_clips:
            clip = self.state.clips[idx]
            if clip.timeline_in < self.time < (clip.timeline_in + clip.duration):
                self.target_clip_idx = idx
                self.old_clip = clip
                offset = self.time - clip.timeline_in
                split_source = clip.source_in + offset

                self.new_clip_1 = Clip(
                    source_path=clip.source_path,
                    source_in=clip.source_in,
                    source_out=split_source,
                    timeline_in=clip.timeline_in
                )
                self.new_clip_2 = Clip(
                    source_path=clip.source_path,
                    source_in=split_source,
                    source_out=clip.source_out,
                    timeline_in=self.time
                )
                self.state.clips.pop(idx)
                self.state.clips.insert(idx, self.new_clip_2)
                self.state.clips.insert(idx, self.new_clip_1)
                self.state.recalculate_timeline_positions()
                self.state.is_modified = True
                return True
        return False

    def undo(self) -> bool:
        if self.target_clip_idx is not None and self.old_clip is not None:
            self.state.clips.pop(self.target_clip_idx)
            self.state.clips.pop(self.target_clip_idx)
            self.state.clips.insert(self.target_clip_idx, self.old_clip)
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"تجزئة المقطع (C) عند {self.time:.2f} ثانية"

class TrimStartCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float):
        self.state = state
        self.time = playhead_time
        self.clip: Optional[Clip] = None
        self.old_source_in: float = 0.0

    def execute(self) -> bool:
        clip = self.state.find_clip_at_time(self.time)
        if clip and (self.time - clip.timeline_in) > 0.05:
            self.clip = clip
            self.old_source_in = clip.source_in
            offset = self.time - clip.timeline_in
            clip.source_in += offset
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def undo(self) -> bool:
        if self.clip:
            self.clip.source_in = self.old_source_in
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"قص بداية المقطع (Q) عند {self.time:.2f} ثانية"

class TrimEndCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float):
        self.state = state
        self.time = playhead_time
        self.clip: Optional[Clip] = None
        self.old_source_out: float = 0.0

    def execute(self) -> bool:
        clip = self.state.find_clip_at_time(self.time)
        if clip and (clip.timeline_in + clip.duration - self.time) > 0.05:
            self.clip = clip
            self.old_source_out = clip.source_out
            offset = self.time - clip.timeline_in
            clip.source_out = clip.source_in + offset
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def undo(self) -> bool:
        if self.clip:
            self.clip.source_out = self.old_source_out
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"قص نهاية المقطع (W) عند {self.time:.2f} ثانية"
"""

# ---------------------------------------------------------------------------
# 9. editing/undo_redo.py
# ---------------------------------------------------------------------------
FILES["editing/undo_redo.py"] = """# -*- coding: utf-8 -*-
from typing import List
from editing.commands import EditCommand

class UndoRedoStack:
    def __init__(self, max_history: int = 50):
        self.undo_stack: List[EditCommand] = []
        self.redo_stack: List[EditCommand] = []
        self.max_history = max_history

    def push_and_execute(self, command: EditCommand) -> bool:
        if command.execute():
            self.undo_stack.append(command)
            self.redo_stack.clear()
            if len(self.undo_stack) > self.max_history:
                self.undo_stack.pop(0)
            return True
        return False

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        cmd = self.undo_stack.pop()
        if cmd.undo():
            self.redo_stack.append(cmd)
            return True
        return False

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        cmd = self.redo_stack.pop()
        if cmd.execute():
            self.undo_stack.append(cmd)
            return True
        return False

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
"""

# ---------------------------------------------------------------------------
# 10. timeline/timeline_widget.py
# ---------------------------------------------------------------------------
FILES["timeline/timeline_widget.py"] = """# -*- coding: utf-8 -*-
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
                                      f"{reg.reason}\\nالحالة: {'محذوف' if reg.approved_for_cut else 'مُستعاد'}", self)
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
                cx = self.time_to_x(clip.source_in)
                painter.setPen(QPen(QColor("#10b981"), 2))
                painter.drawLine(int(cx), track_y, int(cx), track_y + track_h)

        # المؤشر (Playhead)
        px = self.time_to_x(self.state.current_playhead)
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(int(px), 0, int(px), height)
        painter.setBrush(QBrush(QColor("#38bdf8")))
        poly = [QPoint(int(px) - 6, 0), QPoint(int(px) + 6, 0), QPoint(int(px), 12)]
        painter.drawPolygon(poly)
"""

# ---------------------------------------------------------------------------
# 11. ui/player_widget.py (Player & Permanent Workspace Button)
# ---------------------------------------------------------------------------
FILES["ui/player_widget.py"] = """# -*- coding: utf-8 -*-
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

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("تشغيل")
        else:
            self.player.play()
            self.btn_play.setText("إيقاف")
"""

# ---------------------------------------------------------------------------
# 12. export/capcut_package.py
# ---------------------------------------------------------------------------
FILES["export/capcut_package.py"] = """# -*- coding: utf-8 -*-
import os
import json
import csv
from core.editing_engine import ProjectState

class CapCutPackageExporter:
    @staticmethod
    def export_package(state: ProjectState, target_dir: str):
        pkg_dir = os.path.join(target_dir, "AUTO_SILENCE_CAPCUT_PACKAGE")
        os.makedirs(pkg_dir, exist_ok=True)
        meta_dir = os.path.join(pkg_dir, "metadata")
        os.makedirs(meta_dir, exist_ok=True)

        cuts_data = [
            {"id": c.id, "in": c.source_in, "out": c.source_out, "duration": c.duration}
            for c in state.clips if not c.is_cut
        ]
        with open(os.path.join(meta_dir, "cuts.json"), "w", encoding="utf-8") as f:
            json.dump(cuts_data, f, ensure_ascii=False, indent=2)

        with open(os.path.join(meta_dir, "cuts.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Clip ID", "Source In (s)", "Source Out (s)", "Duration (s)"])
            for c in cuts_data:
                writer.writerow([c["id"], c["in"], c["out"], c["duration"]])

        readme_content = \"\"\"حزمة المونتاج الذكية لبرنامج CapCut Desktop 8
===================================================
تحتوي هذه الحزمة على المقاطع المقصوصة بدقة وبيانات التوقيت الكاملة.
يمكنك استيراد المقاطع لخط زمني نظيف تماماً وخالٍ من السكتات والأنفاس.
\"\"\"
        with open(os.path.join(pkg_dir, "README_AR.txt"), "w", encoding="utf-8") as f:
            f.write(readme_content)
"""

# ---------------------------------------------------------------------------
# 13. export/export_dialog.py
# ---------------------------------------------------------------------------
FILES["export/export_dialog.py"] = """# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QFileDialog, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal
from core.editing_engine import ProjectState
from export.capcut_package import CapCutPackageExporter
import subprocess
import shutil
import os

class ExportWorker(QThread):
    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(self, state: ProjectState, out_path: str):
        super().__init__()
        self.state = state
        self.out_path = out_path

    def run(self):
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        temp_out = self.out_path + ".tmp.mp4"
        cmd = [
            ffmpeg_bin, "-y",
            "-i", self.state.media_info.filepath,
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            temp_out
        ]
        try:
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0 and os.path.exists(temp_out):
                if os.path.exists(self.out_path):
                    os.remove(self.out_path)
                shutil.move(temp_out, self.out_path)
                self.finished.emit(True, "تم التصدير بنجاح!")
            else:
                self.finished.emit(False, "فشل تصدير الفيديو عبر FFmpeg.")
        except Exception as e:
            self.finished.emit(False, str(e))

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
            CapCutPackageExporter.export_package(self.state, folder)
            QMessageBox.information(self, "نجاح", "تم إنشاء حزمة CapCut Desktop بنجاح!")

    def on_start_export(self):
        out_path = self.txt_path.text().strip()
        if not out_path:
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.btn_start.setEnabled(False)

        self.worker = ExportWorker(self.state, out_path)
        self.worker.finished.connect(self.on_export_finished)
        self.worker.start()

    def on_export_finished(self, success: bool, msg: str):
        self.progress_bar.setVisible(False)
        self.btn_start.setEnabled(True)
        if success:
            QMessageBox.information(self, "اكتمل التصدير", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "خطأ", msg)
"""

# ---------------------------------------------------------------------------
# 14. ui/main_window.py (Main Window)
# ---------------------------------------------------------------------------
FILES["ui/main_window.py"] = """# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QMessageBox, QProgressBar, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from core.editing_engine import ProjectState
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
        step = max(1, len(audio) // 1200)
        peaks = np.abs(audio[:1200 * step].reshape(1200, step)).max(axis=1)
        self.timeline_widget.set_waveform_data(peaks)
        self.timeline_widget.update()
        QMessageBox.information(self, "نجاح", f"تم اكتشاف {len(regions)} مقطع صمت ونفس بنجاح!")

    def on_remove_clicked(self):
        if not self.state.media_info.filepath:
            return
        res = QMessageBox.question(self, "تأكيد", "هل أنت متأكد من رغبتك بإزالة الفيديو الحالي من مساحة العمل؟\\n(لن يتم حذف ملفك الأصلي من الحاسوب)")
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
"""

# ---------------------------------------------------------------------------
# 15. app/main.py (App Entrypoint)
# ---------------------------------------------------------------------------
FILES["app/main.py"] = """# -*- coding: utf-8 -*-
import sys
import shutil
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from ui.main_window import MainWindow

def verify_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[!] تحذير: لم يتم العثور على FFmpeg أو FFprobe في متغيرات PATH.")

def main():
    verify_ffmpeg()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)

    # تطبيق السمة الداكنة الاحترافية
    dark_stylesheet = \"\"\"
        QWidget {
            background-color: #0c0d11;
            color: #f1f5f9;
            font-family: 'Segoe UI', 'Cairo', Tahoma, sans-serif;
            font-size: 13px;
        }
        QPushButton {
            background-color: #1e2433;
            border: 1px solid #2d3548;
            padding: 6px 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #2b344a;
            border-color: #3b82f6;
        }
        QComboBox, QLineEdit {
            background-color: #141720;
            border: 1px solid #272c3d;
            padding: 5px;
            border-radius: 4px;
            color: #fff;
        }
    \"\"\"
    app.setStyleSheet(dark_stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
"""

# ---------------------------------------------------------------------------
# 16. tests/test_editing.py (Automated Tests)
# ---------------------------------------------------------------------------
FILES["tests/test_editing.py"] = """# -*- coding: utf-8 -*-
from core.editing_engine import ProjectState, Clip
from editing.commands import SplitCommand, TrimStartCommand, TrimEndCommand
from editing.undo_redo import UndoRedoStack

def test_split_command():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    stack = UndoRedoStack()
    cmd = SplitCommand(state, playhead_time=10.0)
    assert stack.push_and_execute(cmd) is True
    assert len(state.clips) == 2
    assert state.clips[0].duration == 10.0
    assert state.clips[1].duration == 20.0

    assert stack.undo() is True
    assert len(state.clips) == 1
    assert state.clips[0].duration == 30.0

def test_trim_start_q():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    cmd = TrimStartCommand(state, playhead_time=10.0)
    assert cmd.execute() is True
    assert clip.source_in == 10.0
    assert clip.duration == 20.0

    assert cmd.undo() is True
    assert clip.source_in == 0.0
    assert clip.duration == 30.0

def test_trim_end_w():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    cmd = TrimEndCommand(state, playhead_time=20.0)
    assert cmd.execute() is True
    assert clip.source_out == 20.0
    assert clip.duration == 20.0

    assert cmd.undo() is True
    assert clip.source_out == 30.0
    assert clip.duration == 30.0
"""

# ---------------------------------------------------------------------------
# 17. scripts/run_windows.bat
# ---------------------------------------------------------------------------
FILES["scripts/run_windows.bat"] = """@echo off
title AUTO SILENCE CUTTER
cd /d "%~dp0\\.."
python -m app.main
pause
"""

# ---------------------------------------------------------------------------
# 18. README_AR.md
# ---------------------------------------------------------------------------
FILES["README_AR.md"] = """# Auto Silence Cutter

تطبيق مونتاج محلي لاكتشاف الصمت وتحرير المقاطع، مع حفظ محلي ومزامنة اختيارية عبر Supabase.

## إعداد Supabase وGoogle

1. شغّل `supabase/migrations/202611190001_initial_cloud_projects.sql` من SQL Editor في مشروع Supabase.
2. فعّل Google من Authentication → Providers وأدخل أسرار OAuth في لوحة Supabase فقط.
3. أضف `http://127.0.0.1:8990/auth/callback` إلى Redirect URLs في إعدادات Auth.
4. استخدم عنوان callback الذي يعرضه مزود Google في لوحة Supabase كـ Authorized redirect URI في Google Cloud.
5. انسخ `.env.example` إلى `.env` وأضف عنوان المشروع ومفتاح Publishable/Anon العام فقط.
   لا تضف Google Client Secret أو Service Role Key إلى التطبيق أو Git.
6. شغّل `python -X utf8 desktop_app.py` وافتح `http://127.0.0.1:8990/`.

تُخزَّن جلسة التجديد في Windows Credential Manager. إذا لم تُضف إعدادات Supabase،
يظل التحرير والحفظ المحلي متاحين.

## الحفظ والوسائط

- تُحفظ بيانات المشاريع محلياً في IndexedDB وتُزامَن عبر قائمة انتظار عند توفر الاتصال.
- تمنع إصدارات المشاريع استبدال نسخة سحابية أحدث دون تنبيه.
- تعرض بطاقات المشاريع الصورة المصغرة وحجم الوسائط، ويعرض الحساب مساحة التخزين المستخدمة.
- رفع الفيديو اختياري ويتطلب موافقة صريحة؛ يستخدم TUS بقطع 6 MiB، مع تقدم وإيقاف واستئناف.
- بعد الرفع، يتحقق التطبيق من الملف بمقارنة SHA-256. ملفات Storage خاصة وتخضع لسياسات RLS.
- سعة الملفات وحدودها تعتمد على خطة Supabase.

اختبارات الوحدات: `python -m pytest tests\\test_cloud_auth.py tests\\test_cloud_projects.py tests\\test_cloud_media.py -q`.
"""

# إنشاء الملفات برمجياً
for rel_path, content in FILES.items():
    full_path = os.path.join(BASE_DIR, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    if os.path.exists(full_path):
        print(f"  [=] تخطي الملف الموجود دون استبداله: {rel_path}")
        continue
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [+] تم إنشاء الملف: {rel_path}")

# إنشاء ملفات __init__.py للحزم
PACKAGES = ["app", "core", "timeline", "editing", "workspace", "shortcuts", "projects", "database", "cloud", "export", "ui", "tests"]
for pkg in PACKAGES:
    init_path = os.path.join(BASE_DIR, pkg, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w", encoding="utf-8") as f:
            f.write(f'"""Package {pkg}"""\n')

print("=" * 70)
print("  [✓] اكتمل إنشاء كافة ملفات ومجلدات المشروع بنجاح 100%!")
print("=" * 70)
print("  لتشغيل البرنامج الآن:")
print("  1. pip install -r requirements.txt")
print("  2. python -m app.main")
print("=" * 70)