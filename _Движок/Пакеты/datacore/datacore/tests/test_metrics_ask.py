from datetime import date, datetime, timedelta, timezone

import pytest

from datacore.schema.config import load_config
from datacore.schema.engine import connect
from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve import ask
from datacore.serve.metrics import last_as_of, new_first_sql
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
MSK = timezone(timedelta(hours=3))
PROV = Provenance("crm_mirror", "D", "L-m", datetime(2026, 9, 14, 12, tzinfo=MSK))


def seed(engine):
    migrate(engine)
    register_load(engine, PROV, date(2026, 9, 14))
    write_facts(engine, "contact", [dict(contact_id=i, brand="b1", created_at=datetime(2026, 8, 1, tzinfo=MSK)) for i in (1, 2, 3)], PROV)
    deals = [dict(deal_id=1, contact_id=1, brand="b1", pipeline="p", status="s", is_new_first=True, created_at=datetime(2026, 8, 31, 23, 30, tzinfo=MSK)),
             dict(deal_id=2, contact_id=2, brand="b1", pipeline="p", status="s", is_new_first=True, created_at=datetime(2026, 9, 1, 0, 30, tzinfo=MSK)),
             dict(deal_id=3, contact_id=3, brand="b2", pipeline="p", status="s", is_new_first=True, created_at=datetime(2026, 8, 10, tzinfo=MSK)),
             dict(deal_id=4, contact_id=3, brand="b2", pipeline="p", status="s", is_new_first=False, created_at=datetime(2026, 8, 11, tzinfo=MSK)),
             dict(deal_id=5, contact_id=1, brand="b1", pipeline="p", status="s", is_new_first=True, created_at=datetime(2026, 8, 12, tzinfo=MSK))]
    write_facts(engine, "deal", deals, PROV)
    write_facts(engine, "exclusion", [dict(entity="deal", entity_key="5", reason="тест", since=date(2026, 9, 10), decided_by="t")], PROV)
    finish_load(engine, PROV.load_id, rows_read=5, rows_written=9, status="ok")


def test_new_first_sql_by_brand_company_and_window_in_instance_timezone(engine):
    seed(engine)
    aug = (date(2026, 8, 1), date(2026, 9, 1))
    brz = new_first_sql(engine, CFG, "b1", *aug)
    assert (brz.value, brz.status, brz.as_of) == (1, Status.FACT, date(2026, 9, 14))     # сделка 2 — уже сентябрь по МСК; 5 исключена
    assert new_first_sql(engine, CFG, "b2", *aug).value == 1
    company = new_first_sql(engine, CFG, "company", *aug)
    # компания — это все её сделки, а не сумма кабинетов: складывать нечего, поэтому и scope остаётся «company»
    # (решение 17.09.2026; раньше 4 сделки августа без бренда выпадали — 2 889 вместо 2 893)
    assert (company.value, company.scope) == (2, "company")
    assert last_as_of(engine, ("crm_mirror", "crm_api")) == date(2026, 9, 14)


def test_unknown_brand_in_window_lowers_status(engine):
    """Сделка с неопределённым брендом входит в компанию и понижает статус до оценки.

    В число она входит с 17.09.2026: компания — это все её сделки, а не сумма кабинетов (раньше 4 сделки августа
    без бренда выпадали). Но бренд неизвестен — значит разложить число по кабинетам нельзя, и это оценка."""
    seed(engine)
    write_facts(engine, "contact", [dict(contact_id=9, brand="unknown", created_at=datetime(2026, 8, 1, tzinfo=MSK))], PROV)
    write_facts(engine, "deal", [dict(deal_id=9, contact_id=9, brand="unknown", pipeline="p", status="s", is_new_first=True, created_at=datetime(2026, 8, 5, tzinfo=MSK))], PROV)
    n = new_first_sql(engine, CFG, "company", date(2026, 8, 1), date(2026, 9, 1))
    assert n.status == Status.ESTIMATE and "неопределённым брендом" in n.missing and n.value == 3


def test_ask_rejects_writes_with_k8_and_reads_only(tmp_path):
    path = tmp_path / "a.duckdb"
    with connect(f"duckdb:///{path.as_posix()}") as e:
        seed(e)
    url = f"duckdb:///{path.as_posix()}"
    for bad in ("INSERT INTO facts.deal VALUES (1)", "SELECT 1; DROP TABLE facts.deal", "UPDATE facts.deal SET price = 0", "CREATE TABLE x (a INT)",
                "SELECT * FROM raw.contacts", "SELECT * FROM raw._dlt_loads"):
        with pytest.raises(RuleViolation) as err:
            ask.run_sql(url, bad, limit=10)
        assert err.value.code == "К8"
    rows = ask.run_sql(url, "SELECT COUNT(*) FROM facts.deal", limit=10)
    assert rows == [(5,)]
    out = ask.main(["--config", str(SYNTHETIC), "--db", url, "metric", "new_first_sql", "--scope", "company", "--from", "2026-08-01", "--to", "2026-09-01"])
    assert out == 0


