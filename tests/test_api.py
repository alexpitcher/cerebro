"""Integration-style tests for the Cerebro API."""

from __future__ import annotations

import logging
import pytest
import fakeredis
from flask import Flask

from manager.config import AppConfig
from manager.queue import JobQueue, JobStatus
from manager.server import create_api_blueprint, register_error_handlers


@pytest.fixture()
def app() -> Flask:
    config = AppConfig(
        redis_host="test",
        redis_port=6379,
        redis_db=0,
        redis_job_timeout=1,
        redis_block_timeout=1,
        job_ttl_seconds=60,
        job_history_size=20,
        debug_logging=True,
    )
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    job_queue = JobQueue(config=config, redis_client=fake_redis)

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["APP_CONFIG"] = config
    flask_app.config["JOB_QUEUE"] = job_queue
    flask_app.register_blueprint(create_api_blueprint(job_queue))
    register_error_handlers(flask_app)
    return flask_app


@pytest.fixture()
def client(app: Flask):
    return app.test_client()


def _submit_sample_job(client):
    response = client.post(
        "/submit_job",
        json={
            "messages": [{"role": "user", "content": "Hello, Cerebro!"}],
            "metadata": {"priority": "normal"},
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload is not None and "job_id" in payload
    return payload["job_id"]


def test_job_lifecycle(client, caplog):
    with caplog.at_level(logging.INFO, logger="manager.server"):
        job_id = _submit_sample_job(client)

        # Worker pulls job
        response = client.post("/get_job", headers={"X-Worker-ID": "worker-1"})
        assert response.status_code == 200
        job_payload = response.get_json()
        assert job_payload is not None
        assert job_payload["job_id"] == job_id
        assert job_payload["messages"][0]["content"] == "Hello, Cerebro!"

        # Worker completes job successfully
        completion = client.post(
            "/complete_job",
            headers={"X-Worker-ID": "worker-1"},
            json={
                "job_id": job_id,
                "status": JobStatus.COMPLETED.value,
                "result": {"message": {"content": "Done"}},
            },
        )
        assert completion.status_code == 200
        job_data = completion.get_json()
        assert job_data is not None
        assert job_data["status"] == JobStatus.COMPLETED.value
        assert job_data["result"] == {"message": {"content": "Done"}}

        # Client can poll for result
        result_response = client.get(f"/get_result/{job_id}")
        assert result_response.status_code == 200
        result_payload = result_response.get_json()
        assert result_payload is not None
        assert result_payload["result"] == {"message": {"content": "Done"}}

    # Validate logging captures payloads/results under debug logging
    log_messages = [record.getMessage() for record in caplog.records]
    assert any("payload=[{'content': 'Hello, Cerebro!'" in message for message in log_messages)
    assert any("result={'message': {'content': 'Done'}}" in message for message in log_messages)


def test_stats_and_health(client):
    stats_before = client.get("/stats")
    assert stats_before.status_code == 200
    assert stats_before.get_json() == {"queued": 0, "processing": 0}

    _submit_sample_job(client)
    stats_after_submit = client.get("/stats")
    payload = stats_after_submit.get_json()
    assert payload == {"queued": 1, "processing": 0}

    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}


def test_worker_registration(client):
    register = client.post(
        "/register_worker",
        json={"worker_id": "worker-abc", "hostname": "test-host"},
    )
    assert register.status_code == 201

    workers = client.get("/workers")
    assert workers.status_code == 200
    data = workers.get_json()
    assert isinstance(data, list)
    assert any(entry["worker_id"] == "worker-abc" for entry in data)

    deregister = client.post("/deregister_worker", json={"worker_id": "worker-abc"})
    assert deregister.status_code == 200

    workers_after = client.get("/workers").get_json()
    assert all(entry["worker_id"] != "worker-abc" for entry in workers_after)


def test_recent_jobs_endpoint(client):
    job_id = _submit_sample_job(client)
    client.post("/get_job", headers={"X-Worker-ID": "worker-1"})
    client.post(
        "/complete_job",
        headers={"X-Worker-ID": "worker-1"},
        json={
            "job_id": job_id,
            "status": JobStatus.COMPLETED.value,
            "result": {"message": {"content": "Done"}},
        },
    )

    response = client.get("/recent_jobs?limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)
    assert payload[0]["job_id"] == job_id
    assert payload[0]["result_preview"] == "Done"
    assert payload[0]["messages"][0]["content"] == "Hello, Cerebro!"

    # Worker completes job successfully
    completion = client.post(
        "/complete_job",
        json={
            "job_id": job_id,
            "status": JobStatus.COMPLETED.value,
            "result": {"output": "Done"},
        },
    )
    assert completion.status_code == 200
    job_data = completion.get_json()
    assert job_data is not None
    assert job_data["status"] == JobStatus.COMPLETED.value
    assert job_data["result"] == {"output": "Done"}

    # Client can poll for result
    result_response = client.get(f"/get_result/{job_id}")
    assert result_response.status_code == 200
    result_payload = result_response.get_json()
    assert result_payload is not None
    assert result_payload["result"] == {"output": "Done"}


def test_stats_and_health(client):
    stats_before = client.get("/stats")
    assert stats_before.status_code == 200
    assert stats_before.get_json() == {"queued": 0, "processing": 0}

    _submit_sample_job(client)
    stats_after_submit = client.get("/stats")
    payload = stats_after_submit.get_json()
    assert payload == {"queued": 1, "processing": 0}

    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}
