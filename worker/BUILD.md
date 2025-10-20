# Cerebro Worker GUI Packaging Guide

## Prerequisites

- Windows 10/11
- Python 3.11+
- Visual C++ Build Tools (recommended)
- Install build dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r gui_requirements.txt
pip install pyinstaller
```

## Building the Executable

1. Open **Developer PowerShell** in the `worker` directory.
2. Activate the virtual environment if not already active.
3. Run the build script:

```powershell
build.bat
```

The script cleans previous artifacts, invokes PyInstaller using `build.spec`, and stages the output in `dist/`.

## Output

- `dist/CerebroWorker.exe` – single-file executable
- `dist/CerebroWorker/` – unpacked directory with resources and config (if using one-folder mode)
- `dist/config.json` – default GUI configuration
- `.env.example` – copied for convenience

Estimated executable size: ~120–150 MB (PyQt6 runtime bundled).

## Testing the Build

1. Run the executable:

```powershell
dist\CerebroWorker.exe
```

2. Confirm the system tray icon appears and you can submit a job via the dashboard (`http://localhost:5000/`).
3. Verify log output in `worker.log` and GUI log viewer.

## Distribution Notes

- Distribute `CerebroWorker.exe` together with `config.json` and `.env.example` if users need editable defaults.
- GPU checks require `nvidia-smi` present on the target machine.
- When packaging for environments without PyQt6 installed, ensure `gui_requirements.txt` dependencies are pre-installed.

## Optional: Create a Windows Service

You can run the GUI worker via NSSM to keep it running in the background:

```powershell
nssm install CerebroWorker "C:\path\to\CerebroWorker.exe"
nssm set CerebroWorker AppDirectory "C:\path\to"
nssm start CerebroWorker
```

Configure service recovery options (restart on failure) as needed.

## Known Limitations

- Gaming mode overlay is purely visual; ensure the worker is paused before expecting GPU to be free.
- Notifications rely on Windows toast support.
- For large log files, the log viewer trims to the last 500 lines.
