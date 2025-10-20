# Cerebro

Cerebro is a lightweight distributed job queue tailored for LLM workloads. It exposes a simple Flask-powered REST API backed by Redis for FIFO job management, allowing producers to submit chat-style payloads and workers to process them asynchronously.

## Features

- Submit, dequeue, and complete jobs with a minimal JSON API
- FIFO queue semantics backed by Redis
- Job lifecycle tracking (`queued → processing → completed/failed`)
- Health and stats endpoints for monitoring
- Configurable via environment variables or `.env` file
- Docker Compose stack with Flask manager service and Redis
- Windows worker automatically detects installed Ollama models and picks the closest match

## Quick Start

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Authenticate with GHCR (replace TOKEN with a PAT that has `read:packages`):
   ```bash
   echo TOKEN | docker login ghcr.io -u <your-github-username> --password-stdin
   ```
3. Start the stack with Docker Compose:
   ```bash
   docker compose up -d
   ```
4. The API and dashboard are available at `http://localhost:5000/`.

## Docker Compose

The repository includes a ready-to-run Compose file that pulls the published GHCR image and starts Cerebro alongside Redis:

```yaml
services:
  manager:
    image: ghcr.io/alexpitcher/cerebro:${CEREBRO_TAG:-latest}
    restart: unless-stopped
    ports:
      - "${API_PORT:-5000}:5000"
    environment:
      FLASK_ENV: ${FLASK_ENV:-production}
      REDIS_HOST: redis
      REDIS_PORT: 6379
      REDIS_DB: ${REDIS_DB:-0}
      REDIS_JOB_TIMEOUT: ${REDIS_JOB_TIMEOUT:-30}
      REDIS_BLOCK_TIMEOUT: ${REDIS_BLOCK_TIMEOUT:-5}
      JOB_TTL_SECONDS: ${JOB_TTL_SECONDS:-3600}
      MANAGER_DEBUG_LOG: ${MANAGER_DEBUG_LOG:-false}
      JOB_HISTORY_SIZE: ${JOB_HISTORY_SIZE:-50}
    depends_on:
      - redis

  redis:
    image: redis:7.2-alpine
    restart: unless-stopped
    ports:
      - "${REDIS_PORT:-6379}:6379"
    volumes:
      - redis_data:/data

volumes:
  redis_data:
```

Run `docker compose up -d` from the project root to start both services, then visit `http://localhost:5000/` for a lightweight dashboard to submit jobs and inspect recent prompts/results. To build the image from source instead, run `docker build -t ghcr.io/<you>/cerebro:dev .` and update the `image` reference accordingly.

## Project Layout

```
cerebro/
├── manager/              # Manager (coordinator) service
│   ├── __init__.py
│   ├── server.py
│   ├── queue.py
│   └── config.py
├── worker/               # Worker-side helpers
│   ├── __init__.py
│   ├── worker.py
│   ├── example_worker.py
│   ├── setup_venv.sh
│   ├── README.md
│   ├── requirements.txt
│   └── .env.example
├── tests/                # Pytest suite
│   └── test_api.py
├── scripts/
│   └── setup_venv.sh
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

## API Overview

| Method | Endpoint             | Description                               |
|--------|----------------------|-------------------------------------------|
| POST   | `/submit_job`        | Submit a job with OpenAI-style `messages` |
| POST   | `/get_job`           | Blocking pop for workers (returns 204 when idle) |
| POST   | `/complete_job`      | Mark a job as completed or failed         |
| GET    | `/get_result/<id>`   | Poll for job status and results           |
| GET    | `/stats`             | Retrieve queue metrics                    |
| GET    | `/health`            | Health check                              |
| POST   | `/register_worker`   | Register worker presence (logged)         |
| POST   | `/deregister_worker` | Remove worker state                       |
| GET    | `/workers`           | List workers known to the manager         |

Example submission payload:

```json
{
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "Summarize the latest report." }
  ],
  "metadata": {
    "priority": "normal"
  }
}
```

## Development

Set up a virtual environment and run the tests:

```bash
./scripts/setup_venv.sh
source .venv/bin/activate
pytest
```

To run the manager API locally:

```bash
flask --app manager.server:app run --port 5000
```

Launch the CLI worker (optional):

```bash
python worker/worker.py
```

### Windows Tray Worker

- Download `CerebroWorkerInstaller.exe` from the latest GitHub release.
- Run the installer (no admin rights needed). Files are placed under `%LOCALAPPDATA%\Programs\CerebroWorker` and configuration lives in `%LOCALAPPDATA%\CerebroWorker\config.json`.
- Start the tray app, open **Settings**, and pick a model. The dropdown is populated from Ollama; if your desired model is missing the worker falls back automatically (preferring `phi*`, `llama*`, `qwen*`, `gemma*`, `deepseek*`).
- Enable “Run Cerebro Worker at logon” to register a per-user startup entry; disable it in Settings to remove the entry.

## Configuration

All configuration values can be supplied via environment variables or a `.env` file:

| Variable             | Default | Description                          |
|----------------------|---------|--------------------------------------|
| `API_PORT`           | `5000`  | Flask port when running locally      |
| `REDIS_HOST`         | `localhost` | Redis host name                 |
| `REDIS_PORT`         | `6379`  | Redis port                           |
| `REDIS_DB`           | `0`     | Redis database index                 |
| `REDIS_JOB_TIMEOUT`  | `30`    | Worker job processing timeout (seconds) |
| `REDIS_BLOCK_TIMEOUT`| `5`     | Blocking timeout for worker dequeue (seconds) |
| `JOB_TTL_SECONDS`    | `3600`  | TTL for job metadata in Redis        |
| `JOB_HISTORY_SIZE`   | `50`    | Number of recent jobs stored for the dashboard |
| `MANAGER_DEBUG_LOG`  | `false` | When `true`, log full prompts/results and every worker poll |
| `MODEL_NAME`         | `phi4-mini` | Desired Ollama model (worker falls back to closest installed match) |

If the requested Ollama model is unavailable, the worker queries `/api/tags` and switches to the best available option (preferring `phi*`, then `llama*`, `qwen*`, `gemma*`, `deepseek*`). The dashboard lists each job with `Worker` and `Model` tags so you can see which agent handled it.

## License

MIT License.
