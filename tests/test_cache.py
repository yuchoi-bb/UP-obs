import os
import time

from app import cache


def test_clean_all_removes_files_and_empty_dirs(tmp_path):
    root = tmp_path / "cache"
    (root / "bucket" / "DA-share").mkdir(parents=True)
    (root / "bucket" / "DA-share" / "file.txt").write_text("x")

    cache.clean_all(root=root)

    assert list(root.rglob("*")) == []


def test_clean_stale_keeps_recent_files(tmp_path):
    root = tmp_path / "cache"
    sub = root / "bucket"
    sub.mkdir(parents=True)
    recent = sub / "recent.txt"
    recent.write_text("x")
    old = sub / "old.txt"
    old.write_text("x")

    old_time = time.time() - 8 * 86400
    os.utime(old, (old_time, old_time))

    cache.clean_stale(max_age_days=7, root=root)

    assert recent.exists()
    assert not old.exists()


def test_cache_dir_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = cache.cache_dir()
    assert path.exists()
    assert path == tmp_path / "S3Explorer" / "cache"