def test_ask_rejects_quoted_raw_comments_and_file_functions(tmp_path):
    path = tmp_path / "c.duckdb"
    with connect(f"duckdb:///{path.as_posix()}") as e:
        seed(e)
        e.execute("CREATE SCHEMA IF NOT EXISTS raw")
        e.execute("CREATE TABLE raw.contacts (id BIGINT)")
    url = f"duckdb:///{path.as_posix()}"
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN=synthetic", encoding="utf-8")
    for bad in ('SELECT * FROM "raw"."contacts"', "SELECT * FROM raw /* обход */ . contacts", "SELECT * FROM RAW . contacts",
                f"SELECT content FROM read_text('{secret.as_posix()}')", f"SELECT * FROM read_csv('{secret.as_posix()}')"):
        with pytest.raises(RuleViolation) as err:
            ask.run_sql(url, bad, limit=10)
        assert err.value.code == "К8", bad


def test_read_only_connection_blocks_file_access_even_without_ask_check(tmp_path):
    """Второй рубеж: даже если разбор запроса пропустит файловую функцию, соединение агента файл не прочитает."""
    path = tmp_path / "d.duckdb"
    with connect(f"duckdb:///{path.as_posix()}") as e:
        migrate(e)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN=synthetic", encoding="utf-8")
    ro = connect(f"duckdb:///{path.as_posix()}", read_only=True)
    with pytest.raises(Exception) as err:
        ro.fetchall(f"SELECT content FROM read_text('{secret.as_posix()}')")
    assert "Permission" in type(err.value).__name__ or "Cannot access file" in str(err.value)
    with pytest.raises(Exception):
        ro.execute("SET enable_external_access = true")
    ro.close()


def test_read_only_connection_blocks_writes_at_db_level(tmp_path):
    path = tmp_path / "b.duckdb"
    with connect(f"duckdb:///{path.as_posix()}") as e:
        migrate(e)
    ro = connect(f"duckdb:///{path.as_posix()}", read_only=True)
    with pytest.raises(Exception):
        ro.execute("INSERT INTO meta.schema_version VALUES (9, 'x', now())")
    ro.close()


def test_ask_passes_segment_to_metric(engine, tmp_path, capsys):
    """Через ask можно спросить любое из пяти чисел набора и конверсию по каналу.

    Без разреза команда умела только «сколько всего» — то есть пять чисел этапа 5 были доступны из кода,
    но не из командной строки, ради которой ядро и делается (17.09.2026)."""
    from datacore.serve.ask import main
    seed(engine)
    # у сделки 1 есть маркер источника site — витрина её исключает
    t = Provenance("tracker_b1", "C", "L-mark", datetime(2026, 9, 14, tzinfo=MSK))
    register_load(engine, t, date(2026, 9, 14))
    write_facts(engine, "order_marker", [dict(deal_id=1, marker="site_x", visit_id=None,
                                              ordered_at=datetime(2026, 8, 31, 23, 30, tzinfo=MSK),
                                              marker_level_1="site")], t, mode="upsert")
    finish_load(engine, t.load_id, rows_read=1, rows_written=1, status="ok")
    engine.close()                      # DuckDB не открывает ту же базу «только для чтения» при живом соединении
    args = ["--config", str(SYNTHETIC), "--db", engine.url, "metric", "new_first_sql", "--scope", "b1",
            "--from", "2026-08-01", "--to", "2026-09-01"]
    assert main(args) == 0
    assert "значение: 1 шт" in capsys.readouterr().out
    assert main(args + ["--segment", "contour=narrow"]) == 0
    out = capsys.readouterr().out
    assert "значение: 0 шт" in out and "contour=narrow" in out


def test_ask_refuses_unknown_segment(engine, capsys):
    """Выдуманный разрез — отказ с кодом правила, а не тихий ответ «как будто без разреза»."""
    from datacore.serve.ask import main
    seed(engine)
    engine.close()
    assert main(["--config", str(SYNTHETIC), "--db", engine.url, "metric", "new_first_sql", "--scope", "b1",
                 "--from", "2026-08-01", "--to", "2026-09-01", "--segment", "contour=выдуманный"]) == 1
    assert "К2" in capsys.readouterr().out


def test_share_is_printed_readably():
    """Доля печатается процентами: 0.0049152174342170195 в отчёте нечитаемо, и это мешает заметить ошибку."""
    from datacore.serve.ask import render
    from datacore.schema.number import Number, Status
    n = Number(metric="conversion", level="new_first_sql", scope="b1", flow="all",
               period_start=date(2026, 8, 1), period_end=date(2026, 9, 1), segment="channel_group=seo",
               source="D:datacore:facts.visit", unit="доля", denominator="visits",
               status=Status.FACT, value=0.0049152174342170195, as_of=date(2026, 9, 17))
    out = render(n)
    assert "0,49 %" in out and "0.00491" not in out


def test_share_prints_the_fraction(engine):
    """Ревью Codex этапа 5 (п.7): долю нельзя проверить, если не видно, из чего она получена.

    «0,49 %» — это утверждение, «249 из 50 659 visits» — проверяемый факт: можно пересчитать и заметить, что
    знаменатель не тот. Без дроби ошибка в определении группы канала осталась бы незаметной."""
    from datacore.serve.metrics import conversion
    from datacore.serve.ask import render
    from datacore.tests.test_conversion import visits
    from datacore.tests.test_contour_metrics import deal as cdeal, marker as cmarker, setup_deals as csetup
    csetup(engine, [cdeal(1)], [cmarker(1, "seo")])
    visits(engine, "seo", 200)
    n = conversion(engine, CFG, "b1", date(2026, 8, 1), date(2026, 9, 1), segment="channel_group=seo")
    assert n.denominator_value == 200.0
    out = render(n)
    assert "1 из 200" in out and "0,50 %" in out, out
