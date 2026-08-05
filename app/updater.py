"""자동 업데이트: 릴리즈 조회, 다운로드, updater.cmd 생성/실행 (11장).

public repo이므로 토큰이 필요 없다. 업데이트 체크용 HTTP는 시스템 프록시를
따른다 (4장 참고: S3용 프록시 정책과 달리 환경변수 기반 기본 동작을 그대로 쓴다).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from app.errors import AppError, get_log_dir, log_error
from app.version import __version__

RELEASES_API_URL = "https://api.github.com/repos/yuchoi-bb/UP-obs/releases/latest"
ASSET_NAME_PREFIX = "UP-obs-v"
REQUEST_TIMEOUT = 10
_CHUNK_SIZE = 64 * 1024


def update_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = (Path(base) / "S3Explorer" / "update") if base else (Path.home() / ".s3explorer" / "update")
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_version(tag: str) -> tuple:
    """'v1.3.0' -> (1, 3, 0). 숫자가 아닌 부분은 0으로 취급한다."""
    text = tag.lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(latest_tag: str, current_version: str = __version__) -> bool:
    """11.3: 태그와 내장 버전이 어긋나면 배너가 무한 반복되므로 빌드 시 반드시 일치시켜야 한다."""
    return parse_version(latest_tag) > parse_version(current_version)


def fetch_latest_release() -> dict:
    """11.1: GET /releases/latest. 11.4: 실패는 E-5001로 로그만 남기고 AppError를 던진다.

    대화상자를 띄울지는 호출자(백그라운드 자동 확인 vs 수동 확인)가 결정한다.
    """
    request = urllib.request.Request(
        RELEASES_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "UP-obs-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        req_id = log_error("E-5001", detail=str(exc))
        raise AppError("E-5001", detail=str(exc), req_id=req_id) from exc
    return data


def find_exe_asset(release: dict) -> dict:
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.startswith(ASSET_NAME_PREFIX) and name.endswith(".exe"):
            return asset
    req_id = log_error("E-5002", detail=f"tag={release.get('tag_name')}")
    raise AppError("E-5002", detail=f"tag={release.get('tag_name')}", req_id=req_id)


def download_asset(url: str, dest_path: Path, progress_cb: Optional[Callable[[int, int], None]] = None) -> None:
    request = urllib.request.Request(
        url, headers={"Accept": "application/octet-stream", "User-Agent": "UP-obs-updater"}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        req_id = log_error("E-5003", detail=str(exc))
        raise AppError("E-5003", detail=str(exc), req_id=req_id) from exc


def current_exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.argv[0]).resolve()


# 11.2: updater.cmd 동작
#   1. 부모 PID가 사라질 때까지 대기 (최대 30초)
#   2. 기존 exe를 .bak으로 이동
#   3. 새 exe를 원래 경로로 이동
#   4. 새 exe 실행
#   5. 자기 자신 삭제
#   실패 시 .bak을 되돌리고 E-5004를 로그에 남긴다.
_UPDATER_CMD_TEMPLATE = r"""@echo off
setlocal enabledelayedexpansion
set "PARENT_PID={parent_pid}"
set "TARGET_EXE={target_exe}"
set "NEW_EXE={new_exe}"
set "BAK_EXE=%TARGET_EXE%.bak"
set "LOG_FILE={log_file}"

set /a WAITED=0
:WAITLOOP
tasklist /fi "PID eq %PARENT_PID%" | find "%PARENT_PID%" >nul
if errorlevel 1 goto DOUPDATE
if %WAITED% geq 30 goto DOUPDATE
timeout /t 1 /nobreak >nul
set /a WAITED+=1
goto WAITLOOP

:DOUPDATE
if exist "%BAK_EXE%" del /f /q "%BAK_EXE%"
move /y "%TARGET_EXE%" "%BAK_EXE%" >nul 2>&1
if errorlevel 1 goto FAIL
move /y "%NEW_EXE%" "%TARGET_EXE%" >nul 2>&1
if errorlevel 1 goto RESTORE
start "" "%TARGET_EXE%"
del /f /q "%BAK_EXE%" >nul 2>&1
(goto) 2>nul & del "%~f0"
exit /b 0

:RESTORE
move /y "%BAK_EXE%" "%TARGET_EXE%" >nul 2>&1

:FAIL
echo %date% %time% ^| ERROR ^| E-5004 ^| 업데이트 적용에 실패했습니다 ^| detail=updater.cmd ^| req_id=updater>>"%LOG_FILE%"
(goto) 2>nul & del "%~f0"
exit /b 1
"""


def build_updater_script(parent_pid: int, target_exe: Path, new_exe: Path, log_file: Path) -> str:
    return _UPDATER_CMD_TEMPLATE.format(
        parent_pid=parent_pid, target_exe=target_exe, new_exe=new_exe, log_file=log_file
    )


def write_updater_script(parent_pid: int, target_exe: Path, new_exe: Path, log_file: Optional[Path] = None) -> Path:
    log_file = log_file or (get_log_dir() / "s3explorer.log")
    script_path = update_dir() / "updater.cmd"
    script_path.write_text(build_updater_script(parent_pid, target_exe, new_exe, log_file), encoding="utf-8")
    return script_path


def perform_update(
    parent_pid: int, release: dict, progress_cb: Optional[Callable[[int, int], None]] = None
) -> Path:
    """다운로드 + updater.cmd 작성까지 수행한다. 실행(launch_updater)은 호출자가 UI 확인 후 별도로 한다."""
    asset = find_exe_asset(release)
    new_exe = update_dir() / asset["name"]
    download_asset(asset["browser_download_url"], new_exe, progress_cb)
    return write_updater_script(parent_pid, current_exe_path(), new_exe)


def launch_updater(script_path: Path) -> None:
    """updater.cmd를 실행한다. 호출 직후 본체를 종료하는 것은 호출자의 책임이다."""
    detached = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", str(script_path)], creationflags=detached, close_fds=True)
