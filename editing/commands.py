# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional
from core.editing_engine import ProjectState, Clip, seconds_to_us

class EditCommand(ABC):
    @abstractmethod
    def execute(self) -> bool:
        pass

    @abstractmethod
    def undo(self) -> bool:
        pass

    @abstractmethod
    def description(self) -> str:
        pass

class SplitCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float = 0.0, *, time_us: Optional[int] = None):
        self.state = state
        self.time_us = seconds_to_us(playhead_time) if time_us is None else time_us
        self.time = self.time_us / 1_000_000
        self.target_clip_idx: Optional[int] = None
        self.old_clip: Optional[Clip] = None
        self.new_clip_1: Optional[Clip] = None
        self.new_clip_2: Optional[Clip] = None

    def execute(self) -> bool:
        if self.new_clip_1 is None:
            clip = self.state.find_clip_at_time_us(self.time_us)
            if clip is None or self.time_us <= clip.timeline_start_us:
                return False
            clip_index = self.state.clips.index(clip)
            if self.time_us >= clip.timeline_start_us + clip.duration_us:
                return False
            split_source_us = clip.source_time_at_timeline_us(self.time_us)
            self.target_clip_idx = clip_index
            self.old_clip = clip
            self.new_clip_1 = Clip(
                id=clip.id,
                media_id=clip.media_id,
                track_id=clip.track_id,
                source_path=clip.source_path,
                source_in_us=clip.source_in_us,
                source_out_us=split_source_us,
                timeline_start_us=clip.timeline_start_us,
                playback_rate_num=clip.playback_rate_num,
                playback_rate_den=clip.playback_rate_den,
                linked_group_id=clip.linked_group_id,
                video_enabled=clip.video_enabled,
                audio_enabled=clip.audio_enabled,
                origin_type=clip.origin_type,
            )
            self.new_clip_2 = Clip(
                media_id=clip.media_id,
                track_id=clip.track_id,
                source_path=clip.source_path,
                source_in_us=split_source_us,
                source_out_us=clip.source_out_us,
                timeline_start_us=self.time_us,
                playback_rate_num=clip.playback_rate_num,
                playback_rate_den=clip.playback_rate_den,
                linked_group_id=clip.linked_group_id,
                video_enabled=clip.video_enabled,
                audio_enabled=clip.audio_enabled,
                origin_type=clip.origin_type,
            )
        else:
            self.target_clip_idx = next(
                (i for i, clip in enumerate(self.state.clips) if clip.id == self.old_clip.id),
                None,
            )
            if self.target_clip_idx is None:
                return False

        self.state.clips.pop(self.target_clip_idx)
        self.state.clips[self.target_clip_idx:self.target_clip_idx] = [
            self.new_clip_1,
            self.new_clip_2,
        ]
        self.state.recalculate_timeline_positions()
        self.state.is_modified = True
        return True

    def undo(self) -> bool:
        if self.old_clip is None or self.new_clip_1 is None or self.new_clip_2 is None:
            return False
        left_index = next(
            (i for i, clip in enumerate(self.state.clips) if clip.id == self.new_clip_1.id),
            None,
        )
        right_index = next(
            (i for i, clip in enumerate(self.state.clips) if clip.id == self.new_clip_2.id),
            None,
        )
        if left_index is None or right_index != left_index + 1:
            return False
        self.state.clips[left_index:left_index + 2] = [self.old_clip]
        self.state.recalculate_timeline_positions()
        self.state.is_modified = True
        return True

    def description(self) -> str:
        return f"تجزئة المقطع (C) عند {self.time:.2f} ثانية"

class TrimStartCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float = 0.0, *, time_us: Optional[int] = None):
        self.state = state
        self.time_us = seconds_to_us(playhead_time) if time_us is None else time_us
        self.time = self.time_us / 1_000_000
        self.clip: Optional[Clip] = None
        self.old_source_in_us: Optional[int] = None

    def execute(self) -> bool:
        clip = self.state.find_clip_at_time_us(self.time_us)
        if clip and (self.time_us - clip.timeline_start_us) > 50_000:
            self.clip = clip
            self.old_source_in_us = clip.source_in_us
            clip.source_in_us = clip.source_time_at_timeline_us(self.time_us)
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def undo(self) -> bool:
        if self.clip and self.old_source_in_us is not None:
            self.clip.source_in_us = self.old_source_in_us
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"قص بداية المقطع (Q) عند {self.time:.2f} ثانية"

class TrimEndCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float = 0.0, *, time_us: Optional[int] = None):
        self.state = state
        self.time_us = seconds_to_us(playhead_time) if time_us is None else time_us
        self.time = self.time_us / 1_000_000
        self.clip: Optional[Clip] = None
        self.old_source_out_us: Optional[int] = None

    def execute(self) -> bool:
        clip = self.state.find_clip_at_time_us(self.time_us)
        if clip and (clip.timeline_start_us + clip.duration_us - self.time_us) > 50_000:
            self.clip = clip
            self.old_source_out_us = clip.source_out_us
            clip.source_out_us = clip.source_time_at_timeline_us(self.time_us)
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def undo(self) -> bool:
        if self.clip and self.old_source_out_us is not None:
            self.clip.source_out_us = self.old_source_out_us
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"قص نهاية المقطع (W) عند {self.time:.2f} ثانية"


