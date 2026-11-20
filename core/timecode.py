import math


def format_timecode(seconds, fps=30):
    seconds = float(seconds)
    fps = float(fps)
    if not math.isfinite(seconds):
        raise ValueError("Timecode seconds must be finite.")
    if not math.isfinite(fps) or fps <= 0:
        fps = 30

    frame_rate = max(1, int(round(fps)))
    total_frames = int(round(max(0.0, seconds) * frame_rate))
    hours, remainder = divmod(total_frames, frame_rate * 3600)
    minutes, remainder = divmod(remainder, frame_rate * 60)
    whole_seconds, frames = divmod(remainder, frame_rate)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}:{frames:02d}"
