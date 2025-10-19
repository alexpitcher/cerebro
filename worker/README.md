# Cerebro Worker (Windows Setup)

This worker polls the Cerebro manager for queued LLM jobs and executes them locally via the Ollama API.

## 1. Prerequisites

- Windows 10/11 with Python 3.11+
- NVIDIA GPU drivers (if applicable) and `nvidia-smi` on PATH
- Ollama running locally (`http://localhost:11434`)
- Access to the Cerebro manager API

## 2. Installation

Open **PowerShell** in the `worker` directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> **Note (macOS/Linux):** Run `./setup_venv.sh` to create and populate a virtual environment.

Copy and edit the environment file:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `CEREBRO_URL`, `MODEL_NAME`, and any other overrides required for your environment.

## 3. Running Manually

```powershell
python worker.py
```

Press `Ctrl+C` to stop the worker gracefully.

## 4. Optional: Install as a Windows Service (NSSM)

1. Download NSSM from <https://nssm.cc/download> and extract it (e.g., `C:\nssm\nssm.exe`).
2. With the virtual environment activated, locate the full paths:
   - Python executable: `$(Resolve-Path .venv\Scripts\python.exe)`
   - Worker script: `$(Resolve-Path worker.py)`
3. Install the service (replace placeholders with resolved paths):

```powershell
C:\nssm\nssm.exe install CerebroWorker "C:\path\to\.venv\Scripts\python.exe" "C:\path\to\worker.py"
```

4. In the NSSM GUI:
   - Set the **Startup directory** to the `worker` folder.
   - On the **Environment** tab, add key/value pairs for required variables (e.g., `CEREBRO_URL=http://manager:5000`).
   - Save the service.

5. Start the service:

```powershell
Start-Service CerebroWorker
```

Use `Get-Service CerebroWorker` to check status, and `Stop-Service CerebroWorker` to stop it.

## 5. Logs

Logs are printed to stdout with structured fields: timestamp, worker ID, job ID, and status. When running as a service, redirect output using the NSSM **I/O** tab or an external log aggregator.

## 6. Updating

Stop the worker, pull changes, reinstall dependencies if needed, and restart the process or service.
