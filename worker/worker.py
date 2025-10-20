"""Command-line entrypoint for the Cerebro worker."""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

from worker.worker_engine import WorkerCallbacks, WorkerConfig, WorkerCore, configure_logging


def install_signal_handlers(worker: WorkerCore) -> None:
    """Attach signal handlers for graceful shutdown."""

    def _handler(signum: int, _frame: Any) -> None:
        logging.getLogger("cerebro.worker").info(
            "Signal %s received; shutting down.",
            signum,
            extra={"status": "shutdown"},
        )
        worker.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except ValueError:
            continue


def main() -> None:
    config = WorkerConfig.from_env()
    configure_logging(config.worker_id)

    callbacks = WorkerCallbacks(
        on_status=lambda status: logging.getLogger("cerebro.worker").debug("Status changed: %s", status),
        on_error=lambda message: logging.getLogger("cerebro.worker").warning("Worker warning: %s", message),
    )
    worker = WorkerCore(config, callbacks=callbacks)
    install_signal_handlers(worker)
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
