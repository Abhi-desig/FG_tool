#!/bin/bash
# Focus Toolkit — macOS launcher.
# Make it a desktop app: chmod +x start.command, then drag it to the Dock.
cd "$(dirname "$0")" || exit 1
exec uv run python -m backend.main
