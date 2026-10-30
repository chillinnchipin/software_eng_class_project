$ErrorActionPreference = 'Stop'

try {
    $ProjectDirectory = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    Set-Location -LiteralPath $ProjectDirectory

    $testsDirectory = Join-Path $ProjectDirectory 'tests'
    if (-not (Test-Path -LiteralPath $testsDirectory -PathType Container)) {
        throw "Required test directory not found: $testsDirectory"
    }

    foreach ($requiredFile in @('tests\.coveragerc', 'tests\settings.py')) {
        $filePath = Join-Path $ProjectDirectory $requiredFile
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "Required test file not found: $filePath"
        }
    }

    $python = Join-Path $ProjectDirectory '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Virtualenv Python not found: $python. Run scripts\install_dependencies.ps1 first."
    }

    Write-Host 'Running tests with coverage...'
    & $python -m coverage run --rcfile=tests/.coveragerc manage.py test tests --settings=tests.settings
    $testStatus = $LASTEXITCODE

    Write-Host 'Generating coverage report...'
    & $python -m coverage report --rcfile=tests/.coveragerc
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    exit $testStatus
}
catch {
    Write-Error $_
    exit 1
}
