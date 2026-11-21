# -*- coding: utf-8 -*-
import os
import json
import math
import functools
import queue
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from fractions import Fraction
from typing import Optional, Sequence, Tuple

from export.export_settings import (
    AUDIO_ENCODERS,
    QUALITY_CRF,
    RESOLUTION_HEIGHTS,
    VIDEO_ENCODERS,
    ExportSettings,
    detect_ffmpeg_capabilities,
    estimated_video_bitrate_kbps,
)
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


class ExportCancelledError(RuntimeError):
    pass


def _parse_fraction(value: str, fallback: Fraction) -> Fraction:
    try:
        parsed = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return fallback
    return parsed if parsed > 0 else fallback


def _even_dimension(value: float) -> int:
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded - 1


def _av1_cpu_used(preset: str) -> int:
    speed_order = (
        "ultrafast", "superfast", "veryfast", "faster",
        "fast", "medium", "slow", "slower",
    )
    return 8 - speed_order.index(preset)


def merge_contiguous_segments(segments, tolerance=0.002):
    """يدمج المقاطع المتلاصقة (بدون قص بينها) كي لا يظهر fade وسط كلام متواصل."""
    merged = []
    for segment in segments:
        item = dict(segment)
        if merged and abs(float(item["start"]) - float(merged[-1]["end"])) <= tolerance:
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(item["end"]))
        else:
            merged.append(item)
    return merged


