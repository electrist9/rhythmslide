"""素材パネル（計画書 F2 / M1）。

フォルダ絞り込みと検索付きのアイコングリッド。サムネイルは ThumbnailCache（SQLite）から
可視分だけ遅延読み込みし、生成済みになり次第 dataChanged で反映する。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QAbstractListModel, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListView, QLineEdit, QVBoxLayout, QWidget,
)

from ..core.models import PhotoItem
from ..core.thumbnails import ThumbnailCache
from ..i18n import fmt

_PLACEHOLDER: QPixmap | None = None


class PhotoListModel(QAbstractListModel):
    def __init__(self, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self.cache = cache
        self._all: list[PhotoItem] = []
        self._rows: list[PhotoItem] = []
        self._folder = ""
        self._query = ""
        self._icons: dict[str, QIcon] = {}

    # ---- Qt モデル -----------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self._rows)

    def data(self, index: QModelIndex, role=int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid():
            return None
        p = self._rows[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            return self._icon(p)
        if role == Qt.ItemDataRole.DisplayRole:
            return Path(p.path).name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{p.path}\n{p.taken_at or ''}"
        if role == Qt.ItemDataRole.UserRole:
            return p.path
        return None

    def _icon(self, p: PhotoItem) -> QIcon:
        cached = self._icons.get(p.path)
        if cached is not None:
            return cached
        data = self.cache.get(p.path)
        if data:
            pm = QPixmap()
            if pm.loadFromData(data) and not pm.isNull():
                icon = QIcon(pm)
                self._icons[p.path] = icon
                return icon
        return QIcon(_placeholder())

    # ---- データ操作 ----------------------------------------------------
    def set_photos(self, photos: list[PhotoItem]) -> None:
        self.beginResetModel()
        self._all = list(photos)
        self._apply_filter()
        self.endResetModel()

    def set_folder(self, folder: str) -> None:
        self._folder = folder
        self._refilter()

    def set_query(self, query: str) -> None:
        self._query = query.strip().lower()
        self._refilter()

    def folders(self) -> list[str]:
        seen: set[str] = set()
        for p in self._all:
            seen.add(p.folder or ".")
        return sorted(seen)

    def thumbnail_ready(self, path: str) -> None:
        self._icons.pop(path, None)
        for row, p in enumerate(self._rows):
            if p.path == path:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                break

    def _refilter(self) -> None:
        self.beginResetModel()
        self._apply_filter()
        self.endResetModel()

    def _apply_filter(self) -> None:
        rows = []
        for p in self._all:
            if self._folder and (p.folder or ".") != self._folder:
                continue
            if self._query and self._query not in Path(p.path).name.lower():
                continue
            rows.append(p)
        self._rows = rows

    def row_photo(self, row: int) -> PhotoItem | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class MaterialPanel(QWidget):
    photoActivated = Signal(str)  # ダブルクリックでプレビュー etc.

    def __init__(self, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self.model = PhotoListModel(cache, self)
        self.view = QListView(self)
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setSpacing(8)
        self.view.setUniformItemSizes(True)
        self.view.setIconSize(_icon_size())
        self.view.setGridSize(_grid_size())
        self.view.setWordWrap(True)
        self.view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.view.doubleClicked.connect(self._on_double)

        self.folder_combo = QComboBox(self)
        self.folder_combo.addItem(self.tr("すべてのフォルダ"), "")
        self.folder_combo.currentIndexChanged.connect(
            lambda: self.model.set_folder(self.folder_combo.currentData() or "")
        )
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(self.tr("ファイル名で絞り込み"))
        self.search.textChanged.connect(self.model.set_query)
        self.count_label = QLabel(fmt(self.tr("%1 枚"), 0), self)

        top = QHBoxLayout()
        top.addWidget(QLabel(self.tr("フォルダ:")))
        top.addWidget(self.folder_combo, 1)
        top.addWidget(self.search, 1)
        top.addWidget(self.count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)

    def set_photos(self, photos: list[PhotoItem]) -> None:
        self.model.set_photos(photos)
        self._refresh_folders()
        self.count_label.setText(fmt(self.tr("%1 枚"), len(photos)))

    def _refresh_folders(self) -> None:
        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr("すべてのフォルダ"), "")
        for f in self.model.folders():
            self.folder_combo.addItem(f, f)
        idx = self.folder_combo.findData(current or "")
        self.folder_combo.setCurrentIndex(max(0, idx))
        self.folder_combo.blockSignals(False)

    def thumbnail_ready(self, path: str) -> None:
        self.model.thumbnail_ready(path)

    def _on_double(self, index: QModelIndex) -> None:
        p = self.model.row_photo(index.row())
        if p is not None:
            self.photoActivated.emit(p.path)


def _placeholder() -> QPixmap:
    global _PLACEHOLDER
    if _PLACEHOLDER is None or _PLACEHOLDER.isNull():
        _PLACEHOLDER = QPixmap(96, 96)
        _PLACEHOLDER.fill(QColor(60, 60, 60))
    return _PLACEHOLDER


def _icon_size():
    from PySide6.QtCore import QSize

    return QSize(128, 128)


def _grid_size():
    from PySide6.QtCore import QSize

    return QSize(150, 168)
