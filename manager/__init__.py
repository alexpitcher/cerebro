"""Application factory for the Cerebro manager service."""

from flask import Flask

from .config import AppConfig, configure_logging
from .queue import JobQueue
from .server import create_api_blueprint, register_error_handlers


def create_app(config: AppConfig | None = None) -> Flask:
    """Create the Flask application with configured job queue."""
    config = config or AppConfig.from_env()
    configure_logging(config)

    app = Flask(__name__)
    app.config["APP_CONFIG"] = config

    job_queue = JobQueue(config=config)
    app.config["JOB_QUEUE"] = job_queue

    app.register_blueprint(create_api_blueprint(job_queue))
    register_error_handlers(app)

    return app
