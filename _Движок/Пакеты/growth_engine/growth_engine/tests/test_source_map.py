from pathlib import Path

import pytest
import yaml

from growth_engine.core.artifacts import SourceMapEntry
from growth_engine.core.errors import GuardViolation
from growth_engine.core.source_map import class_status, load_source_map, parse_source_map

FIXTURE = Path(__file__).parent / "fixtures" / "source_map_minimal.yaml"
RAW = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_loads_as_entries():
    entries = load_source_map(FIXTURE)
    assert len(entries) == 3 and all(isinstance(e, SourceMapEntry) for e in entries)
    assert entries[0].traps == ("визиты дописываются задним числом",)
    assert entries[2].secret_env_names == ()


def test_duplicate_names_rejected():
    with pytest.raises(GuardViolation, match="дважды"):
        parse_source_map({"sources": RAW["sources"] + [RAW["sources"][0]]})


def test_connected_without_probe_rejected():
    with pytest.raises(GuardViolation) as e:
        parse_source_map({"sources": [{**RAW["sources"][0], "probe": ""}]})
    assert e.value.guard == 13


def test_class_status_prefers_connected():
    assert class_status(load_source_map(FIXTURE)) == {"C": "подключён", "A": "есть, нет доступа", "F": "подключён"}


def test_class_without_sources_is_absent_not_connected():
    assert "B" not in class_status(load_source_map(FIXTURE))
