# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
# Removes JARVIS and everything it saved on this PC (API key, chats, memory, notes, vault).
Add-Type -AssemblyName System.Windows.Forms
$dir = $PSScriptRoot
$r = [System.Windows.Forms.MessageBox]::Show(
    "Remove JARVIS from this PC?`n`nThis also deletes everything it saved here: your API key, chats, memory and notes.`n`n$dir",
    "Uninstall JARVIS-LV", "YesNo", "Warning")
if ($r -ne "Yes") { exit 0 }
foreach ($p in @((Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVIS.lnk"),
                 (Join-Path ([Environment]::GetFolderPath("Programs")) "JARVIS"))) {
    Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\JARVIS-LV" -Recurse -Force -ErrorAction SilentlyContinue
# The folder holds this very script, so delete it from a detached process a moment after we exit.
Start-Process cmd.exe -WindowStyle Hidden -ArgumentList "/c ping -n 3 127.0.0.1 >nul & rmdir /s /q `"$dir`""
