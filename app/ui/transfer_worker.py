"""S3 I/O를 UI 스레드 밖에서 실행하기 위한 QThreadPool 워커 + 전송 큐(7장, 8장).

CLAUDE.md 원칙: UI 스레드에서 S3 I/O를 호출하지 말 것.
모든 S3 호출은 이 Worker를 통해 QThreadPool에서 실행하고, 결과는 시그널로
메인 스레드에 전달한다.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.errors import AppError, log_error
from app.s3client import S3Client

MAX_PATH_LENGTH = 260  # 7.3: Windows 260자 제한


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object)  # AppError
    finished = Signal()


class Worker(QRunnable):
    """임의의 콜러블을 백그라운드 스레드에서 실행한다."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except AppError as exc:
            self.signals.error.emit(exc)
        except Exception as exc:  # noqa: BLE001 - 미분류 오류도 UI로 전달
            self.signals.error.emit(AppError("E-9001", detail=str(exc)))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


def submit(
    pool: QThreadPool,
    keep_alive: set,
    fn: Callable[..., Any],
    *args: Any,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[AppError], None] | None = None,
    **kwargs: Any,
) -> Worker:
    """Worker를 스레드풀에 제출한다.

    QThreadPool.start()는 C++ 쪽에서 QRunnable 수명을 관리하지만, Python 쪽
    Worker/WorkerSignals 참조가 사라지면 완료 전에 GC되어 시그널이 유실될 수
    있다. 호출자가 들고 있는 keep_alive 집합에 완료 시까지 보관한다.

    result/error 콜백은 반드시 pool.start() 이전에 연결해야 한다 (스레드풀이
    즉시 실행을 시작해 start() 반환 후 connect()하면 시그널을 놓칠 수 있음).
    """
    worker = Worker(fn, *args, **kwargs)
    keep_alive.add(worker)
    worker.signals.finished.connect(lambda: keep_alive.discard(worker))
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    pool.start(worker)
    return worker


class TransferCancelled(Exception):
    pass


@dataclass
class TransferItem:
    """전송 큐의 한 건. kind에 따라 사용하는 필드가 다르다.

    - upload: local_path -> bucket/key
    - download: bucket/key -> local_path
    - mkdir: bucket/key (빈 폴더 마커, local_path 없음)
    - delete: bucket/key 삭제 (Superuser 전용, guard가 강제)
    - rename: bucket/src_key -> bucket/key (CopyObject 후 DeleteObject, Superuser 전용)
    """

    kind: str  # "upload" | "download" | "mkdir" | "delete" | "rename"
    bucket: str
    key: str
    local_path: Optional[Path] = None
    src_key: Optional[str] = None


@dataclass
class TransferResult:
    item: TransferItem
    success: bool
    cancelled: bool = False
    error: Optional[AppError] = None


class TransferSignals(QObject):
    progress = Signal(int, int)  # done, total
    all_finished = Signal(list)  # list[TransferResult]


class TransferManager:
    """QThreadPool 기반 업로드/다운로드 큐 (8장: 진행률/취소/실패 수집)."""

    def __init__(self, max_concurrency: int = 4):
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(max_concurrency, 1))
        self.signals = TransferSignals()
        self._cancel_event = threading.Event()
        self._results: list[TransferResult] = []
        self._total = 0
        self._done = 0
        self._lock = threading.Lock()
        self._runners: set = set()

    def is_running(self) -> bool:
        with self._lock:
            return self._total > 0 and self._done < self._total

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self, client: S3Client, items: list[TransferItem]) -> None:
        self._cancel_event.clear()
        self._results = []
        self._total = len(items)
        self._done = 0
        if self._total == 0:
            self.signals.all_finished.emit([])
            return
        for item in items:
            runner = _TransferRunner(self, client, item)
            self._runners.add(runner)
            self._pool.start(runner)

    def _on_item_done(self, runner: "_TransferRunner", result: TransferResult) -> None:
        self._runners.discard(runner)
        with self._lock:
            self._results.append(result)
            self._done += 1
            done = self._done
            total = self._total
        self.signals.progress.emit(done, total)
        if done == total:
            self.signals.all_finished.emit(list(self._results))


class _TransferRunner(QRunnable):
    def __init__(self, manager: TransferManager, client: S3Client, item: TransferItem):
        super().__init__()
        self._manager = manager
        self._client = client
        self._item = item

    def run(self) -> None:
        item = self._item
        try:
            if self._manager.is_cancelled():
                raise TransferCancelled()
            if item.kind == "upload":
                self._client.put_object(item.bucket, item.key, item.local_path)
            elif item.kind == "mkdir":
                self._client.create_folder(item.bucket, item.key)
            elif item.kind == "delete":
                self._client.delete_object(item.bucket, item.key)
            elif item.kind == "rename":
                assert item.src_key is not None
                self._client.copy_object(item.bucket, item.src_key, item.key)
                self._client.delete_object(item.bucket, item.src_key)
            else:  # download
                assert item.local_path is not None
                if len(str(item.local_path)) >= MAX_PATH_LENGTH:
                    # 7.3: 다운로드 대상 경로 사전 검사. S3Client를 거치지 않으므로 직접 로그.
                    req_id = log_error("E-4004", detail=str(item.local_path))
                    raise AppError("E-4004", detail=str(item.local_path), req_id=req_id)
                item.local_path.parent.mkdir(parents=True, exist_ok=True)
                self._client.get_object(item.bucket, item.key, item.local_path)
            result = TransferResult(item=item, success=True)
        except TransferCancelled:
            result = TransferResult(item=item, success=False, cancelled=True)
        except AppError as exc:
            # S3Client._raise가 이미 로그를 남기므로 여기서는 다시 남기지 않는다.
            result = TransferResult(item=item, success=False, error=exc)
        except OSError as exc:
            code = "E-4003" if isinstance(exc, PermissionError) else "E-4002"
            req_id = log_error(code, detail=str(exc))
            result = TransferResult(item=item, success=False, error=AppError(code, detail=str(exc), req_id=req_id))
        self._manager._on_item_done(self, result)


