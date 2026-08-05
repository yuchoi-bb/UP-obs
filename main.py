"""UP-obs 엔트리 포인트."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app import cache
from app.config import ProfileStore
from app.errors import AppError
from app.ui.main_window import MainWindow
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    cache.clean_stale()  # 7.2: 7일 경과한 캐시 항목 정리

    store = ProfileStore()
    try:
        store.load()
    except AppError as exc:
        QMessageBox.critical(None, "오류", exc.display_text())
        store.profiles = []
        store.active_profile = None

    if not store.profiles:
        dialog = SettingsDialog(store)
        dialog.exec()  # 취소해도 계속 진행: 빈 상태로 시작하고 설정에서 다시 추가 가능

    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
