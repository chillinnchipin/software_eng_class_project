[CmdletBinding()]
param(
    [string]$ProjectDirectory = (Join-Path $PSScriptRoot '..')
)

$ErrorActionPreference = 'Stop'

try {
    $ProjectDirectory = (Resolve-Path -LiteralPath $ProjectDirectory).Path
    $requirementsFile = Join-Path $ProjectDirectory 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirementsFile -PathType Leaf)) {
        throw "requirements.txt not found in project directory: $ProjectDirectory"
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw 'Python was not found on PATH. Install Python and ensure it is available as "python".'
    }

    $venvDirectory = Join-Path $ProjectDirectory '.venv'
    Write-Host "Creating Python virtual environment at $venvDirectory"
    & $pythonCommand.Source -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment (exit code $LASTEXITCODE)."
    }

    $venvPython = Join-Path $venvDirectory 'Scripts\python.exe'
    Write-Host "Installing project dependencies from $requirementsFile"
    & $venvPython -m pip install -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed (exit code $LASTEXITCODE)."
    }
}
catch {
    Write-Error $_
    exit 1
}
