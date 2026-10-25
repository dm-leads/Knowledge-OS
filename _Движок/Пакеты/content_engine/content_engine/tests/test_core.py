"""Профиль (Т2), реестры (Т3, П5), нормализация (Т5.3)."""
import json

import pytest
import yaml

from content_engine.core import profile as profile_mod
from content_engine.core import store
from content_engine.core.errors import ProfileError, StoreError
from content_engine.core.normalize import Normalizer
from content_engine.core.number import Number

from .conftest import base_profile


# --- профиль ---

def test_template_profile_is_valid():
    profile_mod.validate(yaml.safe_load(profile_mod.template_text()))


def test_valid_profile_reads(project):
    assert profile_mod.load(project)["site"]["domains"] == ["example.ru"]


def test_unknown_key_named():
    data = base_profile()
    data["queue"]["speed"] = 1
    with pytest.raises(ProfileError, match="неизвестное поле «queue.speed»"):
        profile_mod.validate(data)


def test_missing_key_named():
    data = base_profile()
    del data["thresholds"]["key_overlap"]
    with pytest.raises(ProfileError, match="пропущено обязательное поле «thresholds.key_overlap»"):
        profile_mod.validate(data)


def test_secret_value_in_profile_refused():
    data = base_profile()
    data["sources"]["keysso"]["key_env"] = "a1b2c3d4e5f6-secret-value"
    with pytest.raises(ProfileError, match="sources.keysso.key_env.*имя переменной"):
        profile_mod.validate(data)


def test_missing_key_in_list_section_named():
    data = base_profile()
    del data["corpus"]["folders"][0]["pattern"]
    with pytest.raises(ProfileError, match=r"corpus.folders\[0\].pattern"):
        profile_mod.validate(data)


# --- реестры ---

def test_write_reads_back(tmp_path):
    line = store.write_json(tmp_path / "q.json", "queue", [{"a": 1}, {"a": 2}])
    assert line == "q.json: записано 2 — прочитано 2"


def test_tampered_write_fails_readback(tmp_path, monkeypatch):
    real = store._dump

    def lossy(path, text):
        data = json.loads(text)
        data["records"] = data["records"][:-1]
        real(path, json.dumps(data))

    monkeypatch.setattr(store, "_dump", lossy)
    with pytest.raises(StoreError, match="записано 2 — прочитано 1"):
        store.write_json(tmp_path / "q.json", "queue", [{"a": 1}, {"a": 2}])


def test_empty_write_on_nonempty_input_is_error(tmp_path):
    with pytest.raises(StoreError, match="ноль записанных"):
        store.write_json(tmp_path / "q.json", "queue", [], source_count=5)
    with pytest.raises(StoreError, match="ноль записанных"):
        store.write_jsonl(tmp_path / "d.jsonl", [], source_count=5)


def test_foreign_schema_version_is_error(tmp_path):
    (tmp_path / "q.json").write_text(json.dumps({"schema_version": 99, "kind": "queue", "records": []}),
                                     encoding="utf-8")
    with pytest.raises(StoreError, match="версия схемы 99"):
        store.read_json(tmp_path / "q.json", "queue")


def test_journal_appends_only(tmp_path):
    path = tmp_path / "r.jsonl"
    store.append_jsonl(path, [{"x": 1}])
    assert store.append_jsonl(path, [{"x": 2}, {"x": 3}]) == "r.jsonl: дописано 2 — прочитано 2"
    assert [r["x"] for r in store.read_jsonl(path)] == [1, 2, 3]


def test_number_needs_period_source_status():
    n = Number(12, "2026-01-01", "2026-01-31", "консоль:запрос", "факт", "2026-02-01")
    assert Number.from_dict(n.to_dict()) == n
    with pytest.raises(StoreError):
        Number(None, "2026-01-01", "2026-01-31", "консоль:запрос", "факт", "2026-02-01")
    with pytest.raises(StoreError):
        Number(0, "2026-01-01", "2026-01-31", "консоль:запрос", "нет данных", "2026-02-01")


# --- нормализация (Т5.3) ---

N = Normalizer()


def same(a, b):
    return N.key(a).similarity(N.key(b)) == 1.0


@pytest.mark.parametrize("group", [["котёл", "котла", "котлом", "котлы"], ["окно", "окна", "окон"],
                                   ["печь", "печи", "печью"]])
def test_word_forms_match(group):
    for a in group:
        for b in group:
            assert same(a, b), (a, b)


def test_short_words_unchanged_and_distinct():
    words = ["дом", "свет", "газ", "пол"]
    for w in words:
        assert N.key(w).stems() == [w]
    for a in words:
        for b in words:
            if a != b:
                assert N.key(a).similarity(N.key(b)) == 0.0


def test_mel_mol_distinct():
    assert N.key("мел").similarity(N.key("мол")) == 0.0


def test_profile_stop_word_gives_no_match():
    norm = Normalizer(["сад"])
    assert not norm.key("сад")
    assert norm.key("полив сада").similarity(norm.key("сад у дома")) == 0.0


def test_yo_and_case():
    assert N.key("ЁЛКА").stems() == N.key("елка").stems()
