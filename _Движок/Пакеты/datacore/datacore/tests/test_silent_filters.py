"""Ни один отбор не молчит: число без списка того, чего в нём нет, — правдоподобная ложь.

Просьба владельца 17.09.2026: систему будут спрашивать не только про маркетинг, поэтому важно видеть, что именно
ядро отсекло само. Живой масштаб: в августе 2026 из 2 893 сделок метрика показывала 630, и среди скрытых
279 с оплатами на 23,5 млн ₽ — об этом не было сказано ни слова."""
from datetime import date, datetime, timezone

from datacore.schema.config import load_config
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import new_first_sql
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
AUG = (date(2026, 8, 1), date(2026, 9, 1))
NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def deal(deal_id, *, new_first=True, deleted_at=None):
    return dict(deal_id=deal_id, contact_id=1, brand="b1", pipeline="100", status="142",
                created_at=datetime(2026, 8, 5, tzinfo=timezone.utc), is_new_first=new_first,
                deleted_at=deleted_at)


def setup_deals(engine, rows, *, exclusions=()):
    """Сделки CRM в фактах вместе с их контактом и списком исключений."""
    migrate(engine)
    p = Provenance("crm_mirror", "D", "L-crm", NOW)
    register_load(engine, p, NOW.date())
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1",
                                         created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))], p, mode="upsert")
    write_facts(engine, "deal", list(rows), p, mode="upsert")
    if exclusions:
        write_facts(engine, "exclusion", [dict(entity="deal", entity_key=str(k), reason="технический тест",
                                               since=date(2026, 1, 1), decided_by="тест")
                                          for k in exclusions], p, mode="upsert")
    finish_load(engine, p.load_id, rows_read=len(rows), rows_written=len(rows), status="ok")


def test_deleted_deals_are_named(engine):
    """Удалённая в CRM сделка в число не входит — и это сказано вслух."""
    setup_deals(engine, [deal(1), deal(2, deleted_at=datetime(2026, 8, 7, tzinfo=timezone.utc))])
    n = new_first_sql(engine, CFG, "b1", *AUG)
    assert n.value == 1
    assert "удалённых в CRM" in n.missing and "1" in n.missing


def test_excluded_test_deals_are_named(engine):
    """Технический тест — не сделка, но его количество называется."""
    setup_deals(engine, [deal(1), deal(2)], exclusions=[2])
    n = new_first_sql(engine, CFG, "b1", *AUG)
    assert n.value == 1 and "исключённых" in n.missing


def test_nothing_hidden_means_no_note(engine):
    """Когда отсекать нечего, пояснение не выдумывается: лишний шум не лучше молчания."""
    setup_deals(engine, [deal(1)])
    n = new_first_sql(engine, CFG, "b1", *AUG)
    assert "удалённых" not in n.missing and "исключённых" not in n.missing


def test_company_scope_counts_hidden_across_brands(engine):
    """У компании счёт отсечённого идёт по всем брендам, а не по одному кабинету."""
    setup_deals(engine, [deal(1),
                         dict(deal(2, deleted_at=datetime(2026, 8, 7, tzinfo=timezone.utc)), brand="b2")])
    n = new_first_sql(engine, CFG, "company", *AUG)
    assert "удалённых в CRM" in n.missing and "1" in n.missing


def test_named_filters_do_not_lower_status(engine):
    """Названный штатный отбор — не повод объявлять число оценкой.

    Удалённая в CRM сделка и технический тест отсекаются намеренно: они делают число правильным, а не неполным.
    Статус «оценка» бережём для настоящей неопределённости — незавершённой загрузки, непокрытого периода,
    неизвестного бренда. Иначе «оценка» перестанет что-либо значить (17.09.2026)."""
    from datacore.schema.number import Status
    setup_deals(engine, [deal(1), deal(2, deleted_at=datetime(2026, 8, 7, tzinfo=timezone.utc)), deal(3)],
                exclusions=[3])
    n = new_first_sql(engine, CFG, "b1", *AUG)
    assert n.value == 1
    assert "удалённых в CRM" in n.missing and "исключённых" in n.missing
    assert n.status is Status.FACT              # пояснение есть, но число остаётся фактом


def test_hidden_counts_respect_the_contour(engine):
    """Ревью Codex этапа 5 (п.5): счётчики отсечённого не учитывали набор отбора.

    Удалённая сделка, которая и без того не входит в набор (источник её исключает), называлась «удалённой» —
    хотя в этом контуре её не было бы в любом случае. Пояснение говорило о том, чего число и так не содержало."""
    from datacore.tests.test_contour_metrics import marker, setup_deals as setup_with_markers
    from datacore.serve.metrics import new_first_sql as nfs
    setup_with_markers(engine,
                       [dict(deal(1)), dict(deal(2), deleted_at=datetime(2026, 8, 7, tzinfo=timezone.utc))],
                       [marker(1, "seo"), marker(2, "site")])
    # в наборе «витрина» сделка 2 отсекается источником site, а не удалением
    n = nfs(engine, CFG, "b1", *AUG, contour="narrow")
    assert n.value == 1
    assert "удалённых" not in n.missing, n.missing
