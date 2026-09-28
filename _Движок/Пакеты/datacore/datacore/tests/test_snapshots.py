"""Снимки чисел (решение владельца 16.09.2026, вариант 2): каждый прогон сохраняет посчитанные числа на свою дату съёма,
и потом ядро отвечает на вопрос «что мы знали такого-то числа» из снимка, а не пересчётом по изменившимся фактам.

Источники дописывают данные задним числом (август в снимках 03.09 → 09.09 → 16.09 давал 61 395 → 61 404 → 61 272),
поэтому без снимков прошлое воспроизвести нельзя в принципе."""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from datacore.checks.known import KnownAnswer, reproduce
from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import visits
from datacore.serve.snapshots import periods_to_snapshot, take_snapshots
from datacore.tests.helpers import SYNTHETIC
from datacore.tests.test_metrics_ask import seed

CFG = load_config(SYNTHETIC)
SEP = (date(2026, 9, 1), date(2026, 9, 3))


def tracked(engine, scope, visits_rows, as_of, covered=("2026-09-01", "2026-09-09")):
    system = CFG.tracking["system_by_scope"][scope]
    p = Provenance(system, "C", f"L-{scope}-{uuid4().hex[:8]}", datetime(2026, 9, 20, tzinfo=timezone.utc))
    register_load(engine, p, as_of)
    if visits_rows:
        write_facts(engine, "visit", list(visits_rows), p, mode="upsert")
    lo = datetime.fromisoformat(covered[0] + "T00:00:00+00:00")
    hi = datetime.fromisoformat(covered[1] + "T00:00:00+00:00")
    engine.execute("INSERT INTO meta.window_log (source_system, window_start, window_end, rows_loaded, min_started_at, "
                   "max_started_at, load_id, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT (source_system, window_start) DO UPDATE SET max_started_at = EXCLUDED.max_started_at",
                   (system, 0, 1000000, len(visits_rows), lo, hi, p.load_id, datetime(2026, 9, 20, tzinfo=timezone.utc)))
    engine.commit()
    finish_load(engine, p.load_id, rows_read=len(visits_rows), rows_written=len(visits_rows), status="ok")
    return p


def v(i, day):
    return dict(visit_id=i, started_at=datetime(2026, 9, day, 9, tzinfo=timezone.utc), marker="seo", marker_level_1="seo",
                landing_page="/", referrer_host=None, client_hash=None, device=None, geo=None)


def test_snapshot_keeps_the_number_of_its_day(engine):
    """Съём 10.09 — 2 визита; к 16.09 источник дописал третий. Число на 10.09 обязано остаться прежним."""
    seed(engine)
    tracked(engine, "b1", [v(1, 1), v(2, 2)], date(2026, 9, 10))
    written = take_snapshots(engine, CFG, date(2026, 9, 10), periods=[("visits", "b1", *SEP, "")])
    assert written == 1
    tracked(engine, "b1", [v(3, 2)], date(2026, 9, 16))          # источник дописал визит задним числом
    assert visits(engine, CFG, "b1", *SEP).value == 3            # сегодня их три
    old = visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 10))
    assert old.value == 2 and old.as_of == date(2026, 9, 10)           # а на 10.09 было два — из снимка
    assert "снимок" in old.missing.lower()


def test_number_for_a_day_without_snapshot_is_still_refused(engine):
    """Снимка нет — ядро по-прежнему не выдумывает число на ту дату."""
    seed(engine)
    tracked(engine, "b1", [v(1, 1)], date(2026, 9, 16))
    with pytest.raises(RuleViolation) as e:
        visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 10))
    assert e.value.code == "К2"


def test_known_answer_is_reproduced_from_its_snapshot(engine):
    """Известный ответ на свою дату воспроизводится буквально и помечается «точно», без допуска."""
    seed(engine)
    tracked(engine, "b1", [v(1, 1), v(2, 2)], date(2026, 9, 10))
    take_snapshots(engine, CFG, date(2026, 9, 10), periods=[("visits", "b1", *SEP, "")])
    tracked(engine, "b1", [v(3, 2)], date(2026, 9, 16))
    answer = KnownAnswer(id="visits_snap", metric="visits", level="visit", scope="b1", flow="web",
                         period_start=SEP[0], period_end=SEP[1], value=2, unit="шт", status="факт",
                         source="C:tracker:analytics_data visits daily", as_of=date(2026, 9, 10), stage=3)
    check = reproduce(engine, CFG, [answer], stage=3)[0]
    assert check.ok and check.got == 2 and "снимок" in check.note.lower()


