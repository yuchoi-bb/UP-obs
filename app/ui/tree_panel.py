"""좌측 트리: 지연 로딩(펼칠 때 조회) (6.1)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from app.s3client import S3Client
from app.ui.transfer_worker import submit

_LOADING_TEXT = "불러오는 중..."


class TreePanel(QWidget):
    path_selected = Signal(str, str)  # bucket, prefix
    error_occurred = Signal(object)  # AppError

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: Optional[S3Client] = None
        self._pool = QThreadPool.globalInstance()
        self._workers: set = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

    def set_client(self, client: S3Client, bucket: str = "", root_prefix: str = "") -> None:
        """root_prefix: 5.1 - 일반 모드는 DA-share/, Superuser는 버킷 루트(빈 값)."""
        self._client = client
        self.tree.clear()
        if bucket:
            self._add_bucket_root(bucket, root_prefix)
        else:
            self._load_buckets()

    def _add_bucket_root(self, bucket: str, root_prefix: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem([bucket])
        item.setData(0, Qt.ItemDataRole.UserRole, {"bucket": bucket, "prefix": root_prefix, "loaded": False})
        self._add_placeholder(item)
        self.tree.addTopLevelItem(item)
        return item

    def _load_buckets(self) -> None:
        if self._client is None:
            return
        submit(
            self._pool,
            self._workers,
            self._client.list_buckets,
            on_result=self._on_buckets_loaded,
            on_error=self.error_occurred.emit,
        )

    def _on_buckets_loaded(self, buckets: list) -> None:
        for bucket in buckets:
            self._add_bucket_root(bucket)

    @staticmethod
    def _add_placeholder(item: QTreeWidgetItem) -> None:
        item.addChild(QTreeWidgetItem([_LOADING_TEXT]))

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("loaded") or self._client is None:
            return
        bucket = data["bucket"]
        prefix = data["prefix"]
        submit(
            self._pool,
            self._workers,
            self._collect_child_prefixes,
            bucket,
            prefix,
            on_result=lambda prefixes, it=item: self._on_children_loaded(it, prefixes),
            on_error=self.error_occurred.emit,
        )

    def _collect_child_prefixes(self, bucket: str, prefix: str) -> list:
        assert self._client is not None
        prefixes: list = []
        for page in self._client.list_objects(bucket, prefix=prefix, delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                prefixes.append(cp["Prefix"])
        return prefixes

    def _on_children_loaded(self, item: QTreeWidgetItem, prefixes: list) -> None:
        item.takeChildren()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        data["loaded"] = True
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        bucket = data["bucket"]
        for prefix in prefixes:
            name = prefix.rstrip("/").rsplit("/", 1)[-1]
            child = QTreeWidgetItem([name])
            child.setData(0, Qt.ItemDataRole.UserRole, {"bucket": bucket, "prefix": prefix, "loaded": False})
            self._add_placeholder(child)
            item.addChild(child)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        self.path_selected.emit(data["bucket"], data["prefix"])

    def refresh_node(self, item: Optional[QTreeWidgetItem] = None) -> None:
        """새 폴더 생성 등 이후 하위 목록을 다시 불러온다."""
        item = item or self.tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        data["loaded"] = False
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        item.takeChildren()
        self._add_placeholder(item)
        if item.isExpanded():
            self._on_item_expanded(item)
