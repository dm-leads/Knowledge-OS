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
    assert n.source == "D:datacore:publish"
    assert "запрос ядра: D:datacore:revenue" in n.missing
    assert "платежей без сделки" in n.missing


def test_one_publication_is_one_source(adapter):
    """Выручка и себестоимость одной публикации — один источник: иначе тождество PnL видит смешанную величину
    (страж 4), хотя ядро определило их согласованно (1.7.0)."""
    assert ask(adapter, "revenue").source == ask(adapter, "cogs").source == "D:datacore:publish"


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


def test_level_comes_from_engine_config(published):
    """Уровень числа — из конфигурации движка, как у остальных адаптеров: у ядра выручка «payment», у движка
    «money»; без этого страж 4 останавливает средний чек на совпадающей величине (1.7.0)."""
    from types import SimpleNamespace
    cfg = SimpleNamespace(metrics={"revenue": None},
                          rule=lambda metric: SimpleNamespace(level="payment"))
    a = DatacoreAdapter({"published": str(published), "metrics": {"revenue": "revenue"}}, cfg)
    n = ask(a, "revenue")
    assert n.level == "payment" and "уровень ядра «money»" in n.missing


def test_breakdown_by_dimension_returns_its_segments_or_nothing(adapter):
    """Разрез по измерению: строки «измерение=значение» или пусто — движок пишет «в ответе нет», а не падает (1.7.0).
    Разрез со значением, которого нет, — по-прежнему отказ источника."""
    assert [n.segment for n in ask_all(adapter, "new_first_sql", breakdown="contour")] == ["contour=all_deals"]
    assert ask_all(adapter, "revenue", breakdown="marker_level_1") == []
    with pytest.raises(SourceError):
        ask_all(adapter, "revenue", breakdown="contour=digital")


def ask_all(adapter, metric, scope="b1", breakdown=None):
    q = Query(metric=metric, scope=scope, flow="all", period_start=AUG[0], period_end=AUG[1],
              breakdown=breakdown, as_of=RUN)
    return adapter.fetch(q)


def test_window_of_months_is_sum_of_published_months_by_engine_rules(tmp_path):
    """Окно в два месяца, которого нет в публикации, — сумма месяцев арифметикой движка; без разрешения сложения
    по периоду в конфигурации — страж 3 (1.7.0)."""
    from types import SimpleNamespace
    rows = [dict(number("new_first_sql", "b1", 586.0)),
            dict(number("new_first_sql", "b1", 542.0), period_start="2026-09-01", period_end="2026-10-01")]
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"numbers": rows}, ensure_ascii=False), encoding="utf-8")

    def cfg(additive):
        rule = SimpleNamespace(level="money", summable=True, period_additive=additive, cross_scope_additive=False)
        return SimpleNamespace(metrics={"new_first_sql": rule}, rule=lambda metric: rule)

    q = Query(metric="new_first_sql", scope="b1", flow="all", period_start=date(2026, 8, 1),
              period_end=date(2026, 10, 1), breakdown=None, as_of=RUN)
    got = DatacoreAdapter({"published": str(path), "metrics": {"new_first_sql": "new_first_sql"}}, cfg(True)).fetch(q)[0]
    assert got.value == 1128.0 and got.period_start == date(2026, 8, 1) and got.period_end == date(2026, 10, 1)
    with pytest.raises(GuardViolation):
        DatacoreAdapter({"published": str(path), "metrics": {"new_first_sql": "new_first_sql"}}, cfg(False)).fetch(q)


def test_sum_of_months_keeps_publication_source_and_missing_window_is_refused(tmp_path):
    """Ревью 17.11.2026: сумма месяцев — тот же источник публикации, что и число окна из файла (иначе тождество
    PnL падает на смеси «publish / сумма»); разрез за окно, которого в файле нет совсем, — отказ, а не пустой список."""
    from types import SimpleNamespace
    rows = [dict(number("cogs", "b1", 10.0)),
            dict(number("cogs", "b1", 20.0), period_start="2026-09-01", period_end="2026-10-01"),
            dict(number("revenue", "b1", 50.0), period_start="2026-08-01", period_end="2026-10-01")]
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"numbers": rows}, ensure_ascii=False), encoding="utf-8")
    rule = SimpleNamespace(level="money", summable=True, period_additive=True, cross_scope_additive=False)
    cfg = SimpleNamespace(metrics={"cogs": rule, "revenue": rule}, rule=lambda metric: rule)
    a = DatacoreAdapter({"published": str(path), "metrics": {"cogs": "cogs", "revenue": "revenue"}}, cfg)

    def q(metric, breakdown=None, end=date(2026, 10, 1)):
        return Query(metric=metric, scope="b1", flow="all", period_start=date(2026, 8, 1), period_end=end,
                     breakdown=breakdown, as_of=RUN)

    cogs, revenue = a.fetch(q("cogs"))[0], a.fetch(q("revenue"))[0]
    assert cogs.value == 30.0 and cogs.source == revenue.source == "D:datacore:publish"
    assert a.fetch(q("revenue", "marker_level_1")) == []
    with pytest.raises(SourceError):
        a.fetch(q("revenue", "marker_level_1", end=date(2026, 12, 1)))


def test_flow_is_part_of_the_address(adapter):
    """Число всех потоков не отдаётся под меткой веб-потока (1.7.1)."""
    q = Query(metric="new_first_sql", scope="b1", flow="web", period_start=AUG[0], period_end=AUG[1],
              breakdown=None, as_of=RUN)
    with pytest.raises(SourceError, match="поток «all»"):
        adapter.fetch(q)
