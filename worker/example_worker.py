"""Example Cerebro worker that polls the manager API for jobs."""

from __future__ import annotations

import os
import time
from typing import Any

import requests


MANAGER_BASE_URL = os.getenv("CEREBRO_MANAGER_URL", "http://localhost:5000")
POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL", "2"))


def main() -> None:
    """Continuously fetch jobs, pretend to process them, and submit results."""
    while True:
        job = _get_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        job_id = job["job_id"]
        messages = job["messages"]
        print(f"[worker] Processing job {job_id} ({len(messages)} messages)")

        # Placeholder processing logic; replace with actual LLM invocation.
        result_payload = {
            "result": {
                "response": f"Processed {len(messages)} messages.",
                "messages": messages,
            },
            "status": "completed",
        }

        _complete_job(job_id=job_id, result=result_payload["result"], status=result_payload["status"])


def _get_job() -> dict[str, Any] | None:
    response = requests.post(f"{MANAGER_BASE_URL}/get_job", timeout=10)
    if response.status_code == 204:
        return None
    response.raise_for_status()
    return response.json()


def _complete_job(job_id: str, result: dict[str, Any], status: str = "completed") -> None:
    payload = {
        "job_id": job_id,
        "status": status,
        "result": result,
    }
    response = requests.post(f"{MANAGER_BASE_URL}/complete_job", json=payload, timeout=10)
    response.raise_for_status()
    print(f"[worker] Completed job {job_id}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nWorker stopped.")
