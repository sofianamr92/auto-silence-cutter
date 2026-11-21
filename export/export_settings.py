from dataclasses import asdict, dataclass
from functools import lru_cache
import math
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple


FORMAT_VIDEO_CODECS = {
    "mp4": ("h264", "hevc", "av1"),
    "mov": ("h264", "hevc"),
    "mkv": ("h264", "hevc", "av1"),
    "webm": ("av1",),
}

FORMAT_AUDIO_CODECS = {
    "mp4": ("aac", "mp3"),
    "mov": ("aac", "mp3", "pcm"),
    "mkv": ("aac", "mp3", "pcm", "opus", "vorbis"),
    "webm": ("opus", "vorbis"),
}

VIDEO_ENCODERS = {
    "h264": ("libx264", "h264_nvenc", "h264_qsv", "h264_amf"),
    "hevc": ("libx265", "hevc_nvenc", "hevc_qsv", "hevc_amf"),
    "av1": ("libaom-av1",),
}

AUDIO_ENCODERS = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "pcm": "pcm_s16le",
    "opus": "libopus",
    "vorbis": "libvorbis",
}

QUALITY_CRF = {
    "h264": {"lower": 30, "recommended": 23, "high": 20, "higher": 18, "maximum": 16},
    "hevc": {"lower": 33, "recommended": 28, "high": 25, "higher": 22, "maximum": 20},
    "av1": {"lower": 42, "recommended": 32, "high": 28, "higher": 24, "maximum": 20},
}

QUALITY_FACTORS = {
    "lower": 0.65,
    "recommended": 1.0,
    "high": 1.3,
    "higher": 1.6,
    "maximum": 2.0,
}

ENCODER_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
)

FPS_RATES = {
    "23.976": (24000, 1001),
    "24": (24, 1),
    "25": (25, 1),
    "29.97": (30000, 1001),
    "30": (30, 1),
    "50": (50, 1),
    "59.94": (60000, 1001),
    "60": (60, 1),
}

RESOLUTION_HEIGHTS = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
}


class ExportSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class ExportSettings:
    format: str = "mp4"
    resolution: str = "match_source"
    width: Optional[int] = None
    height: Optional[int] = None
    lock_aspect_ratio: bool = True
    frame_rate: str = "match_source"
    custom_fps: float = 30.0
    video_codec: str = "h264"
    quality: str = "recommended"
    custom_crf: int = 23
    automatic_bitrate: bool = True
    rate_control: str = "vbr"
    target_bitrate_mbps: float = 8.0
    min_bitrate_mbps: Optional[float] = None
    max_bitrate_mbps: Optional[float] = None
    encoder_preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate_kbps: int = 192
    sample_rate: str = "match_source"
    channels: str = "match_source"
    include_audio: bool = True
    normalize_audio: bool = False
    aspect_ratio: str = "original"
    aspect_mode: str = "fit"
    include_metadata: bool = True
    export_range: str = "entire_timeline"
    range_start_seconds: float = 0.0
    range_end_seconds: Optional[float] = None
    hardware_acceleration: str = "auto"

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "ExportSettings":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ExportSettingsError("إعدادات التصدير يجب أن تكون كائنًا.")
        known_fields = set(cls.__dataclass_fields__)
        unknown = set(payload) - known_fields
        if unknown:
            raise ExportSettingsError(
                "تحتوي إعدادات التصدير على حقول غير معروفة: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        try:
            settings = cls(**payload)
        except (TypeError, ValueError) as exc:
            raise ExportSettingsError(f"إعدادات التصدير غير صالحة: {exc}") from exc
        settings.validate()
        return settings

    def validate(self):
        if self.format not in FORMAT_VIDEO_CODECS:
            raise ExportSettingsError("صيغة الفيديو غير مدعومة.")
        if self.video_codec not in FORMAT_VIDEO_CODECS[self.format]:
            raise ExportSettingsError("ترميز الفيديو غير متوافق مع صيغة الملف المختارة.")
        if self.audio_codec not in FORMAT_AUDIO_CODECS[self.format]:
            raise ExportSettingsError("ترميز الصوت غير متوافق مع صيغة الملف المختارة.")
        if self.resolution not in (*RESOLUTION_HEIGHTS, "match_source", "custom"):
            raise ExportSettingsError("دقة الفيديو غير مدعومة.")
        if self.resolution == "custom":
            if (
                isinstance(self.width, bool)
                or isinstance(self.height, bool)
                or not isinstance(self.width, int)
                or not isinstance(self.height, int)
                or self.width < 64
                or self.height < 64
                or self.width > 16384
                or self.height > 16384
                or self.width % 2
                or self.height % 2
            ):
                raise ExportSettingsError("الدقة المخصصة يجب أن تكون أبعادًا زوجية بين 64 و16384.")
        if self.aspect_ratio == "custom" and self.resolution != "custom":
            raise ExportSettingsError("نسبة العرض المخصصة تتطلب دقة مخصصة.")
        if self.frame_rate not in (*FPS_RATES, "match_source", "custom"):
            raise ExportSettingsError("معدل الإطارات غير مدعوم.")
        if self.frame_rate == "custom" and (
            isinstance(self.custom_fps, bool)
            or not isinstance(self.custom_fps, (int, float))
            or not 1 <= self.custom_fps <= 240
        ):
            raise ExportSettingsError("معدل الإطارات المخصص يجب أن يكون بين 1 و240 FPS.")
        if self.quality not in (*QUALITY_FACTORS, "custom"):
            raise ExportSettingsError("مستوى جودة الفيديو غير مدعوم.")
        crf_max = 63 if self.video_codec == "av1" else 51
        if (
            isinstance(self.custom_crf, bool)
            or not isinstance(self.custom_crf, int)
            or not 0 <= self.custom_crf <= crf_max
        ):
            raise ExportSettingsError(f"قيمة CRF يجب أن تكون بين 0 و{crf_max}.")
        if self.rate_control not in ("vbr", "cbr"):
            raise ExportSettingsError("نمط التحكم في معدل البت غير مدعوم.")
        self._validate_bitrate(self.target_bitrate_mbps, "معدل البت المستهدف", required=not self.automatic_bitrate)
        self._validate_bitrate(self.min_bitrate_mbps, "الحد الأدنى لمعدل البت")
        self._validate_bitrate(self.max_bitrate_mbps, "الحد الأقصى لمعدل البت")
        if self.min_bitrate_mbps is not None and self.max_bitrate_mbps is not None:
            if self.min_bitrate_mbps > self.max_bitrate_mbps:
                raise ExportSettingsError("الحد الأدنى لمعدل البت أكبر من الحد الأقصى.")
        if not self.automatic_bitrate:
            target = self.target_bitrate_mbps
            if self.min_bitrate_mbps is not None and self.min_bitrate_mbps > target:
                raise ExportSettingsError("الحد الأدنى لمعدل البت أكبر من المعدل المستهدف.")
            if self.max_bitrate_mbps is not None and self.max_bitrate_mbps < target:
                raise ExportSettingsError("الحد الأقصى لمعدل البت أقل من المعدل المستهدف.")
        if self.encoder_preset not in ENCODER_PRESETS:
            raise ExportSettingsError("إعداد سرعة الترميز غير مدعوم.")
        if self.audio_bitrate_kbps not in (96, 128, 160, 192, 256, 320):
            raise ExportSettingsError("معدل بت الصوت غير مدعوم.")
        if self.sample_rate not in ("match_source", "44100", "48000"):
            raise ExportSettingsError("معدل أخذ عينات الصوت غير مدعوم.")
        if self.channels not in ("match_source", "mono", "stereo"):
            raise ExportSettingsError("عدد قنوات الصوت غير مدعوم.")
        if self.aspect_ratio not in ("original", "16:9", "9:16", "1:1", "4:3", "21:9", "custom"):
            raise ExportSettingsError("نسبة العرض إلى الارتفاع غير مدعومة.")
        if self.aspect_mode not in ("fit", "fill", "crop"):
            raise ExportSettingsError("طريقة ملاءمة الصورة غير مدعومة.")
        if self.export_range not in ("entire_timeline", "selected_range"):
            raise ExportSettingsError("نطاق التصدير غير مدعوم.")
        if self.export_range == "selected_range":
            start = self.range_start_seconds
            end = self.range_end_seconds
            if (
                isinstance(start, bool)
                or not isinstance(start, (int, float))
                or not math.isfinite(start)
                or start < 0
                or end is None
                or isinstance(end, bool)
                or not isinstance(end, (int, float))
                or not math.isfinite(end)
                or end <= start
            ):
                raise ExportSettingsError("نطاق التصدير المحدد غير صالح.")
        for field_name in (
            "lock_aspect_ratio",
            "automatic_bitrate",
            "include_audio",
            "normalize_audio",
            "include_metadata",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ExportSettingsError(f"قيمة {field_name} يجب أن تكون نعم أو لا.")
        if self.hardware_acceleration not in ("auto", "software", "nvenc", "qsv", "amf"):
            raise ExportSettingsError("خيار تسريع العتاد غير مدعوم.")
        if self.include_audio and self.audio_codec == "pcm" and self.format == "mp4":
            raise ExportSettingsError("PCM غير مدعوم داخل حاوية MP4.")

    @staticmethod
    def _validate_bitrate(value, label: str, required: bool = False):
        if value is None:
            if required:
                raise ExportSettingsError(f"{label} مطلوب.")
            return
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.1 <= value <= 200
        ):
            raise ExportSettingsError(f"{label} يجب أن يكون بين 0.1 و200 Mbps.")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def fps_ratio(self) -> Optional[Tuple[int, int]]:
        if self.frame_rate == "match_source":
            return None
        if self.frame_rate == "custom":
            numerator = round(float(self.custom_fps) * 1000)
            denominator = 1000
            divisor = _gcd(numerator, denominator)
            return numerator // divisor, denominator // divisor
        return FPS_RATES[self.frame_rate]


def _gcd(first: int, second: int) -> int:
    while second:
        first, second = second, first % second
    return max(1, first)


def estimated_video_bitrate_kbps(
    width: int,
    height: int,
    fps: float,
    codec: str,
    quality: str,
    custom_crf: int = 23,
) -> int:
    if quality == "custom":
        codec_factor = {"h264": 1.0, "hevc": 0.58, "av1": 0.48}.get(codec)
        if codec_factor is None:
            raise ExportSettingsError("لا يمكن تقدير معدل بت لهذا الترميز.")
        baseline = max(
            300,
            width * height * max(1.0, fps) * 0.07 * codec_factor / 1000,
        )
        reference_crf = QUALITY_CRF.get(codec, {}).get("recommended", 23)
        return int(min(200_000, baseline * (2 ** ((reference_crf - custom_crf) / 6))))
    codec_factor = {"h264": 1.0, "hevc": 0.58, "av1": 0.48}.get(codec)
    if codec_factor is None:
        raise ExportSettingsError("لا يمكن تقدير معدل بت لهذا الترميز.")
    factor = QUALITY_FACTORS.get(quality)
    if factor is None:
        raise ExportSettingsError("لا يمكن تقدير معدل بت لمستوى الجودة.")
    pixels_per_second = max(64, width) * max(64, height) * max(1.0, fps)
    return int(max(300, min(200_000, pixels_per_second * 0.07 * codec_factor * factor / 1000)))


def select_segments_for_range(segments: list, settings: ExportSettings) -> list:
    if settings.export_range == "entire_timeline":
        return list(segments)
    timeline_duration = sum(item["end"] - item["start"] for item in segments)
    start = float(settings.range_start_seconds)
    end = float(settings.range_end_seconds)
    if end > timeline_duration + 0.000001:
        raise ExportSettingsError("نهاية النطاق المحدد تتجاوز مدة الخط الزمني.")
    if start >= timeline_duration:
        raise ExportSettingsError("بداية النطاق المحدد تتجاوز مدة الخط الزمني.")

    selected = []
    timeline_cursor = 0.0
    for segment in segments:
        segment_duration = segment["end"] - segment["start"]
        overlap_start = max(start, timeline_cursor)
        overlap_end = min(end, timeline_cursor + segment_duration)
        if overlap_end > overlap_start:
            source_start = segment["start"] + overlap_start - timeline_cursor
            source_end = segment["start"] + overlap_end - timeline_cursor
            selected.append({
                **segment,
                "start": source_start,
                "end": source_end,
            })
        timeline_cursor += segment_duration
        if timeline_cursor >= end:
            break
    if not selected:
        raise ExportSettingsError("لا توجد مقاطع داخل نطاق التصدير المحدد.")
    return selected


@lru_cache(maxsize=1)
def detect_ffmpeg_capabilities() -> dict:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"تعذر فحص مُرمّزات FFmpeg: {exc}") from exc
    if result.returncode:
        raise RuntimeError((result.stderr or "").strip() or "تعذر قراءة قائمة مُرمّزات FFmpeg.")
    available = {
        line.split()[1]
        for line in result.stdout.splitlines()
        if len(line.split()) >= 2 and line.split()[0].startswith(("V", "A"))
    }

    video_codecs: List[str] = []
    audio_codecs: List[str] = []
    for codec, encoders in VIDEO_ENCODERS.items():
        if any(encoder in available for encoder in encoders):
            video_codecs.append(codec)
    for codec, encoder in AUDIO_ENCODERS.items():
        if encoder in available:
            audio_codecs.append(codec)

    try:
        muxer_result = subprocess.run(
            [ffmpeg, "-hide_banner", "-muxers"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"تعذر فحص صيغ الإخراج في FFmpeg: {exc}") from exc
    muxers = set()
    for line in muxer_result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "E" in parts[0]:
            muxers.add(parts[1])

    hardware: Dict[str, List[str]] = {"h264": [], "hevc": [], "av1": []}
    for codec, encoders in VIDEO_ENCODERS.items():
        for encoder in encoders[1:]:
            if encoder not in available:
                continue
            if _test_hardware_encoder(ffmpeg, encoder):
                hardware[codec].append(encoder.rsplit("_", 1)[-1])

    return {
        "formats": [
            format_name
            for format_name, muxer in (
                ("mp4", "mp4"),
                ("mov", "mov"),
                ("mkv", "matroska"),
                ("webm", "webm"),
            )
            if muxer in muxers
        ],
        "video_codecs": video_codecs,
        "audio_encoders": sorted(
            encoder for encoder in available if encoder in set(AUDIO_ENCODERS.values())
        ),
        "audio_codecs": audio_codecs,
        "video_encoders": sorted(encoder for encoder in available if encoder in {
            encoder for encoders in VIDEO_ENCODERS.values() for encoder in encoders
        }),
        "hardware": hardware,
    }


def _test_hardware_encoder(ffmpeg: str, encoder: str) -> bool:
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x64:r=1:d=1",
                "-frames:v",
                "1",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
