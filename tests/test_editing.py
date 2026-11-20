# -*- coding: utf-8 -*-
from core.editing_engine import ProjectState, Clip
from editing.commands import SplitCommand, TrimStartCommand, TrimEndCommand
from editing.undo_redo import UndoRedoStack

def test_split_command():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    stack = UndoRedoStack()
    cmd = SplitCommand(state, playhead_time=10.0)
    assert stack.push_and_execute(cmd) is True
    assert len(state.clips) == 2
    assert state.clips[0].duration == 10.0
    assert state.clips[1].duration == 20.0

    assert stack.undo() is True
    assert len(state.clips) == 1
    assert state.clips[0].duration == 30.0

def test_trim_start_q():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    cmd = TrimStartCommand(state, playhead_time=10.0)
    assert cmd.execute() is True
    assert clip.source_in == 10.0
    assert clip.duration == 20.0

    assert cmd.undo() is True
    assert clip.source_in == 0.0
    assert clip.duration == 30.0

def test_trim_end_w():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    cmd = TrimEndCommand(state, playhead_time=20.0)
    assert cmd.execute() is True
    assert clip.source_out == 20.0
    assert clip.duration == 20.0

    assert cmd.undo() is True
    assert clip.source_out == 30.0
    assert clip.duration == 30.0

def test_split_redo():
    state = ProjectState()
    clip = Clip(source_in=0.0, source_out=30.0, timeline_in=0.0)
    state.clips.append(clip)

    stack = UndoRedoStack()
    assert stack.push_and_execute(SplitCommand(state, 10.0)) is True
    assert stack.undo() is True
    assert stack.redo() is True
    assert len(state.clips) == 2
    assert [c.duration for c in state.clips] == [10.0, 20.0]

def test_split_requires_interior_playhead():
    state = ProjectState()
    state.clips.append(Clip(source_in=0.0, source_out=30.0, timeline_in=0.0))

    stack = UndoRedoStack()
    assert stack.push_and_execute(SplitCommand(state, 0.0)) is False
    assert stack.push_and_execute(SplitCommand(state, 30.0)) is False
