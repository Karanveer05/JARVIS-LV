# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
# JARVIS installer - runs on the NEW PC after JARVIS-Setup.exe unpacks itself.
# Per-user install, no administrator rights. Re-running it updates the program files and
# leaves everything the app saved on that PC (API key, chats, memory) untouched.
param(
    [string]$TargetDir = (Join-Path $env:LOCALAPPDATA "JARVIS"),
    [switch]$SkipDeps,        # testing: unpack only
    [switch]$NoShortcuts,
    [switch]$NoLaunch,
    [switch]$Quiet            # testing: no message boxes
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
$log = Join-Path $env:TEMP "jarvis-install.log"
try { Start-Transcript -Path $log -Force | Out-Null } catch {}

function Say($t)  { Write-Host $t }
function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Box($text, $title = "JARVIS Setup", $buttons = "OK", $icon = "Information") {
    if ($Quiet) { return "Yes" }
    return [System.Windows.Forms.MessageBox]::Show($text, $title, $buttons, $icon).ToString()
}

function Test-Py($exe) {
    try {
        $v = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
        $p = ("$v").Trim().Split('.')
        return ([int]$p[0] -eq 3 -and [int]$p[1] -ge 11 -and [int]$p[1] -le 13)
    } catch { return $false }
}
function Find-Python {
    foreach ($ver in "3.12", "3.13", "3.11") {
        try {
            $exe = (& py "-$ver" -c "import sys;print(sys.executable)" 2>$null)
            if ($exe -and (Test-Path "$exe") -and (Test-Py "$exe")) { return "$exe" }
        } catch {}
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps" -and (Test-Py $cmd.Source)) { return $cmd.Source }
    foreach ($d in "Python312", "Python313", "Python311") {
        $c = Join-Path $env:LOCALAPPDATA "Programs\Python\$d\python.exe"
        if ((Test-Path $c) -and (Test-Py $c)) { return $c }
    }
    return $null
}

try {
    if ((Box "Install JARVIS-LV by Karanveer Singh on this PC?`n`nInstall folder:`n$TargetDir`n`nThis installer carries no personal data: no API key, chats or memory. You will be asked for your own free Gemini API key the first time JARVIS starts.`n`nIt needs an internet connection to download its Python libraries." "JARVIS Setup" "YesNo" "Question") -ne "Yes") { exit 0 }

    Step "1/5  Copying program files"
    $zip = Join-Path $PSScriptRoot "payload.zip"
    if (-not (Test-Path $zip)) { throw "payload.zip is missing next to the installer." }
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $TargetDir -Force
    Say "   Installed to $TargetDir"
    if ($SkipDeps) { Say "   (SkipDeps: stopping after unpack)"; exit 0 }

    Step "2/5  Looking for Python 3.11 - 3.13"
    $py = Find-Python
    if (-not $py) {
        Say "   Not found. Installing Python 3.12 for this user with winget..."
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Start-Process "https://www.python.org/downloads/windows/"
            throw "Python 3.11-3.13 is not installed and winget is not available. Install Python 3.12 from python.org (tick 'Add python.exe to PATH'), then run this installer again."
        }
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        $py = Find-Python
        if (-not $py) { throw "Python was installed but could not be found yet. Close this window and run the installer once more." }
    }
    Say "   Using $py"

    Step "3/5  Creating a private Python environment"
    $venv = Join-Path $TargetDir ".venv"
    $vpy  = Join-Path $venv "Scripts\python.exe"
    $vpyw = Join-Path $venv "Scripts\pythonw.exe"
    if (-not (Test-Path $vpy)) { & $py -m venv $venv; if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." } }

    Step "4/5  Installing libraries (several minutes, needs internet)"
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    Push-Location $TargetDir
    try {
        & $vpy -m pip install --upgrade pip --quiet
        & $vpy setup.py
        if ($LASTEXITCODE -ne 0) { throw "Installing the libraries failed. See $log" }
    } finally { Pop-Location }

    Step "5/5  Creating shortcuts"
    if (-not $NoShortcuts) {
        $sh   = New-Object -ComObject WScript.Shell
        $icon = Join-Path $TargetDir "config\jarvis.ico"
        $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "JARVIS"
        New-Item -ItemType Directory -Force -Path $menu | Out-Null
        foreach ($dir in @([Environment]::GetFolderPath("Desktop"), $menu)) {
            $lnk = $sh.CreateShortcut((Join-Path $dir "JARVIS.lnk"))
            $lnk.TargetPath = $vpyw; $lnk.Arguments = '"' + (Join-Path $TargetDir "main.py") + '"'
            $lnk.WorkingDirectory = $TargetDir; $lnk.IconLocation = $icon; $lnk.Description = "JARVIS-LV voice assistant by Karanveer Singh"
            $lnk.Save()
        }
        $un = $sh.CreateShortcut((Join-Path $menu "Uninstall JARVIS.lnk"))
        $un.TargetPath = "powershell.exe"
        $un.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $TargetDir "uninstall.ps1") + '"'
        $un.WorkingDirectory = $TargetDir; $un.IconLocation = $icon; $un.Save()
        Say "   Desktop and Start-menu shortcuts created."
    }

    # Per-user entry in Settings > Apps, so Windows shows the name and publisher. HKCU needs no admin.
    try {
        $reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\JARVIS-LV"
        New-Item -Path $reg -Force | Out-Null
        $vals = @{
            DisplayName     = "JARVIS-LV"
            Publisher       = "Karanveer Singh"
            DisplayIcon     = (Join-Path $TargetDir "config\jarvis.ico")
            InstallLocation = $TargetDir
            URLInfoAbout    = "https://github.com/Karanveer05/JARVIS-LV"
            UninstallString = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $TargetDir "uninstall.ps1") + '"'
        }
        foreach ($k in $vals.Keys) { Set-ItemProperty -Path $reg -Name $k -Value $vals[$k] }
        Set-ItemProperty -Path $reg -Name NoModify -Value 1 -Type DWord
        Set-ItemProperty -Path $reg -Name NoRepair -Value 1 -Type DWord
    } catch { Say "   (Could not add the Settings > Apps entry: $($_.Exception.Message))" }

    Say ""; Say "JARVIS is installed."
    if ((Box "JARVIS is installed.`n`nStart it now? It will ask for your Gemini API key (free from aistudio.google.com/app/apikey)." "JARVIS Setup" "YesNo" "Information") -eq "Yes" -and -not $NoLaunch -and -not $Quiet) {
        Start-Process -FilePath $vpyw -ArgumentList ('"' + (Join-Path $TargetDir "main.py") + '"') -WorkingDirectory $TargetDir
    }
}
catch {
    Write-Host ""; Write-Host "INSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Box "JARVIS could not be installed.`n`n$($_.Exception.Message)`n`nLog: $log" "JARVIS Setup" "OK" "Error" | Out-Null
    exit 1
}
finally { try { Stop-Transcript | Out-Null } catch {} }
