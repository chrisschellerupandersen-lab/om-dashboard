# run_sp.ps1 – Wrapper til run_sp.py
# Kan bruges direkte eller via Windows Opgaveplanlægger.
#
# Sæt op i Opgaveplanlægger:
#   Program:   powershell.exe
#   Argumenter: -ExecutionPolicy Bypass -File "C:\sti\til\run_sp.ps1"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pythonScript = Join-Path $scriptDir "run_sp.py"

# Find Python – søger i kendte installationsstier (virker også i Opgaveplanlægger)
$pyExe = $null

# Kendte installationsstier
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\Program Files\Python313\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python311\python.exe",
    "C:\Program Files\Python310\python.exe"
)

# Prøv først via PATH (men spring WindowsApps-stub over)
foreach ($candidate in @("python", "python3")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notlike "*WindowsApps*") {
        $pyExe = $found.Source; break
    }
}

# Ellers søg i kendte stier
if (-not $pyExe) {
    foreach ($path in $candidates) {
        if (Test-Path $path) { $pyExe = $path; break }
    }
}

if (-not $pyExe) {
    Write-Host ""
    Write-Host "FEJL: Python blev ikke fundet paa denne maskine." -ForegroundColor Red
    Write-Host "Installer Python fra: https://www.python.org/downloads/"
    Write-Host "Husk at saette hak i 'Add Python to PATH' under installationen."
    exit 1
}

Write-Host "Bruger Python: $pyExe"

# Installer pymssql hvis det mangler
& $pyExe -c "import pymssql" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installerer pymssql..."
    & $pyExe -m pip install pymssql --quiet
}

# Kør Python-scriptet
Write-Host "Starter $pythonScript ..."
& $pyExe $pythonScript

if ($LASTEXITCODE -eq 0) {
    Write-Host "Stored procedure koerte OK."
} else {
    Write-Host "Stored procedure FEJLEDE. Tjek log.txt for detaljer."
    exit 1
}