def test_periods_to_snapshot_covers_months_and_known_answers(engine):
    """Что снимать: периоды известных ответов этапа плюс последние месяцы — чтобы история накапливалась сама."""
    periods = periods_to_snapshot(CFG, date(2026, 9, 16), stage=3)
    assert ("visits", "b1", date(2026, 8, 1), date(2026, 9, 1), "") in periods
    assert any(m == "new_first_sql" and s == "company" for m, s, *_ in periods)
    assert all(end > start for _, _, start, end, _ in periods)
    assert len(periods) == len(set(periods))               # без повторов


# --- снимок хранит число целиком (ревью методологии 28.09.2026) ------------------------------------------------------

def failed_load(engine, scope, as_of):
    """Загрузка трекера, оборванная после успешной: её частичные строки могли остаться — число становится оценкой."""
    system = CFG.tracking["system_by_scope"][scope]
    p = Provenance(system, "C", f"F-{scope}-{uuid4().hex[:8]}", datetime(2026, 9, 21, tzinfo=timezone.utc))
    register_load(engine, p, as_of)
    finish_load(engine, p.load_id, rows_read=0, rows_written=0, status="failed")


def test_snapshot_keeps_status_and_note_of_its_number(engine):
    """Снятая «оценка» из снимка возвращается «оценкой» со своим пояснением, а не «фактом»."""
    seed(engine)
    tracked(engine, "b1", [v(1, 1), v(2, 2)], date(2026, 9, 10))
    failed_load(engine, "b1", date(2026, 9, 11))
    live = visits(engine, CFG, "b1", *SEP)
    assert live.status is Status.ESTIMATE and live.as_of == date(2026, 9, 10)
    assert take_snapshots(engine, CFG, date(2026, 9, 11), periods=[("visits", "b1", *SEP, "")]) == 1
    tracked(engine, "b1", [v(3, 2)], date(2026, 9, 16))                # позже источник починился и дописал визит
    old = visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 10))
    assert old.value == 2 and old.status is Status.ESTIMATE
    assert "не завершена" in old.missing and "снимок на 10.09.2026" in old.missing


def test_snapshot_is_signed_with_the_number_date_not_the_run_date(engine):
    """Прогон 12.09 при источнике, застрявшем на 10.09: снимок подписан 10.09 — датой съёма числа. На 12.09 снимка
    нет, и число на ту дату по-прежнему не выдумывается (К2)."""
    seed(engine)
    tracked(engine, "b1", [v(1, 1), v(2, 2)], date(2026, 9, 10))
    take_snapshots(engine, CFG, date(2026, 9, 12), periods=[("visits", "b1", *SEP, "")])
    row = engine.fetchone("SELECT as_of, taken_on, status FROM facts.snapshot_number WHERE metric = 'visits'")
    assert row == (date(2026, 9, 10), date(2026, 9, 12), "факт")
    assert visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 10)).value == 2
    with pytest.raises(RuleViolation) as e:
        visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 12))
    assert e.value.code == "К2"


def test_snapshot_of_a_date_is_written_once(engine):
    """Снимок одной даты съёма не перезаписывается: поздний прогон той же даты не подменяет то, что ядро знало."""
    from datacore.serve.snapshots import snapshot_run
    seed(engine)
    tracked(engine, "b1", [v(1, 1), v(2, 2)], date(2026, 9, 10))
    first = snapshot_run(engine, CFG, date(2026, 9, 10), periods=[("visits", "b1", *SEP, "")])
    tracked(engine, "b1", [v(3, 2)], date(2026, 9, 10))                # повторный прогон того же дня дописал визит
    again = snapshot_run(engine, CFG, date(2026, 9, 10), periods=[("visits", "b1", *SEP, "")])
    assert (first.saved, again.saved, again.kept) == (1, 0, 1)
    assert visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 10)).value == 2


