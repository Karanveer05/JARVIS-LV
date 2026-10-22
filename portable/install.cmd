@echo off
rem Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
rem Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
title JARVIS Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
exit /b %errorlevel%
