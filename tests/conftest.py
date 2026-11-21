import pytest

from cardledger_database import http


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """No test waits for a retry: the waits are recorded instead."""
    waits: list[float] = []
    monkeypatch.setattr(http, "sleep", waits.append, raising=False)
    return waits
