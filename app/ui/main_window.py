"""메인 윈도우: 툴바 + 트리/목록 스플리터 + 상태바 (9장)."""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
)

from app.config import ProfileStore
from app.errors import AppError, get_log_dir
from app.s3client import S3Client
from app.ui.object_table import ObjectTable, format_size
from app.ui.settings_dialog import SettingsDialog
from app.ui.tree_panel import TreePanel

_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')


class MainWindow(QMainWindow):
    def __init__(self, store: ProfileStore):
        super().__init__()
        self.setWindowTitle("UP-obs")
        self.resize(1024, 640)

        self._store = store
        self._client: Optional[S3Client] = None
        self._current_bucket: str = ""
        self._current_prefix: str = ""

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        self._reload_profile_combo()
        if self._store.profiles:
            self._connect_profile(self._store.active_profile or self._store.profiles[0].name)

    # ---- 구성 ---------------------------------------------------------

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("메인")
        toolbar.setMovable(False)

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        toolbar.addWidget(self.profile_combo)
        toolbar.addSeparator()

        self.upload_btn = QPushButton("↑업로드")
        self.upload_btn.setEnabled(False)
        self.upload_btn.setToolTip("3단계(드래그앤드롭)에서 지원 예정")
        toolbar.addWidget(self.upload_btn)

        self.download_btn = QPushButton("↓받기")
        self.download_btn.setEnabled(False)
        self.download_btn.setToolTip("3단계(드래그앤드롭)에서 지원 예정")
        toolbar.addWidget(self.download_btn)

        self.new_folder_btn = QPushButton("새폴더")
        self.new_folder_btn.clicked.connect(self._on_new_folder)
        toolbar.addWidget(self.new_folder_btn)

        settings_btn = QPushButton("⚙설정")
        settings_btn.clicked.connect(self._on_open_settings)
        toolbar.addWidget(settings_btn)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree_panel = TreePanel()
        self.tree_panel.path_selected.connect(self._on_path_selected)
        self.tree_panel.error_occurred.connect(self._show_error)
        splitter.addWidget(self.tree_panel)

        self.object_table = ObjectTable()
        self.object_table.folder_activated.connect(self._on_path_selected)
        self.object_table.error_occurred.connect(self._show_error)
        self.object_table.listing_loaded.connect(self._on_listing_loaded)
        splitter.addWidget(self.object_table)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

    def _build_statusbar(self) -> None:
        self.status_label = QLabel("연결 안 됨")
        self.statusBar().addWidget(self.status_label)

    # ---- 프로파일 연결 --------------------------------------------------

    def _reload_profile_combo(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems([p.name for p in self._store.profiles])
        if self._store.active_profile:
            self.profile_combo.setCurrentText(self._store.active_profile)
        self.profile_combo.blockSignals(False)

    def _on_profile_changed(self, name: str) -> None:
        if not name:
            return
        self._connect_profile(name)

    def _connect_profile(self, name: str) -> None:
        matches = [p for p in self._store.profiles if p.name == name]
        if not matches:
            return
        profile = matches[0]
        try:
            profile.validate()
            client = S3Client(profile)
        except AppError as exc:
            self._show_error(exc)
            self.status_label.setText("연결 안 됨")
            return

        self._client = client
        self._store.active_profile = name
        self.tree_panel.set_client(client, bucket=profile.bucket)
        self.object_table.set_client(client)
        self._current_bucket = profile.bucket
        self._current_prefix = ""
        self.status_label.setText(f"연결됨 · {profile.endpoint_url or 'AWS'}")
        if profile.bucket:
            self.object_table.load(profile.bucket, "")

    # ---- 트리/목록 상호작용 ----------------------------------------------

    def _on_path_selected(self, bucket: str, prefix: str) -> None:
        self._current_bucket = bucket
        self._current_prefix = prefix
        self.object_table.load(bucket, prefix)

    def _on_listing_loaded(self, file_count: int, total_bytes: int) -> None:
        endpoint = self._client.profile.endpoint_url if self._client else ""
        self.status_label.setText(
            f"연결됨 · {endpoint or 'AWS'} · {file_count}개 · {format_size(total_bytes)}"
        )

    # ---- 새 폴더 (6.2) ----------------------------------------------------

    def _on_new_folder(self) -> None:
        if self._client is None or not self._current_bucket:
            QMessageBox.information(self, "새 폴더", "먼저 프로파일에 연결하고 위치를 선택하세요.")
            return
        name, ok = QInputDialog.getText(self, "새 폴더", "폴더 이름")
        if not ok or not name.strip():
            return
        name = name.strip()
        if _INVALID_NAME_CHARS.search(name):
            self._show_error(AppError("E-4005", detail=name))
            return
        new_prefix = f"{self._current_prefix}{name}/"
        try:
            self._client.create_folder(self._current_bucket, new_prefix)
        except AppError as exc:
            self._show_error(exc)
            return
        self.object_table.load(self._current_bucket, self._current_prefix)
        self.tree_panel.refresh_node()

    # ---- 설정 -----------------------------------------------------------

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(self._store, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self._reload_profile_combo()
            if self._store.active_profile:
                self._connect_profile(self._store.active_profile)

    # ---- 오류 표시 (10.4) ---------------------------------------------------

    def _show_error(self, err: AppError) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("오류")
        box.setText(err.display_text())
        if err.detail:
            box.setDetailedText(err.detail)
        open_log_btn = box.addButton("로그 폴더 열기", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() == open_log_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_log_dir())))
