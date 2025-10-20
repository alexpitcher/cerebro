Param(
    [string]$InstallDir = "C:\Program Files\CerebroWorker",
    [string]$ServiceName = "CerebroWorkerService"
)

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "This script must be run as Administrator."
        exit 1
    }
}

function Copy-Files {
    param(
        [string]$SourceDir,
        [string]$DestinationDir
    )
    if (-not (Test-Path $DestinationDir)) {
        New-Item -ItemType Directory -Path $DestinationDir | Out-Null
    }
    Copy-Item -Path "$SourceDir\CerebroWorkerService.exe" -Destination $DestinationDir -Force
    Copy-Item -Path "$SourceDir\CerebroWorker.exe" -Destination $DestinationDir -Force
    if (Test-Path "$SourceDir\config.json") { Copy-Item "$SourceDir\config.json" $DestinationDir -Force }
    if (Test-Path "$SourceDir\.env.example") { Copy-Item "$SourceDir\.env.example" $DestinationDir -Force }
}

function Install-Service {
    param(
        [string]$ExecutablePath,
        [string]$ServiceName
    )

    & $ExecutablePath remove 2>$null | Out-Null
    & $ExecutablePath --startup auto install
    & $ExecutablePath start
}

Assert-Admin

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Files -SourceDir $scriptDir -DestinationDir $InstallDir

$serviceExe = Join-Path $InstallDir "CerebroWorkerService.exe"
Install-Service -ExecutablePath $serviceExe -ServiceName $ServiceName

Write-Host "Service '$ServiceName' installed and started."
Write-Host "Logs: C:\ProgramData\CerebroWorker\worker.log"
