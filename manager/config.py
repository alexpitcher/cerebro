"""Configuration utilities for the Cerebro manager service."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class AppConfig:
    """Application configuration derived from environment variables."""

    redis_host: str
    redis_port: int
    redis_db: int
    redis_job_timeout: int
    redis_block_timeout: int
    job_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment variables (with defaults)."""
        load_dotenv()

        return cls(
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            redis_job_timeout=int(os.getenv("REDIS_JOB_TIMEOUT", "30")),
            redis_block_timeout=int(os.getenv("REDIS_BLOCK_TIMEOUT", "5")),
            job_ttl_seconds=int(os.getenv("JOB_TTL_SECONDS", "3600")),
        )


def configure_logging(config: AppConfig, level: int | str | None = None) -> None:
    """Configure application-wide logging."""
    logging.basicConfig(
        level=level or os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
