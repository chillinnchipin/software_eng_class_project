[CmdletBinding()]
param(
    [string]$ProjectDirectory = (Join-Path $PSScriptRoot '..\..')
)

$ErrorActionPreference = 'Stop'

try {
    $ProjectDirectory = (Resolve-Path -LiteralPath $ProjectDirectory).Path
    $manageFile = Join-Path $ProjectDirectory 'manage.py'
    if (-not (Test-Path -LiteralPath $manageFile -PathType Leaf)) {
        throw "manage.py not found in application directory: $ProjectDirectory"
    }

    $python = Join-Path $ProjectDirectory '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw 'Python was not found. Create .venv or add Python to PATH.'
        }
        $python = $pythonCommand.Source
    }

    $pidFile = Join-Path $ProjectDirectory '.runserver.pid'
    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        $existingState = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        $existingProcess = Get-Process -Id ([int]$existingState.ProcessId) -ErrorAction SilentlyContinue
        if ($null -ne $existingProcess -and
            $existingProcess.StartTime.ToUniversalTime().Ticks -eq [long]$existingState.StartTimeUtcTicks) {
            throw "A server started by this script is already running (PID $($existingState.ProcessId)). Run scripts\powershell\stop_app.ps1 first."
        }
        Remove-Item -LiteralPath $pidFile -Force
    }

    $oldSettings = $env:DJANGO_SETTINGS_MODULE
    $oldDatabasePath = $env:SQLITE_DB_PATH
    $env:DJANGO_SETTINGS_MODULE = 'config.settings.settings'
    if ([string]::IsNullOrWhiteSpace($env:SQLITE_DB_PATH)) {
        $env:SQLITE_DB_PATH = Join-Path $ProjectDirectory 'db.sqlite3'
    }
    try {
        Write-Host 'Applying database migrations...'
        & $python $manageFile migrate --noinput
        if ($LASTEXITCODE -ne 0) {
            throw "Database migration failed (exit code $LASTEXITCODE)."
        }

        $stdoutLog = Join-Path $ProjectDirectory '.runserver.stdout.log'
        $stderrLog = Join-Path $ProjectDirectory '.runserver.stderr.log'
        $server = Start-Process -FilePath $python `
            -ArgumentList @('manage.py', 'runserver') `
            -WorkingDirectory $ProjectDirectory `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -WindowStyle Hidden `
            -PassThru

        Start-Sleep -Seconds 1
        $server.Refresh()
        if ($server.HasExited) {
            throw "The development server exited during startup. Check $stderrLog for details."
        }

        $state = @{
            ProcessId = $server.Id
            StartTimeUtcTicks = $server.StartTime.ToUniversalTime().Ticks
        }
        $state | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding ASCII
        Write-Host "Development server started (PID $($server.Id))."
        Write-Host 'Open http://127.0.0.1:8000 in your browser.'
        Write-Host "Logs: $stdoutLog and $stderrLog"
    }
    finally {
        if ($null -eq $oldSettings) {
            Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue
        }
        else {
            $env:DJANGO_SETTINGS_MODULE = $oldSettings
        }

        if ($null -eq $oldDatabasePath) {
            Remove-Item Env:SQLITE_DB_PATH -ErrorAction SilentlyContinue
        }
        else {
            $env:SQLITE_DB_PATH = $oldDatabasePath
        }
    }
}
catch {
    Write-Error $_
    exit 1
}
