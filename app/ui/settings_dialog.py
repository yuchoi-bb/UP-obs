"""설정창: 프로파일 목록 관리 + 기본/고급 항목 편집 (3장)."""
from __future__ import annotations

import dataclasses
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.config import Profile, ProfileStore, import_aws_cli_profiles

_CUSTOM_CA_LABEL = "CA 파일 경로..."


class _ImportProfilesDialog(QDialog):
    """~/.aws/credentials 에서 가져올 프로파일을 고르는 보조 창 (읽기 전용, 3.4)."""

    def __init__(self, profiles: list[Profile], parent=None):
        super().__init__(parent)
        self.setWindowTitle("AWS CLI 프로파일 가져오기")
        self._profiles = profiles

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for profile in profiles:
            item = QListWidgetItem(f"{profile.name}  ({profile.access_key_id[:4]}****)")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_profiles(self) -> list[Profile]:
        result = []
        for idx in range(self.list_widget.count()):
            item = self.list_widget.item(idx)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(self._profiles[idx])
        return result


class SettingsDialog(QDialog):
    """프로파일 CRUD + 활성 프로파일 선택."""

    def __init__(self, store: ProfileStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.resize(680, 480)

        self._store = store
        self._profiles: list[Profile] = [dataclasses.replace(p) for p in store.profiles]
        self._active_name: Optional[str] = store.active_profile
        self._current_index: int = -1

        self._build_ui()
        self._refresh_list()
        if self._profiles:
            self.list_widget.setCurrentRow(0)

    # ---- UI 구성 ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        left.addWidget(self.list_widget)

        list_buttons = QHBoxLayout()
        add_btn = QPushButton("추가")
        add_btn.clicked.connect(self._add_profile)
        remove_btn = QPushButton("삭제")
        remove_btn.clicked.connect(self._remove_profile)
        activate_btn = QPushButton("활성으로 설정")
        activate_btn.clicked.connect(self._activate_profile)
        list_buttons.addWidget(add_btn)
        list_buttons.addWidget(remove_btn)
        list_buttons.addWidget(activate_btn)
        left.addLayout(list_buttons)

        import_btn = QPushButton("AWS CLI 프로파일 가져오기")
        import_btn.clicked.connect(self._import_from_aws_cli)
        left.addWidget(import_btn)

        root.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("빈값 = AWS (virtual-hosted)")
        self.access_key_edit = QLineEdit()
        self.secret_key_edit = QLineEdit()
        self.secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        secret_row = QHBoxLayout()
        secret_row.addWidget(self.secret_key_edit)
        self.secret_toggle_btn = QToolButton()
        self.secret_toggle_btn.setText("표시")
        self.secret_toggle_btn.setCheckable(True)
        self.secret_toggle_btn.toggled.connect(self._toggle_secret_visibility)
        secret_row.addWidget(self.secret_toggle_btn)
        secret_widget = QWidget()
        secret_widget.setLayout(secret_row)
        self.region_edit = QLineEdit("us-east-1")

        form.addRow("프로파일 이름", self.name_edit)
        form.addRow("Endpoint URL", self.endpoint_edit)
        form.addRow("Access Key ID", self.access_key_edit)
        form.addRow("Secret Access Key", secret_widget)
        form.addRow("Region", self.region_edit)
        right.addLayout(form)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("고급 설정 ▸")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        right.addWidget(self.advanced_toggle)

        self.advanced_box = QFrame()
        adv_form = QFormLayout(self.advanced_box)

        self.bucket_edit = QLineEdit()
        self.bucket_edit.setPlaceholderText("비우면 ListBuckets 시도")
        adv_form.addRow("Bucket", self.bucket_edit)

        self.addressing_combo = QComboBox()
        self.addressing_combo.addItems(["auto", "path", "virtual"])
        adv_form.addRow("주소 방식", self.addressing_combo)

        ssl_row = QHBoxLayout()
        self.ssl_combo = QComboBox()
        self.ssl_combo.addItems(["true", "false", _CUSTOM_CA_LABEL])
        self.ssl_combo.currentTextChanged.connect(self._on_ssl_combo_changed)
        self.ssl_ca_edit = QLineEdit()
        self.ssl_ca_edit.setEnabled(False)
        ssl_browse_btn = QPushButton("찾아보기")
        ssl_browse_btn.clicked.connect(self._browse_ca_file)
        ssl_row.addWidget(self.ssl_combo)
        ssl_row.addWidget(self.ssl_ca_edit)
        ssl_row.addWidget(ssl_browse_btn)
        ssl_widget = QWidget()
        ssl_widget.setLayout(ssl_row)
        adv_form.addRow("SSL 검증", ssl_widget)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("비우면 프록시 미사용")
        adv_form.addRow("프록시", self.proxy_edit)

        self.connect_timeout_spin = QSpinBox()
        self.connect_timeout_spin.setRange(1, 300)
        self.connect_timeout_spin.setValue(10)
        adv_form.addRow("연결 타임아웃(초)", self.connect_timeout_spin)

        self.read_timeout_spin = QSpinBox()
        self.read_timeout_spin.setRange(1, 900)
        self.read_timeout_spin.setValue(60)
        adv_form.addRow("읽기 타임아웃(초)", self.read_timeout_spin)

        self.max_retries_spin = QSpinBox()
        self.max_retries_spin.setRange(0, 10)
        self.max_retries_spin.setValue(3)
        adv_form.addRow("재시도 횟수", self.max_retries_spin)

        self.multipart_spin = QSpinBox()
        self.multipart_spin.setRange(5, 5000)
        self.multipart_spin.setValue(64)
        self.multipart_spin.setSuffix(" MB")
        adv_form.addRow("Multipart 임계값", self.multipart_spin)

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 32)
        self.concurrency_spin.setValue(4)
        adv_form.addRow("동시 전송 수", self.concurrency_spin)

        self.advanced_box.setVisible(False)
        right.addWidget(self.advanced_box)
        right.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)

        root.addLayout(right, 2)

    # ---- 프로파일 목록 <-> 폼 동기화 ----------------------------------

    def _refresh_list(self) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for profile in self._profiles:
            label = profile.name
            if profile.name == self._active_name:
                label = f"● {label}"
            self.list_widget.addItem(label)
        self.list_widget.blockSignals(False)

    def _flush_form_to_current(self) -> None:
        if self._current_index < 0 or self._current_index >= len(self._profiles):
            return
        profile = self._profiles[self._current_index]
        profile.name = self.name_edit.text().strip()
        profile.endpoint_url = self.endpoint_edit.text().strip()
        profile.access_key_id = self.access_key_edit.text().strip()
        profile.secret_access_key = self.secret_key_edit.text()
        profile.region = self.region_edit.text().strip() or "us-east-1"
        profile.bucket = self.bucket_edit.text().strip()
        profile.addressing_style = self.addressing_combo.currentText()
        if self.ssl_combo.currentText() == _CUSTOM_CA_LABEL:
            profile.verify_ssl = self.ssl_ca_edit.text().strip()
        else:
            profile.verify_ssl = self.ssl_combo.currentText()
        profile.proxy = self.proxy_edit.text().strip()
        profile.connect_timeout = self.connect_timeout_spin.value()
        profile.read_timeout = self.read_timeout_spin.value()
        profile.max_retries = self.max_retries_spin.value()
        profile.multipart_threshold = self.multipart_spin.value() * 1024 * 1024
        profile.max_concurrency = self.concurrency_spin.value()
        profile.__post_init__()  # 2.2 정규화 재적용 (bucket s3://, endpoint_url 끝 '/')

    def _load_profile_into_form(self, profile: Profile) -> None:
        self.name_edit.setText(profile.name)
        self.endpoint_edit.setText(profile.endpoint_url)
        self.access_key_edit.setText(profile.access_key_id)
        self.secret_key_edit.setText(profile.secret_access_key)
        self.region_edit.setText(profile.region)
        self.bucket_edit.setText(profile.bucket)
        self.addressing_combo.setCurrentText(profile.addressing_style)
        if profile.verify_ssl in ("true", "false"):
            self.ssl_combo.setCurrentText(profile.verify_ssl)
            self.ssl_ca_edit.setText("")
        else:
            self.ssl_combo.setCurrentText(_CUSTOM_CA_LABEL)
            self.ssl_ca_edit.setText(profile.verify_ssl)
        self.proxy_edit.setText(profile.proxy)
        self.connect_timeout_spin.setValue(profile.connect_timeout)
        self.read_timeout_spin.setValue(profile.read_timeout)
        self.max_retries_spin.setValue(profile.max_retries)
        self.multipart_spin.setValue(max(profile.multipart_threshold // (1024 * 1024), 1))
        self.concurrency_spin.setValue(profile.max_concurrency)

    def _on_row_changed(self, row: int) -> None:
        self._flush_form_to_current()
        self._current_index = row
        if 0 <= row < len(self._profiles):
            self._load_profile_into_form(self._profiles[row])
            self.setEnabled(True)

    # ---- 버튼 핸들러 ---------------------------------------------------

    def _add_profile(self) -> None:
        self._flush_form_to_current()
        new_profile = Profile(name=f"새 프로파일 {len(self._profiles) + 1}")
        self._profiles.append(new_profile)
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._profiles) - 1)

    def _remove_profile(self) -> None:
        if self._current_index < 0:
            return
        profile = self._profiles[self._current_index]
        reply = QMessageBox.question(self, "프로파일 삭제", f"'{profile.name}' 프로파일을 삭제할까요?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self._profiles[self._current_index]
        if self._active_name == profile.name:
            self._active_name = self._profiles[0].name if self._profiles else None
        self._current_index = -1
        self._refresh_list()
        if self._profiles:
            self.list_widget.setCurrentRow(0)
        else:
            self.name_edit.clear()

    def _activate_profile(self) -> None:
        if self._current_index < 0:
            return
        self._flush_form_to_current()
        self._active_name = self._profiles[self._current_index].name
        self._refresh_list()
        self.list_widget.setCurrentRow(self._current_index)

    def _toggle_secret_visibility(self, checked: bool) -> None:
        self.secret_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.secret_toggle_btn.setText("숨기기" if checked else "표시")

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_box.setVisible(checked)
        self.advanced_toggle.setText("고급 설정 ▾" if checked else "고급 설정 ▸")

    def _on_ssl_combo_changed(self, text: str) -> None:
        self.ssl_ca_edit.setEnabled(text == _CUSTOM_CA_LABEL)

    def _browse_ca_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CA 인증서 파일 선택")
        if path:
            self.ssl_combo.setCurrentText(_CUSTOM_CA_LABEL)
            self.ssl_ca_edit.setText(path)

    def _import_from_aws_cli(self) -> None:
        imported = import_aws_cli_profiles()
        if not imported:
            QMessageBox.information(self, "가져오기", "~/.aws/credentials 에서 가져올 프로파일이 없습니다.")
            return
        dialog = _ImportProfilesDialog(imported, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_profiles()
        if not selected:
            return
        self._flush_form_to_current()
        existing_names = {p.name for p in self._profiles}
        for profile in selected:
            name = profile.name
            suffix = 2
            while name in existing_names:
                name = f"{profile.name} ({suffix})"
                suffix += 1
            profile.name = name
            existing_names.add(name)
            self._profiles.append(profile)
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._profiles) - 1)

    def _on_save(self) -> None:
        self._flush_form_to_current()
        try:
            for profile in self._profiles:
                profile.validate()
        except Exception as exc:  # AppError
            QMessageBox.critical(self, "설정 오류", getattr(exc, "display_text", lambda: str(exc))())
            return

        self._store.profiles = self._profiles
        self._store.active_profile = self._active_name or (self._profiles[0].name if self._profiles else None)
        try:
            self._store.save()
        except Exception as exc:  # AppError
            QMessageBox.critical(self, "저장 실패", getattr(exc, "display_text", lambda: str(exc))())
            return
        self.accept()
