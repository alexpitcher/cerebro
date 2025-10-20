"""Windows service entrypoint for the Cerebro worker."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

try:
    import servicemanager  # type: ignore[import]
except ImportError:
    servicemanager = None  # type: ignore[assignment]

try:
    import win32event  # type: ignore[import]
    import win32service  # type: ignore[import]
    import win32serviceutil  # type: ignore[import]
except ImportError as exc:  # pragma: no cover - runtime guard
    raise ImportError(
        "pywin32 is required to run CerebroWorkerService. Install pywin32 on Windows."
    ) from exc

from worker.worker_engine import (  # reuse core logic
    WorkerCallbacks,
    WorkerConfig,
    WorkerCore,
    configure_logging,
)

LOGGER = logging.getLogger("cerebro.worker.service")


def _load_config_from_file(path: Path) -> dict[str, object]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOGGER.warning("Failed to parse config.json (%s): %s", path, exc)
    return {}


def _apply_config_overrides(config_data: dict[str, object]) -> None:
    mapping = {
        "cerebro_url": "CEREBRO_URL",
        "ollama_url": "OLLAMA_URL",
        "model_name": "MODEL_NAME",
        "worker_id": "WORKER_ID",
        "poll_interval": "POLL_INTERVAL_SECONDS",
        "gpu_threshold": "GPU_THRESHOLD",
        "max_backoff": "MAX_BACKOFF_SECONDS",
        "request_timeout": "REQUEST_TIMEOUT_SECONDS",
        "check_gpu": "CHECK_GPU",
    }
    for key, env_name in mapping.items():
        if key in config_data and config_data[key] is not None:
            os.environ[env_name] = str(config_data[key])


class CerebroWorkerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "CerebroWorkerService"
    _svc_display_name_ = "Cerebro Worker"
    _svc_description_ = "Background worker that processes Cerebro LLM jobs."

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.worker: WorkerCore | None = None
        self.thread: threading.Thread | None = None
        self.log_path = Path(os.getenv("CEREBRO_WORKER_LOG", r"C:\\ProgramData\\CerebroWorker\\worker.log"))
        self.config_dir = Path(os.getenv("CEREBRO_WORKER_DIR", r"C:\\Program Files\\CerebroWorker"))
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def SvcDoRun(self):
        if servicemanager:
            servicemanager.LogInfoMsg("Cerebro Worker service starting...")
        config_file = self.config_dir / "config.json"
        config_data = _load_config_from_file(config_file)
        _apply_config_overrides(config_data)

        config = WorkerConfig.from_env()
        configure_logging(config.worker_id)
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | worker=%(worker_id)s | job=%(job_id)s | status=%(status)s | %(message)s"
        )
        file_handler.setFormatter(formatter)
        logging.getLogger().addHandler(file_handler)

        callbacks = WorkerCallbacks(on_error=lambda msg: LOGGER.error(msg))
        self.worker = WorkerCore(config, callbacks=callbacks)
        self.thread = threading.Thread(target=self.worker.run, daemon=True)
        self.thread.start()

        if servicemanager:
            servicemanager.LogInfoMsg("Cerebro Worker service running.")
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

        if self.worker:
            self.worker.shutdown()
        if self.thread:
            self.thread.join(timeout=15)
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        if servicemanager:
            servicemanager.LogInfoMsg("Cerebro Worker service stopped.")

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self.ReportServiceStatus(win32service.SERVICE_STOPPED)


def main() -> None:
if len(sys.argv) == 1:
        if not servicemanager:
            raise ImportError(
                "servicemanager is unavailable. Run this binary only on Windows and ensure pywin32 is installed."
            )
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(CerebroWorkerService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(CerebroWorkerService)


if __name__ == "__main__":
    main()
