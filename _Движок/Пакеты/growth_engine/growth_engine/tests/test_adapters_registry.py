"""Список подключаемых систем: адаптер хранилища фактов собирается по имени секции и секретов не берёт."""
import json

from growth_engine import adapters
from growth_engine.sources.datacore import DatacoreAdapter


def test_datacore_is_registered_and_builds_without_secrets(tmp_path):
    published = tmp_path / "публикация.json"
    published.write_text(json.dumps({"numbers": []}), encoding="utf-8")
    raw = {"sources": {"datacore": {"class": "D", "published": str(published), "metrics": {"m": "m"}}}}
    secrets = tmp_path / ".env"
    secrets.write_text("", encoding="utf-8")
    adapter = adapters.build("datacore", raw, cfg=None, secrets_path=secrets)
    assert isinstance(adapter, DatacoreAdapter)
