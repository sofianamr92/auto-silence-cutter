# -*- coding: utf-8 -*-
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
                        try:
                            info.fps = float(num) / max(1.0, float(den))
                        except (TypeError, ValueError, ZeroDivisionError):
                            info.fps = 30.0
                    else:
                        try:
                            info.fps = float(r_frame_rate)
                        except (TypeError, ValueError):
                            info.fps = 30.0
                elif codec_type == "audio" and not info.audio_codec:
                    info.audio_codec = stream.get("codec_name", "")
                    info.audio_sample_rate = int(stream.get("sample_rate", 44100))
                    info.audio_channels = int(stream.get("channels", 2))

            return info
        except Exception as ex:
            print(f"[!] MediaProbe Error: {ex}")
            return None
