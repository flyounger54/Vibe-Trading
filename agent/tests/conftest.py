"""Shared fixtures and sys.path setup for all tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure agent/ is on sys.path so imports like `backtest.*` and `src.*` work.
AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@pytest.fixture(scope="session", autouse=True)
def isolate_unified_state_database(tmp_path_factory: pytest.TempPathFactory):
    """Prevent tests using production defaults from touching user state."""
    key = "VIBE_TRADING_STATE_DB_PATH"
    previous = os.environ.get(key)
    isolated = tmp_path_factory.mktemp("vibe-state") / "vibe.db"
    os.environ[key] = str(isolated)
    try:
        yield isolated
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
