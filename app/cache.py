"""다운로드 캐시 폴더 관리 (7.2).

앱 -> 탐색기 드래그는 %LOCALAPPDATA%\\S3Explorer\\cache\\ 를 경유한다.
캐시는 앱 종료 시, 그리고 7일 경과 항목에 대해 정리한다.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_DEFAULT_MAX_AGE_DAYS = 7


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = (Path(base) / "S3Explorer" / "cache") if base else (Path.home() / ".s3explorer" / "cache")
    root.mkdir(parents=True, exist_ok=True)
    return root


def clean_all(root: Path | None = None) -> None:
    """앱 종료 시 호출: 캐시 전체를 비운다."""
    root = root or cache_dir()
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            pass


def clean_stale(max_age_days: int = _DEFAULT_MAX_AGE_DAYS, root: Path | None = None) -> None:
    """앱 시작 시 호출: max_age_days 경과한 파일만 정리한다."""
    root = root or cache_dir()
    cutoff = time.time() - max_age_days * 86400
    for path in root.rglob("*"):
        if path.is_file():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                if not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
