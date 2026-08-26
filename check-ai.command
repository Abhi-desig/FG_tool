#!/bin/bash
# Focus Toolkit — AI self-check (macOS).
# Run this when the AI features fail. It says whether the fault is this
# computer or the API key. Costs nothing.
cd "$(dirname "$0")" || exit 1
uv run python -m backend.diagnose
echo
read -r -n 1 -p "Press any key to close."
