"""Cerebro worker process that consumes jobs and executes them via Ollama."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from requests import Response
from requests.exceptions import RequestException


LOGGER = logging.getLogger("cerebro.worker")


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

        return cls(
            cerebro_url=cerebro_url,
            ollama_url=ollama_url,
            model_name=model_name,
            worker_id=worker_id,
            poll_interval=poll_interval,
            gpu_threshold=gpu_threshold,
            max_backoff=max_backoff,
            request_timeout=request_timeout,
        )


class StructuredLogger(logging.LoggerAdapter):
    """Logger adapter that injects worker/job metadata into log records."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("worker_id", extra.get("worker_id", "unknown"))
        extra.setdefault("job_id", extra.get("job_id", "-"))
        extra.setdefault("status", extra.get("status", "n/a"))
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


class CerebroWorker:
    """Main worker loop handling job polling, execution, and completion."""

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.stop_event = threading.Event()
        self.session = requests.Session()
        self.logger = StructuredLogger(LOGGER, {"worker_id": self.config.worker_id})

    def run(self) -> None:
        """Start polling for jobs until shutdown."""
        self.logger.info("Worker started.", extra={"status": "startup"})

        while not self.stop_event.is_set():
            if not self._can_use_gpu():
                self.logger.info(
                    "GPU utilization above threshold; waiting before retry.",
                    extra={"status": "gpu_busy"},
                )
                self.stop_event.wait(self.config.poll_interval)
                continue

            job = self._fetch_job_with_retry()
            if job is None:
                self.stop_event.wait(self.config.poll_interval)
                continue

            job_id = job.get("job_id")
            if not job_id:
                self.logger.error(
                    "Received malformed job payload without job_id.",
                    extra={"status": "error"},
                )
                self.stop_event.wait(self.config.poll_interval)
                continue

            self.logger.info(
                "Received job.",
                extra={"status": "received", "job_id": job_id},
            )

            try:
                result = self._process_job(job)
            except Exception as exc:  # noqa: BLE001
                self.logger.exception(
                    "Unexpected error while processing job.",
                    extra={"status": "failed", "job_id": job_id},
                )
                self._report_failure(job_id, str(exc))
                continue

            if result is None:
                self.logger.warning(
                    "Job processing returned no result; job marked as failed.",
                    extra={"status": "failed", "job_id": job_id},
                )
                self._report_failure(job_id, "Job processing failed without result.")
                continue

            self._report_success(job_id, result)

        self.logger.info("Worker shutdown complete.", extra={"status": "shutdown"})

    def shutdown(self) -> None:
        """Signal the worker to stop."""
        self.logger.info("Shutdown signal received.", extra={"status": "shutdown"})
        self.stop_event.set()

    # --- Job lifecycle helpers ------------------------------------------------

    def _fetch_job_with_retry(self) -> Optional[dict[str, Any]]:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                return self._fetch_job()
            except RequestException as exc:
                self.logger.warning(
                    "Network error fetching job: %s",
                    exc,
                    extra={"status": "retry"},
                )
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, self.config.max_backoff)
        return None

    def _fetch_job(self) -> Optional[dict[str, Any]]:
        response = self.session.post(
            f"{self.config.cerebro_url}/get_job",
            timeout=self.config.request_timeout,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            self.logger.error(
                "Failed to decode job JSON: %s",
                exc,
                extra={"status": "error"},
            )
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

    def _post_with_retry(self, url: str, payload: dict[str, Any], log_status: str, job_id: str) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                response = self.session.post(url, json=payload, timeout=self.config.request_timeout)
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
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, self.config.max_backoff)

    # --- Utilities ------------------------------------------------------------

    def _can_use_gpu(self) -> bool:
        """Return False when GPU utilization exceeds threshold."""
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


def configure_logging(worker_id: str) -> None:
    """Configure structured logging format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | worker=%(worker_id)s | job=%(job_id)s | status=%(status)s | %(message)s",
    )
    logging.getLogger().addFilter(ContextFilter(worker_id))


def install_signal_handlers(worker: CerebroWorker) -> None:
    """Attach signal handlers for graceful shutdown."""

    def _handler(signum: int, _frame: Any) -> None:
        worker.logger.info(
            "Signal %s received; shutting down.",
            signum,
            extra={"status": "shutdown"},
        )
        worker.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except ValueError:
            # Signals like SIGTERM are not available on Windows prior to Python 3.8
            continue


def main() -> None:
    config = WorkerConfig.from_env()
    configure_logging(config.worker_id)
    worker = CerebroWorker(config)
    install_signal_handlers(worker)
    worker.run()


if __name__ == "__main__":
    main()
