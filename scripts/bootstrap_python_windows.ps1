param()

$ErrorActionPreference = "Stop"

function Test-Python {
  if (Get-Command python -ErrorAction SilentlyContinue) {
    try {
      & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" *> $null
      if ($LASTEXITCODE -eq 0) { return $true }
    } catch {}
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
      & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" *> $null
      if ($LASTEXITCODE -eq 0) { return $true }
    } catch {}
  }
  return $false
}

if (Test-Python) {
  exit 0
}

if ($env:LOCAL_COMPUTER_AUTO_INSTALL_PYTHON -eq "0") {
  throw "Python 3.12 or newer is required. Install Python and run Locus again."
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  throw "Python 3.12+ is required and winget is not available for automatic installation."
}

Write-Host "[python] Python was not found. Installing Python automatically for Locus."
winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements

if (-not (Test-Python)) {
  $userPython = Join-Path $env:LOCALAPPDATA "Programs\\Python\\Python312"
  $env:Path = "$userPython;$userPython\\Scripts;$env:Path"
}

if (-not (Test-Python)) {
  throw "Python installation finished, but Python is still not available on PATH. Restart PowerShell and run Locus again."
}

Write-Host "[python] Python installed."
