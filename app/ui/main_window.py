"""메인 윈도우: 툴바 + 트리/목록 스플리터 + 상태바 (9장)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThreadPool, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
)

from app import cache, permissions
from app.config import ProfileStore
from app.errors import AppError, get_log_dir
from app.s3client import S3Client
from app.ui.object_table import ObjectTable, format_size
from app.ui.settings_dialog import SettingsDialog
from app.ui.transfer_worker import (
    TransferManager,
    expand_delete_items,
    expand_download_items,
    expand_rename_items,
    expand_upload_paths,
    find_existing_keys,
    submit,
)
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
        self._pool = QThreadPool.globalInstance()
        self._workers: set = set()
        self._transfer_manager: Optional[TransferManager] = None

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        self._reload_profile_combo()
        if self._store.profiles:
            self._connect_profile(self._store.active_profile or self._store.profiles[0].name)

    # ---- 구성 ---------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("메뉴")
        self.superuser_action = QAction("관리자 모드", self)
        self.superuser_action.setCheckable(True)
        self.superuser_action.toggled.connect(self._on_toggle_superuser)
        menu.addAction(self.superuser_action)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("메인")
        toolbar.setMovable(False)

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        toolbar.addWidget(self.profile_combo)
        toolbar.addSeparator()

        self.upload_btn = QPushButton("↑업로드")
        self.upload_btn.clicked.connect(self._on_upload_clicked)
        toolbar.addWidget(self.upload_btn)

        self.download_btn = QPushButton("↓받기")
        self.download_btn.clicked.connect(self._on_download_clicked)
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
        self.object_table.files_dropped.connect(self._start_upload)
        self.object_table.download_requested.connect(self._download_items)
        self.object_table.delete_requested.connect(self._on_delete_requested)
        self.object_table.rename_requested.connect(self._on_rename_requested)
        splitter.addWidget(self.object_table)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

    def _build_statusbar(self) -> None:
        self.status_label = QLabel("연결 안 됨")
        self.statusBar().addWidget(self.status_label)

        self.superuser_badge = QLabel("SUPERUSER")
        self.superuser_badge.setProperty("role", "badge-superuser")
        self.superuser_badge.setVisible(False)
        self.statusBar().addWidget(self.superuser_badge)

        self.transfer_label = QLabel("")
        self.transfer_label.setVisible(False)
        self.statusBar().addPermanentWidget(self.transfer_label)

        self.transfer_progress_bar = QProgressBar()
        self.transfer_progress_bar.setMaximumWidth(160)
        self.transfer_progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.transfer_progress_bar)

        self.cancel_transfer_btn = QPushButton("취소")
        self.cancel_transfer_btn.setVisible(False)
        self.cancel_transfer_btn.clicked.connect(self._on_cancel_transfer)
        self.statusBar().addPermanentWidget(self.cancel_transfer_btn)

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
        root = permissions.root_prefix()
        self.tree_panel.set_client(client, bucket=profile.bucket, root_prefix=root)
        self.object_table.set_client(client)
        self._current_bucket = profile.bucket
        self._current_prefix = root
        self.status_label.setText(f"연결됨 · {profile.endpoint_url or 'AWS'}")
        if profile.bucket:
            self.object_table.load(profile.bucket, root)

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

    # ---- 전송: 업로드/다운로드 (7장, 8장) -----------------------------------

    def _on_upload_clicked(self) -> None:
        if self._client is None or not self._current_bucket:
            QMessageBox.information(self, "업로드", "먼저 프로파일에 연결하고 위치를 선택하세요.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "업로드할 파일 선택")
        if not paths:
            return
        self._start_upload([Path(p) for p in paths])

    def _start_upload(self, local_paths: list) -> None:
        if self._client is None or not self._current_bucket:
            QMessageBox.information(self, "업로드", "먼저 프로파일에 연결하고 위치를 선택하세요.")
            return
        if self._transfer_manager is not None and self._transfer_manager.is_running():
            QMessageBox.information(self, "업로드", "다른 전송이 진행 중입니다. 완료 후 다시 시도하세요.")
            return
        bucket = self._current_bucket
        prefix = self._current_prefix
        submit(
            self._pool,
            self._workers,
            expand_upload_paths,
            local_paths,
            bucket,
            prefix,
            on_result=self._on_upload_items_expanded,
            on_error=self._show_error,
        )

    def _on_upload_items_expanded(self, items: list) -> None:
        if not items or self._client is None:
            self._run_transfer("업로드", items)
            return
        submit(
            self._pool,
            self._workers,
            find_existing_keys,
            self._client,
            items,
            on_result=lambda existing: self._confirm_overwrite_and_upload(items, existing),
            on_error=self._show_error,
        )

    def _confirm_overwrite_and_upload(self, items: list, existing_keys: set) -> None:
        """5.4: 동일 키 업로드는 실질적 삭제와 같으므로 덮어쓰기 전 확인한다. 취소 시 해당 항목만 건너뜀."""
        filtered = []
        for item in items:
            if item.kind == "upload" and item.key in existing_keys:
                reply = QMessageBox.question(
                    self, "덮어쓰기 확인", f"'{item.key}' 항목이 이미 있습니다. 덮어쓸까요?"
                )
                if reply != QMessageBox.StandardButton.Yes:
                    continue
            filtered.append(item)
        self._run_transfer("업로드", filtered)

    def _on_download_clicked(self) -> None:
        if self._client is None:
            QMessageBox.information(self, "받기", "먼저 프로파일에 연결하세요.")
            return
        selected = self.object_table.selected_keys()
        if not selected:
            QMessageBox.information(self, "받기", "다운로드할 항목을 선택하세요.")
            return
        self._download_items(selected)

    def _download_items(self, selected: list) -> None:
        if self._client is None or not selected:
            return
        if self._transfer_manager is not None and self._transfer_manager.is_running():
            QMessageBox.information(self, "받기", "다른 전송이 진행 중입니다. 완료 후 다시 시도하세요.")
            return
        dest_dir = QFileDialog.getExistingDirectory(self, "받을 폴더 선택")
        if not dest_dir:
            return
        client = self._client
        bucket = self._current_bucket
        submit(
            self._pool,
            self._workers,
            expand_download_items,
            client,
            bucket,
            selected,
            Path(dest_dir),
            on_result=lambda items: self._run_transfer("받기", items),
            on_error=self._show_error,
        )

    def _on_delete_requested(self, selected: list) -> None:
        """5.1: 삭제는 Superuser 전용. guard()가 최종 방어선이므로 여기서도 확인만 하고 그대로 흘려보낸다."""
        if self._client is None or not selected:
            return
        if self._transfer_manager is not None and self._transfer_manager.is_running():
            QMessageBox.information(self, "삭제", "다른 전송이 진행 중입니다. 완료 후 다시 시도하세요.")
            return
        reply = QMessageBox.question(self, "삭제 확인", f"{len(selected)}개 항목을 삭제할까요? 되돌릴 수 없습니다.")
        if reply != QMessageBox.StandardButton.Yes:
            return
        client = self._client
        bucket = self._current_bucket
        submit(
            self._pool,
            self._workers,
            expand_delete_items,
            client,
            bucket,
            selected,
            on_result=lambda items: self._run_transfer("삭제", items),
            on_error=self._show_error,
        )

    def _on_rename_requested(self, entry: dict) -> None:
        """5.1: 이름변경은 Superuser 전용. 6.3: CopyObject 후 DeleteObject, 폴더는 하위 전체 순회."""
        if self._client is None:
            return
        if self._transfer_manager is not None and self._transfer_manager.is_running():
            QMessageBox.information(self, "이름변경", "다른 전송이 진행 중입니다. 완료 후 다시 시도하세요.")
            return
        current_name = (
            entry["key"].rsplit("/", 1)[-1]
            if entry["kind"] == "file"
            else entry["prefix"].rstrip("/").rsplit("/", 1)[-1]
        )
        new_name, ok = QInputDialog.getText(self, "이름변경", "새 이름", text=current_name)
        if not ok or not new_name.strip() or new_name.strip() == current_name:
            return
        new_name = new_name.strip()
        if _INVALID_NAME_CHARS.search(new_name):
            self._show_error(AppError("E-4005", detail=new_name))
            return
        client = self._client
        bucket = self._current_bucket
        submit(
            self._pool,
            self._workers,
            expand_rename_items,
            client,
            bucket,
            entry,
            new_name,
            on_result=lambda items: self._run_transfer("이름변경", items),
            on_error=self._show_error,
        )

    def _run_transfer(self, label: str, items: list) -> None:
        if not items:
            QMessageBox.information(self, label, "전송할 항목이 없습니다.")
            return
        if self._client is None:
            return
        manager = TransferManager(max_concurrency=self._client.profile.max_concurrency)
        self._transfer_manager = manager
        manager.signals.progress.connect(lambda done, total, lbl=label: self._on_transfer_progress(lbl, done, total))
        manager.signals.all_finished.connect(lambda results, lbl=label: self._on_transfer_finished(lbl, results))

        self.transfer_label.setVisible(True)
        self.transfer_progress_bar.setVisible(True)
        self.transfer_progress_bar.setRange(0, len(items))
        self.transfer_progress_bar.setValue(0)
        self.transfer_label.setText(f"{label} 0/{len(items)}")
        self.cancel_transfer_btn.setVisible(True)

        manager.run(self._client, items)

    def _on_transfer_progress(self, label: str, done: int, total: int) -> None:
        self.transfer_progress_bar.setValue(done)
        self.transfer_label.setText(f"{label} {done}/{total}")

    def _on_cancel_transfer(self) -> None:
        if self._transfer_manager is not None:
            self._transfer_manager.cancel()

    def _on_transfer_finished(self, label: str, results: list) -> None:
        self.transfer_label.setVisible(False)
        self.transfer_progress_bar.setVisible(False)
        self.cancel_transfer_btn.setVisible(False)
        self._transfer_manager = None

        succeeded = [r for r in results if r.success]
        cancelled = [r for r in results if not r.success and r.cancelled]
        failed = [r for r in results if not r.success and not r.cancelled]

        lines = [f"{label} 완료: 성공 {len(succeeded)}건"]
        if cancelled:
            lines.append(f"취소됨 {len(cancelled)}건")
        if failed:
            lines.append(f"실패 {len(failed)}건")
            for r in failed[:10]:
                lines.append(f"  - {r.item.key}: {r.error.display_text()}")
            if len(failed) > 10:
                lines.append(f"  ... 외 {len(failed) - 10}건")

        box = QMessageBox(self)
        box.setWindowTitle(f"{label} 결과")
        box.setIcon(QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information)
        box.setText("\n".join(lines))
        open_log_btn = box.addButton("로그 폴더 열기", QMessageBox.ButtonRole.ActionRole) if failed else None
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if open_log_btn is not None and box.clickedButton() == open_log_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_log_dir())))

        if self._current_bucket:
            self.object_table.load(self._current_bucket, self._current_prefix)
        self.tree_panel.refresh_node()

    # ---- 설정 -----------------------------------------------------------

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(self._store, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self._reload_profile_combo()
            if self._store.active_profile:
                self._connect_profile(self._store.active_profile)

    # ---- 관리자 모드 (5.2) -------------------------------------------------

    def _on_toggle_superuser(self, checked: bool) -> None:
        if checked:
            password, ok = QInputDialog.getText(
                self, "관리자 모드", "비밀번호", QLineEdit.EchoMode.Password
            )
            if not ok:
                self._revert_superuser_action(False)
                return
            try:
                permissions.enable_superuser(password)
            except ValueError:
                QMessageBox.warning(self, "관리자 모드", "비밀번호가 올바르지 않습니다.")
                self._revert_superuser_action(False)
                return
        else:
            permissions.disable_superuser()  # 5.2: 해제는 비밀번호 없이 가능
        self._apply_permission_mode()

    def _revert_superuser_action(self, checked: bool) -> None:
        self.superuser_action.blockSignals(True)
        self.superuser_action.setChecked(checked)
        self.superuser_action.blockSignals(False)

    def _apply_permission_mode(self) -> None:
        self.superuser_badge.setVisible(permissions.is_superuser())
        if self._client is None or not self._current_bucket:
            return
        root = permissions.root_prefix()
        self._current_prefix = root
        self.tree_panel.set_client(self._client, bucket=self._current_bucket, root_prefix=root)
        self.object_table.load(self._current_bucket, root)

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

    # ---- 종료 (7.2: 캐시는 앱 종료 시 정리) -------------------------------

    def closeEvent(self, event) -> None:
        if self._transfer_manager is not None:
            self._transfer_manager.cancel()
        cache.clean_all()
        super().closeEvent(event)
