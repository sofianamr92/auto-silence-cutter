# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional
from core.editing_engine import ProjectState, Clip

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
    def __init__(self, state: ProjectState, playhead_time: float):
        self.state = state
        self.time = playhead_time
        self.target_clip_idx: Optional[int] = None
        self.old_clip: Optional[Clip] = None
        self.new_clip_1: Optional[Clip] = None
        self.new_clip_2: Optional[Clip] = None

    def execute(self) -> bool:
        active_clips = [i for i, c in enumerate(self.state.clips) if not c.is_cut]
        for idx in active_clips:
            clip = self.state.clips[idx]
            if clip.timeline_in < self.time < (clip.timeline_in + clip.duration):
                self.target_clip_idx = idx
                self.old_clip = clip
                offset = self.time - clip.timeline_in
                split_source = clip.source_in + offset

                self.new_clip_1 = Clip(
                    source_path=clip.source_path,
                    source_in=clip.source_in,
                    source_out=split_source,
                    timeline_in=clip.timeline_in
                )
                self.new_clip_2 = Clip(
                    source_path=clip.source_path,
                    source_in=split_source,
                    source_out=clip.source_out,
                    timeline_in=self.time
                )
                self.state.clips.pop(idx)
                self.state.clips.insert(idx, self.new_clip_2)
                self.state.clips.insert(idx, self.new_clip_1)
                self.state.recalculate_timeline_positions()
                self.state.is_modified = True
                return True
        return False

    def undo(self) -> bool:
        if self.target_clip_idx is not None and self.old_clip is not None:
            self.state.clips.pop(self.target_clip_idx)
            self.state.clips.pop(self.target_clip_idx)
            self.state.clips.insert(self.target_clip_idx, self.old_clip)
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"تجزئة المقطع (C) عند {self.time:.2f} ثانية"

class TrimStartCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float):
        self.state = state
        self.time = playhead_time
        self.clip: Optional[Clip] = None
        self.old_source_in: float = 0.0

    def execute(self) -> bool:
        clip = self.state.find_clip_at_time(self.time)
        if clip and (self.time - clip.timeline_in) > 0.05:
            self.clip = clip
            self.old_source_in = clip.source_in
            offset = self.time - clip.timeline_in
            clip.source_in += offset
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def undo(self) -> bool:
        if self.clip:
            self.clip.source_in = self.old_source_in
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"قص بداية المقطع (Q) عند {self.time:.2f} ثانية"

class TrimEndCommand(EditCommand):
    def __init__(self, state: ProjectState, playhead_time: float):
        self.state = state
        self.time = playhead_time
        self.clip: Optional[Clip] = None
        self.old_source_out: float = 0.0

    def execute(self) -> bool:
        clip = self.state.find_clip_at_time(self.time)
        if clip and (clip.timeline_in + clip.duration - self.time) > 0.05:
            self.clip = clip
            self.old_source_out = clip.source_out
            offset = self.time - clip.timeline_in
            clip.source_out = clip.source_in + offset
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def undo(self) -> bool:
        if self.clip:
            self.clip.source_out = self.old_source_out
            self.state.recalculate_timeline_positions()
            self.state.is_modified = True
            return True
        return False

    def description(self) -> str:
        return f"قص نهاية المقطع (W) عند {self.time:.2f} ثانية"
