[CmdletBinding()]
param(
    [string]$ProjectDirectory = (Join-Path $PSScriptRoot '..')
)

$ErrorActionPreference = 'Stop'

try {
    $ProjectDirectory = (Resolve-Path -LiteralPath $ProjectDirectory).Path
    $pidFile = Join-Path $ProjectDirectory '.runserver.pid'
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
        Write-Host 'No server started by scripts\start_app.ps1 is running.'
        exit 0
    }

    $state = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $serverProcessId = [int]$state.ProcessId
    $server = Get-Process -Id $serverProcessId -ErrorAction SilentlyContinue
    if ($null -eq $server -or
        $server.StartTime.ToUniversalTime().Ticks -ne [long]$state.StartTimeUtcTicks) {
        Remove-Item -LiteralPath $pidFile -Force
        Write-Host 'The recorded server process is no longer running.'
        exit 0
    }

    $processes = Get-CimInstance -ClassName Win32_Process
    $descendantIds = New-Object 'System.Collections.Generic.List[int]'
    $seenIds = New-Object 'System.Collections.Generic.HashSet[int]'
    [void]$seenIds.Add($serverProcessId)

    function Add-DescendantProcessIds {
        param([int]$ParentProcessId)

        foreach ($child in $processes | Where-Object { $_.ParentProcessId -eq $ParentProcessId }) {
            $childId = [int]$child.ProcessId
            if ($seenIds.Add($childId)) {
                [void]$descendantIds.Add($childId)
                Add-DescendantProcessIds -ParentProcessId $childId
            }
        }
    }

    Add-DescendantProcessIds -ParentProcessId $serverProcessId
    foreach ($processId in $descendantIds) {
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            Stop-Process -Id $processId -Force
        }
    }
    Stop-Process -Id $serverProcessId -Force
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "Stopped the development server (PID $serverProcessId)."
}
catch {
    Write-Error $_
    exit 1
}
