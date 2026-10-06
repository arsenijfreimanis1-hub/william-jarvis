"""Shared test isolation: keep the free-provider gateway away from live keys and the live usage ledger."""

from __future__ import annotations

import os

import pytest

from jarvis.services.providers import catalog, usage, vectors

_PROVIDER_ENV = sorted({env for spec in catalog.PROVIDERS.values() for env in spec.env_keys})


@pytest.fixture(autouse=True)
def _isolate_provider_gateway(monkeypatch, tmp_path_factory):
    """Every test gets a throwaway ledger DB and no provider keys unless it sets them explicitly."""
    ledger = tmp_path_factory.mktemp("ledger") / "providers.db"
    monkeypatch.setattr(usage, "DB_PATH", ledger)
    monkeypatch.setattr(vectors, "DB_PATH", ledger)
    monkeypatch.setattr("jarvis.config.settings.keychain_lookup_enabled", False)
    monkeypatch.setattr("jarvis.config.settings.keys_file", tmp_path_factory.mktemp("keys") / "keys.env")
    for env in _PROVIDER_ENV:
        monkeypatch.delenv(env, raising=False)
    for spec in catalog.PROVIDERS.values():
        if spec.settings_attr:
            monkeypatch.setattr(f"jarvis.config.settings.{spec.settings_attr}", "")
    yield
    # save_key() exports into os.environ; monkeypatch cannot restore keys that did not exist before.
    for env in _PROVIDER_ENV:
        os.environ.pop(env, None)
