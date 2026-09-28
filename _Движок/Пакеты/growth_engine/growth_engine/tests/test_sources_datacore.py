"""Адаптер Ядра данных: числа читаются из публикации ядра, контракт не теряется по дороге.

Публикация — файл, а не живая связь с базой: ядро живёт своим окружением (DuckDB, dlt, SQLMesh), и тянуть его
зависимости в движок значило бы склеить две системы в одну.
"""
import json
from datetime import date

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.sources.base import Query, SourceError
from growth_engine.sources.datacore import DatacoreAdapter

AUG = (date(2026, 8, 1), date(2026, 9, 1))
RUN = date(2026, 9, 21)
CORE_AS_OF = "2026-09-15"     # ядро снимает факты своим расписанием, обычно раньше прогона движка


def number(metric, scope, value, *, status="факт", unit="шт", segment="", missing="",
           denominator=None, level="money", as_of=CORE_AS_OF):
    return {"metric": metric, "level": level, "scope": scope, "flow": "all",
            "period_start": "2026-08-01", "period_end": "2026-09-01", "segment": segment,
            "source": f"D:datacore:{metric}", "status": status, "value": value, "unit": unit,
            "denominator": denominator, "denominator_value": None, "missing": missing, "as_of": as_of}


@pytest.fixture
def published(tmp_path):
    """Файл публикации ядра с горсткой чисел."""
    payload = {"instance": "тест", "published_at": "2026-09-21T10:00:00+00:00", "as_of": CORE_AS_OF,
               "contract": "период с исключающей правой границей", "numbers": [
                   number("new_first_sql", "b1", 586.0),
                   number("revenue", "b1", 26420945.0, status="оценка", unit="₽",
                          missing="платежей без сделки на 224 887 ₽"),
                   number("cogs", "b1", 14875214.0, status="оценка", unit="₽",
                          missing="позиций без закупочной цены на 2 957 231 ₽ продаж"),
                   number("gross_profit", "b1", 11545731.0, status="оценка", unit="₽"),
                   dict(number("lead_conversion", "b1", 0.205, status="оценка", unit="доля",
                               denominator="лидов"), denominator_value=1857.0),
                   number("revenue", "b2", None, status="нет данных", unit="₽",
                          missing="загруженных окон источника нет"),
                   number("new_first_sql", "b1", 2893.0, segment="contour=all_deals"),
                   dict(number("ampu_per_lead", "b1", 11109.0, status="оценка", unit="₽",
                               denominator="лидов"), denominator_value=586.0),
               ]}
    path = tmp_path / "published_numbers.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def adapter(published):
    return DatacoreAdapter({"published": str(published), "scopes": ["b1", "b2"],
                            "metrics": {"new_first_sql": "new_first_sql", "revenue": "revenue",
                                        "cogs": "cogs", "gross_profit": "gross_profit",
                                        "c1": "lead_conversion", "ampu": "ampu_per_lead"}})


def ask(adapter, metric, scope="b1", breakdown=None):
    q = Query(metric=metric, scope=scope, flow="all", period_start=AUG[0], period_end=AUG[1],
              breakdown=breakdown, as_of=RUN)
    return adapter.fetch(q)[0]


def test_number_keeps_its_provenance_and_status(adapter):
    """Происхождение и статус не теряются по дороге: иначе движок примет оценку за факт."""
    n = ask(adapter, "revenue")
    assert n.value == 26420945.0 and n.unit == "₽"
    assert n.status is Status.ESTIMATE
    assert n.source == "D:datacore:revenue"
    assert "платежей без сделки" in n.missing


def test_gap_between_snapshots_is_named(adapter):
    """Дата съёма ядра старше прогона движка — обычное дело, но молчать нельзя: иначе числа разных съёмов
    сложатся незаметно (П3)."""
    n = ask(adapter, "new_first_sql")
    assert n.as_of == date(2026, 9, 15)
    assert "дата съёма ядра 15.09.2026" in n.missing and "прогон движка 21.09.2026" in n.missing


def test_no_data_stays_no_data(adapter):
    """«Нет данных» не превращается в ноль: у второго кабинета деньги не загружены."""
    n = ask(adapter, "revenue", scope="b2")
    assert n.value is None and n.status is Status.NO_DATA
    assert "загруженных окон" in n.missing


def test_share_keeps_its_denominator(adapter):
    """Доля обязана иметь знаменатель — он переносится вместе с числом.

    У числа движка нет поля под величину знаменателя, поэтому она сохраняется в пояснении: «23,6 %» ничего не
    говорит, «381 из 1 857 лидов» проверяется умножением (ревью Codex этапа 5, п.4)."""
    n = ask(adapter, "c1")
    assert n.unit == "доля" and n.denominator == "лидов" and n.value == pytest.approx(0.205)
    assert "знаменатель: 381 из 1 857 лидов" in n.missing, n.missing


def test_segment_selects_another_number(adapter):
    """Разрез — часть определения числа: без него 586 «по кабинету» неотличимо от 2 893 «все сделки»."""
    assert ask(adapter, "new_first_sql").value == 586.0
    assert ask(adapter, "new_first_sql", breakdown="contour=all_deals").value == 2893.0


def test_identity_holds_through_the_adapter(adapter):
    """Тождество прибыли сохраняется и после перевода контракта."""
    got = adapter.totals("b1", ["revenue", "cogs", "gross_profit"], *AUG)
    assert got["gross_profit"] == pytest.approx(got["revenue"] - got["cogs"])


def test_period_outside_publication_is_an_error_not_zero(adapter):
    """Числа за период, которого в публикации нет, не выдумываются — источник честно отказывает."""
    q = Query(metric="revenue", scope="b1", flow="all", period_start=date(2020, 1, 1),
              period_end=date(2020, 2, 1), breakdown=None, as_of=RUN)
    with pytest.raises(SourceError, match="нет числа"):
        adapter.fetch(q)


def test_unmapped_metric_is_refused(adapter):
    """Метрика без соответствия в конфигурации не угадывается: имена метрик — часть инстанса (страж 9)."""
    q = Query(metric="выдуманная", scope="b1", flow="all", period_start=AUG[0], period_end=AUG[1],
              breakdown=None, as_of=RUN)
    with pytest.raises(GuardViolation):
        adapter.fetch(q)


def test_missing_publication_says_how_to_make_it(tmp_path):
    """Файла нет — сообщение называет команду, которой ядро его публикует."""
    a = DatacoreAdapter({"published": str(tmp_path / "нет.json"), "metrics": {"revenue": "revenue"}})
    results = a.probe()
    assert not results[0].ok and "datacore.serve.publish" in results[0].detail


def test_probe_reports_publication_state(adapter):
    """Проба говорит, что за публикация и насколько она полна."""
    r = adapter.probe()[0]
    assert r.ok and "чисел 8" in r.detail and "без данных 1" in r.detail


def test_average_denominator_is_not_written_as_a_share(adapter):
    """У среднего знаменатель пишется «на сколько поделено», а не «часть из целого».

    Прибыль на лид, умноженная на число лидов, даёт прибыль, а не долю: запись «6 509 857 из 586 лидов» была бы
    бессмыслицей (найдено при живой проверке адаптера 21.09.2026)."""
    n = ask(adapter, "ampu")
    assert n.unit == "₽" and n.value == 11109.0
    assert "знаменатель: на 586 лидов" in n.missing, n.missing
    assert " из " not in n.missing.split(";")[0], n.missing
