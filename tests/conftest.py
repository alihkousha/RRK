import sys
from pathlib import Path

import pytest

# Imports are top-level (``apex``, ``rrs``, ...), as when running from src/rrk.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "rrk"))


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Record the client's pauses instead of waiting them out."""
    from rrs import client

    recorded = []
    monkeypatch.setattr(client.time, "sleep", recorded.append)
    return recorded