class MoveClipCommand(EditCommand):
    def __init__(self, state: ProjectState, clip_id: str, target_index: int):
        self.state = state
        self.clip_id = clip_id
        self.target_index = target_index
        self.old_index: Optional[int] = None

    def execute(self) -> bool:
        clip = next((item for item in self.state.clips if item.id == self.clip_id), None)
        if clip is None or clip.is_cut:
            return False
        active = [item for item in self.state.clips if not item.is_cut]
        current_index = active.index(clip)
        if self.old_index is None:
            self.old_index = self.state.clips.index(clip)
        target_index = max(0, min(self.target_index, len(active) - 1))
        if current_index == target_index:
            return False
        self.state.clips.remove(clip)
        remaining_active = [item for item in self.state.clips if not item.is_cut]
        if target_index >= len(remaining_active):
            insertion_index = len(self.state.clips)
        else:
            insertion_index = self.state.clips.index(remaining_active[target_index])
        self.state.clips.insert(insertion_index, clip)
        self.state.recalculate_timeline_positions()
        self.state.is_modified = True
        return True

    def undo(self) -> bool:
        clip = next((item for item in self.state.clips if item.id == self.clip_id), None)
        if clip is None or self.old_index is None:
            return False
        self.state.clips.remove(clip)
        self.state.clips.insert(min(self.old_index, len(self.state.clips)), clip)
        self.state.recalculate_timeline_positions()
        self.state.is_modified = True
        return True

    def description(self) -> str:
        return f"نقل المقطع {self.clip_id}"


class DeleteClipCommand(EditCommand):
    def __init__(self, state: ProjectState, clip_id: str):
        self.state = state
        self.clip_id = clip_id
        self.clip: Optional[Clip] = None
        self.old_index: Optional[int] = None

    def execute(self) -> bool:
        index = next(
            (i for i, item in enumerate(self.state.clips) if item.id == self.clip_id),
            None,
        )
        if index is None:
            return False
        self.old_index = index
        self.clip = self.state.clips.pop(index)
        self.state.selected_clip_ids = [
            item for item in self.state.selected_clip_ids if item != self.clip_id
        ]
        self.state.recalculate_timeline_positions()
        self.state.is_modified = True
        return True

    def undo(self) -> bool:
        if self.clip is None or self.old_index is None:
            return False
        self.state.clips.insert(min(self.old_index, len(self.state.clips)), self.clip)
        self.state.recalculate_timeline_positions()
        self.state.is_modified = True
        return True

    def description(self) -> str:
        return f"حذف المقطع {self.clip_id}"


class TimelineSnappingEngine:
    def __init__(self, tolerance_us: int = 100_000):
        self.tolerance_us = max(0, tolerance_us)

    def snap(self, time_us: int, candidates_us) -> int:
        nearby = [
            candidate for candidate in candidates_us
            if abs(candidate - time_us) <= self.tolerance_us
        ]
        return min(nearby, key=lambda candidate: abs(candidate - time_us)) if nearby else time_us


class TimelineController:
    def __init__(self, document: ProjectState, undo_stack):
        self.document = document
        self.undo_stack = undo_stack
        self.snapping = TimelineSnappingEngine()

    def snap_time(self, time_us: int, excluded_clip_id: Optional[str] = None) -> int:
        candidates = [0, self.document.current_playhead_us]
        for clip in self.document.clips:
            if clip.is_cut or clip.id == excluded_clip_id:
                continue
            candidates.extend((
                clip.timeline_start_us,
                clip.timeline_start_us + clip.duration_us,
            ))
        for region in self.document.detected_regions:
            candidates.append(self.document.nearest_timeline_position_for_source_us(region.start_time_us))
            candidates.append(self.document.nearest_timeline_position_for_source_us(region.end_time_us))
        return self.snapping.snap(time_us, candidates)

    def execute(self, command: EditCommand) -> bool:
        return self.undo_stack.push_and_execute(command)

    def split_at(self, timeline_time_us: int) -> bool:
        return self.execute(SplitCommand(
            self.document,
            time_us=timeline_time_us,
        ))

    def trim_start_at(self, timeline_time_us: int) -> bool:
        return self.execute(TrimStartCommand(
            self.document,
            time_us=timeline_time_us,
        ))

    def trim_end_at(self, timeline_time_us: int) -> bool:
        return self.execute(TrimEndCommand(
            self.document,
            time_us=timeline_time_us,
        ))

    def move_clip(self, clip_id: str, target_timeline_us: int) -> bool:
        clip = next((item for item in self.document.clips if item.id == clip_id), None)
        if clip is None:
            return False
        target_timeline_us = self.snap_time(target_timeline_us, clip_id)
        active = [item for item in self.document.clips if not item.is_cut]
        target_index = 0
        for index, item in enumerate(active):
            if target_timeline_us >= item.timeline_start_us + item.duration_us:
                target_index = index + 1
        target_index = min(target_index, len(active) - 1)
        return self.execute(MoveClipCommand(self.document, clip_id, target_index))

    def delete_clip(self, clip_id: str) -> bool:
        return self.execute(DeleteClipCommand(self.document, clip_id))