class FFmpegRenderer:
    """Synchronous FFmpeg rendering helpers used by desktop and web exports."""

    @staticmethod
    def get_ffmpeg_path() -> str:
        return shutil.which("ffmpeg") or "ffmpeg"

    @staticmethod
    def get_ffprobe_path() -> str:
        return shutil.which("ffprobe") or "ffprobe"

    @staticmethod
    def probe_media(source_path: str) -> dict:
        result = subprocess.run(
            [
                FFmpegRenderer.get_ffprobe_path(),
                "-v", "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,sample_rate,channels",
                "-of", "json",
                source_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or "").strip() or "تعذر تحليل ملف الوسائط عبر FFprobe.")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("أعاد FFprobe بيانات وسائط غير صالحة.") from exc

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
    def _render_configured(
        source_path: str,
        segments: Sequence[Tuple[float, float]],
        output_path: str,
        settings: ExportSettings,
        progress_callback=None,
        cancel_event=None,
        overwrite: bool = False,
    ) -> Tuple[bool, str]:
        source_path = os.path.abspath(source_path)
        output_path = os.path.abspath(output_path)
        if not os.path.isfile(source_path):
            return False, "ملف الفيديو الأصلي غير موجود."
        if os.path.normcase(source_path) == os.path.normcase(output_path):
            return False, "لا يمكن استبدال ملف المصدر بملف التصدير."
        if not overwrite and os.path.exists(output_path):
            return False, "يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر."

        try:
            settings.validate()
            extension = os.path.splitext(output_path)[1].lower().lstrip(".")
            if extension != settings.format:
                raise ValueError("امتداد الملف لا يطابق صيغة التصدير المختارة.")
            valid_segments = []
            for start, end in segments:
                start = float(start)
                end = float(end)
                if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                    raise ValueError("توقيت أحد المقاطع غير صالح.")
                valid_segments.append((start, end))
            if not valid_segments:
                raise ValueError("لا توجد مقاطع صالحة للتصدير.")

            source_data = FFmpegRenderer.probe_media(source_path)
            source_streams = source_data.get("streams", [])
            video_stream = next(
                (stream for stream in source_streams if stream.get("codec_type") == "video"),
                None,
            )
            if video_stream is None:
                raise ValueError("ملف المصدر لا يحتوي على مسار فيديو.")
            source_width = int(video_stream.get("width") or 0)
            source_height = int(video_stream.get("height") or 0)
            if source_width < 1 or source_height < 1:
                raise ValueError("تعذر تحديد أبعاد الفيديو الأصلي.")
            source_audio = next(
                (stream for stream in source_streams if stream.get("codec_type") == "audio"),
                None,
            )
            include_audio = settings.include_audio and source_audio is not None
            source_fps = _parse_fraction(
                video_stream.get("avg_frame_rate")
                or video_stream.get("r_frame_rate")
                or "30/1",
                Fraction(30, 1),
            )
            output_width, output_height = FFmpegRenderer._output_dimensions(
                settings,
                source_width,
                source_height,
            )
            source_duration = sum(end - start for start, end in valid_segments)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            output_extension = "." + settings.format
            temp_output = os.path.join(
                os.path.dirname(output_path),
                f".{os.path.basename(output_path)}.{os.getpid()}.{threading.get_ident()}.tmp{output_extension}",
            )
            ffmpeg = FFmpegRenderer.get_ffmpeg_path()
            filter_script_path = temp_output + ".filter.txt"
            capabilities = detect_ffmpeg_capabilities()
            if settings.format not in capabilities.get("formats", []):
                raise ValueError(f"صيغة {settings.format.upper()} غير مدعومة في نسخة FFmpeg الحالية.")
            if settings.video_codec not in capabilities.get("video_codecs", []):
                raise ValueError(f"ترميز الفيديو {settings.video_codec} غير متاح في نسخة FFmpeg الحالية.")
            if include_audio:
                audio_encoder = AUDIO_ENCODERS[settings.audio_codec]
                if audio_encoder not in capabilities.get("audio_encoders", []):
                    raise ValueError(f"ترميز الصوت {settings.audio_codec} غير متاح في نسخة FFmpeg الحالية.")
            encoder, _hardware_name = FFmpegRenderer._select_video_encoder(
                settings,
                capabilities,
            )
            command = FFmpegRenderer._build_configured_command(
                ffmpeg=ffmpeg,
                source_path=source_path,
                output_path=temp_output,
                segments=valid_segments,
                settings=settings,
                source_width=source_width,
                source_height=source_height,
                include_audio=include_audio,
                output_width=output_width,
                output_height=output_height,
                encoder=encoder,
                source_fps=source_fps,
                filter_script_path=filter_script_path,
            )

            try:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                FFmpegRenderer._run_with_progress(
                    command,
                    source_duration,
                    progress_callback,
                    cancel_event,
                )
                if cancel_event is not None and cancel_event.is_set():
                    raise ExportCancelledError("تم إلغاء التصدير.")
                FFmpegRenderer._verify_output(
                    temp_output,
                    expected_duration=source_duration,
                    require_audio=include_audio,
                    expected_width=output_width,
                    expected_height=output_height,
                )
                FFmpegRenderer._commit_output(temp_output, output_path, overwrite)
                if progress_callback:
                    progress_callback({
                        "percent": 99,
                        "stage": "finalizing",
                        "output_size_bytes": os.path.getsize(output_path),
                    })
                return True, "تم تصدير الفيديو والتحقق من صلاحيته بنجاح."
            finally:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                if os.path.exists(filter_script_path):
                    try:
                        os.remove(filter_script_path)
                    except OSError:
                        pass
        except ExportCancelledError as exc:
            return False, str(exc)
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    @staticmethod
    def _output_dimensions(settings: ExportSettings, source_width: int, source_height: int):
        source_ratio = source_width / source_height
        ratio = source_ratio
        if settings.aspect_ratio != "original" and settings.aspect_ratio != "custom":
            numerator, denominator = settings.aspect_ratio.split(":")
            ratio = float(numerator) / float(denominator)
        elif settings.aspect_ratio == "custom" and settings.width and settings.height:
            ratio = settings.width / settings.height

        if settings.resolution == "custom":
            width = settings.width
            height = settings.height
            if settings.lock_aspect_ratio:
                height = _even_dimension(width / ratio)
            elif settings.aspect_ratio != "original" and settings.aspect_ratio != "custom":
                width = _even_dimension(height * ratio)
        elif settings.resolution in RESOLUTION_HEIGHTS:
            height = RESOLUTION_HEIGHTS[settings.resolution]
            width = _even_dimension(height * ratio)
        elif settings.aspect_ratio not in ("original", "custom"):
            height = _even_dimension(source_height)
            width = _even_dimension(height * ratio)
        else:
            return None, None

        return _even_dimension(width), _even_dimension(height)

    @staticmethod
    def _select_video_encoder(settings: ExportSettings, capabilities: dict):
        available_encoders = set(capabilities.get("video_encoders", []))
        encoders = VIDEO_ENCODERS[settings.video_codec]
        software_encoder = encoders[0]
        requested = settings.hardware_acceleration
        if requested != "software":
            for encoder in encoders[1:]:
                suffix = encoder.rsplit("_", 1)[-1]
                if requested not in ("auto", suffix):
                    continue
                supported = capabilities.get("hardware", {}).get(settings.video_codec, [])
                if suffix in supported:
                    return encoder, suffix
                if requested == suffix:
                    raise RuntimeError(
                        f"مُرمّز {suffix.upper()} غير متاح فعليًا لهذا الترميز. اختر Software Encoding."
                    )
        if requested not in ("auto", "software"):
            raise RuntimeError(
                f"تسريع {requested.upper()} غير متاح فعليًا مع ترميز {settings.video_codec}."
            )
        if software_encoder not in available_encoders:
            raise RuntimeError(f"مُرمّز الفيديو {software_encoder} غير موجود في FFmpeg.")
        return software_encoder, "software"

    @staticmethod
    def _build_configured_command(
        ffmpeg: str,
        source_path: str,
        output_path: str,
        segments: Sequence[Tuple[float, float]],
        settings: ExportSettings,
        source_width: int,
        source_height: int,
        include_audio: bool,
        output_width: Optional[int],
        output_height: Optional[int],
        encoder: str,
        source_fps: Fraction,
        filter_script_path: Optional[str] = None,
    ) -> list:
        video_filters = []
        audio_filters = []
        for index, (start, end) in enumerate(segments):
            video_filter = (
                f"[0:v:0]trim=start={start:.6f}:end={end:.6f},"
                "setpts=PTS-STARTPTS"
            )
            if settings.fps_ratio:
                video_filter += f",fps={settings.fps_ratio[0]}/{settings.fps_ratio[1]}"
            if output_width and output_height:
                video_filter += "," + FFmpegRenderer._aspect_filter(
                    settings,
                    source_width,
                    source_height,
                    output_width,
                    output_height,
                )
                video_filter += ",setsar=1"
            video_filter += ",format=yuv420p"
            video_filters.append(f"{video_filter}[v{index}]")
            if include_audio:
                # fade قصير على حدود المقطع يمنع "التكّات" عند نقط القص
                fade_len = min(0.010, (end - start) / 4)
                audio_filter = (
                    f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},"
                    "asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={fade_len:.4f},"
                    f"afade=t=out:st={max(0.0, (end - start) - fade_len):.6f}:d={fade_len:.4f}"
                )
                audio_filters.append(f"{audio_filter}[a{index}]")

        video_inputs = "".join(f"[v{index}]" for index in range(len(segments)))
        filter_parts = video_filters + audio_filters
        filter_parts.append(
            f"{video_inputs}concat=n={len(segments)}:v=1:a=0[vout]"
        )
        audio_output = None
        if include_audio:
            audio_inputs = "".join(f"[a{index}]" for index in range(len(segments)))
            filter_parts.append(
                f"{audio_inputs}concat=n={len(segments)}:v=0:a=1[aout]"
            )
            audio_output = "aout"
            if settings.normalize_audio:
                filter_parts.append("[aout]loudnorm=I=-16:LRA=11:TP=-1.5[anorm]")
                audio_output = "anorm"

        filter_graph = ";".join(filter_parts)
        if filter_script_path:
            # الفلتر الطويل في ملف بدل سطر الأوامر (حد Windows ~32K حرف)
            with open(filter_script_path, "w", encoding="utf-8") as handle:
                handle.write(filter_graph)
            filter_args = [FFmpegRenderer._filter_script_option(ffmpeg), filter_script_path]
        else:
            filter_args = ["-filter_complex", filter_graph]

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-nostats",
            "-stats_period",
            "0.25",
            "-progress",
            "pipe:1",
            "-i",
            source_path,
            *filter_args,
            "-map",
            "[vout]",
        ]
        if audio_output:
            command.extend(["-map", f"[{audio_output}]"])
        command.extend(["-map_metadata", "0" if settings.include_metadata else "-1"])
        command.extend(["-c:v", encoder])
        command.extend(
            FFmpegRenderer._video_encoder_options(
                settings,
                encoder,
                source_fps,
                output_width or source_width,
                output_height or source_height,
            )
        )
        if include_audio:
            command.extend(["-c:a", AUDIO_ENCODERS[settings.audio_codec]])
            if settings.audio_codec != "pcm":
                command.extend(["-b:a", f"{settings.audio_bitrate_kbps}k"])
            sample_rate = settings.sample_rate
            if sample_rate != "match_source":
                command.extend(["-ar", sample_rate])
            if settings.channels != "match_source":
                command.extend(["-ac", "1" if settings.channels == "mono" else "2"])
        if settings.format in ("mp4", "mov"):
            command.extend(["-movflags", "+faststart"])
        command.append(output_path)
        return command

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _filter_script_option(ffmpeg: str) -> str:
        """يختار طريقة تمرير الفلتر من ملف: `-/filter_complex` (FFmpeg 7+) أو
        `-filter_complex_script` (الأقدم)، بتجربة فعلية مرة واحدة."""
        script_path = None
        try:
            descriptor, script_path = tempfile.mkstemp(prefix="auto_silence_probe_", suffix=".txt")
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("color=c=black:s=16x16:d=0.1[out]")
            result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-v", "error",
                    "-/filter_complex", script_path,
                    "-map", "[out]", "-f", "null", "-",
                ],
                capture_output=True,
                timeout=20,
            )
            if result.returncode == 0:
                return "-/filter_complex"
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.remove(script_path)
                except OSError:
                    pass
        return "-filter_complex_script"

    @staticmethod
    def _aspect_filter(
        settings: ExportSettings,
        source_width: int,
        source_height: int,
        output_width: int,
        output_height: int,
    ) -> str:
        if settings.aspect_mode == "fit":
            return (
                f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease:"
                "force_divisible_by=2,"
                f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2"
            )
        if settings.aspect_mode == "fill":
            return (
                f"scale={output_width}:{output_height}:force_original_aspect_ratio=increase:"
                "force_divisible_by=2,"
                f"crop={output_width}:{output_height}"
            )

        source_ratio = source_width / source_height
        target_ratio = output_width / output_height
        if source_ratio > target_ratio:
            crop_width = _even_dimension(source_height * target_ratio)
            x = max(0, (source_width - crop_width) // 2)
            return f"crop={crop_width}:{source_height}:{x}:0,scale={output_width}:{output_height}"
        crop_height = _even_dimension(source_width / target_ratio)
        y = max(0, (source_height - crop_height) // 2)
        return f"crop={source_width}:{crop_height}:0:{y},scale={output_width}:{output_height}"

    @staticmethod
    def _video_encoder_options(
        settings: ExportSettings,
        encoder: str,
        source_fps: Fraction,
        width: int,
        height: int,
    ) -> list:
        fps = (
            settings.fps_ratio[0] / settings.fps_ratio[1]
            if settings.fps_ratio
            else float(source_fps)
        )
        estimated_kbps = estimated_video_bitrate_kbps(
            width,
            height,
            fps,
            settings.video_codec,
            settings.quality,
            settings.custom_crf,
        )
        is_hardware = encoder != VIDEO_ENCODERS[settings.video_codec][0]
        if settings.automatic_bitrate:
            target_kbps = estimated_kbps
        else:
            target_kbps = int(round(settings.target_bitrate_mbps * 1000))

        options = []
        if is_hardware:
            options.extend(["-b:v", f"{target_kbps}k"])
            if settings.rate_control == "cbr":
                options.extend([
                    "-minrate", f"{target_kbps}k",
                    "-maxrate", f"{target_kbps}k",
                    "-bufsize", f"{target_kbps * 2}k",
                ])
            else:
                max_kbps = (
                    int(round(settings.max_bitrate_mbps * 1000))
                    if settings.max_bitrate_mbps is not None
                    else int(target_kbps * 1.5)
                )
                options.extend(["-maxrate", f"{max_kbps}k", "-bufsize", f"{max_kbps * 2}k"])
            options.extend(FFmpegRenderer._hardware_preset_options(encoder, settings.encoder_preset))
            return options

        if settings.automatic_bitrate:
            if settings.quality == "custom":
                crf = settings.custom_crf
            else:
                crf = QUALITY_CRF[settings.video_codec][settings.quality]
            options.extend(["-crf", str(crf)])
            if settings.video_codec == "av1":
                options.extend(["-b:v", "0"])
            if encoder == "libaom-av1":
                options.extend(["-cpu-used", str(_av1_cpu_used(settings.encoder_preset))])
            else:
                options.extend(["-preset", settings.encoder_preset])
            return options

        options.extend(["-b:v", f"{target_kbps}k"])
        if settings.rate_control == "cbr":
            minimum = settings.min_bitrate_mbps or settings.target_bitrate_mbps
            maximum = settings.max_bitrate_mbps or settings.target_bitrate_mbps
            options.extend([
                "-minrate", f"{int(round(minimum * 1000))}k",
                "-maxrate", f"{int(round(maximum * 1000))}k",
                "-bufsize", f"{max(target_kbps, int(round(maximum * 1000))) * 2}k",
            ])
        elif settings.max_bitrate_mbps is not None:
            options.extend([
                "-maxrate", f"{int(round(settings.max_bitrate_mbps * 1000))}k",
                "-bufsize", f"{int(round(settings.max_bitrate_mbps * 2000))}k",
            ])
        options.extend(["-preset", settings.encoder_preset])
        return options

    @staticmethod
    def _hardware_preset_options(encoder: str, preset: str) -> list:
        speed_order = (
            "ultrafast", "superfast", "veryfast", "faster",
            "fast", "medium", "slow", "slower",
        )
        index = speed_order.index(preset)
        if encoder.endswith("_nvenc"):
            return ["-preset", f"p{min(7, index + 1)}"]
        if encoder.endswith("_qsv"):
            qsv_presets = ("veryfast", "veryfast", "faster", "faster", "fast", "medium", "slow", "slower")
            return ["-preset", qsv_presets[index]]
        if encoder.endswith("_amf"):
            quality = "speed" if index < 3 else "balanced" if index < 6 else "quality"
            return ["-quality", quality]
        return []

    @staticmethod
    def _run_with_progress(command, duration, progress_callback=None, cancel_event=None):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        progress_lines = queue.Queue()
        stderr_lines = deque(maxlen=400)

        def read_progress():
            try:
                for line in process.stdout:
                    progress_lines.put(line)
            finally:
                progress_lines.put(None)

        def read_errors():
            for line in process.stderr:
                stderr_lines.append(line.rstrip())

        stdout_thread = threading.Thread(target=read_progress, daemon=True)
        stderr_thread = threading.Thread(target=read_errors, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        values = {}
        eof = False
        try:
            while not eof:
                if cancel_event is not None and cancel_event.is_set():
                    FFmpegRenderer._terminate_process(process)
                    raise ExportCancelledError("تم إلغاء التصدير.")
                try:
                    line = progress_lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    eof = True
                    continue
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                values[key] = value
                if key == "progress" and progress_callback:
                    try:
                        out_time_us = int(values.get("out_time_us") or values.get("out_time_ms") or 0)
                    except ValueError:
                        out_time_us = 0
                    elapsed = max(0.0, out_time_us / 1_000_000)
                    percent = min(99, max(0, int(elapsed * 100 / duration))) if duration else 0
                    progress_callback({
                        "percent": percent,
                        "stage": "encoding",
                        "out_time_seconds": elapsed,
                        "fps": values.get("fps"),
                        "speed": values.get("speed"),
                    })
            return_code = process.wait()
        finally:
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            if process.poll() is None:
                FFmpegRenderer._terminate_process(process)
        if return_code:
            detail = "\n".join(stderr_lines).strip()[-2000:]
            raise RuntimeError(detail or f"فشل FFmpeg برمز الخروج {return_code}.")

    @staticmethod
    def _terminate_process(process):
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    @staticmethod
    def _verify_output(
        output_path: str,
        expected_duration: float,
        require_audio: bool,
        expected_width: Optional[int],
        expected_height: Optional[int],
    ):
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("لم ينتج FFmpeg ملف فيديو صالحًا.")
        probe = FFmpegRenderer.probe_media(output_path)
        streams = probe.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if video is None:
            raise RuntimeError("الملف الناتج لا يحتوي على مسار فيديو.")
        if require_audio and not any(stream.get("codec_type") == "audio" for stream in streams):
            raise RuntimeError("الملف الناتج يفتقد مسار الصوت المطلوب.")
        if expected_width and expected_height:
            if int(video.get("width") or 0) != expected_width or int(video.get("height") or 0) != expected_height:
                raise RuntimeError("أبعاد الملف الناتج لا تطابق إعدادات التصدير.")
        try:
            actual_duration = float(probe.get("format", {}).get("duration") or 0)
        except (TypeError, ValueError):
            actual_duration = 0
        if actual_duration <= 0 or abs(actual_duration - expected_duration) > max(0.75, expected_duration * 0.03):
            raise RuntimeError("مدة الملف الناتج لا تطابق مدة المقاطع المطلوبة.")

    @staticmethod
    def _commit_output(temp_path: str, output_path: str, overwrite: bool):
        if overwrite:
            os.replace(temp_path, output_path)
            return
        try:
            os.link(temp_path, output_path)
        except FileExistsError as exc:
            raise FileExistsError("يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر.") from exc
        except OSError as exc:
            # hard links غير مدعومة (exFAT / FAT32 / بعض الأقراص الشبكية): إعادة تسمية
            if os.path.exists(output_path):
                raise FileExistsError("يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر.") from exc
            try:
                os.rename(temp_path, output_path)
            except FileExistsError as exists_exc:
                raise FileExistsError("يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر.") from exists_exc
            except OSError as rename_exc:
                raise OSError("تعذر إنشاء الملف النهائي دون استبدال ملف موجود.") from rename_exc
            return
        os.remove(temp_path)

    @staticmethod
    def render_segments(
        source_path: str,
        segments: Sequence[Tuple[float, float]],
        output_path: str,
        resolution: str = "الدقة الأصلية الكاملة",
        fast_copy: bool = True,
        include_audio: Optional[bool] = None,
        settings: Optional[ExportSettings] = None,
        progress_callback=None,
        cancel_event=None,
        overwrite: bool = False,
    ) -> Tuple[bool, str]:
        if settings is not None:
            return FFmpegRenderer._render_configured(
                source_path,
                segments,
                output_path,
                settings,
                progress_callback,
                cancel_event,
                overwrite,
            )
        source_path = os.path.abspath(source_path)
        if not os.path.isfile(source_path):
            return False, "ملف الفيديو الأصلي غير موجود."
        output_path = os.path.abspath(output_path)
        if os.path.normcase(source_path) == os.path.normcase(output_path):
            return False, "لا يمكن استبدال ملف المصدر بملف التصدير."
        if not overwrite and os.path.exists(output_path):
            return False, "يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر."

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

            FFmpegRenderer._commit_output(temp_output, output_path, overwrite)
            return True, "تم تصدير الفيديو الممنتج بنجاح."
        except Exception as exc:
            return False, str(exc)
        finally:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except OSError:
                    pass
