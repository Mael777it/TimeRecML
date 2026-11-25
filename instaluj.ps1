<#
.SYNOPSIS
  Instaluje "TimeRecML": skrot w menu Start (i opcjonalnie na pulpicie / w autostarcie).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\instaluj.ps1
  powershell -ExecutionPolicy Bypass -File .\instaluj.ps1 -Pulpit -Autostart
  powershell -ExecutionPolicy Bypass -File .\instaluj.ps1 -Odinstaluj
#>
param(
    [switch]$Pulpit,      # dodatkowo skrot na pulpicie
    [switch]$Autostart,   # uruchamiaj po zalogowaniu do Windows
    [switch]$Odinstaluj   # usun wszystkie skroty (dane zostaja)
)
$ErrorActionPreference = 'Stop'

$nazwa   = 'TimeRecML'
$skrypt  = Join-Path $PSScriptRoot 'TimeRecML.pyw'
$miejsca = @{
    Start     = Join-Path ([Environment]::GetFolderPath('Programs')) "$nazwa.lnk"
    Pulpit    = Join-Path ([Environment]::GetFolderPath('Desktop'))  "$nazwa.lnk"
    Autostart = Join-Path ([Environment]::GetFolderPath('Startup'))  "$nazwa.lnk"
}

if ($Odinstaluj) {
    foreach ($p in $miejsca.Values) {
        if (Test-Path $p) { Remove-Item $p -Confirm:$false; Write-Host "Usunieto: $p" }
    }
    Write-Host "Gotowe. Dane (dane.json) zostaly w: $PSScriptRoot"
    return
}

if (-not (Test-Path $skrypt)) { throw "Nie znaleziono $skrypt - uruchom instalator z folderu aplikacji." }

# --- znajdz pythonw.exe (Python 3.8+ z tkinter)
$pythonw = $null
foreach ($kandydat in @('pythonw.exe', 'pyw.exe')) {
    $c = Get-Command $kandydat -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*WindowsApps*' } | Select-Object -First 1
    if ($c) { $pythonw = $c.Source; break }
}
if (-not $pythonw) {
    Write-Host "Nie znaleziono Pythona." -ForegroundColor Red
    Write-Host "Zainstaluj Python 3 (np.: winget install Python.Python.3.12) i uruchom instalator ponownie."
    exit 1
}
$python = $pythonw -replace 'pythonw\.exe$', 'python.exe' -replace 'pyw\.exe$', 'py.exe'
& $python -c "import sys, tkinter; assert sys.version_info >= (3, 8)" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Python musi byc w wersji 3.8+ i miec modul tkinter (zaznacz 'tcl/tk' przy instalacji)." }

function New-Skrot($sciezka) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($sciezka)
    $lnk.TargetPath       = $pythonw
    $lnk.Arguments        = "`"$skrypt`""
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation     = (Join-Path $PSScriptRoot 'TimeRecML.ico')
    $lnk.Description      = 'Rejestr czynnosci i czasu pracy'
    $lnk.Save()
    Write-Host "Utworzono: $sciezka"
}

New-Skrot $miejsca.Start
if ($Pulpit)    { New-Skrot $miejsca.Pulpit }
if ($Autostart) { New-Skrot $miejsca.Autostart }

Write-Host ""
Write-Host "Zainstalowano. Uruchom '$nazwa' z menu Start." -ForegroundColor Green
Write-Host "Dane zapisuja sie w: $(Join-Path $PSScriptRoot 'dane.json')"
