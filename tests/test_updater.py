"""11장 자동 업데이트 회귀 테스트."""
from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import pytest

from app import updater
from app.errors import AppError


def test_parse_version_basic():
    assert updater.parse_version("v1.3.0") == (1, 3, 0)
    assert updater.parse_version("1.3.0") == (1, 3, 0)
    assert updater.parse_version("v0.1.0") == (0, 1, 0)


def test_is_newer_true_and_false():
    assert updater.is_newer("v1.3.0", current_version="0.1.0") is True
    assert updater.is_newer("v0.1.0", current_version="0.1.0") is False
    assert updater.is_newer("v0.0.9", current_version="0.1.0") is False


def test_find_exe_asset_matches_expected_name():
    release = {
        "tag_name": "v1.3.0",
        "assets": [
            {"name": "source.zip", "browser_download_url": "http://x/source.zip"},
            {"name": "UP-obs-v1.3.0.exe", "browser_download_url": "http://x/UP-obs-v1.3.0.exe"},
        ],
    }
    asset = updater.find_exe_asset(release)
    assert asset["name"] == "UP-obs-v1.3.0.exe"


def test_find_exe_asset_missing_raises_e5002():
    release = {"tag_name": "v1.3.0", "assets": [{"name": "readme.txt"}]}
    with pytest.raises(AppError) as exc_info:
        updater.find_exe_asset(release)
    assert exc_info.value.code == "E-5002"


def test_fetch_latest_release_success(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"tag_name": "v1.3.0", "assets": []}'

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    release = updater.fetch_latest_release()
    assert release["tag_name"] == "v1.3.0"


def test_fetch_latest_release_failure_raises_e5001(monkeypatch):
    def raise_error(*a, **k):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(updater.urllib.request, "urlopen", raise_error)
    with pytest.raises(AppError) as exc_info:
        updater.fetch_latest_release()
    assert exc_info.value.code == "E-5001"


def test_download_asset_writes_file_and_reports_progress(tmp_path, monkeypatch):
    content = b"x" * 1000

    class FakeResponse:
        def __init__(self):
            self.headers = {"Content-Length": str(len(content))}
            self._buf = io.BytesIO(content)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return self._buf.read(n)

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: FakeResponse())

    progress_calls = []
    dest = tmp_path / "UP-obs-v1.3.0.exe"
    updater.download_asset("http://x/exe", dest, progress_cb=lambda done, total: progress_calls.append((done, total)))

    assert dest.read_bytes() == content
    assert progress_calls[-1][0] == len(content)
    assert progress_calls[-1][1] == len(content)


def test_download_asset_failure_raises_e5003(monkeypatch, tmp_path):
    def raise_error(*a, **k):
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(updater.urllib.request, "urlopen", raise_error)
    with pytest.raises(AppError) as exc_info:
        updater.download_asset("http://x/exe", tmp_path / "out.exe")
    assert exc_info.value.code == "E-5003"


def test_build_updater_script_contains_required_steps():
    script = updater.build_updater_script(
        parent_pid=1234,
        target_exe=Path(r"C:\Program Files\UP-obs\UP-obs.exe"),
        new_exe=Path(r"C:\Users\me\AppData\Local\S3Explorer\update\UP-obs-v1.3.0.exe"),
        log_file=Path(r"C:\Users\me\AppData\Local\S3Explorer\logs\s3explorer.log"),
    )
    assert "1234" in script
    assert "UP-obs.exe" in script
    assert "UP-obs-v1.3.0.exe" in script
    assert ":WAITLOOP" in script
    assert ":DOUPDATE" in script
    assert ":RESTORE" in script
    assert "E-5004" in script
    assert ".bak" in script.lower()


def test_write_updater_script_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    script_path = updater.write_updater_script(
        parent_pid=1, target_exe=Path("target.exe"), new_exe=Path("new.exe")
    )
    assert script_path.exists()
    assert script_path.name == "updater.cmd"
    assert script_path.parent == tmp_path / "S3Explorer" / "update"


def test_perform_update_downloads_and_writes_script(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    release = {
        "tag_name": "v1.3.0",
        "assets": [{"name": "UP-obs-v1.3.0.exe", "browser_download_url": "http://x/exe"}],
    }
    monkeypatch.setattr(updater, "download_asset", lambda url, dest, progress_cb=None: dest.write_bytes(b"fake"))

    script_path = updater.perform_update(parent_pid=42, release=release)

    assert script_path.exists()
    downloaded = tmp_path / "S3Explorer" / "update" / "UP-obs-v1.3.0.exe"
    assert downloaded.read_bytes() == b"fake"
