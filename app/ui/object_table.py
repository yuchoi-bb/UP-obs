"""우측 목록: 현재 prefix의 폴더/파일만 표시 (6.1, 6.2) + 드래그앤드롭(7장)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QMimeData, QThreadPool, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.cache import cache_dir
from app.errors import AppError
from app.permissions import is_superuser
from app.s3client import S3Client
from app.ui.transfer_worker import submit

_COLUMNS = ["이름", "크기", "수정일", "타입"]
_DRAG_SIZE_LIMIT = 50 * 1024 * 1024  # 7.2: 50MB 초과 시 드래그 중단


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class _DropTargetTable(QTableWidget):
    """드롭(업로드) 수신 + 드래그 시작(다운로드) 처리를 ObjectTable에 위임한다."""

    def __init__(self, owner: "ObjectTable"):
        super().__init__(0, len(_COLUMNS))
        self._owner = owner
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        self._owner._handle_drop(event)

    def startDrag(self, supportedActions) -> None:
        self._owner._handle_drag_start()


class ObjectTable(QWidget):
    folder_activated = Signal(str, str)  # bucket, prefix
    error_occurred = Signal(object)  # AppError
    listing_loaded = Signal(int, int)  # file_count, total_bytes
    files_dropped = Signal(list)  # list[Path] - 7.1: 탐색기 -> 앱 업로드
    download_requested = Signal(list)  # list[dict] - 컨텍스트 메뉴 "다운로드"
    delete_requested = Signal(list)  # list[dict] - 컨텍스트 메뉴 "삭제" (Superuser 전용)
    rename_requested = Signal(dict)  # 단일 항목 - 컨텍스트 메뉴 "이름변경" (Superuser 전용)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: Optional[S3Client] = None
        self._pool = QThreadPool.globalInstance()
        self._workers: set = set()
        self.bucket: str = ""
        self.prefix: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = _DropTargetTable(self)
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
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
            self._set_row(row, "📄 " + name, format_size(size), modified, ext, {"kind": "file", "key": key, "size": size})
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
        """선택된 행의 (kind, key/prefix) 목록. 컨텍스트 메뉴/전송에서 사용."""
        keys = []
        for item in self.table.selectedItems():
            if item.column() != 0:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                keys.append(data)
        return keys

    # ---- 7.1: 탐색기 -> 앱 (업로드 드롭) ------------------------------------

    def _handle_drop(self, event) -> None:
        urls = event.mimeData().urls()
        paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        if not paths:
            self.error_occurred.emit(AppError("E-4001", detail="드롭한 항목에서 로컬 경로를 찾을 수 없음"))
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths)

    # ---- 7.2: 앱 -> 탐색기 (다운로드 드래그, 캐시 경유) ------------------------

    def _handle_drag_start(self) -> None:
        if self._client is None:
            return
        selected = self.selected_keys()
        if not selected:
            return

        total_size = 0
        has_folder = False
        for entry in selected:
            if entry["kind"] == "folder":
                has_folder = True  # 폴더는 하위 크기를 알 수 없으므로 안전하게 임계값 초과로 취급
            else:
                total_size += entry.get("size", 0)

        if has_folder or total_size > _DRAG_SIZE_LIMIT:
            QToolTip.showText(
                QCursor.pos(),
                "선택 항목이 50MB를 넘습니다. '↓받기' 버튼으로 다운로드하세요.",
                self.table,
            )
            return

        local_paths = self._download_selection_to_cache(selected)
        if not local_paths:
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in local_paths])
        drag = QDrag(self.table)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _download_selection_to_cache(self, selected: list) -> list:
        """선택된 파일들을 캐시 폴더로 내려받는다. 완료까지 이벤트 루프를 펌프한다."""
        assert self._client is not None
        targets = [(entry["key"], cache_dir() / self.bucket / entry["key"]) for entry in selected if entry["kind"] == "file"]
        if not targets:
            return []

        results: dict = {}

        def make_result_cb(key):
            return lambda _r: results.setdefault(key, True)

        def make_error_cb(key):
            return lambda err: results.setdefault(key, err)

        for key, local_path in targets:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            submit(
                self._pool,
                self._workers,
                self._client.get_object,
                self.bucket,
                key,
                local_path,
                on_result=make_result_cb(key),
                on_error=make_error_cb(key),
            )

        app = QApplication.instance()
        deadline = time.time() + 30
        while len(results) < len(targets) and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

        ok_paths = []
        for key, local_path in targets:
            outcome = results.get(key)
            if outcome is True:
                ok_paths.append(local_path)
            elif isinstance(outcome, AppError):
                self.error_occurred.emit(outcome)
        return ok_paths

    # ---- 9장: 컨텍스트 메뉴 ------------------------------------------------

    def _on_context_menu(self, pos) -> None:
        selected = self.selected_keys()
        if not selected:
            return
        menu = self._build_context_menu(selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _build_context_menu(self, selected: list) -> QMenu:
        menu = QMenu(self.table)
        single = selected[0] if len(selected) == 1 else None

        if single is not None and single["kind"] == "file":
            open_action = menu.addAction("열기")
            open_action.triggered.connect(lambda: self._open_selected(single))

        download_action = menu.addAction("다운로드")
        download_action.triggered.connect(lambda: self.download_requested.emit(selected))

        # 5.1: 삭제/이름변경은 Superuser 전용. 버튼 숨김은 오조작 방지용이며
        # 실제 차단은 s3client의 guard()가 담당한다 (버튼 숨김만으론 부족: 5.3).
        if is_superuser():
            menu.addSeparator()
            if single is not None:
                rename_action = menu.addAction("이름변경")
                rename_action.triggered.connect(lambda: self.rename_requested.emit(single))
            delete_action = menu.addAction("삭제")
            delete_action.triggered.connect(lambda: self.delete_requested.emit(selected))

        menu.addSeparator()
        copy_path_action = menu.addAction("경로 복사")
        copy_path_action.triggered.connect(lambda: self._copy_paths(selected))

        files_only = [entry for entry in selected if entry["kind"] == "file"]
        if files_only:
            presign_action = menu.addAction("presigned URL 복사")
            presign_action.triggered.connect(lambda: self._copy_presigned_urls(files_only))

        return menu

    def _open_selected(self, entry: dict) -> None:
        local_paths = self._download_selection_to_cache([entry])
        for path in local_paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _copy_paths(self, selected: list) -> None:
        lines = []
        for entry in selected:
            key = entry["key"] if entry["kind"] == "file" else entry["prefix"]
            lines.append(f"s3://{self.bucket}/{key}")
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_presigned_urls(self, files: list) -> None:
        if self._client is None:
            return
        urls = []
        for entry in files:
            try:
                urls.append(self._client.generate_presigned_url(self.bucket, entry["key"]))
            except AppError as exc:
                self.error_occurred.emit(exc)
                return
        QApplication.clipboard().setText("\n".join(urls))
