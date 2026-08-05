"""우측 목록: 현재 prefix의 폴더/파일만 표시 (6.1, 6.2)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.s3client import S3Client
from app.ui.transfer_worker import submit

_COLUMNS = ["이름", "크기", "수정일", "타입"]


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class ObjectTable(QWidget):
    folder_activated = Signal(str, str)  # bucket, prefix
    error_occurred = Signal(object)  # AppError
    listing_loaded = Signal(int, int)  # file_count, total_bytes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: Optional[S3Client] = None
        self._pool = QThreadPool.globalInstance()
        self._workers: set = set()
        self.bucket: str = ""
        self.prefix: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table)

    def set_client(self, client: S3Client) -> None:
        self._client = client

    def load(self, bucket: str, prefix: str) -> None:
        if self._client is None:
            return
        self.bucket = bucket
        self.prefix = prefix
        submit(
            self._pool,
            self._workers,
            self._fetch_listing,
            bucket,
            prefix,
            on_result=self._on_loaded,
            on_error=self.error_occurred.emit,
        )

    def _fetch_listing(self, bucket: str, prefix: str) -> dict:
        assert self._client is not None
        folders = []
        files = []
        for page in self._client.list_objects(bucket, prefix=prefix, delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                folders.append(cp["Prefix"])
            for obj in page.get("Contents", []):
                if obj["Key"] == prefix:
                    continue  # 6.2: 빈 폴더를 나타내는 자기 자신 키는 파일 목록에서 제외
                files.append(obj)
        return {"folders": folders, "files": files}

    def _on_loaded(self, listing: dict) -> None:
        folders = listing["folders"]
        files = listing["files"]
        self.table.setRowCount(len(folders) + len(files))

        row = 0
        for prefix in folders:
            name = prefix.rstrip("/").rsplit("/", 1)[-1]
            self._set_row(row, "📁 " + name, "", "", "폴더", {"kind": "folder", "prefix": prefix})
            row += 1

        total_bytes = 0
        for obj in files:
            key = obj["Key"]
            name = key.rsplit("/", 1)[-1]
            size = obj["Size"]
            total_bytes += size
            modified = obj["LastModified"].strftime("%Y-%m-%d %H:%M") if obj.get("LastModified") else ""
            ext = name.rsplit(".", 1)[-1].upper() if "." in name else "파일"
            self._set_row(row, "📄 " + name, format_size(size), modified, ext, {"kind": "file", "key": key})
            row += 1

        self.listing_loaded.emit(len(files), total_bytes)

    def _set_row(self, row: int, name: str, size: str, modified: str, type_label: str, data: dict) -> None:
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.ItemDataRole.UserRole, data)
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QTableWidgetItem(size))
        self.table.setItem(row, 2, QTableWidgetItem(modified))
        self.table.setItem(row, 3, QTableWidgetItem(type_label))

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        name_item = self.table.item(item.row(), 0)
        data = name_item.data(Qt.ItemDataRole.UserRole)
        if data and data.get("kind") == "folder":
            self.folder_activated.emit(self.bucket, data["prefix"])

    def selected_keys(self) -> list:
        """선택된 행의 (kind, key/prefix) 목록. 컨텍스트 메뉴/전송 단계에서 사용."""
        keys = []
        for item in self.table.selectedItems():
            if item.column() != 0:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                keys.append(data)
        return keys
