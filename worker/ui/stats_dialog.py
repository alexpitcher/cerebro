"""Stats dialog for displaying worker runtime metrics."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class StatsDialog(QDialog):
    """Displays current worker status and aggregated metrics."""

    def __init__(self, fetch_stats: Callable[[], dict], parent=None):
        super().__init__(parent)
        self.fetch_stats = fetch_stats
        self.setWindowTitle("Worker Statistics")
        self.resize(360, 260)
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.refresh)

        self.label_status = QLabel("unknown")
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("font-size: 24px; color: gray;")

        self.label_uptime = QLabel("00:00:00")
        self.label_current_job = QLabel("-")
        self.label_completed = QLabel("0")
        self.label_failed = QLabel("0")
        self.label_success_rate = QLabel("0%")
        self.label_avg_time = QLabel("0.0s")

        self.button_refresh = QPushButton("Refresh")
        self.button_refresh.clicked.connect(self.refresh)

        form = QFormLayout()
        form.addRow("Status:", self._combine(self.status_indicator, self.label_status))
        form.addRow("Uptime:", self.label_uptime)
        form.addRow("Current job:", self.label_current_job)
        form.addRow("Completed:", self.label_completed)
        form.addRow("Failed:", self.label_failed)
        form.addRow("Success rate:", self.label_success_rate)
        form.addRow("Average time:", self.label_avg_time)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.button_refresh)
        self.setLayout(layout)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def refresh(self) -> None:
        stats = self.fetch_stats()
        if not stats:
            return
        status = stats.get("status", "unknown")
        self.label_status.setText(status)
        color = {
            "working": "#22c55e",
            "idle": "#facc15",
            "paused": "#6366f1",
            "gpu_busy": "#f97316",
            "stopped": "#ef4444",
        }.get(status, "#9ca3af")
        self.status_indicator.setStyleSheet(f"font-size: 24px; color: {color};")

        uptime = stats.get("uptime", 0.0)
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)
        self.label_uptime.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

        current_job = stats.get("current_job_id") or "-"
        self.label_current_job.setText(str(current_job))

        completed = int(stats.get("jobs_completed", 0))
        failed = int(stats.get("jobs_failed", 0))
        total = completed + failed
        success_rate = (completed / total * 100) if total else 0.0
        self.label_completed.setText(str(completed))
        self.label_failed.setText(str(failed))
        self.label_success_rate.setText(f"{success_rate:.1f}%")

        avg_time = stats.get("avg_job_time", 0.0)
        self.label_avg_time.setText(f"{avg_time:.2f}s")

    @staticmethod
    def _combine(left: QLabel, right: QLabel) -> QLabel:
        wrapper = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(left)
        layout.addWidget(right)
        wrapper.setLayout(layout)
        return wrapper
