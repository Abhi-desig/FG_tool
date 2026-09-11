#!/usr/bin/env python3
"""Build the shop PC's zip. The name people will look for.

    uv run python scripts/build_shop_zip.py

**Why this is four lines and not a script.** `package_windows.py` already built
this zip, refuses everything that must never ship, and is covered by tests. A
second implementation under a friendlier name would be two things to keep in
step, and the one that drifts is always the one somebody runs by accident. So
this is the name, and that is the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from package_windows import main  # noqa: E402  (after the path insert, by necessity)

if __name__ == "__main__":
    sys.exit(main())
