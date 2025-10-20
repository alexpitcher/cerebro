"""Unit tests for the Cerebro worker."""

from __future__ import annotations

from typing import Any

import pytest
import responses

from worker.worker_engine import WorkerConfig, WorkerCore, WorkerCallbacks, WorkerStartupError


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
        check_gpu=False,
    )


@pytest.fixture()
def worker(worker_config: WorkerConfig) -> WorkerCore:
    callbacks = WorkerCallbacks()
    instance = WorkerCore(worker_config, callbacks=callbacks)
    instance.logger.logger.setLevel("CRITICAL")  # type: ignore[attr-defined]
    return instance


@responses.activate
def test_process_job_success(worker: WorkerCore, worker_config: WorkerConfig) -> None:
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
def test_process_job_handles_ollama_error(worker: WorkerCore, worker_config: WorkerConfig) -> None:
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


def test_process_job_requires_messages(worker: WorkerCore) -> None:
    """Jobs without message payloads should be rejected."""
    result = worker._process_job({"job_id": "abc123", "messages": []})
    assert result is None


def test_parse_ollama_response_requires_message(worker: WorkerCore) -> None:
    """Parsing fails gracefully when the message key is missing."""
    class DummyResponse:
        def json(self) -> dict[str, Any]:
            return {"not_message": {}}

    assert worker._parse_ollama_response(DummyResponse(), "test-job") is None


def test_loop_marks_error_result_as_failure(worker: WorkerCore, monkeypatch) -> None:
    job = {"job_id": "abc123", "messages": [{"role": "user", "content": "Ping"}]}
    failures: list[str] = []

    def fake_fetch():
        worker.stop_event.set()
        return job

    def fake_process(_job):
        return {"error": "Boom"}

    def fake_failure(job_id: str, message: str):
        failures.append(message)

    monkeypatch.setattr(worker, "_fetch_job_with_retry", fake_fetch)
    monkeypatch.setattr(worker, "_process_job", fake_process)
    monkeypatch.setattr(worker, "_report_failure", fake_failure)

    worker._loop()
    assert failures and "Boom" in failures[0]


@responses.activate
def test_ensure_model_prefers_llama_when_phi_missing(worker: WorkerCore):
    worker.config.model_name = "nonexistent"
    responses.get(
        "http://localhost:11434/api/tags",
        json={"models": [{"name": "qwen2:1.5b"}, {"name": "llama3.2:3b"}]},
    )
    worker._ensure_model()
    assert worker.config.model_name == "llama3.2:3b"


@responses.activate
def test_ensure_model_raises_when_ollama_unreachable(worker: WorkerCore):
    responses.get("http://localhost:11434/api/tags", status=503)
    with pytest.raises(WorkerStartupError):
        worker._ensure_model()
