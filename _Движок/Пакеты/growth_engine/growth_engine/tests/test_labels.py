"""Русские заголовки листов книги (24.09.2026): человек читает «Гипотеза», движок работает с `formulation`.

Просьба владельца: латинские шапки читаются как чужая история рядом с русскими листами. Движок находит колонки по
именам, поэтому подписи — словарь «имя → подпись», а перевод в обе стороны делает обёртка бэкенда. Лист со старой
латинской шапкой читается как раньше — ничего не ломается и не переписывается само.
"""
import pytest

from growth_engine.core.storage import COLUMNS, SHEETS, SYSTEM_COLUMNS, RegistryStore
from growth_engine.storage.labels import LABELS, NAMES, LabelledBackend
from growth_engine.storage.memory import MemoryBackend
from growth_engine.tests.helpers import candidate


def test_every_engine_column_has_a_cyrillic_label():
    engine = {column for kind in SHEETS for column in COLUMNS[kind]} | set(SYSTEM_COLUMNS)
    assert sorted(engine - set(LABELS)) == []
    assert all(any("а" <= char.lower() <= "я" or char == "№" for char in label) for label in LABELS.values())


@pytest.mark.parametrize("kind", list(SHEETS))
def test_labels_are_unique_within_a_sheet(kind):
    labels = [LABELS[column] for column in list(COLUMNS[kind]) + list(SYSTEM_COLUMNS)]
    assert len(labels) == len(set(labels))


def test_labels_translate_back():
    assert all(NAMES[label] == name for name, label in LABELS.items())


def test_new_sheet_gets_cyrillic_header_and_the_engine_still_reads_it():
    inner = MemoryBackend()
    store = RegistryStore(LabelledBackend(inner), allowed_domains=())
    hypothesis = candidate()
    store.create(hypothesis)
    raw = inner.header(SHEETS["hypotheses"])
    assert raw[:3] == ["№", "Гипотеза", "Статус"] and "formulation" not in raw
    assert store.read("hypotheses", id=hypothesis.id) == [hypothesis]


def test_sheet_with_the_old_latin_header_is_read_and_written_as_before():
    """Книга, собранная до подписей, не ломается: латинская шапка — тоже шапка движка."""
    inner = MemoryBackend()
    RegistryStore(inner, allowed_domains=()).create(candidate())      # как было: латиница
    store = RegistryStore(LabelledBackend(inner), allowed_domains=())
    assert store.read("hypotheses", id=candidate().id) == [candidate()]
    assert "formulation" in inner.header(SHEETS["hypotheses"]) and "Гипотеза" not in inner.header(SHEETS["hypotheses"])


def test_people_column_between_engine_columns_keeps_its_name():
    inner = MemoryBackend()
    backend = LabelledBackend(inner)
    backend.create_sheet("Карта знаний", ["id", "statement"])
    inner.add_columns("Карта знаний", ["комментарий Дмитрия"])
    backend.add_columns("Карта знаний", ["verdict"])
    assert inner.header("Карта знаний") == ["№", "Утверждение", "комментарий Дмитрия", "Вердикт"]
    assert backend.header("Карта знаний") == ["id", "statement", "комментарий Дмитрия", "verdict"]
