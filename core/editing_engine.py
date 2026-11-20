# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
from typing import Optional, Sequence, Tuple

from timeline.document import (
    Clip,
    ClipModel,
    DetectedRegion,
    MediaInfo,
    MediaSource,
    ProjectState,
    TimelineDocument,
    TrackModel,
    seconds_to_us,
)


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
        include_audio: Optional[bool] = None,
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
                    "-map", "0:v:0",
                    "-c", "copy",
                ]
                if include_audio is not False:
                    cmd.extend(["-map", "0:a?"])
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
                has_audio = (
                    include_audio is not False
                    and FFmpegRenderer.has_audio_stream(source_path)
                )
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
