"""Redis-backed job queue utilities for the Cerebro manager."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from redis import Redis
from redis.exceptions import RedisError

from .config import AppConfig

LOGGER = logging.getLogger(__name__)


class JobQueueError(Exception):
    """Base class for queue errors."""


class JobNotFound(JobQueueError):
    """Raised when a job is not present in Redis."""


class InvalidJobStatus(JobQueueError):
    """Raised when attempting to set an invalid job status."""


class JobStatus(str, Enum):
    """Valid Job statuses."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class JobRecord:
    """In-memory representation of a job stored in Redis."""

    job_id: str
    status: JobStatus
    messages: list[dict[str, Any]]
    created_at: float
    updated_at: float
    metadata: dict[str, Any] | None = None
    started_at: float | None = None
    completed_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_json(self) -> str:
        """Serialize the record to a JSON string."""
        payload = {
            "job_id": self.job_id,
            "status": self.status.value,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }
        return json.dumps(payload)

    @staticmethod
    def from_json(data: str) -> "JobRecord":
        """Deserialize from a JSON string."""
        payload = json.loads(data)
        payload["status"] = JobStatus(payload["status"])
        return JobRecord(**payload)


class JobQueue:
    """Redis-backed FIFO queue managing job lifecycle."""

    queue_key: Final[str] = "cerebro:queue"
    processing_key: Final[str] = "cerebro:processing"
    job_key_prefix: Final[str] = "cerebro:job:"

    def __init__(self, config: AppConfig, redis_client: Redis | None = None):
        self.config = config
        self.redis = redis_client or Redis(
            host=config.redis_host,
            port=config.redis_port,
            db=config.redis_db,
            decode_responses=True,
        )

    def submit_job(
        self,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Enqueue a new job and return its identifier."""
        if not isinstance(messages, list) or not messages:
            raise JobQueueError("`messages` must be a non-empty list.")

        job_id = str(uuid.uuid4())
        timestamp = time.time()
        record = JobRecord(
            job_id=job_id,
            status=JobStatus.QUEUED,
            messages=messages,
            created_at=timestamp,
            updated_at=timestamp,
            metadata=metadata,
        )

        job_key = self._job_key(job_id)
        try:
            with self.redis.pipeline() as pipe:
                pipe.set(job_key, record.to_json(), ex=self.config.job_ttl_seconds)
                pipe.rpush(self.queue_key, job_id)
                pipe.execute()
        except RedisError as exc:
            LOGGER.exception("Failed to submit job %s", job_id)
            raise JobQueueError("Failed to submit job") from exc

        LOGGER.info("Submitted job %s", job_id)
        return job_id

    def get_next_job(self) -> dict[str, Any] | None:
        """Retrieve the next job for processing, blocking up to configured timeout."""
        timeout = max(self.config.redis_block_timeout, 0)
        job_id: str | None = None
        try:
            if timeout == 0:
                job_id = self.redis.lpop(self.queue_key)
            else:
                response = self.redis.blpop(self.queue_key, timeout=timeout)
                if response:
                    _, job_id = response
        except RedisError as exc:
            LOGGER.exception("Failed to fetch next job")
            raise JobQueueError("Failed to fetch next job") from exc

        if not job_id:
            return None

        job = self._load_job(job_id)
        if not job:
            LOGGER.warning("Job %s was missing after dequeue; skipping", job_id)
            return None

        job.status = JobStatus.PROCESSING
        job.started_at = time.time()
        job.updated_at = job.started_at
        self._save_job(job)

        try:
            self.redis.sadd(self.processing_key, job_id)
        except RedisError:
            LOGGER.warning("Failed to add job %s to processing set", job_id, exc_info=True)

        LOGGER.info("Dequeued job %s for processing", job_id)
        return {
            "job_id": job.job_id,
            "messages": job.messages,
            "metadata": job.metadata,
        }

    def complete_job(
        self,
        job_id: str,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> JobRecord:
        """Mark a job as completed or failed with optional result payload."""
        if status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
            raise InvalidJobStatus("Completion requires completed or failed status.")

        job = self._load_job(job_id)
        if not job:
            raise JobNotFound(f"Job {job_id} not found")

        job.status = status
        job.completed_at = time.time()
        job.updated_at = job.completed_at
        job.result = result
        job.error = error
        self._save_job(job)

        try:
            self.redis.srem(self.processing_key, job_id)
        except RedisError:
            LOGGER.warning("Failed to remove job %s from processing set", job_id, exc_info=True)

        LOGGER.info("Marked job %s as %s", job_id, status.value)
        return job

    def get_result(self, job_id: str) -> JobRecord:
        """Retrieve the current state of a job."""
        job = self._load_job(job_id)
        if not job:
            raise JobNotFound(f"Job {job_id} not found")
        return job

    def get_stats(self) -> dict[str, int]:
        """Return current queue metrics."""
        try:
            queued = self.redis.llen(self.queue_key)
            processing = self.redis.scard(self.processing_key)
        except RedisError as exc:
            LOGGER.exception("Failed to retrieve stats")
            raise JobQueueError("Failed to retrieve stats") from exc

        return {"queued": int(queued), "processing": int(processing)}

    def health_check(self) -> bool:
        """Return True when Redis responds to a ping."""
        try:
            return bool(self.redis.ping())
        except RedisError:
            LOGGER.exception("Redis health check failed")
            return False

    def _job_key(self, job_id: str) -> str:
        return f"{self.job_key_prefix}{job_id}"

    def _load_job(self, job_id: str) -> JobRecord | None:
        job_key = self._job_key(job_id)
        try:
            data = self.redis.get(job_key)
        except RedisError as exc:
            LOGGER.exception("Failed to load job %s", job_id)
            raise JobQueueError("Failed to load job") from exc

        if not data:
            return None

        return JobRecord.from_json(data)

    def _save_job(self, job: JobRecord) -> None:
        job_key = self._job_key(job.job_id)
        try:
            self.redis.set(job_key, job.to_json(), ex=self.config.job_ttl_seconds)
        except RedisError as exc:
            LOGGER.exception("Failed to persist job %s", job.job_id)
            raise JobQueueError("Failed to save job") from exc