def expand_upload_paths(local_paths: list[Path], bucket: str, target_prefix: str) -> list[TransferItem]:
    """7.1: 드롭된 로컬 파일/폴더를 업로드 항목 목록으로 펼친다.

    폴더는 재귀 순회하며 상대 경로를 그대로 prefix에 매핑한다. 파일이 하나도
    없는 빈 하위 폴더는 6.2 규칙에 맞춰 0바이트 마커(mkdir)로 표현한다.
    """
    items: list[TransferItem] = []
    for path in local_paths:
        if path.is_dir():
            _expand_directory(path, path.name, bucket, target_prefix, items)
        elif path.is_file():
            items.append(TransferItem(kind="upload", bucket=bucket, key=target_prefix + path.name, local_path=path))
    return items


def _expand_directory(dir_path: Path, rel_root: str, bucket: str, target_prefix: str, items: list[TransferItem]) -> None:
    entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
    if not entries:
        items.append(TransferItem(kind="mkdir", bucket=bucket, key=f"{target_prefix}{rel_root}"))
        return
    for entry in entries:
        rel = f"{rel_root}/{entry.name}"
        if entry.is_dir():
            _expand_directory(entry, rel, bucket, target_prefix, items)
        elif entry.is_file():
            items.append(TransferItem(kind="upload", bucket=bucket, key=target_prefix + rel, local_path=entry))


def expand_download_items(
    client: S3Client, bucket: str, selected: list[dict], dest_dir: Path
) -> list[TransferItem]:
    """받기 버튼: 선택 항목(파일/폴더)을 dest_dir 하위 다운로드 목록으로 펼친다.

    폴더는 Delimiter 없이 재귀 조회해 하위 전체 객체를 대상으로 삼는다.
    """
    items: list[TransferItem] = []
    for entry in selected:
        if entry["kind"] == "file":
            key = entry["key"]
            name = key.rsplit("/", 1)[-1]
            items.append(TransferItem(kind="download", bucket=bucket, key=key, local_path=dest_dir / name))
        else:
            prefix = entry["prefix"]
            folder_name = prefix.rstrip("/").rsplit("/", 1)[-1]
            for page in client.list_objects(bucket, prefix=prefix, delimiter=""):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key == prefix:
                        continue
                    rel = key[len(prefix):]
                    local_path = dest_dir / folder_name / rel
                    items.append(TransferItem(kind="download", bucket=bucket, key=key, local_path=local_path))
    return items


def find_existing_keys(client: S3Client, items: list[TransferItem]) -> set[str]:
    """5.4: 업로드 전 존재 여부를 확인해 덮어쓰기 대상 key 집합을 반환한다.

    빈 폴더 마커(mkdir)는 덮어써도 데이터 유실이 아니므로 대상에서 제외한다.
    """
    existing: set[str] = set()
    for item in items:
        if item.kind == "upload" and client.object_exists(item.bucket, item.key):
            existing.add(item.key)
    return existing


def expand_delete_items(client: S3Client, bucket: str, selected: list[dict]) -> list[TransferItem]:
    """삭제 대상(파일/폴더)을 펼친다. 폴더는 하위 전체를 재귀 조회해 개별 삭제 항목으로 만든다."""
    items: list[TransferItem] = []
    for entry in selected:
        if entry["kind"] == "file":
            items.append(TransferItem(kind="delete", bucket=bucket, key=entry["key"]))
        else:
            prefix = entry["prefix"]
            for page in client.list_objects(bucket, prefix=prefix, delimiter=""):
                for obj in page.get("Contents", []):
                    items.append(TransferItem(kind="delete", bucket=bucket, key=obj["Key"]))
    return items


def expand_rename_items(client: S3Client, bucket: str, entry: dict, new_name: str) -> list[TransferItem]:
    """이름변경 대상을 펼친다 (6.3). 폴더면 하위 전체를 재귀적으로 새 prefix에 매핑한다."""
    items: list[TransferItem] = []
    if entry["kind"] == "file":
        key = entry["key"]
        parent = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
        new_key = parent + new_name
        items.append(TransferItem(kind="rename", bucket=bucket, key=new_key, src_key=key))
        return items

    old_prefix = entry["prefix"]
    parent = old_prefix.rstrip("/").rsplit("/", 1)[0] + "/" if "/" in old_prefix.rstrip("/") else ""
    new_prefix = f"{parent}{new_name}/"
    for page in client.list_objects(bucket, prefix=old_prefix, delimiter=""):
        for obj in page.get("Contents", []):
            old_key = obj["Key"]
            rel = old_key[len(old_prefix):]
            new_key = new_prefix + rel
            items.append(TransferItem(kind="rename", bucket=bucket, key=new_key, src_key=old_key))
    return items
