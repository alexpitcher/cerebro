"""System tray integration for the Cerebro worker GUI."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

STATUS_ICONS = {
    "stopped": QColor("#ef4444"),  # red
    "idle": QColor("#facc15"),  # yellow
    "working": QColor("#22c55e"),  # green
    "error": QColor("#111827"),  # near-black
    "gpu_busy": QColor("#f97316"),  # orange
    "paused": QColor("#6366f1"),  # indigo
}


def _create_circle_icon(color: QColor, size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size // 2 - 4
    painter.drawEllipse(QPoint(size // 2, size // 2), radius, radius)
    painter.end()
    return QIcon(pixmap)


def _create_gaming_overlay(base_icon: QIcon) -> QIcon:
    pixmap = base_icon.pixmap(64, 64)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0ea5e9"))
    painter.drawEllipse(QPoint(48, 16), 12, 12)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRect(44, 12, 8, 8)
    painter.end()
    return QIcon(pixmap)


def _ensure_icons_written(base_path: Path) -> None:
    """Persist generated icons to disk for packaging."""
    base_path.mkdir(parents=True, exist_ok=True)
    for name, color in STATUS_ICONS.items():
        icon_path = base_path / f"{name}.png"
        if not icon_path.exists():
            icon = _create_circle_icon(color)
            icon.pixmap(64, 64).save(os.fspath(icon_path), "PNG")


class TrayManager(QObject):
    """Manage the system tray icon, menus, and notifications."""

    gaming_mode_changed = pyqtSignal(bool)

    def __init__(
        self,
        app: QApplication,
        engine,
        *,
        show_settings: Callable[[], None],
        show_stats: Callable[[], None],
        show_logs: Callable[[], None],
        exit_app: Callable[[], None],
        get_gaming_mode_hours: Callable[[], int] | None = None,
        parent: Optional[QObject] = None,
        icons_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.engine = engine
        self.show_settings = show_settings
        self.show_stats = show_stats
        self.show_logs = show_logs
        self.exit_app = exit_app
        self.current_status = "stopped"
        self.gaming_mode = False
        self.gaming_mode_until: float | None = None
        self.icons_path = icons_path or Path(__file__).resolve().parent.parent / "resources" / "icons"
        _ensure_icons_written(self.icons_path)
        self.get_gaming_mode_hours = get_gaming_mode_hours or (lambda: 6)

        self.icons = {status: _create_circle_icon(color) for status, color in STATUS_ICONS.items()}
        self.icons["gaming"] = _create_gaming_overlay(self.icons["paused"])

        self.tray = QSystemTrayIcon(self.icons["stopped"], parent)
        self.tray.setToolTip("Cerebro worker: stopped")
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.show()

        self.tray.activated.connect(self._on_tray_activated)

        self.engine.status_changed.connect(self.update_status)
        self.engine.job_completed.connect(self._on_job_completed)
        self.engine.error_occurred.connect(self._on_error)
        self.engine.stats_updated.connect(self._on_stats_updated)

        self.stats_cache: dict[str, float | int | str | None] = {}

    # ------------------------------------------------------------------ #
    # Menu & actions
    # ------------------------------------------------------------------ #

    def _build_menu(self) -> None:
        self.action_start = QAction("Start Worker", self.menu)
        self.action_start.triggered.connect(self.engine.start)
        self.menu.addAction(self.action_start)

        self.action_stop = QAction("Stop Worker", self.menu)
        self.action_stop.triggered.connect(self.engine.stop)
        self.menu.addAction(self.action_stop)

        self.menu.addSeparator()

        self.action_gaming_mode = QAction("Toggle Gaming Mode", self.menu, checkable=True)
        self.action_gaming_mode.triggered.connect(self.toggle_gaming_mode)
        self.menu.addAction(self.action_gaming_mode)

        self.menu.addSeparator()

        self.action_settings = QAction("Settings", self.menu)
        self.action_settings.triggered.connect(self.show_settings)
        self.menu.addAction(self.action_settings)

        self.action_stats = QAction("Stats", self.menu)
        self.action_stats.triggered.connect(self.show_stats)
        self.menu.addAction(self.action_stats)

        self.action_logs = QAction("View Logs", self.menu)
        self.action_logs.triggered.connect(self.show_logs)
        self.menu.addAction(self.action_logs)

        self.menu.addSeparator()

        self.action_exit = QAction("Exit", self.menu)
        self.action_exit.triggered.connect(self.exit_app)
        self.menu.addAction(self.action_exit)

    # ------------------------------------------------------------------ #
    # Status updates
    # ------------------------------------------------------------------ #

    def update_status(self, status: str) -> None:
        self.current_status = status
        icon_key = status
        if status not in self.icons:
            icon_key = "error" if "error" in status else "idle"
        icon = self.icons.get(icon_key, self.icons["idle"])
        if self.gaming_mode:
            icon = self.icons["gaming"]
        self.tray.setIcon(icon)

        tooltip_lines = [f"Status: {status}"]
        if self.gaming_mode and self.gaming_mode_until:
            remaining = max(0, self.gaming_mode_until - time.time())
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            tooltip_lines.append(f"Gaming mode: {hours}h {minutes}m remaining")
        if self.stats_cache:
            tooltip_lines.append(
                "Jobs completed: {jobs_completed} | failed: {jobs_failed}".format(**self.stats_cache)
            )
        self.tray.setToolTip("\n".join(tooltip_lines))

    def _on_job_completed(self, job_id: str, _result: dict) -> None:
        self.tray.showMessage(
            "Cerebro worker",
            f"Job {job_id} completed.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _on_error(self, message: str) -> None:
        self.tray.showMessage(
            "Cerebro worker error",
            message,
            QSystemTrayIcon.MessageIcon.Critical,
            5000,
        )

    def _on_stats_updated(self, stats: dict) -> None:
        self.stats_cache = stats
        if self.gaming_mode and stats.get("gaming_mode_remaining") is not None:
            self.gaming_mode_until = time.time() + stats["gaming_mode_remaining"]
            if stats["gaming_mode_remaining"] <= 0:
                self._toggle_gaming_mode(False, notify=True)
        self.update_status(self.current_status)

    # ------------------------------------------------------------------ #
    # Gaming mode
    # ------------------------------------------------------------------ #

    def toggle_gaming_mode(self) -> None:
        self._toggle_gaming_mode(not self.gaming_mode, notify=True)

    def _toggle_gaming_mode(self, enabled: bool, *, notify: bool) -> None:
        self.gaming_mode = enabled
        self.action_gaming_mode.setChecked(enabled)
        if enabled:
            hours = max(1, self.get_gaming_mode_hours())
            self.engine.pause_for(hours * 3600)
            self.gaming_mode_until = time.time() + hours * 3600
            self.gaming_mode_changed.emit(True)
            if notify:
                self.tray.showMessage(
                    "Gaming mode enabled",
                    "Worker paused. Use the tray icon to resume.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
        else:
            self.engine.resume()
            self.gaming_mode_until = None
            self.gaming_mode_changed.emit(False)
            if notify:
                self.tray.showMessage(
                    "Gaming mode disabled",
                    "Worker resumed.",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )
        self.update_status(self.current_status)

    # ------------------------------------------------------------------ #
    # Tray interactions
    # ------------------------------------------------------------------ #

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_stats_tooltip()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_stats()

    def _show_stats_tooltip(self) -> None:
        if not self.stats_cache:
            self.tray.showMessage(
                "Cerebro worker",
                "No stats available yet.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            return
        uptime = self.stats_cache.get("uptime", 0)
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)
        msg = (
            f"Uptime: {hours:02d}:{minutes:02d}:{seconds:02d}\n"
            f"Completed: {self.stats_cache.get('jobs_completed', 0)} | "
            f"Failed: {self.stats_cache.get('jobs_failed', 0)}"
        )
        self.tray.showMessage(
            "Cerebro worker stats",
            msg,
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )
