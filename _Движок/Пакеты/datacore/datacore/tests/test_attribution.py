"""«Источник известен» и «без следа» — доли, а не штуки, и считаются от одного знаменателя.

Определение проверено на фактах 16.09.2026: «без следа» — это ПУСТОЙ канал входа сделки, а не пустой
маркер визита: по маркеру пустых больше чем вдвое, и это другое число."""
from datacore.schema.number import Status
from datacore.serve.metrics import no_trace, source_known
from datacore.tests.test_contour_metrics import AUG, CFG, deal, marker, setup_deals


def test_no_trace_is_empty_entry_channel(engine):
    setup_deals(engine,
                [deal(1, summary="маркер трекера"), deal(2, summary=None), deal(3, summary="звонок")],
                [marker(1, "seo"), marker(2, "unknown"), marker(3, "nosource-crm")])
    n = no_trace(engine, CFG, "b1", *AUG)
    assert n.unit == "доля" and n.denominator                   # доля без знаменателя — не факт (К2)
    assert abs(n.value - 1 / 3) < 1e-9


def test_source_known_counts_nosource_bucket_as_known(engine):
    """Бакет nosource — это «канал известен, визита нет»: сделка пришла из известного канала входа.
    Иначе доля известного упала бы примерно в полтора раза (проверено на фактах)."""
    setup_deals(engine,
                [deal(1, summary="маркер трекера"), deal(2, summary="звонок"), deal(3, summary=None)],
                [marker(1, "seo"), marker(2, "nosource-crm"), marker(3, "unknown")])
    assert abs(source_known(engine, CFG, "b1", *AUG).value - 2 / 3) < 1e-9


def test_shares_sum_to_one(engine):
    """Доли считаются от одного знаменателя и вместе дают единицу — иначе это разные метрики под одним именем."""
    setup_deals(engine, [deal(1, summary="звонок"), deal(2, summary=None)],
                [marker(1, "seo"), marker(2, "unknown")])
    known = source_known(engine, CFG, "b1", *AUG)
    empty = no_trace(engine, CFG, "b1", *AUG)
    assert abs(known.value + empty.value - 1.0) < 1e-9
    assert known.denominator == empty.denominator


def test_share_follows_the_contour(engine):
    """Доля считается внутри того же набора отбора, что и знаменатель: смешивать контуры нельзя."""
    setup_deals(engine,
                [deal(1, summary="звонок"), deal(2, summary=None), deal(3, summary=None, tech="Лиды партнёра")],
                [marker(1, "seo"), marker(2, "seo"), marker(3, "seo")])
    full = no_trace(engine, CFG, "b1", *AUG)
    assert abs(full.value - 2 / 3) < 1e-9                       # без следа 2 из 3
    pik = no_trace(engine, CFG, "b1", *AUG, contour="narrow_tech")
    assert abs(pik.value - 1 / 2) < 1e-9                        # сделка с «Лиды партнёра» вышла из отбора
    assert pik.segment == "contour=narrow_tech"


def test_empty_window_is_no_data_not_zero(engine):
    """В окне нет сделок — доля не «ноль», а «нет данных»: делить не на что."""
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    from datetime import date
    n = no_trace(engine, CFG, "b1", date(2026, 5, 1), date(2026, 6, 1))
    assert n.status is Status.NO_DATA and n.value is None


def test_no_trace_reads_the_channel_counted_by_the_core(engine):
    """С 1.4.0 «без следа» — по сводному каналу, посчитанному ядром, а не по полю CRM: поле заполнял внешний
    скрипт, и месяц без его прогона давал «без следа» у всех сделок (первый инстанс, октябрь 2026)."""
    setup_deals(engine,
                [deal(1, summary=None, derived="звонок"), deal(2, summary="звонок", derived=None)],
                [marker(1, "seo"), marker(2, "seo")])
    n = no_trace(engine, CFG, "b1", *AUG)
    assert abs(n.value - 1 / 2) < 1e-9          # пуст только посчитанный ядром канал сделки 2


def test_contour_can_check_the_counted_channel(engine):
    """Правило отбора проверяет посчитанный ядром канал так же, как поле CRM."""
    from datacore.serve.contour import contour_clause
    import copy
    cfg = copy.copy(CFG)
    contours = copy.deepcopy(CFG.contours)
    contours["by_derived"] = {"base": {"new_first_only": True},
                              "exclude": [{"field": "entry_channel_derived", "exclude": ["партнёр"]}]}
    object.__setattr__(cfg, "contours", contours)
    clause, params = contour_clause(cfg, "by_derived")
    assert "d.entry_channel_derived" in clause and params == ["партнёр"]
    from datacore.serve.metrics import new_first_sql
    setup_deals(engine, [deal(1, summary=None, derived="партнёр"), deal(2, summary="партнёр", derived=None)],
                [marker(1, "seo"), marker(2, "seo")])
    assert new_first_sql(engine, cfg, "b1", *AUG, contour="by_derived").value == 1


def test_share_works_on_contour_that_returns_by_referrer(engine):
    """Набор с возвратом по площадке перехода (keep_referrer) смотрит в визиты: доля обязана их присоединить,
    иначе падает ошибкой SQL (22.10.2026, digital-контур первого инстанса)."""
    setup_deals(engine, [deal(1, summary=None), deal(2, summary="звонок")], [marker(1, "seo"), marker(2, "seo")])
    n = no_trace(engine, CFG, "b1", *AUG, contour="with_content")
    assert abs(n.value - 1 / 2) < 1e-9
    assert n.segment == "contour=with_content"
