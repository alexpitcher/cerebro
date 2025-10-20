"""Unit tests for the Cerebro worker."""

from __future__ import annotations

from typing import Any

import pytest
import responses

from worker.worker import CerebroWorker, WorkerConfig


@pytest.fixture()
def worker_config() -> WorkerConfig:
    return WorkerConfig(
        cerebro_url="http://manager:5000",
        ollama_url="http://localhost:11434/api/chat",
        model_name="phi4-mini",
        worker_id="test-worker",
        poll_interval=0.1,
        gpu_threshold=95.0,
        max_backoff=0.2,
        request_timeout=1.0,
    )


@pytest.fixture()
def worker(worker_config: WorkerConfig) -> CerebroWorker:
    instance = CerebroWorker(worker_config)
    instance.logger.logger.setLevel("CRITICAL")  # type: ignore[attr-defined]
    return instance


@responses.activate
def test_process_job_success(worker: CerebroWorker, worker_config: WorkerConfig) -> None:
    """Ensure successful Ollama responses are propagated."""
    responses.post(
        worker_config.ollama_url,
        json={
            "message": {
                "role": "assistant",
                "content": "Space is mostly empty, but not entirely so!",
            }
        },
        status=200,
    )
    job = {
        "job_id": "abc123",
        "messages": [{"role": "user", "content": "Fun fact?"}],
    }

    result = worker._process_job(job)
    assert result is not None
    assert result["message"]["content"].startswith("Space is mostly empty")


@responses.activate
def test_process_job_handles_ollama_error(worker: CerebroWorker, worker_config: WorkerConfig) -> None:
    """Worker should wrap Ollama connectivity errors into result payload."""
    responses.post(worker_config.ollama_url, status=404)
    job = {
        "job_id": "abc123",
        "messages": [{"role": "user", "content": "Fun fact?"}],
    }

    result = worker._process_job(job)
    assert result is not None
    assert "error" in result
    assert "404 Client Error" in result["error"]


def test_process_job_requires_messages(worker: CerebroWorker) -> None:
    """Jobs without message payloads should be rejected."""
    result = worker._process_job({"job_id": "abc123", "messages": []})
    assert result is None


def test_parse_ollama_response_requires_message(worker: CerebroWorker) -> None:
    """Parsing fails gracefully when the message key is missing."""
    class DummyResponse:
        def json(self) -> dict[str, Any]:
            return {"not_message": {}}

    assert worker._parse_ollama_response(DummyResponse(), "test-job") is None
