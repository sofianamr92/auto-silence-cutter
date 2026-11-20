# -*- coding: utf-8 -*-
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
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw_data, stderr_data = proc.communicate()
        if proc.returncode != 0:
            error = stderr_data.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or "تعذر فك ترميز ملف الصوت عبر FFmpeg")
        audio = np.frombuffer(raw_data, dtype=np.float32)
        duration = len(audio) / sample_rate if sample_rate > 0 else 0.0
        return audio, duration

    def analyze(self, filepath: str) -> List[DetectedRegion]:
        sample_rate = 16000
        audio, total_duration = self.extract_pcm_float(filepath, sample_rate)
        return self.analyze_audio(audio, sample_rate, total_duration)

    def analyze_audio(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        total_duration: float = 0.0,
    ) -> List[DetectedRegion]:
        if len(audio) == 0:
            return []
        if sample_rate <= 0:
            raise ValueError("Audio sample rate must be positive.")
        if total_duration <= 0:
            total_duration = len(audio) / sample_rate

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
            t = i * hop_size / sample_rate
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
