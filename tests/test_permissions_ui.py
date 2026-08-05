"""5장 권한 모델 UI 통합 테스트: 모드 전환, 루트 스코프, 컨텍스트 메뉴 가드, 우회 시도."""
from __future__ import annotations

import time

import pytest
from moto import mock_aws
from PySide6.QtWidgets import QInputDialog, QMessageBox

from app import permissions
from app.config import Profile, ProfileStore
from app.errors import AppError
from app.ui.main_window import MainWindow

BUCKET = "toolhub-objectstorage"


def pump(app, seconds: float = 2.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    """QMessageBox.exec()과 warning/information/critical 정적 메서드 모두 헤드리스에서 무한 대기하므로 우회한다."""
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture(autouse=True)
def reset_permissions():
    permissions.disable_superuser()
    yield
    permissions.disable_superuser()


@pytest.fixture
def moto_bucket():
    with mock_aws():
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="DA-share/", Body=b"")
        s3.put_object(Bucket=BUCKET, Key="DA-share/a.txt", Body=b"x")
        s3.put_object(Bucket=BUCKET, Key="outside/secret.txt", Body=b"x")
        yield


@pytest.fixture
def store(tmp_path):
    s = ProfileStore(config_path=tmp_path / "config.json")
    s.profiles = [
        Profile(name="verify", access_key_id="testing", secret_access_key="testing", region="us-east-1", bucket=BUCKET)
    ]
    s.active_profile = "verify"
    return s


def test_starts_in_normal_mode_rooted_at_da_share(qapp, moto_bucket, store):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        assert permissions.is_superuser() is False
        assert window._current_prefix == "DA-share/"
        assert window.superuser_badge.isVisible() is False
        names = [window.object_table.table.item(r, 0).text() for r in range(window.object_table.table.rowCount())]
        assert any("a.txt" in n for n in names)
        assert not any("outside" in n for n in names)
    finally:
        window.close()


def test_enable_superuser_with_correct_password_switches_root(qapp, moto_bucket, store, monkeypatch):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: (permissions.SUPERUSER_PASSWORD, True))
        )
        window.superuser_action.setChecked(True)
        pump(qapp, 2.0)

        assert permissions.is_superuser() is True
        assert window.superuser_badge.isVisible() is True
        assert window._current_prefix == ""
        names = [window.object_table.table.item(r, 0).text() for r in range(window.object_table.table.rowCount())]
        assert any("DA-share" in n for n in names)
        assert any("outside" in n for n in names)
    finally:
        window.close()


def test_enable_superuser_wrong_password_stays_normal_mode(qapp, moto_bucket, store, monkeypatch):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("wrong", True)))
        window.superuser_action.setChecked(True)
        pump(qapp, 2.0)

        assert permissions.is_superuser() is False
        assert window.superuser_action.isChecked() is False
        assert window.superuser_badge.isVisible() is False
    finally:
        window.close()


def test_disable_superuser_needs_no_password_and_resets_root(qapp, moto_bucket, store):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        permissions.enable_superuser(permissions.SUPERUSER_PASSWORD)
        window._revert_superuser_action(True)  # UI 체크 상태만 맞추고
        window._apply_permission_mode()
        pump(qapp, 1.0)
        assert window._current_prefix == ""

        window.superuser_action.setChecked(False)
        pump(qapp, 2.0)

        assert permissions.is_superuser() is False
        assert window._current_prefix == "DA-share/"
        assert window.superuser_badge.isVisible() is False
    finally:
        window.close()


def test_context_menu_hides_delete_rename_in_normal_mode(qapp, moto_bucket, store):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        selected = [{"kind": "file", "key": "DA-share/a.txt", "size": 1}]
        menu = window.object_table._build_context_menu(selected)
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "삭제" not in labels
        assert "이름변경" not in labels
        assert "다운로드" in labels
    finally:
        window.close()


def test_context_menu_shows_delete_rename_in_superuser_mode(qapp, moto_bucket, store):
    permissions.enable_superuser(permissions.SUPERUSER_PASSWORD)
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        selected = [{"kind": "file", "key": "DA-share/a.txt", "size": 1}]
        menu = window.object_table._build_context_menu(selected)
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "삭제" in labels
        assert "이름변경" in labels
    finally:
        window.close()


def test_delete_requested_blocked_by_guard_when_bypassed_in_normal_mode(qapp, moto_bucket, store, monkeypatch):
    """컨텍스트 메뉴는 숨겨지지만, 신호가 강제로 발생해도 guard()가 최종 차단해야 한다."""
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

        captured = {}
        original_finish = window._on_transfer_finished

        def spy(label, results):
            captured["results"] = results
            return original_finish(label, results)

        monkeypatch.setattr(window, "_on_transfer_finished", spy)

        window._on_delete_requested([{"kind": "file", "key": "DA-share/a.txt", "size": 1}])
        pump(qapp, 2.0)

        results = captured["results"]
        assert len(results) == 1
        assert results[0].success is False
        assert isinstance(results[0].error, AppError)
        assert results[0].error.code == "E-3010"
        assert window._client.object_exists(BUCKET, "DA-share/a.txt") is True
    finally:
        window.close()


def test_rename_and_delete_succeed_in_superuser_mode(qapp, moto_bucket, store, monkeypatch):
    permissions.enable_superuser(permissions.SUPERUSER_PASSWORD)
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("renamed.txt", True))
        )
        window._on_rename_requested({"kind": "file", "key": "DA-share/a.txt"})
        pump(qapp, 2.0)
        assert window._client.object_exists(BUCKET, "DA-share/renamed.txt") is True
        assert window._client.object_exists(BUCKET, "DA-share/a.txt") is False

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        window._on_delete_requested([{"kind": "file", "key": "DA-share/renamed.txt", "size": 1}])
        pump(qapp, 2.0)
        assert window._client.object_exists(BUCKET, "DA-share/renamed.txt") is False
    finally:
        window.close()


def test_overwrite_confirmation_skips_when_cancelled(qapp, moto_bucket, store, tmp_path, monkeypatch):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"

        local_file = tmp_path / "a.txt"
        local_file.write_text("new content")

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
        window._start_upload([local_file])
        pump(qapp, 2.0)

        # 취소했으므로 기존 내용이 유지되어야 한다
        import io

        buf = io.BytesIO()
        window._client._client.download_fileobj(BUCKET, "DA-share/a.txt", buf)
        assert buf.getvalue() == b"x"
    finally:
        window.close()


def test_overwrite_confirmation_proceeds_when_confirmed(qapp, moto_bucket, store, tmp_path, monkeypatch):
    window = MainWindow(store)
    window.show()
    pump(qapp, 2.0)
    try:
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"

        local_file = tmp_path / "a.txt"
        local_file.write_text("new content")

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        window._start_upload([local_file])
        pump(qapp, 2.0)

        import io

        buf = io.BytesIO()
        window._client._client.download_fileobj(BUCKET, "DA-share/a.txt", buf)
        assert buf.getvalue() == b"new content"
    finally:
        window.close()
