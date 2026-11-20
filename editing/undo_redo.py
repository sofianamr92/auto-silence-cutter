# -*- coding: utf-8 -*-
from typing import List
from editing.commands import EditCommand

class UndoRedoStack:
    def __init__(self, max_history: int = 50):
        self.undo_stack: List[EditCommand] = []
        self.redo_stack: List[EditCommand] = []
        self.max_history = max_history

    def push_and_execute(self, command: EditCommand) -> bool:
        if command.execute():
            self.undo_stack.append(command)
            self.redo_stack.clear()
            if len(self.undo_stack) > self.max_history:
                self.undo_stack.pop(0)
            return True
        return False

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        cmd = self.undo_stack.pop()
        if cmd.undo():
            self.redo_stack.append(cmd)
            return True
        return False

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        cmd = self.redo_stack.pop()
        if cmd.execute():
            self.undo_stack.append(cmd)
            return True
        return False

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
