"""11장 자동 업데이트 UI 통합 테스트 (네트워크는 모두 모킹)."""
from __future__ import annotations

import time

import pytest
from moto import mock_aws
from PySide6.QtWidgets import QMessageBox

from app import permissions, updater
from app.config import Profile, ProfileStore
from app.ui.main_window import MainWindow

BUCKET = "toolhub-objectstorage"


def pump(app, seconds: float = 2.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture(autouse=True)
def reset_permissions():
    permissions.disable_superuser()
    yield
    permissions.disable_superuser()


@pytest.fixture(autouse=True)
def no_real_background_check(monkeypatch):
    """MainWindow가 생성될 때마다 3초 뒤 백그라운드 확인 타이머가 걸린다.

    테스트는 각 흐름을 직접 호출해 검증하므로, 자동 타이머가 실제 네트워크를
    두드리지 않도록 fetch_latest_release 자체를 항상 안전한 실패로 막아둔다.
    개별 테스트에서 필요하면 다시 monkeypatch해서 원하는 동작을 준다.
    """
    from app.errors import AppError

    def blocked(*a, **k):
        raise AppError("E-5001", detail="blocked in tests")

    monkeypatch.setattr(updater, "fetch_latest_release", blocked)


@pytest.fixture
def moto_bucket():
    with mock_aws():
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="DA-share/", Body=b"")
        yield


@pytest.fixture
def store(tmp_path):
    s = ProfileStore(config_path=tmp_path / "config.json")
    s.profiles = [
        Profile(name="verify", access_key_id="testing", secret_access_key="testing", region="us-east-1", bucket=BUCKET)
    ]
    s.active_profile = "verify"
    return s


def test_background_check_shows_banner_when_newer(qapp, moto_bucket, store, monkeypatch):
    monkeypatch.setattr(
        updater, "fetch_latest_release", lambda: {"tag_name": "v9.9.9", "assets": []}
    )
    window = MainWindow(store)
    window.show()
    pump(qapp, 1.0)
    try:
        assert window.update_banner.isVisible() is False
        window._check_update_background()
        pump(qapp, 1.0)
        assert window.update_banner.isVisible() is True
        assert "v9.9.9" in window.update_banner_label.text()
    finally:
        window.close()


def test_background_check_stays_silent_on_failure(qapp, moto_bucket, store):
    window = MainWindow(store)
    window.show()
    pump(qapp, 1.0)
    try:
        window._check_update_background()
        pump(qapp, 1.0)
        assert window.update_banner.isVisible() is False
    finally:
        window.close()


def test_background_check_hides_banner_when_already_latest(qapp, moto_bucket, store, monkeypatch):
    monkeypatch.setattr(
        updater, "fetch_latest_release", lambda: {"tag_name": "v0.1.0", "assets": []}
    )
    window = MainWindow(store)
    window.show()
    pump(qapp, 1.0)
    try:
        window._check_update_background()
        pump(qapp, 1.0)
        assert window.update_banner.isVisible() is False
    finally:
        window.close()


def test_manual_check_shows_explicit_result_on_failure(qapp, moto_bucket, store, monkeypatch):
    errors = []
    monkeypatch.setattr(MainWindow, "_show_error", lambda self, err: errors.append(err))
    window = MainWindow(store)
    window.show()
    pump(qapp, 1.0)
    try:
        window._check_update_manual()
        pump(qapp, 1.0)
        assert len(errors) == 1
        assert errors[0].code == "E-5001"
    finally:
        window.close()


def test_download_update_flow_and_apply(qapp, moto_bucket, store, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    release = {"tag_name": "v9.9.9", "assets": [{"name": "UP-obs-v9.9.9.exe", "browser_download_url": "http://x/exe"}]}

    def fake_perform_update(parent_pid, rel, progress_cb=None):
        if progress_cb:
            progress_cb(50, 100)
            progress_cb(100, 100)
        script = tmp_path / "updater.cmd"
        script.write_text("@echo off\n")
        return script

    monkeypatch.setattr(updater, "perform_update", fake_perform_update)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    launched = []
    monkeypatch.setattr(updater, "launch_updater", lambda path: launched.append(path))

    window = MainWindow(store)
    window.show()
    pump(qapp, 1.0)
    try:
        window._show_update_banner(release)
        assert window.update_banner.isVisible() is True

        window._on_download_update_clicked()
        pump(qapp, 2.0)

        assert len(launched) == 1
        assert launched[0] == tmp_path / "updater.cmd"
    finally:
        window.close()


def test_download_update_failure_reenables_banner_button(qapp, moto_bucket, store, monkeypatch):
    release = {"tag_name": "v9.9.9", "assets": []}  # find_exe_asset이 E-5002를 던짐

    window = MainWindow(store)
    window.show()
    pump(qapp, 1.0)
    try:
        window._show_update_banner(release)
        window._on_download_update_clicked()
        pump(qapp, 2.0)

        assert window.update_banner_button.isEnabled() is True
        assert window.transfer_label.isVisible() is False
    finally:
        window.close()
