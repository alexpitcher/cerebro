"""Flask application exposing the Cerebro manager API."""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from typing import Any

from flask import Blueprint, Flask, jsonify, request
from werkzeug.exceptions import BadRequest

from .config import AppConfig
from .queue import JobNotFound, JobQueue, JobQueueError, JobRecord, JobStatus

LOGGER = logging.getLogger(__name__)


def create_api_blueprint(job_queue: JobQueue) -> Blueprint:
    """Create the API blueprint with all queue routes."""
    api = Blueprint("manager_api", __name__)

    @api.route("/submit_job", methods=["POST"])
    def submit_job() -> Any:
        payload = _require_json()
        messages = payload.get("messages")
        metadata = payload.get("metadata")

        if not isinstance(messages, list) or not messages:
            return _error_response("`messages` must be a non-empty list.", HTTPStatus.BAD_REQUEST)

        try:
            job_id = job_queue.submit_job(messages=messages, metadata=metadata)
        except JobQueueError as exc:
            LOGGER.exception("Failed to submit job")
            return _error_response(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        return jsonify({"job_id": job_id, "status": JobStatus.QUEUED.value}), HTTPStatus.CREATED

    @api.route("/get_job", methods=["POST"])
    def get_job() -> Any:
        try:
            job = job_queue.get_next_job()
        except JobQueueError as exc:
            LOGGER.exception("Failed to fetch next job")
            return _error_response(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        if job is None:
            return "", HTTPStatus.NO_CONTENT

        return jsonify(job), HTTPStatus.OK

    @api.route("/complete_job", methods=["POST"])
    def complete_job() -> Any:
        payload = _require_json()
        job_id = payload.get("job_id")
        status_str = payload.get("status")
        result = payload.get("result")
        error = payload.get("error")

        if not job_id or not status_str:
            return _error_response("`job_id` and `status` are required.", HTTPStatus.BAD_REQUEST)

        try:
            status = JobStatus(status_str)
        except ValueError:
            return _error_response("`status` must be 'completed' or 'failed'.", HTTPStatus.BAD_REQUEST)

        if status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
            return _error_response("`status` must be 'completed' or 'failed'.", HTTPStatus.BAD_REQUEST)

        try:
            job_record = job_queue.complete_job(job_id=job_id, status=status, result=result, error=error)
        except JobNotFound as exc:
            return _error_response(str(exc), HTTPStatus.NOT_FOUND)
        except JobQueueError as exc:
            LOGGER.exception("Failed to complete job %s", job_id)
            return _error_response(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        return jsonify(_serialize_job(job_record)), HTTPStatus.OK

    @api.route("/get_result/<job_id>", methods=["GET"])
    def get_result(job_id: str) -> Any:
        try:
            job = job_queue.get_result(job_id)
        except JobNotFound as exc:
            return _error_response(str(exc), HTTPStatus.NOT_FOUND)
        except JobQueueError as exc:
            LOGGER.exception("Failed to retrieve job %s", job_id)
            return _error_response(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        return jsonify(_serialize_job(job)), HTTPStatus.OK

    @api.route("/stats", methods=["GET"])
    def stats() -> Any:
        try:
            data = job_queue.get_stats()
        except JobQueueError as exc:
            return _error_response(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        return jsonify(data), HTTPStatus.OK

    @api.route("/health", methods=["GET"])
    def health() -> Any:
        healthy = job_queue.health_check()
        status_code = HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE
        return jsonify({"status": "ok" if healthy else "error"}), status_code

    return api


def register_error_handlers(app: Flask) -> None:
    """Register application-wide error handlers for consistent JSON responses."""

    @app.errorhandler(JobQueueError)
    def handle_queue_error(error: JobQueueError):
        LOGGER.exception("Queue error: %s", error)
        return _error_response(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)

    @app.errorhandler(BadRequest)
    def handle_bad_request(error: BadRequest):
        message = getattr(error, "description", "Bad request.")
        return _error_response(message, HTTPStatus.BAD_REQUEST)


def _serialize_job(job: JobRecord) -> dict[str, Any]:
    """Convert a JobRecord into a JSON-ready dict."""
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "messages": job.messages,
        "metadata": job.metadata,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


def _require_json() -> dict[str, Any]:
    """Parse the request JSON body, returning an object or raising an error."""
    payload = request.get_json(silent=True)
    if payload is None or not isinstance(payload, dict):
        raise BadRequest("Invalid or missing JSON payload.")
    return payload


def _error_response(message: str, status: HTTPStatus):
    """Return a standardized JSON error response."""
    return jsonify({"error": message, "status": status.phrase}), int(status)


def create_wsgi_app() -> Flask:
    """Create an app instance for WSGI servers."""
    config = AppConfig.from_env()
    app = Flask(__name__)
    app.config["APP_CONFIG"] = config
    job_queue = JobQueue(config=config)
    app.config["JOB_QUEUE"] = job_queue

    app.register_blueprint(create_api_blueprint(job_queue))
    register_error_handlers(app)
    return app


app = create_wsgi_app()


if __name__ == "__main__":
    port = int(os.getenv("API_PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
