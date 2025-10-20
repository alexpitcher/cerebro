@echo off
setlocal

pushd %~dp0

echo Checking PyInstaller...
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Install with: pip install pyinstaller
    exit /b 1
)

if exist build (
    echo Cleaning build directory...
    rmdir /s /q build
)
if exist dist (
    rmdir /s /q dist
)

echo Running PyInstaller...
pyinstaller build.spec
if errorlevel 1 (
    echo PyInstaller build failed.
    exit /b 1
)

if exist dist\CerebroWorker (
    copy /y config.json dist\CerebroWorker\config.json >nul
    if exist ..\.env.example copy /y ..\.env.example dist\CerebroWorker\.env.example >nul
)

echo Build complete. Executable located in dist\CerebroWorker.exe or dist\CerebroWorker\.
popd
endlocal
