"""Log viewer dialog for the Cerebro worker GUI."""

from __future__ import annotations

from pathlib import Path
from typing import List

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
)

LOG_LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"]


class LogViewer(QDialog):
    """Display the worker log file with filtering and auto-refresh."""

    def __init__(self, log_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Worker Log Viewer")
        self.resize(720, 520)
        self.log_path = log_path
        self.current_level = "ALL"

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        self.toolbar = QToolBar()
        self.buttons = {}
        for level in LOG_LEVELS:
            btn = QPushButton(level.title())
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, lvl=level: self.set_level(lvl))
            self.toolbar.addWidget(btn)
            self.buttons[level] = btn
        self.buttons["ALL"].setChecked(True)

        self.button_open = QPushButton("Open log file")
        self.button_open.clicked.connect(self._open_external)
        self.button_clear = QPushButton("Clear")
        self.button_clear.clicked.connect(self._clear_log)
        self.button_close = QPushButton("Close")
        self.button_close.clicked.connect(self.close)

        button_bar = QHBoxLayout()
        button_bar.addWidget(self.button_open)
        button_bar.addWidget(self.button_clear)
        button_bar.addStretch()
        button_bar.addWidget(self.button_close)

        layout = QVBoxLayout()
        layout.addWidget(self.toolbar)
        layout.addWidget(self.text_area)
        layout.addLayout(button_bar)
        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def set_level(self, level: str) -> None:
        self.current_level = level
        for key, btn in self.buttons.items():
            btn.setChecked(key == level)
        self.refresh()

    def refresh(self) -> None:
        if not self.log_path.exists():
            self.text_area.setPlainText("Log file not found.")
            return
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            self.text_area.setPlainText(f"Unable to read log file: {exc}")
            return
        lines = lines[-500:]
        if self.current_level != "ALL":
            keyword = f"| {self.current_level.lower()}"
            lines = [line for line in lines if keyword in line.lower()]
        self.text_area.setPlainText("\n".join(lines))
        self.text_area.moveCursor(QTextCursor.MoveOperation.End)

    def _open_external(self) -> None:
        if self.log_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path)))

    def _clear_log(self) -> None:
        try:
            self.log_path.write_text("", encoding="utf-8")
            self.refresh()
        except OSError as exc:
            self.text_area.setPlainText(f"Failed to clear log: {exc}")
