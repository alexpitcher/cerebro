"""Shared worker logic and Qt-enabled engine for Cerebro."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests
from dotenv import load_dotenv
from requests import Response
from requests.exceptions import RequestException

LOGGER = logging.getLogger("cerebro.worker")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class WorkerConfig:
    """Runtime configuration for the worker process."""

    cerebro_url: str
    ollama_url: str
    model_name: str
    worker_id: str
    poll_interval: float
    gpu_threshold: float
    max_backoff: float
    request_timeout: float
    check_gpu: bool = True

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        load_dotenv()

        cerebro_url = os.getenv("CEREBRO_URL", "http://localhost:5000").rstrip("/")
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat").rstrip("/")
        model_name = os.getenv("MODEL_NAME", "phi4-mini")
        worker_id = os.getenv("WORKER_ID") or socket.gethostname()
        poll_interval = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))
        gpu_threshold = float(os.getenv("GPU_THRESHOLD", "30"))
        max_backoff = float(os.getenv("MAX_BACKOFF_SECONDS", "30"))
        request_timeout = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
        check_gpu = os.getenv("CHECK_GPU", "true").strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            cerebro_url=cerebro_url,
            ollama_url=ollama_url,
            model_name=model_name,
            worker_id=worker_id,
            poll_interval=poll_interval,
            gpu_threshold=gpu_threshold,
            max_backoff=max_backoff,
            request_timeout=request_timeout,
            check_gpu=check_gpu,
        )


# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #


class StructuredLogger(logging.LoggerAdapter):
    """Logger adapter that injects worker/job metadata into log records."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        base_extra = dict(self.extra) if hasattr(self, "extra") else {}
        extra = kwargs.get("extra", {})
        merged = {**base_extra, **extra}
        merged.setdefault("worker_id", base_extra.get("worker_id", "unknown"))
        merged.setdefault("job_id", merged.get("job_id", "-"))
        merged.setdefault("status", merged.get("status", "n/a"))
        kwargs["extra"] = merged
        return msg, kwargs


class ContextFilter(logging.Filter):
    """Ensure required logging fields are present on every record."""

    def __init__(self, worker_id: str):
        super().__init__()
        self.worker_id = worker_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "worker_id"):
            record.worker_id = self.worker_id
        if not hasattr(record, "job_id"):
            record.job_id = "-"
        if not hasattr(record, "status"):
            record.status = "n/a"
        return True


