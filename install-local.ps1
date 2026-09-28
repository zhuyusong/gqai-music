$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Candidates = @(
        @{ Command = "py"; Args = @("-3.12") },
        @{ Command = "py"; Args = @("-3.14") },
        @{ Command = "python"; Args = @() }
    )
    $Selected = $null
    foreach ($Candidate in $Candidates) {
        try {
            & $Candidate.Command @($Candidate.Args) -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $Selected = $Candidate; break }
        } catch { }
    }
    if ($null -eq $Selected) {
        throw "GQAI Music requires Python 3.11 or newer."
    }
    & $Selected.Command @($Selected.Args) -m venv --system-site-packages (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the GQAI Music virtual environment." }
}
& $Python -c "import fastapi, httpx, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install "fastapi>=0.115,<1" "httpx>=0.28,<1" "uvicorn>=0.32,<1"
    if ($LASTEXITCODE -ne 0) { throw "Unable to install GQAI Music runtime dependencies." }
}
$SitePackages = (& $Python -c "import site; print(site.getsitepackages()[0])").Trim()
if (-not $SitePackages) { throw "Unable to locate the GQAI Music Python environment." }
$SourcePath = Join-Path $ProjectRoot "src"
[System.IO.File]::WriteAllText(
    (Join-Path $SitePackages "gqai-music-local.pth"),
    $SourcePath + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)
& $Python -c "import gqai_music, fastapi, httpx, uvicorn"
if ($LASTEXITCODE -ne 0) { throw "GQAI Music installation verification failed." }
Write-Host "GQAI Music installation complete."

