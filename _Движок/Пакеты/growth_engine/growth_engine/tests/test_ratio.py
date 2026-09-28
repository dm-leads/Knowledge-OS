import pytest

from growth_engine.core.arithmetic import ratio, render
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.tests.helpers import AS_OF, CFG, LATER, MAY, n


# Известный ответ: CR1 веб-потока, май 2026 = 1 578 / 101 146 = 1,56% (снимок cr1_web_vs_offline, 11.09).
def test_web_cr1_same_nature():
    r = ratio(n("leads", 1578, flow="web", period=MAY), n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    assert round(r.value * 100, 2) == 1.56
    assert r.status is Status.FACT and r.denominator == "visits=101146"


# Смешанный CR1 мая: все заявки 4 682 (из них 3 104 без визита) на визиты веб-потока — стоп.
def test_mixed_cr1_rejected():
    with pytest.raises(GuardViolation) as e:
        ratio(n("leads", 4682, flow="all", period=MAY), n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    assert e.value.guard == 4


# Визиты веб-аналитики и заявки сквозной аналитики считаются разными моделями — не делим одно на другое.
def test_different_source_systems_rejected():
    with pytest.raises(GuardViolation) as e:
        ratio(n("leads", 872, flow="web"), n("visits", 35988, flow="web", source="A:web-analytics-y:data"),
              "cr1", CFG)
    assert e.value.guard == 4


def test_different_source_classes_rejected():
    with pytest.raises(GuardViolation) as e:
        ratio(n("leads", 872, flow="web"), n("visits", 35988, flow="web", source="A:analytics-x:data"), "cr1", CFG)
    assert e.value.guard == 4


def test_level_must_match_config():
    from dataclasses import replace
    with pytest.raises(GuardViolation) as e:
        ratio(replace(n("leads", 1578, flow="web", period=MAY), level="visit"),
              n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    assert e.value.guard == 4


def test_inverted_funnel_rejected():
    with pytest.raises(GuardViolation, match="выше знаменателя"):
        ratio(n("visits", 101146, flow="web", period=MAY), n("leads", 1578, flow="web", period=MAY), "cr1", CFG)


def test_different_segments_rejected():
    from dataclasses import replace
    with pytest.raises(GuardViolation) as e:
        ratio(replace(n("leads", 689, flow="web", period=MAY), segment="marker_level_1=seo"),
              n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    assert e.value.guard == 4


# Доля сегмента в итоге той же метрики — структура, а не смешанная метрика.
def test_share_of_segment_in_its_own_total_allowed():
    from dataclasses import replace
    part = replace(n("leads", 1981, flow="web", period=MAY), segment="marker_level_1=seo")
    share = ratio(part, n("leads", 3414, flow="web", period=MAY), "seo_share", CFG)
    assert round(share.value * 100, 1) == 58.0 and share.segment == "marker_level_1=seo"


def test_segment_of_one_metric_in_total_of_another_rejected():
    from dataclasses import replace
    with pytest.raises(GuardViolation) as e:
        ratio(replace(n("leads", 1981, flow="web", period=MAY), segment="marker_level_1=seo"),
              n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    assert e.value.guard == 4


def test_render_shows_cabinet_and_segment():
    from dataclasses import replace
    text = render(replace(n("leads", 1981, flow="web", period=MAY), segment="marker_level_1=seo"))
    assert "кабинет p1" in text and "разрез marker_level_1=seo" in text


def test_zero_denominator_gives_no_data():
    r = ratio(n("leads", 5, flow="web"), n("visits", 0, flow="web"), "cr1", CFG)
    assert r.status is Status.NO_DATA and r.value is None


def test_render_has_all_standard_fields():
    text = render(ratio(n("leads", 1578, flow="web", period=MAY),
                        n("visits", 101146, flow="web", period=MAY), "cr1", CFG))
    for piece in ("cr1 = 1,56%", "период 01.05.2026–31.05.2026", "знаменатель visits=101146",
                  "источник C:analytics-x:data", "статус факт"):
        assert piece in text


def test_render_no_data_says_what_is_missing():
    text = render(ratio(n("leads", 5, flow="web"), n("visits", 0, flow="web"), "cr1", CFG))
    assert "cr1 = нет данных" in text and "не хватает:" in text


# П3: доля из разных съёмов — смешанная метрика.
def test_share_of_different_fetch_dates_rejected():
    with pytest.raises(GuardViolation) as e:
        ratio(n("leads", 1578, flow="web", period=MAY), n("visits", 101146, flow="web", period=MAY, as_of=LATER),
              "cr1", CFG)
    assert e.value.guard == 4 and "съём" in str(e.value)


def test_share_carries_and_shows_fetch_date():
    r = ratio(n("leads", 1578, flow="web", period=MAY), n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    assert r.as_of == AS_OF and "снято 03.09.2026" in render(r)


# П3 (ревью Codex 14.09): доля из разных запросов одной системы — смешанная метрика.
def test_share_of_different_queries_rejected():
    with pytest.raises(GuardViolation) as e:
        ratio(n("leads", 1578, flow="web", period=MAY),
              n("visits", 101146, flow="web", period=MAY, source="C:analytics-x:other-query"), "cr1", CFG)
    assert e.value.guard == 4


# Оговорки входов не теряются в доле: дозревание оплат видно в C1.
def test_share_keeps_notes_of_inputs():
    from dataclasses import replace
    share = ratio(replace(n("sales", 5), missing="окно дозревает до 28.02.2027 (Т6)"), n("sales_entry", 10), "c1", CFG)
    assert "дозревает" in share.missing
