"""Undo/Redo コマンドの検証（計画書 F5）。"""
from __future__ import annotations

from PySide6.QtGui import QUndoStack

from rhythmslide.core.models import PhotoItem, Project, Settings
from rhythmslide.ui.commands import (
    ChangeSettingsCommand, MoveSlideCommand, ReorderPhotoCommand,
    SetTransitionCommand, ToggleLockCommand,
)


def _project():
    p = Project(photo_root="/photos", settings=Settings())
    p.photos = [PhotoItem(path=f"/photos/{c}.jpg") for c in "abc"]
    return p


def test_move_slide_undo_redo():
    p = _project()
    stack = QUndoStack()
    stack.push(MoveSlideCommand(p, 1, 5.0, 2.0))
    assert p.photos[1].override_start == 5.0
    assert p.photos[1].lock is True
    stack.undo()
    assert p.photos[1].override_start is None
    assert p.photos[1].lock is False
    stack.redo()
    assert p.photos[1].override_start == 5.0


def test_toggle_lock():
    p = _project()
    stack = QUndoStack()
    stack.push(ToggleLockCommand(p, 0))
    assert p.photos[0].lock is True
    stack.undo()
    assert p.photos[0].lock is False


def test_unlock_clears_manual_placement():
    """手動配置（Shift ドラッグ）でロックした写真も、メニュー解除で鍵マークが外れる。"""
    p = _project()
    stack = QUndoStack()
    stack.push(MoveSlideCommand(p, 1, 5.0, 2.0))  # 手動配置 = ロック
    assert p.photos[1].is_locked() is True
    stack.push(ToggleLockCommand(p, 1))  # メニューの「ロック解除」
    assert p.photos[1].lock is False
    assert p.photos[1].override_start is None
    assert p.photos[1].override_duration is None
    assert p.photos[1].is_locked() is False
    stack.undo()  # 解除を取り消し → 手動配置が戻る
    assert p.photos[1].is_locked() is True
    assert p.photos[1].override_start == 5.0


def test_lock_pins_current_position():
    """メニューの「ロック」は現在のスライド位置を手動配置として固定する。"""
    p = _project()
    stack = QUndoStack()
    stack.push(ToggleLockCommand(p, 2, 3.0, 1.5))
    assert p.photos[2].lock is True
    assert p.photos[2].override_start == 3.0
    assert p.photos[2].override_duration == 1.5
    assert p.photos[2].is_locked() is True
    stack.undo()
    assert p.photos[2].is_locked() is False
    assert p.photos[2].override_start is None


def test_set_transition():
    p = _project()
    stack = QUndoStack()
    stack.push(SetTransitionCommand(p, 2, "zoom"))
    assert p.photos[2].transition == "zoom"
    stack.undo()
    assert p.photos[2].transition is None


def test_reorder_roundtrip():
    p = _project()
    stack = QUndoStack()
    stack.push(ReorderPhotoCommand(p, 0, 2))
    assert [x.path.rsplit("/", 1)[-1] for x in p.photos] == ["b.jpg", "c.jpg", "a.jpg"]
    stack.undo()
    assert [x.path.rsplit("/", 1)[-1] for x in p.photos] == ["a.jpg", "b.jpg", "c.jpg"]


def test_change_settings():
    s = Settings(unit_beats=4)
    stack = QUndoStack()
    stack.push(ChangeSettingsCommand(s, "unit_beats", 8))
    assert s.unit_beats == 8
    stack.undo()
    assert s.unit_beats == 4
