"""GitHub Dark 계열 색상 (9장: 기존 ToolHub 대시보드와 통일)."""

BACKGROUND = "#0d1117"
PANEL = "#161b22"
BORDER = "#30363d"
TEXT = "#c9d1d9"
TEXT_MUTED = "#8b949e"
ACCENT = "#58a6ff"
DANGER = "#f85149"

STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {BACKGROUND};
}}
QToolBar {{
    background-color: {PANEL};
    border-bottom: 1px solid {BORDER};
    spacing: 6px;
    padding: 4px;
}}
QStatusBar {{
    background-color: {PANEL};
    border-top: 1px solid {BORDER};
}}
QTreeWidget, QTableWidget, QListWidget {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    alternate-background-color: #1c2129;
}}
QHeaderView::section {{
    background-color: {PANEL};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px;
}}
QLineEdit, QComboBox, QSpinBox {{
    background-color: {BACKGROUND};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px;
}}
QPushButton, QToolButton {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 10px;
}}
QPushButton:hover, QToolButton:hover {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
}}
QLabel[role="badge-superuser"] {{
    background-color: {DANGER};
    color: white;
    border-radius: 3px;
    padding: 2px 6px;
    font-weight: bold;
}}
"""
