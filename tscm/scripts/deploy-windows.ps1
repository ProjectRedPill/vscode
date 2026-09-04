<#
.SYNOPSIS
    Deploy sweep into a self-contained folder on Windows.

.DESCRIPTION
    Creates <InstallDir>\src (the clone), <InstallDir>\.venv (an isolated
    Python environment) and <InstallDir>\sweep.cmd (a launcher). Re-running it
    updates an existing install rather than starting over.

    A dedicated virtual environment is deliberate: installing into a conda base
    environment works, but then sweep breaks whenever that environment is
    rebuilt or a different one is activated. This folder owns everything it
    needs.

.PARAMETER InstallDir
    Where to put it. Defaults to $HOME\.cursor\sweep.

.PARAMETER Branch
    Branch to deploy. Defaults to main; use the feature branch until it merges.

.EXAMPLE
    .\deploy-windows.ps1 -InstallDir "C:\Users\Rick\.cursor\sweep"

.NOTES
    Written on Linux and NOT executed on a real Windows machine. The manual
    steps in WINDOWS.md do the same thing one command at a time and are the
    safer path if this misbehaves. Requires Windows PowerShell 5.1 or later.
#>
param(
    [string]$InstallDir = (Join-Path $HOME ".cursor\sweep"),
    [string]$Branch = "main",
    [string]$RepoUrl = "https://github.com/ProjectRedPill/vscode.git"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Deploying sweep to $InstallDir" -ForegroundColor Cyan
Write-Host ""

# --- prerequisites -------------------------------------------------------
foreach ($tool in @("git", "python")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is not on PATH. Install it, then re-run this script."
    }
}

$pyVersion = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
Write-Host "Python $pyVersion found" -ForegroundColor DarkGray
if ([version]$pyVersion -lt [version]"3.10") {
    throw "Python 3.10 or newer is required (found $pyVersion)."
}

# --- clone or update -----------------------------------------------------
$src = Join-Path $InstallDir "src"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if (Test-Path (Join-Path $src ".git")) {
    Write-Host "Updating existing clone..." -ForegroundColor DarkGray
    git -C $src fetch --depth 1 origin $Branch
    git -C $src checkout $Branch
    git -C $src reset --hard "origin/$Branch"
} else {
    Write-Host "Cloning (shallow -- the full VS Code history is enormous)..." -ForegroundColor DarkGray
    git clone --depth 1 --branch $Branch $RepoUrl $src
}

$pkg = Join-Path $src "tscm"
if (-not (Test-Path (Join-Path $pkg "pyproject.toml"))) {
    throw "No pyproject.toml at $pkg -- is '$Branch' the right branch?"
}

# --- isolated environment ------------------------------------------------
$venv = Join-Path $InstallDir ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating virtual environment..." -ForegroundColor DarkGray
    python -m venv $venv
}

$venvPython = Join-Path $venv "Scripts\python.exe"

# Upgrading pip is a nicety, not a requirement, and it reaches out to the
# network -- a slow mirror can time it out. Never let that abort the deploy.
Write-Host "Upgrading pip (optional)..." -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  pip upgrade failed; continuing with the bundled pip." -ForegroundColor Yellow
}

Write-Host "Installing sweep and dependencies..." -ForegroundColor DarkGray
& $venvPython -m pip install -e "$pkg[all]" --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Install failed. Re-run, or try without extras: $venvPython -m pip install -e `"$pkg`""
}

# --- launcher ------------------------------------------------------------
# %~dp0 is the directory of the .cmd file itself, so the launcher keeps
# working if the whole folder is moved.
$launcher = Join-Path $InstallDir "sweep.cmd"
@'
@echo off
"%~dp0.venv\Scripts\sweep.exe" %*
'@ | Set-Content -Path $launcher -Encoding ASCII

# --- verify --------------------------------------------------------------
Write-Host ""
Write-Host "Verifying..." -ForegroundColor DarkGray
& $venvPython -m sweep --help | Select-Object -First 1

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "  Run it with:" -ForegroundColor White
Write-Host "    $launcher doctor"
Write-Host "    $launcher hwscan"
Write-Host "    $launcher serve --open"
Write-Host ""
Write-Host "  Or add it to PATH for this session:" -ForegroundColor White
Write-Host "    `$env:Path += `";$InstallDir`""
Write-Host "    sweep doctor"
Write-Host ""
