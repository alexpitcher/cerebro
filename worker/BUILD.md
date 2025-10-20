# Cerebro Worker Packaging Guide

## Build Prerequisites

- Windows 10/11
- Python 3.11+
- Visual C++ Build Tools (recommended)
- Inno Setup 6 (`choco install innosetup -y`)

Install Python dependencies once:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r ..\requirements.txt
pip install -r gui_requirements.txt
pip install pyinstaller
```

## Build Steps

```powershell
# From the repo root (or cd worker and adjust paths)
pyinstaller worker/build.spec

# Stage templates for the installer
copy worker\config.json dist\config.json
copy .env.example dist\.env.example
copy worker\BUILD.md dist\BUILD.md

# Build installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" worker\installer.iss
```

Outputs:

- `dist\CerebroWorker.exe` – single-file GUI worker.
- `dist\CerebroWorkerInstaller.exe` – installer that copies files to `%LOCALAPPDATA%\Programs\CerebroWorker` and optionally creates a Run-at-logon entry.

## Post-build Smoke Test

```powershell
dist\CerebroWorkerInstaller.exe
```

Install for the current user, launch the tray app, submit a test job, and confirm:

- The worker selects an installed Ollama model (Settings → Model list).
- Logs appear under `%LOCALAPPDATA%\CerebroWorker\worker.log`.
- Jobs show worker/model tags on the dashboard (`http://localhost:5000/`).

## Notes

- Configuration lives in `%LOCALAPPDATA%\CerebroWorker\config.json`. The GUI saves changes automatically on exit.
- The installer ships `.env.example` as a template; `.env` variables override config defaults.
- GPU checks require `nvidia-smi` on the path (ignored when unavailable).
- Linux/macOS builds can still use `python worker/gui_worker.py`, but the Windows installer is the supported distribution.
