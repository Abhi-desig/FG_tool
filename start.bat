@echo off
REM Focus Toolkit - Windows launcher.
REM Make it a desktop app: right-click this file, Send to, Desktop (create shortcut).
cd /d "%~dp0"
uv run python -m backend.main
if errorlevel 1 pause
