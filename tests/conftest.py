import pytest

from app.core.settings import get_settings


@pytest.fixture(autouse=True)
def _disable_rate_limiting_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
