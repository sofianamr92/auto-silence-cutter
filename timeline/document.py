# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from fractions import Fraction
import math
import os
import json
import tempfile
import uuid
from typing import Dict, List, Optional

MICROSECONDS_PER_SECOND = 1_000_000


def seconds_to_us(seconds: float) -> int:
    value = float(seconds)
    if not math.isfinite(value):
        raise ValueError("Timeline time must be finite.")
    return int(round(value * MICROSECONDS_PER_SECOND))


def _validate_time_us(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer number of microseconds.")
    return value


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
    fps_num: int = 0
    fps_den: int = 0

    def __post_init__(self):
        if self.fps_num <= 0 or self.fps_den <= 0:
            rate = Fraction(str(self.fps)).limit_denominator(100_000)
            self.fps_num = rate.numerator
            self.fps_den = rate.denominator
        if self.fps_num > 0 and self.fps_den > 0:
            self.fps = self.fps_num / self.fps_den

    @property
    def duration_us(self) -> int:
        return max(0, seconds_to_us(self.duration))


@dataclass(init=False)
class DetectedRegion:
    id: str
    region_type: str
    start_time_us: int
    end_time_us: int
    confidence: float
    approved_for_cut: bool
    reason: str

    def __init__(
        self,
        id: Optional[str] = None,
        region_type: str = "silence",
        start_time: float = 0.0,
        end_time: float = 0.0,
        confidence: float = 1.0,
        approved_for_cut: bool = True,
        reason: str = "",
        *,
        start_time_us: Optional[int] = None,
        end_time_us: Optional[int] = None,
    ):
        self.id = id or str(uuid.uuid4())[:8]
        self.region_type = region_type
        self.start_time_us = _validate_time_us(
            seconds_to_us(start_time) if start_time_us is None else start_time_us,
            "start_time_us",
        )
        self.end_time_us = _validate_time_us(
            seconds_to_us(end_time) if end_time_us is None else end_time_us,
            "end_time_us",
        )
        if self.end_time_us < self.start_time_us:
            raise ValueError("Detection end time must not precede its start time.")
        self.confidence = float(confidence)
        self.approved_for_cut = bool(approved_for_cut)
        self.reason = reason

    @property
    def start_time(self) -> float:
        return self.start_time_us / MICROSECONDS_PER_SECOND

    @start_time.setter
    def start_time(self, value: float):
        self.start_time_us = _validate_time_us(seconds_to_us(value), "start_time_us")

    @property
    def end_time(self) -> float:
        return self.end_time_us / MICROSECONDS_PER_SECOND

    @end_time.setter
    def end_time(self, value: float):
        self.end_time_us = _validate_time_us(seconds_to_us(value), "end_time_us")

    @property
    def duration(self) -> float:
        return max(0, self.end_time_us - self.start_time_us) / MICROSECONDS_PER_SECOND


@dataclass(init=False)
class ClipModel:
    id: str
    media_id: str
    track_id: str
    source_path: str
    source_in_us: int
    source_out_us: int
    timeline_start_us: int
    playback_rate_num: int
    playback_rate_den: int
    linked_group_id: Optional[str]
    is_cut: bool
    video_enabled: bool
    audio_enabled: bool
    origin_type: str

    def __init__(
        self,
        id: Optional[str] = None,
        source_path: str = "",
        source_in: float = 0.0,
        source_out: float = 0.0,
        timeline_in: float = 0.0,
        is_cut: bool = False,
        *,
        media_id: Optional[str] = None,
        track_id: str = "video",
        source_in_us: Optional[int] = None,
        source_out_us: Optional[int] = None,
        timeline_start_us: Optional[int] = None,
        playback_rate_num: int = 1,
        playback_rate_den: int = 1,
        linked_group_id: Optional[str] = None,
        video_enabled: bool = True,
        audio_enabled: bool = True,
        origin_type: str = "manual",
    ):
        self.id = id or str(uuid.uuid4())[:8]
        self.source_path = source_path
        self.media_id = media_id or self._media_id_for_path(source_path)
        self.track_id = track_id
        self.source_in_us = _validate_time_us(
            seconds_to_us(source_in) if source_in_us is None else source_in_us,
            "source_in_us",
        )
        self.source_out_us = _validate_time_us(
            seconds_to_us(source_out) if source_out_us is None else source_out_us,
            "source_out_us",
        )
        self.timeline_start_us = _validate_time_us(
            seconds_to_us(timeline_in) if timeline_start_us is None else timeline_start_us,
            "timeline_start_us",
        )
        if self.source_out_us < self.source_in_us:
            raise ValueError("Clip source out-point must not precede its in-point.")
        if playback_rate_num <= 0 or playback_rate_den <= 0:
            raise ValueError("Clip playback rate must be a positive rational value.")
        self.playback_rate_num = int(playback_rate_num)
        self.playback_rate_den = int(playback_rate_den)
        self.linked_group_id = linked_group_id
        self.is_cut = bool(is_cut)
        self.video_enabled = bool(video_enabled)
        self.audio_enabled = bool(audio_enabled)
        self.origin_type = origin_type

    @staticmethod
    def _media_id_for_path(path: str) -> str:
        if not path:
            return ""
        normalized = os.path.normcase(os.path.abspath(path))
        return str(uuid.uuid5(uuid.NAMESPACE_URL, normalized))

    @property
    def clip_id(self) -> str:
        return self.id

    @property
    def source_in(self) -> float:
        return self.source_in_us / MICROSECONDS_PER_SECOND

    @source_in.setter
    def source_in(self, value: float):
        self.source_in_us = _validate_time_us(seconds_to_us(value), "source_in_us")

    @property
    def source_out(self) -> float:
        return self.source_out_us / MICROSECONDS_PER_SECOND

    @source_out.setter
    def source_out(self, value: float):
        self.source_out_us = _validate_time_us(seconds_to_us(value), "source_out_us")

    @property
    def timeline_in(self) -> float:
        return self.timeline_start_us / MICROSECONDS_PER_SECOND

    @timeline_in.setter
    def timeline_in(self, value: float):
        self.timeline_start_us = _validate_time_us(seconds_to_us(value), "timeline_start_us")

    @property
    def source_duration_us(self) -> int:
        return max(0, self.source_out_us - self.source_in_us)

    @property
    def duration_us(self) -> int:
        return self.source_duration_us * self.playback_rate_den // self.playback_rate_num

    @property
    def duration(self) -> float:
        return self.duration_us / MICROSECONDS_PER_SECOND

    def source_time_at_timeline_us(self, timeline_time_us: int) -> int:
        offset_us = timeline_time_us - self.timeline_start_us
        return self.source_in_us + offset_us * self.playback_rate_num // self.playback_rate_den


@dataclass
class TrackModel:
    track_id: str
    name: str
    kind: str
    clip_ids: List[str] = field(default_factory=list)
    enabled: bool = True
    locked: bool = False


@dataclass
class MediaSource:
    media_id: str
    filepath: str
    duration_us: int
    width: int
    height: int
    fps_num: int
    fps_den: int


class TimelineDocument:
    """Canonical native-editor document; legacy second-valued properties are adapters."""

    schema_version = 1

    def __init__(self):
        self.project_id = str(uuid.uuid4())
        self.project_name = "مشروع جديد"
        self._media_info = MediaInfo()
        self.media_sources: Dict[str, MediaSource] = {}
        self.clips: List[ClipModel] = []
        self.detected_regions: List[DetectedRegion] = []
        self.waveform_peaks: List[float] = []
        self._current_playhead_us = 0
        self.is_modified = False
        self.zoom_level = 1.0
        self.horizontal_scroll_us = 0
        self.active_layout = "Standard Horizontal"
        self.selected_clip_ids: List[str] = []
        self.revision = 0
        self.tracks = [
            TrackModel("video", "Video", "video"),
            TrackModel("audio", "Audio", "audio"),
            TrackModel("markers", "Detection", "marker"),
        ]

    @property
    def media_info(self) -> MediaInfo:
        return self._media_info

    @media_info.setter
    def media_info(self, value: MediaInfo):
        self._media_info = value
        self.media_sources = {}
        if value.filepath:
            media_id = ClipModel._media_id_for_path(value.filepath)
            self.media_sources[media_id] = MediaSource(
                media_id=media_id,
                filepath=value.filepath,
                duration_us=value.duration_us,
                width=value.width,
                height=value.height,
                fps_num=value.fps_num,
                fps_den=value.fps_den,
            )

    @property
    def current_playhead_us(self) -> int:
        return self._current_playhead_us

    @current_playhead_us.setter
    def current_playhead_us(self, value: int):
        self._current_playhead_us = _validate_time_us(value, "current_playhead_us")

    @property
    def current_playhead(self) -> float:
        return self.current_playhead_us / MICROSECONDS_PER_SECOND

    @current_playhead.setter
    def current_playhead(self, value: float):
        self.current_playhead_us = _validate_time_us(seconds_to_us(value), "current_playhead_us")

    @property
    def zoom_factor(self) -> float:
        return self.zoom_level

    @zoom_factor.setter
    def zoom_factor(self, value: float):
        self.zoom_level = max(0.2, min(32.0, float(value)))

    def reset(self):
        self.__init__()

    def recalculate_timeline_positions(self):
        position_us = 0
        for clip in self.clips:
            if not clip.is_cut:
                clip.timeline_start_us = position_us
                position_us += clip.duration_us
        self._rebuild_track_clip_ids()
        self.current_playhead_us = min(self.current_playhead_us, self.total_edited_duration_us)
        self.revision += 1

    def _rebuild_track_clip_ids(self):
        tracks = {track.track_id: track for track in self.tracks}
        for track in self.tracks:
            track.clip_ids.clear()
        for clip in self.clips:
            if clip.is_cut:
                continue
            if clip.track_id in tracks:
                tracks[clip.track_id].clip_ids.append(clip.id)
            if clip.audio_enabled and "audio" in tracks and clip.track_id != "audio":
                tracks["audio"].clip_ids.append(clip.id)

    @property
    def total_edited_duration_us(self) -> int:
        return sum(clip.duration_us for clip in self.clips if not clip.is_cut)

    @property
    def total_edited_duration(self) -> float:
        return self.total_edited_duration_us / MICROSECONDS_PER_SECOND

    @property
    def total_removed_duration(self) -> float:
        return max(0.0, self.media_info.duration - self.total_edited_duration)

    def find_clip_at_time_us(self, timeline_time_us: int) -> Optional[ClipModel]:
        for clip in self.clips:
            if (
                not clip.is_cut
                and clip.timeline_start_us <= timeline_time_us
                < clip.timeline_start_us + clip.duration_us
            ):
                return clip
        return None

    def find_clip_at_time(self, timeline_time: float) -> Optional[ClipModel]:
        return self.find_clip_at_time_us(max(0, seconds_to_us(timeline_time)))

    def timeline_to_source_us(self, timeline_time_us: int) -> Optional[int]:
        clip = self.find_clip_at_time_us(timeline_time_us)
        if clip is None:
            if timeline_time_us == self.total_edited_duration_us and self.clips:
                last_clip = next((clip for clip in reversed(self.clips) if not clip.is_cut), None)
                return last_clip.source_out_us if last_clip else None
            return None
        return clip.source_time_at_timeline_us(timeline_time_us)

    def source_to_timeline_us(self, source_time_us: int) -> Optional[int]:
        for clip in self.clips:
            if not clip.is_cut and clip.source_in_us <= source_time_us < clip.source_out_us:
                offset_us = source_time_us - clip.source_in_us
                timeline_offset_us = offset_us * clip.playback_rate_den // clip.playback_rate_num
                return clip.timeline_start_us + timeline_offset_us
        return None

    def nearest_timeline_position_for_source_us(self, source_time_us: int) -> int:
        mapped = self.source_to_timeline_us(source_time_us)
        if mapped is not None:
            return mapped
        preceding = [
            clip.timeline_start_us + clip.duration_us
            for clip in self.clips
            if not clip.is_cut and clip.source_out_us <= source_time_us
        ]
        return max(preceding, default=0)

    def validate(self):
        track_ids = [track.track_id for track in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("Timeline track IDs must be unique.")
        clip_ids = set()
        for clip in self.clips:
            if clip.id in clip_ids:
                raise ValueError(f"Duplicate clip ID: {clip.id}")
            clip_ids.add(clip.id)
            if clip.track_id not in track_ids:
                raise ValueError(f"Clip {clip.id} references an unknown track.")
            if clip.source_out_us <= clip.source_in_us:
                raise ValueError(f"Clip {clip.id} has no source duration.")
            if self.media_info.duration_us and clip.source_out_us > self.media_info.duration_us:
                raise ValueError(f"Clip {clip.id} exceeds the source duration.")
            if clip.timeline_start_us < 0:
                raise ValueError(f"Clip {clip.id} has a negative timeline start.")
            if self.media_info.filepath and clip.source_path != self.media_info.filepath:
                raise ValueError(f"Clip {clip.id} references a different source media file.")
        if any(clip_id not in clip_ids for clip_id in self.selected_clip_ids):
            raise ValueError("Selection contains an unknown clip ID.")
        if any(region.end_time_us > self.media_info.duration_us for region in self.detected_regions):
            raise ValueError("A detection marker exceeds the source duration.")
        if self.current_playhead_us > self.total_edited_duration_us:
            raise ValueError("Playhead is past the edited timeline duration.")
        return True

    def replace_from(self, other: "TimelineDocument"):
        if not isinstance(other, TimelineDocument):
            raise TypeError("Replacement must be a TimelineDocument.")
        values = other.__dict__.copy()
        values["revision"] = self.revision + 1
        self.__dict__.clear()
        self.__dict__.update(values)

    def save_to_file(self, filepath: str):
        self.validate()
        target = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(target)}.",
            suffix=".tmp",
            dir=os.path.dirname(target) or ".",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        self.is_modified = False

    @classmethod
    def load_from_file(cls, filepath: str):
        with open(filepath, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "media_info": vars(self.media_info).copy(),
            "clips": [
                {
                    "id": clip.id,
                    "media_id": clip.media_id,
                    "track_id": clip.track_id,
                    "source_path": clip.source_path,
                    "source_in_us": clip.source_in_us,
                    "source_out_us": clip.source_out_us,
                    "timeline_start_us": clip.timeline_start_us,
                    "playback_rate_num": clip.playback_rate_num,
                    "playback_rate_den": clip.playback_rate_den,
                    "linked_group_id": clip.linked_group_id,
                    "is_cut": clip.is_cut,
                    "video_enabled": clip.video_enabled,
                    "audio_enabled": clip.audio_enabled,
                    "origin_type": clip.origin_type,
                }
                for clip in self.clips
            ],
            "tracks": [
                {
                    "track_id": track.track_id,
                    "name": track.name,
                    "kind": track.kind,
                    "enabled": track.enabled,
                    "locked": track.locked,
                }
                for track in self.tracks
            ],
            "detected_regions": [
                {
                    "id": region.id,
                    "region_type": region.region_type,
                    "start_time_us": region.start_time_us,
                    "end_time_us": region.end_time_us,
                    "confidence": region.confidence,
                    "approved_for_cut": region.approved_for_cut,
                    "reason": region.reason,
                }
                for region in self.detected_regions
            ],
            "waveform_peaks": list(self.waveform_peaks),
            "current_playhead_us": self.current_playhead_us,
            "zoom_level": self.zoom_level,
            "horizontal_scroll_us": self.horizontal_scroll_us,
            "active_layout": self.active_layout,
            "selected_clip_ids": list(self.selected_clip_ids),
        }

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict) or data.get("schema_version") != cls.schema_version:
            raise ValueError("Unsupported native timeline project format.")
        document = cls()
        document.project_id = str(data.get("project_id") or uuid.uuid4())
        document.project_name = str(data.get("project_name") or "مشروع جديد")
        media_info = data.get("media_info", {})
        if not isinstance(media_info, dict):
            raise ValueError("Invalid media metadata in project.")
        document.media_info = MediaInfo(**media_info)
        raw_clips = data.get("clips", [])
        raw_regions = data.get("detected_regions", [])
        if not isinstance(raw_clips, list) or not isinstance(raw_regions, list):
            raise ValueError("Invalid timeline collections in project.")
        document.clips = [ClipModel(**clip) for clip in raw_clips]
        document.detected_regions = [DetectedRegion(**region) for region in raw_regions]
        raw_tracks = data.get("tracks")
        if raw_tracks is not None:
            if not isinstance(raw_tracks, list):
                raise ValueError("Invalid timeline tracks in project.")
            document.tracks = [
                TrackModel(
                    track_id=str(track["track_id"]),
                    name=str(track["name"]),
                    kind=str(track["kind"]),
                    enabled=bool(track.get("enabled", True)),
                    locked=bool(track.get("locked", False)),
                )
                for track in raw_tracks
            ]
        waveform_peaks = data.get("waveform_peaks", [])
        if (
            not isinstance(waveform_peaks, list)
            or len(waveform_peaks) > 100_000
            or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in waveform_peaks)
        ):
            raise ValueError("Invalid waveform cache in project.")
        document.waveform_peaks = [float(value) for value in waveform_peaks]
        document.current_playhead_us = _validate_time_us(
            data.get("current_playhead_us", 0), "current_playhead_us"
        )
        document.zoom_level = max(0.2, min(32.0, float(data.get("zoom_level", 1.0))))
        document.horizontal_scroll_us = _validate_time_us(
            data.get("horizontal_scroll_us", 0), "horizontal_scroll_us"
        )
        document.active_layout = str(data.get("active_layout") or "Standard Horizontal")
        selected = data.get("selected_clip_ids", [])
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            raise ValueError("Invalid clip selection in project.")
        document.selected_clip_ids = selected
        document.recalculate_timeline_positions()
        document.validate()
        document.is_modified = False
        return document


Clip = ClipModel
ProjectState = TimelineDocument
