"""2단계(메인 윈도우/트리/목록) 회귀 테스트.

QThreadPool 워커가 완료 전 GC되어 시그널이 유실되지 않는지가 핵심 검증
대상이다 (transfer_worker.submit 참고).
"""
import time

import pytest
from moto import mock_aws

from app.config import Profile, ProfileStore
from app.ui.main_window import MainWindow

BUCKET = "toolhub-objectstorage"


def pump(app, seconds: float = 2.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture
def moto_bucket():
    with mock_aws():
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="DA-share/", Body=b"")
        s3.put_object(Bucket=BUCKET, Key="DA-share/builds/", Body=b"")
        s3.put_object(Bucket=BUCKET, Key="DA-share/builds/tool.zip", Body=b"x" * 1024)
        s3.put_object(Bucket=BUCKET, Key="DA-share/logs/app.log", Body=b"log")
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


def test_main_window_loads_root_listing(qapp, moto_bucket, store):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        row_count = window.object_table.table.rowCount()
        assert row_count == 1
        assert "DA-share" in window.object_table.table.item(0, 0).text()
    finally:
        window.close()


def test_main_window_navigate_into_folder(qapp, moto_bucket, store):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._on_path_selected(BUCKET, "DA-share/")
        pump(qapp, 2.0)
        names = [window.object_table.table.item(r, 0).text() for r in range(window.object_table.table.rowCount())]
        assert any("builds" in n for n in names)
        assert any("logs" in n for n in names)
        # 6.2: 빈 폴더 자기 자신 키(DA-share/)는 목록에서 제외되어야 함
        assert not any(n.strip() in ("📁", "📄") for n in names)
    finally:
        window.close()


def test_tree_lazy_load_on_expand(qapp, moto_bucket, store):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        root_item = window.tree_panel.tree.topLevelItem(0)
        window.tree_panel.tree.expandItem(root_item)
        pump(qapp, 2.0)
        child_labels = [root_item.child(i).text(0) for i in range(root_item.childCount())]
        assert "DA-share" in child_labels
    finally:
        window.close()


def test_new_folder_creates_marker_and_refreshes(qapp, moto_bucket, store):
    window = MainWindow(store)
    pump(qapp, 2.0)
    try:
        window._current_bucket = BUCKET
        window._current_prefix = "DA-share/"
        window._client.create_folder(BUCKET, "DA-share/new-folder")
        window.object_table.load(BUCKET, "DA-share/")
        pump(qapp, 2.0)
        names = [window.object_table.table.item(r, 0).text() for r in range(window.object_table.table.rowCount())]
        assert any("new-folder" in n for n in names)
    finally:
        window.close()
