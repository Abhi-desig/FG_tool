@echo off
REM Focus Toolkit - AI self-check (Windows).
REM Run this when the AI features fail. It says whether the fault is this
REM computer or the API key. Costs nothing.
cd /d "%~dp0"
uv run python -m backend.diagnose
echo.
pause
