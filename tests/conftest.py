"""Shared test setup.

The API tests drive the real FastAPI app, which means they hit the real database
unless told otherwise. They were doing exactly that — leaving `Test 17874…`
clients in the shop's own `data.db`, visible in the app's own dropdown. A test
run must never touch the operator's data.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend import config, db


@pytest.fixture(scope="session", autouse=True)
def isolated_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point the whole session at a throwaway SQLite file."""
    original = config.DB_PATH
    config.DB_PATH = tmp_path_factory.mktemp("db") / "test.db"
    # Connections are cached per thread; drop them so the new path takes effect.
    db._local = threading.local()
    db.init()
    try:
        yield
    finally:
        config.DB_PATH = original
        db._local = threading.local()


@pytest.fixture(scope="session", autouse=True)
def isolated_workdir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Keep job inputs and outputs out of the real working directory."""
    original = config.WORK_DIR
    config.WORK_DIR = Path(tmp_path_factory.mktemp("work"))
    try:
        yield
    finally:
        config.WORK_DIR = original


@pytest.fixture(autouse=True)
def empty_spend_ledger() -> Iterator[None]:
    """Start every test inside the AI budget.

    Paid routes refuse once the month's budget is spent (NEXT.md 1.1), and the
    session shares one database — so a few dozen tests recording ~₹11.50 apiece
    took the ledger past ₹2,000 and every later test was refused for being over
    budget rather than for the reason it was testing. Each test now starts from
    zero, which also makes the budget tests themselves deterministic.
    """
    db.connect().execute("DELETE FROM ai_spend")
    db.connect().commit()
    yield