def configure_logging(worker_id: str, level: int | str = logging.INFO) -> None:
    """Configure structured logging format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | worker=%(worker_id)s | job=%(job_id)s | status=%(status)s | %(message)s",
    )
    logging.getLogger().addFilter(ContextFilter(worker_id))


# --------------------------------------------------------------------------- #
# Worker callbacks and stats
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class WorkerCallbacks:
    """Callback hooks invoked by the worker during lifecycle events."""

    on_status: Callable[[str], None] = lambda status: None
    on_job_started: Callable[[str], None] = lambda job_id: None
    on_job_completed: Callable[[str, dict[str, Any]], None] = lambda job_id, result: None
    on_job_failed: Callable[[str, str], None] = lambda job_id, message: None
    on_error: Callable[[str], None] = lambda message: None


@dataclass
class WorkerStats:
    """Aggregated statistics for the worker session."""

    start_time: float = field(default_factory=time.time)
    jobs_completed: int = 0
    jobs_failed: int = 0
    total_job_time: float = 0.0
    current_job_id: str | None = None
    last_job_started_at: float | None = None

    @property
    def uptime(self) -> float:
        return time.time() - self.start_time

    @property
    def average_job_time(self) -> float:
        if self.jobs_completed == 0:
            return 0.0
        return self.total_job_time / self.jobs_completed

    def to_dict(self) -> dict[str, Any]:
        return {
            "uptime": self.uptime,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "current_job_id": self.current_job_id,
            "avg_job_time": self.average_job_time,
        }


# --------------------------------------------------------------------------- #
# Core worker (thread-safe, no Qt dependency)
# --------------------------------------------------------------------------- #


class WorkerCore:
    """Headless worker loop that can be driven by CLI or GUI."""

    def __init__(self, config: WorkerConfig, callbacks: WorkerCallbacks | None = None):
        self.config = config
        if not config.worker_id:
            config.worker_id = socket.gethostname()
        self.callbacks = callbacks or WorkerCallbacks()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.session = requests.Session()
        self.logger = StructuredLogger(LOGGER, {"worker_id": self.config.worker_id})
        self.stats = WorkerStats()
        self.state_lock = threading.Lock()
        self._state: str = "stopped"

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Start polling for jobs until shutdown."""
        self._update_state("starting")
        self.logger.info("Worker started.", extra={"status": "startup"})
        self._register_worker()
        try:
            self._loop()
        finally:
            self._update_state("stopped")
            self._deregister_worker()
            self.logger.info("Worker shutdown complete.", extra={"status": "shutdown"})

    def shutdown(self) -> None:
        """Signal the worker to stop."""
        self.logger.info("Shutdown signal received.", extra={"status": "shutdown"})
        self.stop_event.set()
        self.resume()

    def pause(self) -> None:
        if self.pause_event.is_set():
            self.logger.info("Pausing worker.")
            self.pause_event.clear()
            self._update_state("paused")

    def resume(self) -> None:
        if not self.pause_event.is_set():
            self.logger.info("Resuming worker.")
            self.pause_event.set()

    def get_stats(self) -> dict[str, Any]:
        return self.stats.to_dict()

    # ------------------------------------------------------------------ #
    # Internal loop
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self.pause_event.wait()
            if self.stop_event.is_set():
                break

            if self.config.check_gpu and not self._can_use_gpu():
                self._update_state("gpu_busy")
                self.logger.info(
                    "GPU utilization above threshold; waiting before retry.",
                    extra={"status": "gpu_busy"},
                )
                self._sleep(self.config.poll_interval)
                continue

            job = self._fetch_job_with_retry()
            if job is None:
                self._update_state("idle")
                self.logger.debug("No job available; sleeping before next poll.")
                self._sleep(self.config.poll_interval)
                continue

            job_id = job.get("job_id")
            if not job_id:
                self.logger.error("Received malformed job payload without job_id.", extra={"status": "error"})
                self._sleep(self.config.poll_interval)
                continue

            self._update_state("working")
            self.stats.current_job_id = job_id
            self.stats.last_job_started_at = time.time()
            self.callbacks.on_job_started(job_id)
            self.logger.info("Received job.", extra={"status": "received", "job_id": job_id})

            try:
                result = self._process_job(job)
            except Exception as exc:  # noqa: BLE001
                message = f"Unexpected error while processing job: {exc}"
                self.logger.exception(message, extra={"status": "failed", "job_id": job_id})
                self._report_failure(job_id, message)
                continue

            if result is None:
                self.logger.warning(
                    "Job processing returned no result; job marked as failed.",
                    extra={"status": "failed", "job_id": job_id},
                )
                self._report_failure(job_id, "Job processing failed without result.")
                continue

            if isinstance(result, dict) and result.get("error"):
                error_message = str(result.get("error"))
                self.logger.warning(
                    "Job %s failed: %s",
                    job_id,
                    error_message,
                    extra={"status": "failed", "job_id": job_id},
                )
                self._report_failure(job_id, error_message)
                continue

            self._report_success(job_id, result)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            time.sleep(min(0.1, end - time.time()))

    # ------------------------------------------------------------------ #
    # Networking helpers
    # ------------------------------------------------------------------ #

    def _fetch_job_with_retry(self) -> Optional[dict[str, Any]]:
        backoff = 1.0
        while not self.stop_event.is_set():
            self.pause_event.wait()
            if self.stop_event.is_set():
                return None
            try:
                return self._fetch_job()
            except RequestException as exc:
                self._update_state("retrying")
                self.logger.warning(
                    "Network error fetching job: %s",
                    exc,
                    extra={"status": "retry"},
                )
                self.callbacks.on_error(str(exc))
                self._sleep(backoff)
                backoff = min(backoff * 2, self.config.max_backoff)
        return None

    def _fetch_job(self) -> Optional[dict[str, Any]]:
        response = self.session.post(
            f"{self.config.cerebro_url}/get_job",
            headers={"X-Worker-ID": self.config.worker_id},
            timeout=self.config.request_timeout,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            self.logger.error("Failed to decode job JSON: %s", exc, extra={"status": "error"})
            return None

    def _process_job(self, job: dict[str, Any]) -> Optional[dict[str, Any]]:
        job_id = job.get("job_id", "-")
        messages = job.get("messages")
        if not isinstance(messages, list) or not messages:
            self.logger.error(
                "Job payload missing `messages` array.",
                extra={"status": "error", "job_id": job_id},
            )
            return None

        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "stream": False,
        }

        try:
            response = self.session.post(
                self.config.ollama_url,
                json=payload,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
        except RequestException as exc:
            self.logger.error(
                "Ollama request failed: %s",
                exc,
                extra={"status": "ollama_error", "job_id": job_id},
            )
            return {"error": str(exc)}

        result = self._parse_ollama_response(response, job_id)
        if result is None:
            return {"error": "Malformed response from Ollama."}
        return result

    def _parse_ollama_response(self, response: Response, job_id: str) -> Optional[dict[str, Any]]:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            self.logger.error(
                "Invalid JSON from Ollama: %s",
                exc,
                extra={"status": "ollama_error", "job_id": job_id},
            )
            return None

        if "message" not in data:
            self.logger.error(
                "Ollama response missing `message` key.",
                extra={"status": "ollama_error", "job_id": job_id},
            )
            return None

        return data

    def _report_success(self, job_id: str, result: dict[str, Any]) -> None:
        payload = {
            "job_id": job_id,
            "status": "completed",
            "result": result,
        }
        self._post_with_retry(
            f"{self.config.cerebro_url}/complete_job",
            payload,
            log_status="completed",
            job_id=job_id,
        )

        if self.stats.last_job_started_at:
            duration = time.time() - self.stats.last_job_started_at
            self.stats.total_job_time += duration
        self.stats.jobs_completed += 1
        self.stats.current_job_id = None
        self.callbacks.on_job_completed(job_id, result)

    def _report_failure(self, job_id: str, error_message: str) -> None:
        payload = {
            "job_id": job_id,
            "status": "failed",
            "error": error_message,
        }
        self._post_with_retry(
            f"{self.config.cerebro_url}/complete_job",
            payload,
            log_status="failed",
            job_id=job_id,
        )
        self.stats.jobs_failed += 1
        self.stats.current_job_id = None
        self.callbacks.on_job_failed(job_id, error_message)

    def _post_with_retry(self, url: str, payload: dict[str, Any], log_status: str, job_id: str) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            self.pause_event.wait()
            if self.stop_event.is_set():
                return
            try:
                response = self.session.post(
                    url,
                    json=payload,
                    timeout=self.config.request_timeout,
                    headers={"X-Worker-ID": self.config.worker_id},
                )
                response.raise_for_status()
                self.logger.info(
                    "Reported job status to Cerebro.",
                    extra={"status": log_status, "job_id": job_id},
                )
                return
            except RequestException as exc:
                self.logger.warning(
                    "Failed to report job status (%s): %s",
                    log_status,
                    exc,
                    extra={"status": "retry", "job_id": job_id},
                )
                self.callbacks.on_error(str(exc))
                self._sleep(backoff)
                backoff = min(backoff * 2, self.config.max_backoff)

    # ------------------------------------------------------------------ #
    # GPU utilities
    # ------------------------------------------------------------------ #

    def _can_use_gpu(self) -> bool:
        """Return False when GPU utilization exceeds threshold."""
        if not self.config.check_gpu:
            return True
        try:
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.STDOUT,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            self.logger.debug("nvidia-smi unavailable or failed: %s", exc, extra={"status": "gpu_unknown"})
            return True

        try:
            values = [float(line.strip()) for line in output.splitlines() if line.strip()]
        except ValueError:
            self.logger.debug("Failed to parse GPU utilization output: %s", output, extra={"status": "gpu_unknown"})
            return True

        if not values:
            return True

        utilization = max(values)
        if utilization > self.config.gpu_threshold:
            self.logger.debug(
                "GPU utilization %.2f%% exceeds threshold %.2f%%.",
                utilization,
                self.config.gpu_threshold,
                extra={"status": "gpu_busy"},
            )
            return False

        return True

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _update_state(self, state: str) -> None:
        with self.state_lock:
            self._state = state
        self.callbacks.on_status(state)

    @property
    def state(self) -> str:
        with self.state_lock:
            return self._state

    # ------------------------------------------------------------------ #
    # Registration helpers
    # ------------------------------------------------------------------ #

    def _register_worker(self) -> None:
        payload = {
            "worker_id": self.config.worker_id,
            "hostname": socket.gethostname(),
        }
        try:
            response = self.session.post(
                f"{self.config.cerebro_url}/register_worker",
                json=payload,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
            self.logger.info("Registered worker with manager.", extra={"status": "registered"})
        except RequestException as exc:
            self.logger.warning("Failed to register worker: %s", exc, extra={"status": "warning"})

    def _deregister_worker(self) -> None:
        payload = {
            "worker_id": self.config.worker_id,
        }
        try:
            response = self.session.post(
                f"{self.config.cerebro_url}/deregister_worker",
                json=payload,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
            self.logger.info("Deregistered worker from manager.", extra={"status": "stopped"})
        except RequestException as exc:
            self.logger.warning("Failed to deregister worker: %s", exc, extra={"status": "warning"})


# --------------------------------------------------------------------------- #
# Qt worker engine (optional)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - Qt not exercised in CLI tests
    from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QTimer
    from PyQt6.QtWidgets import QApplication

    class _WorkerThread(QThread):
        """QThread wrapper running the WorkerCore loop."""

        def __init__(self, core: WorkerCore):
            super().__init__()
            self.core = core

        def run(self) -> None:
            self.core.run()

    class WorkerEngine(QObject):
        """Qt-enabled worker that exposes signals for UI integration."""

        status_changed = pyqtSignal(str)
        job_started = pyqtSignal(str)
        job_completed = pyqtSignal(str, dict)
        error_occurred = pyqtSignal(str)
        stats_updated = pyqtSignal(dict)

        def __init__(self, config: WorkerConfig, parent: Optional[QObject] = None):
            super().__init__(parent)
            self.config = config
            self.core = WorkerCore(config, callbacks=WorkerCallbacks(
                on_status=self._emit_status,
                on_job_started=self._emit_job_started,
                on_job_completed=self._emit_job_completed,
                on_job_failed=self._emit_job_failed,
                on_error=self._emit_error,
            ))
            self.thread = _WorkerThread(self.core)
            self.thread.finished.connect(self._on_thread_finished)
            self._gaming_mode_until: float | None = None

            self.stats_timer = QTimer(self)
            self.stats_timer.setInterval(5000)
            self.stats_timer.timeout.connect(self._publish_stats)

        # ------------------------------------------------------------------ #
        # Control methods
        # ------------------------------------------------------------------ #

        def start(self) -> None:
            if self.thread.isRunning():
                return
            self.core.stop_event.clear()
            self.thread.start()
            self.stats_timer.start()
            self._emit_status("starting")

        def stop(self) -> None:
            if not self.thread.isRunning():
                return
            self.core.shutdown()
            self.thread.quit()
            self.thread.wait(5000)
            self.stats_timer.stop()
            self._emit_status("stopped")

        def pause(self) -> None:
            self.core.pause()
            self._emit_status("paused")

        def resume(self) -> None:
            self.core.resume()
            self._emit_status(self.core.state)

        def pause_for(self, duration_seconds: float) -> None:
            self.pause()
            self._gaming_mode_until = time.time() + duration_seconds

        def update(self, config: WorkerConfig) -> None:
            was_running = self.thread.isRunning()
            self.stop()
            self.config = config
            callbacks = self.core.callbacks
            self.core = WorkerCore(config, callbacks=callbacks)
            self.thread = _WorkerThread(self.core)
            self.thread.finished.connect(self._on_thread_finished)
            if was_running:
                self.start()

        def get_stats(self) -> dict[str, Any]:
            return self.core.get_stats()

        # ------------------------------------------------------------------ #
        # Internal helpers
        # ------------------------------------------------------------------ #

        def _publish_stats(self) -> None:
            stats = self.core.get_stats()
            if self._gaming_mode_until:
                remaining = max(0, self._gaming_mode_until - time.time())
                stats["gaming_mode_remaining"] = remaining
                if remaining <= 0:
                    self._gaming_mode_until = None
                    self.resume()
                    self.error_occurred.emit("Gaming mode ended, worker resumed.")
            self.stats_updated.emit(stats)

        def _emit_status(self, status: str) -> None:
            self.status_changed.emit(status)

        def _emit_job_started(self, job_id: str) -> None:
            self.job_started.emit(job_id)

        def _emit_job_completed(self, job_id: str, result: dict[str, Any]) -> None:
            self.job_completed.emit(job_id, result)

        def _emit_job_failed(self, job_id: str, message: str) -> None:
            self.error_occurred.emit(f"Job {job_id} failed: {message}")

        def _emit_error(self, message: str) -> None:
            self.error_occurred.emit(message)

        def _on_thread_finished(self) -> None:
            self.stats_timer.stop()
            self._emit_status("stopped")


except ImportError:  # pragma: no cover

    class WorkerEngine:  # type: ignore[override]
        """Placeholder when PyQt6 is unavailable."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("PyQt6 is required for WorkerEngine. Install from gui_requirements.txt.")
