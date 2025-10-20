"""Settings dialog for configuring the Cerebro worker GUI."""

from __future__ import annotations

import json
import platform
import socket
from typing import Any, Dict, Callable

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

DEFAULT_MODELS = ["phi4-mini", "llama3", "qwen"]


class SettingsDialog(QDialog):
    """Dialog allowing the user to configure the worker."""

    def __init__(
        self,
        settings: Dict[str, Any],
        models: list[str],
        refresh_callback: Callable[[str, QWidget | None], list[str]] | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Cerebro Worker Settings")
        self.setModal(True)
        self.settings = settings.copy()
        self.resize(520, 540)
        self.available_models = models
        self.refresh_callback = refresh_callback

        self._build_ui()
        self.set_models(models)
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

        model_layout = QHBoxLayout()
        self.input_model = QComboBox()
        self.input_model.setEditable(True)
        model_layout.addWidget(self.input_model)
        self.button_refresh_models = QPushButton("Refresh models")
        self.button_refresh_models.clicked.connect(self._on_refresh_models)
        model_layout.addWidget(self.button_refresh_models)
        form.addRow("Model:", model_layout)
        self.label_model_info = QLabel()
        form.addRow("", self.label_model_info)

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
        self.checkbox_auto_start_worker = QCheckBox("Start worker on launch")
        self.checkbox_run_at_startup = QCheckBox("Run Cerebro Worker at logon")
        if platform.system() != "Windows":
            self.checkbox_run_at_startup.setVisible(False)
        self.checkbox_start_minimized = QCheckBox("Start minimized to tray")

        form.addRow("", self.checkbox_check_gpu)
        form.addRow("", self.checkbox_auto_start_worker)
        form.addRow("", self.checkbox_run_at_startup)
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
        self._select_model(settings.get("model_name", (self.available_models[0] if self.available_models else "")))

        gpu_threshold = int(settings.get("gpu_threshold", 30))
        self.slider_gpu.setValue(gpu_threshold)
        self.checkbox_check_gpu.setChecked(settings.get("check_gpu", True))
        self.checkbox_auto_start_worker.setChecked(settings.get("auto_start_worker", False))
        self.checkbox_run_at_startup.setChecked(settings.get("run_at_startup", False))
        self.checkbox_start_minimized.setChecked(settings.get("start_minimized", True))
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
                "auto_start_worker": self.checkbox_auto_start_worker.isChecked(),
                "run_at_startup": self.checkbox_run_at_startup.isChecked(),
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

    def set_models(self, models: list[str]) -> None:
        self.available_models = models
        current = self.input_model.currentText().strip()
        self.input_model.blockSignals(True)
        self.input_model.clear()
        if models:
            self.input_model.addItems(models)
            self.label_model_info.setText(f"{len(models)} model(s) available from Ollama.")
        else:
            self.label_model_info.setText("No models detected. Run 'ollama pull <model>' and refresh.")
        if current:
            self._select_model(current)
        self.input_model.blockSignals(False)

    def _select_model(self, model_name: str) -> None:
        if not model_name:
            self.input_model.setEditText("")
            return
        index = self.input_model.findText(model_name)
        if index == -1:
            self.input_model.setEditText(model_name)
        else:
            self.input_model.setCurrentIndex(index)

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

    def _on_refresh_models(self) -> None:
        if not self.refresh_callback:
            return
        models = self.refresh_callback(self.input_ollama_url.text().strip(), self)
        self.set_models(models)

    def get_settings(self) -> Dict[str, Any]:
        return self.settings.copy()
