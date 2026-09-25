import os

# Must be set before app.main is imported, since it builds the app at import time.
os.environ.update(
    {
        "GREENLIGHT_API_KEY": "test-key",
        "FRONTEND_ORIGIN": "http://localhost:5173",
        "GITHUB_BOT_TOKEN": "test-token",
        "LEDGER_PATH": ":memory:",
        "TRUEFORGE_BASE_URL": "http://127.0.0.1:1",
    }
)

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
