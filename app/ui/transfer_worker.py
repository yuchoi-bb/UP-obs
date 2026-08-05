"""S3 I/O를 UI 스레드 밖에서 실행하기 위한 QThreadPool 워커.

CLAUDE.md 원칙: UI 스레드에서 S3 I/O를 호출하지 말 것.
모든 S3 호출은 이 Worker를 통해 QThreadPool에서 실행하고, 결과는 시그널로
메인 스레드에 전달한다.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.errors import AppError


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
