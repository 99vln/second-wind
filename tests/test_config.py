import pytest

from app.config import (
    DEFAULT_APP_TIMEZONE,
    DEFAULT_DATABASE_URL,
    get_settings,
    normalize_database_url,
)

NAMES = ("DATABASE_URL", "APP_TIMEZONE", "GITHUB_WEBHOOK_SECRET", "GITHUB_USERNAME")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_plain_postgres_urls_use_psycopg(monkeypatch, scheme):
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://u:p@host/db")
    assert get_settings().database_url == "postgresql+psycopg://u:p@host/db"


@pytest.mark.parametrize("url", ["sqlite:///x.db", "postgresql+psycopg://u:p@host/db", "sqlite://"])
def test_other_urls_unchanged(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    assert get_settings().database_url == url
    assert normalize_database_url(url) == url


@pytest.mark.parametrize("value", [None, "", "   "])
def test_defaults_when_unset_or_blank(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("DATABASE_URL", value)
        monkeypatch.setenv("APP_TIMEZONE", value)
    settings = get_settings()
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert str(settings.app_timezone) == DEFAULT_APP_TIMEZONE


def test_invalid_timezone_raises(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValueError, match="APP_TIMEZONE"):
        get_settings()


def test_repr_hides_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:hunter2pw@host/db")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "whsec-topsecret")
    text = repr(get_settings())
    assert "hunter2pw" not in text
    assert "whsec-topsecret" not in text
