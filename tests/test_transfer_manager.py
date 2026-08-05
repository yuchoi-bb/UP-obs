"""TransferManager 회귀 테스트 (7장 업로드/다운로드 확장, 8장 진행률/취소/실패 수집)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from moto import mock_aws

from app.config import Profile
from app.errors import AppError
from app.s3client import S3Client
from app.ui.transfer_worker import (
    TransferItem,
    TransferManager,
    expand_download_items,
    expand_upload_paths,
)

BUCKET = "toolhub-objectstorage"


def pump(app, seconds: float = 3.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture
def client():
    with mock_aws():
        profile = Profile(
            name="t", access_key_id="testing", secret_access_key="testing", region="us-east-1", bucket=BUCKET
        )
        c = S3Client(profile)
        c._client.create_bucket(Bucket=BUCKET)
        yield c


def test_expand_upload_paths_recurses_and_marks_empty_dirs(tmp_path):
    root = tmp_path / "builds"
    (root / "v1").mkdir(parents=True)
    (root / "v1" / "tool.zip").write_text("x")
    (root / "empty").mkdir(parents=True)

    items = expand_upload_paths([root], BUCKET, "DA-share/")

    by_key = {item.key: item for item in items}
    assert by_key["DA-share/builds/v1/tool.zip"].kind == "upload"
    assert by_key["DA-share/builds/empty"].kind == "mkdir"


def test_expand_download_items_file_and_folder(client):
    client._client.put_object(Bucket=BUCKET, Key="DA-share/a.txt", Body=b"x")
    client._client.put_object(Bucket=BUCKET, Key="DA-share/builds/", Body=b"")
    client._client.put_object(Bucket=BUCKET, Key="DA-share/builds/tool.zip", Body=b"x")

    selected = [
        {"kind": "file", "key": "DA-share/a.txt"},
        {"kind": "folder", "prefix": "DA-share/builds/"},
    ]
    items = expand_download_items(client, BUCKET, selected, Path("/tmp/dest"))
    keys = {item.key for item in items}
    assert keys == {"DA-share/a.txt", "DA-share/builds/tool.zip"}


def test_transfer_manager_upload_and_download_roundtrip(qapp, client, tmp_path):
    src = tmp_path / "upload.txt"
    src.write_text("hello")
    manager = TransferManager(max_concurrency=2)

    finished = {}
    manager.signals.all_finished.connect(lambda results: finished.setdefault("results", results))

    manager.run(client, [TransferItem(kind="upload", bucket=BUCKET, key="DA-share/upload.txt", local_path=src)])
    pump(qapp, 2.0)

    assert "results" in finished
    assert finished["results"][0].success is True
    assert client.object_exists(BUCKET, "DA-share/upload.txt") is True

    finished.clear()
    dest = tmp_path / "download.txt"
    manager.run(client, [TransferItem(kind="download", bucket=BUCKET, key="DA-share/upload.txt", local_path=dest)])
    pump(qapp, 2.0)

    assert finished["results"][0].success is True
    assert dest.read_text() == "hello"


def test_transfer_manager_collects_failures_without_aborting(qapp, client, tmp_path):
    manager = TransferManager(max_concurrency=2)
    finished = {}
    manager.signals.all_finished.connect(lambda results: finished.setdefault("results", results))

    good = tmp_path / "ok.txt"
    good.write_text("ok")
    items = [
        TransferItem(kind="upload", bucket=BUCKET, key="DA-share/ok.txt", local_path=good),
        TransferItem(kind="download", bucket=BUCKET, key="DA-share/missing.txt", local_path=tmp_path / "out.txt"),
    ]
    manager.run(client, items)
    pump(qapp, 2.0)

    results = finished["results"]
    assert len(results) == 2
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success and not r.cancelled]
    assert len(succeeded) == 1
    assert len(failed) == 1
    assert isinstance(failed[0].error, AppError)
    assert failed[0].error.code == "E-3007"


def test_transfer_manager_mkdir_item(qapp, client):
    manager = TransferManager(max_concurrency=1)
    finished = {}
    manager.signals.all_finished.connect(lambda results: finished.setdefault("results", results))

    manager.run(client, [TransferItem(kind="mkdir", bucket=BUCKET, key="DA-share/new-empty")])
    pump(qapp, 2.0)

    assert finished["results"][0].success is True
    assert client.object_exists(BUCKET, "DA-share/new-empty/") is True


def test_transfer_manager_cancel_stops_pending_items(qapp, client, tmp_path, monkeypatch):
    manager = TransferManager(max_concurrency=1)  # 순차 실행으로 취소 타이밍을 결정적으로 만듦
    finished = {}
    manager.signals.all_finished.connect(lambda results: finished.setdefault("results", results))

    original_put = client.put_object

    def slow_put(bucket, key, path):
        time.sleep(0.3)
        return original_put(bucket, key, path)

    monkeypatch.setattr(client, "put_object", slow_put)

    files = []
    for i in range(3):
        f = tmp_path / f"f{i}.txt"
        f.write_text("x")
        files.append(f)
    items = [TransferItem(kind="upload", bucket=BUCKET, key=f"DA-share/f{i}.txt", local_path=files[i]) for i in range(3)]

    manager.run(client, items)
    manager.cancel()
    pump(qapp, 3.0)

    results = finished["results"]
    assert len(results) == 3
    cancelled = [r for r in results if r.cancelled]
    assert len(cancelled) >= 1  # 첫 항목 실행 중 취소 -> 뒤 항목들은 시작 전에 취소됨
