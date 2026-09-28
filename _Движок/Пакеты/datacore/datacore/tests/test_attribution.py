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
