# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
import uuid
import os
import shutil
import subprocess
from typing import List, Optional, Sequence, Tuple

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
    """الحالة الموحدة والمطلقة للمشروع (Single Source of Truth)"""
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


class FFmpegRenderer:
    """Synchronous FFmpeg rendering helpers used by desktop and web exports."""

    @staticmethod
    def get_ffmpeg_path() -> str:
        return shutil.which("ffmpeg") or "ffmpeg"

    @staticmethod
    def has_audio_stream(source_path: str) -> bool:
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", source_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    @staticmethod
    def get_media_duration(source_path: str) -> float:
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", source_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
            )
            duration = float(result.stdout.strip())
            return duration if duration > 0 else 0.0
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0.0

    @staticmethod
    def render_segments(
        source_path: str,
        segments: Sequence[Tuple[float, float]],
        output_path: str,
        resolution: str = "الدقة الأصلية الكاملة",
        fast_copy: bool = True,
    ) -> Tuple[bool, str]:
        source_path = os.path.abspath(source_path)
        if not os.path.isfile(source_path):
            return False, "ملف الفيديو الأصلي غير موجود."
        output_path = os.path.abspath(output_path)
        if os.path.normcase(source_path) == os.path.normcase(output_path):
            return False, "لا يمكن استبدال ملف المصدر بملف التصدير."

        valid_segments = [
            (max(0.0, float(start)), max(float(start), float(end)))
            for start, end in segments
            if float(end) > float(start)
        ]
        if not valid_segments:
            return False, "لا توجد مقاطع صالحة للتصدير."

        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)
        output_extension = os.path.splitext(output_path)[1].lower() or ".mp4"
        temp_output = os.path.join(
            output_dir,
            f".{os.path.basename(output_path)}.{os.getpid()}.tmp{output_extension}",
        )
        ffmpeg_bin = FFmpegRenderer.get_ffmpeg_path()
        try:
            source_duration = FFmpegRenderer.get_media_duration(source_path)
            is_full_source_copy = (
                fast_copy
                and resolution == "الدقة الأصلية الكاملة"
                and len(valid_segments) == 1
                and valid_segments[0][0] <= 0.001
                and source_duration > 0
                and valid_segments[0][1] >= source_duration - 0.05
                and os.path.splitext(source_path)[1].lower() == output_extension
            )
            if is_full_source_copy:
                cmd = [
                    ffmpeg_bin, "-y", "-i", source_path,
                    "-map", "0:v:0", "-map", "0:a?",
                    "-c", "copy",
                ]
                if output_extension in (".mp4", ".mov"):
                    cmd.extend(["-movflags", "+faststart"])
                cmd.append(temp_output)
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            else:
                scale_filter = {
                    "1080p Full HD": "scale=1920:-2",
                    "720p HD": "scale=1280:-2",
                    "4K Ultra HD": "scale=3840:-2",
                }.get(resolution)
                video_filters = []
                audio_filters = []
                has_audio = FFmpegRenderer.has_audio_stream(source_path)
                for index, (start, end) in enumerate(valid_segments):
                    video_filter = (
                        f"[0:v]trim=start={start:.6f}:end={end:.6f},"
                        f"setpts=PTS-STARTPTS"
                    )
                    if scale_filter:
                        video_filter += f",{scale_filter}"
                    video_filters.append(f"{video_filter}[v{index}]")
                    if has_audio:
                        audio_filters.append(
                            f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                            f"asetpts=PTS-STARTPTS[a{index}]"
                        )

                video_inputs = "".join(f"[v{i}]" for i in range(len(valid_segments)))
                filter_parts = video_filters + audio_filters
                filter_parts.append(f"{video_inputs}concat=n={len(valid_segments)}:v=1:a=0[vout]")
                if has_audio:
                    audio_inputs = "".join(f"[a{i}]" for i in range(len(valid_segments)))
                    filter_parts.append(f"{audio_inputs}concat=n={len(valid_segments)}:v=0:a=1[aout]")

                cmd = [
                    ffmpeg_bin, "-y", "-i", source_path,
                    "-filter_complex", ";".join(filter_parts),
                    "-map", "[vout]",
                ]
                if has_audio:
                    cmd.extend(["-map", "[aout]", "-c:a", "aac"])
                cmd.extend([
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                ])
                if output_extension in (".mp4", ".mov"):
                    cmd.extend(["-movflags", "+faststart"])
                cmd.append(temp_output)
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

            if res.returncode != 0:
                return False, (res.stderr or "").strip()[-2000:] or "فشل FFmpeg في تصدير الفيديو."
            if not os.path.isfile(temp_output):
                return False, "أنشأ FFmpeg الأمر لكنه لم ينتج ملفاً."

            os.replace(temp_output, output_path)
            return True, "تم تصدير الفيديو الممنتج بنجاح."
        except Exception as exc:
            return False, str(exc)
        finally:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except OSError:
                    pass
