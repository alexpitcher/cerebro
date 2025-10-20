"""Settings dialog for configuring the Cerebro worker GUI."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Dict

import requests
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QWidget,
)

DEFAULT_MODELS = ["phi4-mini", "llama3", "mistral"]


class SettingsDialog(QDialog):
    """Dialog allowing the user to configure the worker."""

    def __init__(self, settings: Dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Cerebro Worker Settings")
        self.setModal(True)
        self.settings = settings.copy()
        self.resize(520, 540)

        self._build_ui()
        self._load_settings(settings)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.input_cerebro_url = QLineEdit()
        form.addRow("Cerebro URL:", self.input_cerebro_url)

        self.input_ollama_url = QLineEdit("http://localhost:11434/api/chat")
        form.addRow("Ollama URL:", self.input_ollama_url)

        self.input_worker_id = QLineEdit(socket.gethostname())
        form.addRow("Worker ID:", self.input_worker_id)

        self.input_model = QComboBox()
        self.input_model.addItems(DEFAULT_MODELS)
        self.input_model.setEditable(True)
        form.addRow("Model:", self.input_model)

        self.slider_gpu = QSlider(Qt.Orientation.Horizontal)
        self.slider_gpu.setRange(0, 100)
        self.slider_gpu.setValue(30)
        self.slider_gpu.setTickInterval(5)
        self.slider_gpu.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.slider_gpu)
        self.label_gpu_value = QLabel("30%")
        slider_layout.addWidget(self.label_gpu_value)
        form.addRow("GPU Threshold:", slider_layout)
        self.slider_gpu.valueChanged.connect(
            lambda value: self.label_gpu_value.setText(f"{value}%")
        )

        self.checkbox_check_gpu = QCheckBox("Check GPU before each job")
        self.checkbox_start_on_launch = QCheckBox("Start worker on launch")
        self.checkbox_start_minimized = QCheckBox("Start minimized to tray")

        form.addRow("", self.checkbox_check_gpu)
        form.addRow("", self.checkbox_start_on_launch)
        form.addRow("", self.checkbox_start_minimized)

        self.combo_log_level = QComboBox()
        self.combo_log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        form.addRow("Log Level:", self.combo_log_level)

        self.spin_gaming_hours = QSpinBox()
        self.spin_gaming_hours.setRange(1, 12)
        self.spin_gaming_hours.setValue(6)
        form.addRow("Gaming mode (hours):", self.spin_gaming_hours)

        self.metadata_default = QTextEdit()
        self.metadata_default.setPlaceholderText('{"priority": "normal"}')
        form.addRow("Default metadata (JSON):", self.metadata_default)

        self.button_test = QPushButton("Test Connection")
        self.button_test.clicked.connect(self._test_connection)
        form.addRow("", self.button_test)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)

        layout = QGridLayout()
        layout.addLayout(form, 0, 0)
        layout.addWidget(self.buttons, 1, 0)
        self.setLayout(layout)

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def _load_settings(self, settings: Dict[str, Any]) -> None:
        self.input_cerebro_url.setText(settings.get("cerebro_url", "http://localhost:5000"))
        self.input_ollama_url.setText(settings.get("ollama_url", "http://localhost:11434/api/chat"))
        self.input_worker_id.setText(settings.get("worker_id", socket.gethostname()))
        model_name = settings.get("model_name", DEFAULT_MODELS[0])
        index = self.input_model.findText(model_name)
        if index == -1:
            self.input_model.addItem(model_name)
            index = self.input_model.findText(model_name)
        self.input_model.setCurrentIndex(index)

        gpu_threshold = int(settings.get("gpu_threshold", 30))
        self.slider_gpu.setValue(gpu_threshold)
        self.checkbox_check_gpu.setChecked(settings.get("check_gpu", True))
        self.checkbox_start_on_launch.setChecked(settings.get("start_on_launch", False))
        self.checkbox_start_minimized.setChecked(settings.get("start_minimized", False))
        log_level = settings.get("log_level", "INFO").upper()
        idx = self.combo_log_level.findText(log_level)
        if idx != -1:
            self.combo_log_level.setCurrentIndex(idx)
        self.spin_gaming_hours.setValue(int(settings.get("gaming_mode_hours", 6)))
        default_metadata = settings.get("default_metadata")
        if default_metadata:
            self.metadata_default.setPlainText(json.dumps(default_metadata, indent=2))

    def _on_accept(self) -> None:
        try:
            metadata = self.metadata_default.toPlainText().strip()
            parsed_metadata = json.loads(metadata) if metadata else None
        except json.JSONDecodeError as exc:
            QMessageBox.critical(self, "Invalid metadata", f"Metadata must be valid JSON:\n{exc}")
            return
        self.settings.update(
            {
                "cerebro_url": self.input_cerebro_url.text().strip(),
                "ollama_url": self.input_ollama_url.text().strip(),
                "worker_id": self.input_worker_id.text().strip(),
                "model_name": self.input_model.currentText().strip(),
                "gpu_threshold": self.slider_gpu.value(),
                "check_gpu": self.checkbox_check_gpu.isChecked(),
                "start_on_launch": self.checkbox_start_on_launch.isChecked(),
                "start_minimized": self.checkbox_start_minimized.isChecked(),
                "log_level": self.combo_log_level.currentText().upper(),
                "gaming_mode_hours": self.spin_gaming_hours.value(),
                "default_metadata": parsed_metadata,
            }
        )
        self.accept()

    # ------------------------------------------------------------------ #
    # Helper utilities
    # ------------------------------------------------------------------ #

    def _test_connection(self) -> None:
        url = self.input_cerebro_url.text().strip().rstrip("/")
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please provide a Cerebro URL.")
            return
        try:
            response = requests.get(f"{url}/health", timeout=5)
            if response.status_code == 200:
                QMessageBox.information(self, "Connection successful", "Manager responded OK.")
            else:
                QMessageBox.warning(
                    self,
                    "Connection failed",
                    f"Manager responded with status {response.status_code}.",
                )
        except requests.RequestException as exc:
            QMessageBox.critical(self, "Connection error", str(exc))

    def get_settings(self) -> Dict[str, Any]:
        return self.settings.copy()
