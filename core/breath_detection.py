# -*- coding: utf-8 -*-
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
