"""編集操作の Undo/Redo（計画書 F5 / M4）。

QUndoCommand で「モデルへの破壊的変更」をカプセル化する。各コマンドは実行後に
MainWindow 側で再レイアウト（build_timeline）が走る前提で、モデルだけを編集する。
"""
from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from ..core.models import PhotoItem, Project, Settings


class _ProjectCommand(QUndoCommand):
    def __init__(self, project: Project, text: str) -> None:
        super().__init__(text)
        self.project = project


class MoveSlideCommand(_ProjectCommand):
    """スライドの手動配置（ロック）。開始/長さを override に書き込む。"""

    def __init__(self, project: Project, index: int, start: float, duration: float) -> None:
        super().__init__(project, "スライドを移動")
        self.index = index
        self.new_start = float(start)
        self.new_dur = float(duration)
        p = project.photos[index]
        self.old_start = p.override_start
        self.old_dur = p.override_duration
        self.old_lock = p.lock

    def redo(self) -> None:
        p = self.project.photos[self.index]
        p.override_start = self.new_start
        p.override_duration = self.new_dur
        p.lock = True

    def undo(self) -> None:
        p = self.project.photos[self.index]
        p.override_start = self.old_start
        p.override_duration = self.old_dur
        p.lock = self.old_lock


class SetTransitionCommand(_ProjectCommand):
    def __init__(self, project: Project, index: int, transition: str) -> None:
        super().__init__(project, "トランジション変更")
        self.index = index
        self.new = transition
        self.old = project.photos[index].transition

    def redo(self) -> None:
        self.project.photos[self.index].transition = self.new

    def undo(self) -> None:
        self.project.photos[self.index].transition = self.old


class ToggleLockCommand(_ProjectCommand):
    """ロック/解除。

    - 解除: ``lock`` を消すだけでなく手動配置（``override_start`` /
      ``override_duration``）も消して自動配置に戻す。これにより手動配置由来の
      ロックでも鍵マークが外れる（``is_locked()`` は override が残ると True のままになる）。
    - ロック: 現在のスライド位置（``start`` / ``duration``）を手動配置として固定する。
      位置を渡さない場合、従来どおり ``lock`` フラグのみを立てる。
    """

    def __init__(self, project: Project, index: int,
                 start: float | None = None, duration: float | None = None) -> None:
        super().__init__(project, "ロック切替")
        self.index = index
        p = project.photos[index]
        self.was_locked = p.is_locked()
        self.old_lock = p.lock
        self.old_start = p.override_start
        self.old_dur = p.override_duration
        self.new_start = start
        self.new_dur = duration

    def redo(self) -> None:
        p = self.project.photos[self.index]
        if self.was_locked:
            # 解除: 手動配置も消して自動配置に戻す
            p.lock = False
            p.override_start = None
            p.override_duration = None
        else:
            # ロック: 現在の位置を手動配置として固定する
            p.lock = True
            if self.new_start is not None:
                p.override_start = float(self.new_start)
            if self.new_dur is not None:
                p.override_duration = float(self.new_dur)

    def undo(self) -> None:
        p = self.project.photos[self.index]
        p.lock = self.old_lock
        p.override_start = self.old_start
        p.override_duration = self.old_dur


class ReorderPhotoCommand(_ProjectCommand):
    def __init__(self, project: Project, src: int, dst: int) -> None:
        super().__init__(project, "順序変更")
        self.src, self.dst = src, dst

    def redo(self) -> None:
        self._move(self.src, self.dst)

    def undo(self) -> None:
        self._move(self.dst, self.src)

    def _move(self, src: int, dst: int) -> None:
        photos = self.project.photos
        item = photos.pop(src)
        photos.insert(dst, item)


class ChangeSettingsCommand(_ProjectCommand):
    """設定の単一フィールド変更（unit_beats / transition / transition_ms など）。"""

    def __init__(self, settings: Settings, field: str, new) -> None:
        super().__init__(None, "設定変更")  # project 未使用
        self.settings = settings
        self.field = field
        self.new = new
        self.old = getattr(settings, field)

    def redo(self) -> None:
        setattr(self.settings, self.field, self.new)

    def undo(self) -> None:
        setattr(self.settings, self.field, self.old)