def test_snapshot_of_the_old_format_is_read_as_estimate(engine):
    """Снимки до версии схемы 13 статуса не хранят: чтение не выдаёт их за факт и называет причину."""
    from datacore.serve.snapshots import LEGACY_NOTE
    seed(engine)
    engine.execute("INSERT INTO facts.snapshot_number (metric, scope, flow, segment, period_start, period_end, as_of, "
                   "value, unit, source_system, source_class, load_id, loaded_at) "
                   "VALUES ('visits', 'b1', 'web', '', ?, ?, ?, 5, 'шт', 'datacore', 'C', 'snapshot-old', ?)",
                   (*SEP, date(2026, 9, 5), datetime(2026, 9, 5, tzinfo=timezone.utc)))
    engine.commit()
    old = visits(engine, CFG, "b1", *SEP, as_of=date(2026, 9, 5))
    assert old.value == 5 and old.status is Status.ESTIMATE and LEGACY_NOTE in old.missing


def test_share_keeps_its_denominator_in_the_snapshot(engine):
    """Доля из снимка сохраняет величину знаменателя: без неё «0,5» не проверить умножением."""
    from datacore.serve.metrics import from_snapshot
    seed(engine)
    engine.execute("INSERT INTO facts.snapshot_number (metric, scope, flow, segment, period_start, period_end, as_of, "
                   "value, unit, status, missing, denominator_value, taken_on, source_system, source_class, load_id, "
                   "loaded_at) VALUES ('no_trace', 'b1', 'all', '', ?, ?, ?, 0.5, 'доля', 'факт', '', 40, ?, "
                   "'datacore', 'D', 'snapshot-x', ?)",
                   (*SEP, date(2026, 9, 5), date(2026, 9, 5), datetime(2026, 9, 5, tzinfo=timezone.utc)))
    engine.commit()
    n = from_snapshot(engine, "no_trace", "b1", *SEP, date(2026, 9, 5), level="new_first_sql", flow="all",
                      unit="доля", denominator="new_first_sql", source="D:datacore:facts.snapshot_number")
    assert n.denominator_value == 40.0 and n.status is Status.FACT


def test_periods_cover_known_answers_of_all_stages_by_default(tmp_path):
    """Шаг ночного прогона снимает периоды известных ответов всех этапов, а не только ранних."""
    from dataclasses import replace
    (tmp_path / "known_answers.yaml").write_text(
        "answers:\n" + "".join(
            f"  - {{id: a{stage}, metric: visits, level: visit, scope: b1, flow: web, period_start: 2025-0{stage}-01, "
            f"period_end: 2025-0{stage + 1}-01, value: 1, unit: шт, status: факт, source: 'C:tracker:x', "
            f"as_of: 2025-09-01, stage: {stage}}}\n" for stage in (3, 5, 8)), encoding="utf-8")
    cfg = replace(CFG, duckdb_path=tmp_path / "db.duckdb")
    every = periods_to_snapshot(cfg, date(2026, 9, 16))
    early = periods_to_snapshot(cfg, date(2026, 9, 16), stage=3)
    stage8 = ("visits", "b1", date(2025, 8, 1), date(2025, 9, 1), "")
    assert stage8 in every and stage8 not in early


def test_snapshot_step_reports_and_fails_loudly(engine, capsys, tmp_path):
    """Снимки — отдельный шаг с кодом возврата: удачный — 0 и отчёт «записано и прочитано обратно», сбой — 1."""
    from datacore.serve.snapshots import main
    from datacore.tests.helpers import SYNTHETIC
    seed(engine)
    tracked(engine, "b1", [v(1, 1), v(2, 2)], date(2026, 9, 10))
    engine.close()                      # DuckDB не делит файл с другим соединением на запись
    assert main(["--config", str(SYNTHETIC), "--db", engine.url, "--as-of", "2026-09-10"]) == 0
    assert "записано и прочитано обратно" in capsys.readouterr().out
    empty = f"duckdb:///{(tmp_path / 'empty.duckdb').as_posix()}"     # схемы нет — сбой, а не тихий ноль
    assert main(["--config", str(SYNTHETIC), "--db", empty, "--as-of", "2026-09-10"]) == 1
    assert "🟥" in capsys.readouterr().out
