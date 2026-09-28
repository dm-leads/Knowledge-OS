"""Конверсия визита в New First SQL при ПОЛНЫХ визитах.

Главная ловушка контура (записана в спецификации): фильтр по полю сделки в отчёте поставщика отсекает визиты
почти до нуля, и конверсия в доли процента превращается в почти единицу. Поэтому знаменатель конверсии не зависит от набора
отбора: визиты фильтруются только от роботов. Проверено на фактах 16.09.2026."""
from datetime import date, datetime, timezone

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import conversion
from datacore.tests.test_contour_metrics import AUG, CFG, NOW, deal, marker, setup_deals


def visits(engine, group, n, system="tracker_b1", day=3):
    """Визиты группы канала вместе с окном покрытия: без окна метрика откажется считать (этап 3)."""
    from datacore.tests.helpers import WINDOW_COLUMNS
    p = Provenance(system, "C", f"L-vis-{system}-{group}", NOW)   # свой номер загрузки на кабинет
    register_load(engine, p, NOW.date())
    # id визитов двух кабинетов пересекаются в жизни (проба этапа 3), поэтому ключ факта — (visit_id, system).
    # В тесте разводим их по системе, иначе вставка падает на первичном ключе.
    rows = [dict(visit_id=abs(hash((system, group, i))) % 10**9,
                 started_at=datetime(2026, 8, day, tzinfo=timezone.utc),
                 marker=f"{group}_x", marker_level_1=group) for i in range(n)]
    write_facts(engine, "visit", rows, p, mode="upsert")
    marks = ", ".join("?" for _ in WINDOW_COLUMNS)
    engine.execute(f"INSERT INTO meta.window_log ({', '.join(WINDOW_COLUMNS)}) VALUES ({marks})",
                   (system, abs(hash((system, group))) % 10**6, abs(hash((system, group))) % 10**6 + 1, n,
                    datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
                    p.load_id, NOW))
    engine.commit()
    finish_load(engine, p.load_id, rows_read=n, rows_written=n, status="ok")


def test_conversion_is_deals_over_full_visits(engine):
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    visits(engine, "seo", 200)
    n = conversion(engine, CFG, "b1", *AUG, segment="channel_group=seo")
    assert n.unit == "доля" and n.denominator == "visits"
    assert abs(n.value - 1 / 200) < 1e-9


def test_filtered_contour_does_not_shrink_visits(engine):
    """Отбор сделок не трогает знаменатель: иначе конверсия взлетела бы в сто раз, как в витрине поставщика."""
    setup_deals(engine, [deal(1), deal(2, tech="Лиды партнёра")], [marker(1, "seo"), marker(2, "seo")])
    visits(engine, "seo", 200)
    plain = conversion(engine, CFG, "b1", *AUG, segment="channel_group=seo")
    assert abs(plain.value - 2 / 200) < 1e-9
    # с отбором числитель меньше, знаменатель тот же
    filtered = conversion(engine, CFG, "b1", *AUG, segment="channel_group=seo", contour="narrow_tech")
    assert abs(filtered.value - 1 / 200) < 1e-9


def test_visits_of_another_cabinet_are_not_counted(engine):
    """Визиты чужого кабинета в знаменатель не идут: это разные сайты (К7 запрещает и складывать их)."""
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    visits(engine, "seo", 100)
    visits(engine, "seo", 900, system="tracker_b2")
    n = conversion(engine, CFG, "b1", *AUG, segment="channel_group=seo")
    assert abs(n.value - 1 / 100) < 1e-9


def test_group_without_visits_is_no_data(engine):
    """Группы канала нет в визитах — конверсия не «ноль», а «нет данных»: делить не на что."""
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    visits(engine, "seo", 10)
    n = conversion(engine, CFG, "b1", *AUG, segment="channel_group=direct6")
    assert n.status is Status.NO_DATA and n.value is None


def test_segment_is_required(engine):
    """Конверсия «вообще» не считается: без группы канала это число ни о чём."""
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    visits(engine, "seo", 10)
    with pytest.raises(RuleViolation) as e:
        conversion(engine, CFG, "b1", *AUG)
    assert e.value.code == "К2"
