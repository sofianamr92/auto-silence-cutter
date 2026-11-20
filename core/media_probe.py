# -*- coding: utf-8 -*-
import json
import subprocess
import os
import shutil
import bisect
import hashlib
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional
from core.editing_engine import MediaInfo


class FrameIndexMapper:
    """Maps source presentation timestamps to frame indices without assuming CFR."""

    def __init__(
        self,
        timestamps_us=None,
        fps_num: int = 0,
        fps_den: int = 1,
        duration_us: int = 0,
    ):
        timestamps = list(timestamps_us or [])
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in timestamps):
            raise ValueError("Frame timestamps must be non-negative integer microseconds.")
        if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("Frame timestamps must be strictly increasing.")
        self.timestamps_us = tuple(timestamps)
        if fps_num <= 0 or fps_den <= 0:
            if timestamps:
                fps_num, fps_den = 30, 1
            else:
                raise ValueError("Frame rate must be a positive rational value.")
        self.fps_num = int(fps_num)
        self.fps_den = int(fps_den)
        self.duration_us = max(0, int(duration_us))
        self.source_path = ""

    @classmethod
    def from_frame_rate(cls, fps_num: int, fps_den: int, duration_us: int):
        return cls(fps_num=fps_num, fps_den=fps_den, duration_us=duration_us)

    @classmethod
    def from_timestamps(cls, timestamps_us, fps_num: int = 30, fps_den: int = 1, duration_us: int = 0):
        return cls(
            timestamps_us=timestamps_us,
            fps_num=fps_num,
            fps_den=fps_den,
            duration_us=duration_us,
        )

    @classmethod
    def from_media(cls, filepath: str, fps_num: int = 30, fps_den: int = 1, duration_us: int = 0):
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Video source does not exist: {filepath}")
        stat = os.stat(filepath)
        fingerprint = hashlib.sha256(
            f"{os.path.normcase(os.path.abspath(filepath))}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()
        cache_root = os.path.join(
            os.getenv("LOCALAPPDATA") or tempfile.gettempdir(),
            "AutoSilenceCutter",
            "frame-index",
        )
        cache_path = os.path.join(cache_root, f"{fingerprint}.json")
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                cached = json.load(cache_file)
            timestamps = cached.get("timestamps_us")
            if not isinstance(timestamps, list):
                raise ValueError(f"Invalid frame-index cache: {cache_path}")
            mapper = cls.from_timestamps(timestamps, fps_num, fps_den, duration_us)
            mapper.source_path = os.path.abspath(filepath)
            return mapper

        ffprobe_bin = MediaProbe.get_ffprobe_path()
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_frames",
                "-show_entries", "frame=best_effort_timestamp_time",
                "-of", "csv=p=0",
                filepath,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "").strip() or "FFprobe could not index video frames.")
        timestamps = []
        for line in result.stdout.splitlines():
            value = line.strip()
            if not value or value == "N/A":
                continue
            try:
                timestamp = int(
                    (Decimal(value) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP)
                )
            except InvalidOperation as exc:
                raise ValueError(f"Invalid frame presentation timestamp: {value}") from exc
            if timestamp >= 0 and (not timestamps or timestamp > timestamps[-1]):
                timestamps.append(timestamp)
        if not timestamps:
            raise RuntimeError("FFprobe did not return frame presentation timestamps.")

        os.makedirs(cache_root, exist_ok=True)
        temp_path = cache_path + f".{os.getpid()}.tmp"
        with open(temp_path, "w", encoding="utf-8") as cache_file:
            json.dump({"timestamps_us": timestamps}, cache_file)
        os.replace(temp_path, cache_path)
        if not duration_us:
            duration_us = timestamps[-1]
        mapper = cls.from_timestamps(timestamps, fps_num, fps_den, duration_us)
        mapper.source_path = os.path.abspath(filepath)
        return mapper

    @property
    def is_variable_frame_rate(self) -> bool:
        if len(self.timestamps_us) < 3:
            return False
        expected_scaled = 1_000_000 * self.fps_den
        return any(
            abs((right - left) * self.fps_num - expected_scaled) > self.fps_num
            for left, right in zip(self.timestamps_us, self.timestamps_us[1:])
        )

    @property
    def frame_count(self) -> int:
        if self.timestamps_us:
            return len(self.timestamps_us)
        if not self.duration_us:
            return 0
        numerator = self.duration_us * self.fps_num
        denominator = 1_000_000 * self.fps_den
        return (numerator + denominator - 1) // denominator

    def frame_time_us(self, frame_index: int) -> int:
        if frame_index < 0 or frame_index >= self.frame_count:
            raise IndexError("Frame index is outside the source.")
        if self.timestamps_us:
            return self.timestamps_us[frame_index]
        return frame_index * 1_000_000 * self.fps_den // self.fps_num

    def nearest_frame_index(self, time_us: int) -> int:
        if self.frame_count == 0:
            raise ValueError("Frame index is empty.")
        if self.timestamps_us:
            index = bisect.bisect_left(self.timestamps_us, max(0, time_us))
            if index == 0:
                return 0
            if index == len(self.timestamps_us):
                return index - 1
            before = self.timestamps_us[index - 1]
            after = self.timestamps_us[index]
            return index - 1 if time_us - before <= after - time_us else index
        numerator = max(0, time_us) * self.fps_num
        denominator = 1_000_000 * self.fps_den
        return min(self.frame_count - 1, (numerator + denominator // 2) // denominator)

    def nearest_frame_time_us(self, time_us: int) -> int:
        return self.frame_time_us(self.nearest_frame_index(time_us))

    def previous_frame_time_us(self, time_us: int) -> int:
        if self.frame_count == 0:
            raise ValueError("Frame index is empty.")
        if self.timestamps_us:
            index = bisect.bisect_left(self.timestamps_us, time_us) - 1
            return self.timestamps_us[max(0, index)]
        numerator = max(0, time_us) * self.fps_num
        denominator = 1_000_000 * self.fps_den
        index = (numerator + denominator - 1) // denominator - 1
        return self.frame_time_us(max(0, min(self.frame_count - 1, index)))

    def next_frame_time_us(self, time_us: int) -> int:
        if self.frame_count == 0:
            raise ValueError("Frame index is empty.")
        if self.timestamps_us:
            index = bisect.bisect_right(self.timestamps_us, time_us)
            return self.timestamps_us[min(index, len(self.timestamps_us) - 1)]
        numerator = (max(0, time_us) + 1) * self.fps_num
        denominator = 1_000_000 * self.fps_den
        index = (numerator + denominator - 1) // denominator
        return self.frame_time_us(min(self.frame_count - 1, index))

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
                    rate = (
                        MediaProbe._parse_frame_rate(stream.get("avg_frame_rate"))
                        or MediaProbe._parse_frame_rate(stream.get("r_frame_rate"))
                        or (30, 1)
                    )
                    info.fps_num, info.fps_den = rate
                    info.fps = info.fps_num / info.fps_den
                elif codec_type == "audio" and not info.audio_codec:
                    info.audio_codec = stream.get("codec_name", "")
                    info.audio_sample_rate = int(stream.get("sample_rate", 44100))
                    info.audio_channels = int(stream.get("channels", 2))

            return info
        except Exception as ex:
            print(f"[!] MediaProbe Error: {ex}")
            return None

    @staticmethod
    def _parse_frame_rate(value):
        try:
            if isinstance(value, str) and "/" in value:
                numerator, denominator = value.split("/", 1)
                numerator, denominator = int(numerator), int(denominator)
                if numerator > 0 and denominator > 0:
                    return numerator, denominator
            elif value:
                rate = Decimal(str(value))
                if rate > 0:
                    return int(rate * 1000), 1000
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            return None
        return None
