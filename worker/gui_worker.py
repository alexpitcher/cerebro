"""PyQt-based GUI application for controlling the Cerebro worker."""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import psutil
import requests
from PyQt6.QtCore import QObject, Qt, QSettings, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QWidget
from urllib.parse import urlparse, urlunparse

from worker.worker_engine import WorkerConfig, WorkerEngine, configure_logging
from worker.ui.log_viewer import LogViewer
from worker.ui.settings_dialog import SettingsDialog
from worker.ui.stats_dialog import StatsDialog
from worker.ui.tray import TrayManager

APP_NAME = "Cerebro Worker"
ORGANISATION = "Cerebro"

def get_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.getenv("LOCALAPPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "CerebroWorker"


try:  # pragma: no cover - Windows-only dependency
    import winreg  # type: ignore
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore


class ConfigManager:
    """Load and persist GUI + worker configuration."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config: Dict[str, Any] = {}
        self.load()

    def load(self) -> Dict[str, Any]:
        defaults = WorkerConfig.from_env()
        self.config = {
            "cerebro_url": defaults.cerebro_url,
            "ollama_url": defaults.ollama_url,
            "worker_id": defaults.worker_id,
            "model_name": defaults.model_name,
            "poll_interval": defaults.poll_interval,
            "max_backoff": defaults.max_backoff,
            "request_timeout": defaults.request_timeout,
            "gpu_threshold": defaults.gpu_threshold,
            "check_gpu": defaults.check_gpu,
            "log_level": "INFO",
            "auto_start_worker": False,
            "run_at_startup": False,
            "start_minimized": True,
            "gaming_mode_hours": 6,
            "default_metadata": None,
        }
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.config.update(data)
            except json.JSONDecodeError:
                logging.getLogger(__name__).warning("Invalid config.json; using defaults")
        if "start_on_launch" in self.config:
            self.config["auto_start_worker"] = bool(self.config.pop("start_on_launch"))
        return self.config

    def save(self, config: Dict[str, Any]) -> None:
        self.config.update(config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")


class GuiController(QObject):
    """Coordinate dialogs, tray and worker engine."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.base_dir = Path(__file__).resolve().parent
        self.data_dir = get_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.data_dir / "worker.log"
        self.config_manager = ConfigManager(self.data_dir / "config.json")
        self.settings = QSettings(ORGANISATION, APP_NAME)

        if platform.system() == "Windows":
            self.config_manager.config["run_at_startup"] = self._is_run_at_startup_enabled()

        self._setup_logging(self.config_manager.config.get("log_level", "INFO"))
        self.tray: TrayManager | None = None

        self.worker_engine = WorkerEngine(self._build_worker_config())
        self.worker_engine.stats_updated.connect(self._on_stats_update)
        self.worker_engine.status_changed.connect(self._on_status_update)
        self.worker_engine.job_started.connect(self._on_job_started)
        self.worker_engine.job_completed.connect(self._on_job_completed)
        self.worker_engine.error_occurred.connect(self._on_worker_error)

        self.stats_cache: Dict[str, Any] = {}
        self.available_models: List[str] = []

        self.stats_dialog = StatsDialog(self._gather_stats)
        self.log_viewer = LogViewer(self.log_path)
        self.settings_dialog = SettingsDialog(self.config_manager.config, self.available_models, self._refresh_models)
        self.settings_dialog.accepted.connect(self._apply_settings)

        self._restore_geometry(self.stats_dialog, "stats_geometry")
        self._restore_geometry(self.log_viewer, "log_geometry")
        self.stats_dialog.finished.connect(lambda _: self._save_geometry(self.stats_dialog, "stats_geometry"))
        self.log_viewer.finished.connect(lambda _: self._save_geometry(self.log_viewer, "log_geometry"))

        self.available_models = self._refresh_models(show_dialog=False)
        self.settings_dialog.set_models(self.available_models)

        self.tray = TrayManager(
            app,
            self.worker_engine,
            show_settings=self.show_settings_dialog,
            show_stats=self.show_stats_dialog,
            show_logs=self.show_log_viewer,
            exit_app=self.exit_app,
            get_gaming_mode_hours=lambda: self.config_manager.config.get("gaming_mode_hours", 6),
        )

        if self.config_manager.config.get("run_at_startup", False):
            self._set_run_at_startup(True)

        if self.config_manager.config.get("auto_start_worker", False):
            QTimer.singleShot(0, self.worker_engine.start)
            self.tray.tray.showMessage(APP_NAME, "Worker starting...", QSystemTrayIcon.MessageIcon.Information, 2500)

        if not self.config_manager.config.get("start_minimized", True):
            QTimer.singleShot(1500, self.show_stats_dialog)

        self.app.aboutToQuit.connect(self._on_quit)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _setup_logging(self, level: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        configure_logging(self.config_manager.config.get("worker_id", "worker"), level)
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == str(self.log_path):
                root_logger.removeHandler(handler)
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | worker=%(worker_id)s | job=%(job_id)s | status=%(status)s | %(message)s"
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    def _restore_geometry(self, widget, key: str) -> None:
        geom = self.settings.value(key)
        if geom is not None:
            widget.restoreGeometry(geom)

    def _save_geometry(self, widget, key: str) -> None:
        self.settings.setValue(key, widget.saveGeometry())

    def _ollama_tags_url(self, base_url: str) -> str:
        parsed = urlparse(base_url)
        path = parsed.path or ""
        if path.endswith("/chat"):
            base = path[:-5]
            if not base.endswith("/"):
                base += "/"
            new_path = f"{base}tags"
        else:
            api_base = "/api"
            if "/api/" in path:
                api_base = path[: path.index("/api/") + len("/api")]
            new_path = f"{api_base}/tags".replace("//", "/")
        return urlunparse(parsed._replace(path=new_path, params="", query="", fragment=""))

    def _refresh_models(
        self,
        url: str | None = None,
        parent: QWidget | None = None,
        show_dialog: bool = True,
    ) -> List[str]:
        target_url = (url or self.config_manager.config.get("ollama_url", "http://localhost:11434/api/chat")).strip()
        tags_url = self._ollama_tags_url(target_url)
        try:
            response = requests.get(tags_url, timeout=float(self.config_manager.config.get("request_timeout", 30)))
            response.raise_for_status()
            payload = response.json()
            models: List[str] = []
            for entry in payload.get("models", []):
                for key in ("name", "model", "tag"):
                    value = entry.get(key)
                    if value:
                        models.append(value)
                        break
            self.available_models = models
            if show_dialog and parent:
                if models:
                    QMessageBox.information(parent, "Models updated", f"Found {len(models)} model(s) in Ollama.")
                else:
                    QMessageBox.warning(parent, "No models", "Ollama returned no models. Install one with 'ollama pull'.")
            return models
        except requests.RequestException as exc:
            self.available_models = []
            if show_dialog and parent:
                QMessageBox.critical(parent, "Ollama error", f"Could not reach Ollama:\n{exc}")
            return []
        except ValueError:
            self.available_models = []
            if show_dialog and parent:
                QMessageBox.critical(parent, "Ollama error", "Invalid response from Ollama /api/tags")
            return []
        return self.available_models

    def _build_worker_config(self) -> WorkerConfig:
        cfg = self.config_manager.config
        return WorkerConfig(
            cerebro_url=cfg.get("cerebro_url", "http://localhost:5000"),
            ollama_url=cfg.get("ollama_url", "http://localhost:11434/api/chat"),
            model_name=cfg.get("model_name", "phi4-mini"),
            worker_id=cfg.get("worker_id", "worker"),
            poll_interval=float(cfg.get("poll_interval", 2.0)),
            gpu_threshold=float(cfg.get("gpu_threshold", 30)),
            max_backoff=float(cfg.get("max_backoff", 30)),
            request_timeout=float(cfg.get("request_timeout", 30)),
            check_gpu=bool(cfg.get("check_gpu", True)),
        )

    def _apply_settings(self) -> None:
        new_settings = self.settings_dialog.get_settings()
        self.config_manager.save(new_settings)
        self._setup_logging(new_settings.get("log_level", "INFO"))
        self.worker_engine.update(self._build_worker_config())
        self._set_run_at_startup(new_settings.get("run_at_startup", False))
        self.available_models = self._refresh_models(
            url=new_settings.get("ollama_url"), parent=self.settings_dialog, show_dialog=False
        )
        if self.settings_dialog and self.settings_dialog.isVisible():
            self.settings_dialog.set_models(self.available_models)
        if new_settings.get("auto_start_worker", False) and not self.worker_engine.thread.isRunning():
            self.worker_engine.start()

    def _gather_stats(self) -> Dict[str, Any]:
        stats = self.worker_engine.get_stats()
        stats["status"] = self.worker_engine.core.state
        try:
            stats["cpu_percent"] = psutil.cpu_percent(interval=None)
        except Exception:
            stats["cpu_percent"] = None
        return stats

    # ------------------------------------------------------------------ #
    # UI Callbacks
    # ------------------------------------------------------------------ #

    def show_settings_dialog(self) -> None:
        models = self._refresh_models(show_dialog=False)
        self.settings_dialog = SettingsDialog(
            self.config_manager.config,
            models,
            lambda url, parent: self._refresh_models(url=url, parent=parent),
        )
        self.settings_dialog.accepted.connect(self._apply_settings)
        self.settings_dialog.show()

    def show_stats_dialog(self) -> None:
        self.stats_dialog.refresh()
        self.stats_dialog.show()

    def show_log_viewer(self) -> None:
        self.log_viewer.refresh()
        self.log_viewer.show()

    def exit_app(self) -> None:
        if QMessageBox.question(None, APP_NAME, "Are you sure you want to quit?") == QMessageBox.StandardButton.Yes:
            self.worker_engine.stop()
            self._on_quit()
            self.app.quit()

    # ------------------------------------------------------------------ #
    # Worker signals
    # ------------------------------------------------------------------ #

    def _on_stats_update(self, stats: Dict[str, Any]) -> None:
        self.stats_cache = stats

    def _on_status_update(self, status: str) -> None:
        if status == "starting":
            self._notify("Worker starting", "Connecting to Cerebro ...")
        elif status == "stopped":
            self._notify("Worker stopped", "Worker is no longer polling.")

    def _on_job_started(self, job_id: str) -> None:
        self._notify("Job started", f"Processing job {job_id}", timeout=2000)

    def _on_job_completed(self, job_id: str, _result: dict) -> None:
        self._notify("Job completed", f"Job {job_id} finished successfully.", timeout=2500)

    def _on_worker_error(self, message: str) -> None:
        self._notify("Worker error", message, critical=True)

    def _notify(self, title: str, message: str, *, timeout: int = 3000, critical: bool = False) -> None:
        if self.tray:
            icon = QSystemTrayIcon.MessageIcon.Critical if critical else QSystemTrayIcon.MessageIcon.Information
            self.tray.tray.showMessage(title, message, icon, timeout)

    def _set_run_at_startup(self, enabled: bool) -> None:
        if platform.system() != "Windows" or winreg is None:
            return
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        exe_path = Path(sys.executable).resolve()
        value = f'\"{exe_path}\"'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:  # type: ignore[arg-type]
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            logging.getLogger(__name__).warning("Failed to update startup setting: %s", exc)

    def _is_run_at_startup_enabled(self) -> bool:
        if platform.system() != "Windows" or winreg is None:
            return False
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:  # type: ignore[arg-type]
                value, _ = winreg.QueryValueEx(key, APP_NAME)
                path = Path(str(value).strip('\"'))
                return path.resolve() == Path(sys.executable).resolve()
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def _on_quit(self) -> None:
        self.config_manager.save(self.config_manager.config)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    controller = GuiController(app)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
