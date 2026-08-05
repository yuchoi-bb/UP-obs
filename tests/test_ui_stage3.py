"""3단계(드래그앤드롭 양방향) 회귀 테스트: 업로드/다운로드/드래그 흐름."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from moto import mock_aws
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QMessageBox

from app import cache
from app.config import Profile, ProfileStore
from app.ui.main_window import MainWindow

BUCKET = "toolhub-objectstorage"


def pump(app, seconds: float = 3.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    """QMessageBox.exec()은 헤드리스 환경에서 무한 대기하므로 항상 OK로 즉시 반환시킨다."""
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)


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
        Profile(
            name="verify",
            access_key_id="testing",
            secret_access_key="testing",
            region="us-east-1",
            bucket=BUCKET,
        )
    ]
    s.active_profile = "verify"
    return s


def test_start_upload_puts_files_and_refreshes_table(qapp, moto_bucket, store, tmp_path):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"

        local_file = tmp_path / "report.txt"
        local_file.write_text("hello")

        window._start_upload([local_file])
        pump(qapp, 3.0)

        assert window._client.object_exists(BUCKET, "DA-share/report.txt") is True
        names = [window.object_table.table.item(r, 0).text() for r in range(window.object_table.table.rowCount())]
        assert any("report.txt" in n for n in names)
    finally:
        window.close()


def test_start_upload_recurses_folder(qapp, moto_bucket, store, tmp_path):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"

        folder = tmp_path / "builds"
        (folder / "v1").mkdir(parents=True)
        (folder / "v1" / "tool.zip").write_text("x")

        window._start_upload([folder])
        pump(qapp, 3.0)

        assert window._client.object_exists(BUCKET, "DA-share/builds/v1/tool.zip") is True
    finally:
        window.close()


def test_object_table_drop_event_emits_files_dropped(qapp, moto_bucket, store, tmp_path):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"

        local_file = tmp_path / "dropped.txt"
        local_file.write_text("x")

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(local_file))])

        class FakeEvent:
            def __init__(self, mime):
                self._mime = mime
                self.accepted = False

            def mimeData(self):
                return self._mime

            def acceptProposedAction(self):
                self.accepted = True

            def ignore(self):
                pass

        event = FakeEvent(mime)
        window.object_table._handle_drop(event)
        pump(qapp, 3.0)

        assert event.accepted is True
        assert window._client.object_exists(BUCKET, "DA-share/dropped.txt") is True
    finally:
        window.close()


def test_drag_download_to_cache_under_threshold(qapp, moto_bucket, store, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._client.put_object(BUCKET, "DA-share/small.txt", _write_tmp(tmp_path, "small.txt", "hi"))
        window.object_table.load(BUCKET, "DA-share/")
        pump(qapp, 2.0)

        selected = [{"kind": "file", "key": "DA-share/small.txt", "size": 2}]
        local_paths = window.object_table._download_selection_to_cache(selected)

        assert len(local_paths) == 1
        assert local_paths[0].read_text() == "hi"
        assert local_paths[0].parent == cache.cache_dir() / BUCKET / "DA-share"
    finally:
        window.close()


def test_drag_blocked_when_over_50mb_threshold(qapp, moto_bucket, store, monkeypatch):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        tooltip_calls = []
        monkeypatch.setattr(
            "app.ui.object_table.QToolTip.showText", lambda pos, text, widget=None: tooltip_calls.append(text)
        )

        selected = [{"kind": "file", "key": "DA-share/big.bin", "size": 60 * 1024 * 1024}]
        monkeypatch.setattr(window.object_table, "selected_keys", lambda: selected)

        window.object_table._handle_drag_start()

        assert len(tooltip_calls) == 1
        assert "50MB" in tooltip_calls[0]
    finally:
        window.close()


def test_new_folder_then_download_button_flow(qapp, moto_bucket, store, tmp_path, monkeypatch):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._client.put_object(BUCKET, "DA-share/a.txt", _write_tmp(tmp_path, "a.txt", "content-a"))
        window.object_table.load(BUCKET, "DA-share/")
        pump(qapp, 2.0)

        dest_dir = tmp_path / "downloaded"
        dest_dir.mkdir()
        monkeypatch.setattr(
            "app.ui.main_window.QFileDialog.getExistingDirectory", lambda *a, **k: str(dest_dir)
        )
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"
        monkeypatch.setattr(window.object_table, "selected_keys", lambda: [{"kind": "file", "key": "DA-share/a.txt"}])

        window._on_download_clicked()
        pump(qapp, 3.0)

        assert (dest_dir / "a.txt").read_text() == "content-a"
    finally:
        window.close()


def _write_tmp(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p
