"""Pytest fixtures shared across tests."""

import os
import sys
from pathlib import Path

# Ensure src is on path even without `pip install -e .`
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Provide a benign env so config.Settings() can build during tests.
os.environ.setdefault("PMBOT_MODE", "paper")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("LEARNING_DB_PATH", ":memory:")
