"""Регрессии ревью Codex этапа 3: число вне загруженного покрытия — «нет данных», а не ноль; «звонок с визитом» —
это существующий визит того же кабинета; дата съёма — только при успехе всех обязательных систем; известные ответы
проверяются на свою дату съёма и свой статус."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

MSK = timezone(timedelta(hours=3))

from datacore.checks.known import KnownAnswer, reproduce
from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import calls, visits
from datacore.tests.helpers import SYNTHETIC
from datacore.tests.test_metrics_ask import seed

CFG = load_config(SYNTHETIC)
LOADED = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
SEP = (date(2026, 9, 1), date(2026, 9, 10))


def tracked(engine, scope, *, visits_rows=(), call_rows=(), windows=()):
    system = CFG.tracking["system_by_scope"][scope]
    p = Provenance(system, "C", f"L-{scope}-{uuid4().hex[:8]}", LOADED)
    register_load(engine, p, date(2026, 9, 14))
    for table, rows in (("visit", visits_rows), ("phone_call", call_rows)):
        if rows:
            write_facts(engine, table, list(rows), p, mode="upsert")
    for start, end, lo, hi in windows:
        engine.execute("INSERT INTO meta.window_log (source_system, window_start, window_end, rows_loaded, "
                       "min_started_at, max_started_at, load_id, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (system, start, end, 1, lo, hi, p.load_id, LOADED))
    engine.commit()
    finish_load(engine, p.load_id, rows_read=1, rows_written=1, status="ok")
    return p


def v(i, day, group="seo"):
    return dict(visit_id=i, started_at=datetime(2026, 9, day, 9, tzinfo=timezone.utc), marker=group, marker_level_1=group,
                landing_page="/", referrer_host=None, client_hash=None, device=None, geo=None)


def test_period_outside_coverage_is_no_data_not_zero(engine):
    """Загружены визиты 01–02.09; за 05–07.09 покрытия нет — число «нет данных», не ноль."""
    seed(engine)
    lo, hi = datetime(2026, 9, 1, 9, tzinfo=timezone.utc), datetime(2026, 9, 3, 9, tzinfo=timezone.utc)
    tracked(engine, "b1", visits_rows=[v(1, 1), v(2, 2)], windows=[(0, 1000, lo, hi)])
    covered = visits(engine, CFG, "b1", date(2026, 9, 1), date(2026, 9, 3))
    assert covered.value == 2 and covered.status is Status.FACT
    gap = visits(engine, CFG, "b1", date(2026, 9, 5), date(2026, 9, 8))
    assert gap.value is None and "покрыт" in gap.missing


def test_call_with_visit_requires_the_visit_to_exist(engine):
    """Звонок ссылается на визит 999, которого нет: он не «с визитом»."""
    seed(engine)
    at = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    rows = [dict(call_id=1, started_at=at, visit_id=1, scenario=None, callee_line=None, caller_hash=None, answered=True, duration_s=5, deal_id=None),
            dict(call_id=2, started_at=at, visit_id=999, scenario=None, callee_line=None, caller_hash=None, answered=True, duration_s=5, deal_id=None)]
    lo, hi = datetime(2026, 9, 1, 0, tzinfo=timezone.utc), datetime(2026, 9, 9, 0, tzinfo=timezone.utc)
    tracked(engine, "b1", visits_rows=[v(1, 1)], call_rows=rows, windows=[(0, 1000, lo, hi)])
    assert calls(engine, CFG, "b1", *SEP).value == 2
    assert calls(engine, CFG, "b1", *SEP, segment="visit=linked").value == 1


def test_known_answer_on_another_as_of_is_compared_with_a_note(engine):
    """Снимка чужой даты у ядра нет: ответ сверяется с текущим состоянием, но разница дат названа вслух и сверка идёт
    по допуску, а не «точно» (решение владельца 16.09.2026, вариант 1)."""
    seed(engine)
    lo, hi = datetime(2026, 9, 1, 0, tzinfo=timezone.utc), datetime(2026, 9, 9, 0, tzinfo=timezone.utc)
    tracked(engine, "b1", visits_rows=[v(1, 1), v(2, 2)], windows=[(0, 1000, lo, hi)])
    common = dict(metric="visits", level="visit", scope="b1", flow="web", period_start=date(2026, 9, 1),
                  period_end=date(2026, 9, 3), unit="шт", status="факт",
                  source="C:tracker:analytics_data visits daily", as_of=date(2026, 9, 10), stage=3)
    same = reproduce(engine, CFG, [KnownAnswer(id="visits_same", value=2, **common)], stage=3)[0]
    assert same.ok and same.got == 2 and "эталон снят 10.09.2026" in same.note and "допуск" in same.note

    far = reproduce(engine, CFG, [KnownAnswer(id="visits_far", value=50, **common)], stage=3)[0]
    assert not far.ok and far.got == 2          # расхождение за допуском по-прежнему красное


def test_gap_between_windows_is_not_coverage(engine):
    """Ревью Codex (п.2): покрытие считалось по самому раннему и самому позднему времени фактов, поэтому две
    загрузки — за 1–5 и за 20–25 сентября — выглядели как непрерывное покрытие 1–25. Число за 6–19 сентября
    выдавалось как факт, хотя те дни никогда не читались."""
    from datacore.serve.metrics import coverage_note
    from datacore.schema.migrate import migrate
    from datacore.tests.helpers import WINDOW_COLUMNS
    migrate(engine)
    marks = ", ".join("?" for _ in WINDOW_COLUMNS)
    for start, end, lo, hi in [
        (1, 2, datetime(2026, 9, 1, tzinfo=MSK), datetime(2026, 9, 5, 23, 59, tzinfo=MSK)),
        (3, 4, datetime(2026, 9, 20, tzinfo=MSK), datetime(2026, 9, 25, 23, 59, tzinfo=MSK)),
    ]:
        engine.execute(f"INSERT INTO meta.window_log ({', '.join(WINDOW_COLUMNS)}) VALUES ({marks})",
                       ("sys", start, end, 10, lo, hi, "L-1", datetime(2026, 9, 26, tzinfo=MSK)))
    engine.commit()
    # период целиком внутри первого окна — покрыт
    assert coverage_note(engine, "sys", datetime(2026, 9, 2, tzinfo=MSK), datetime(2026, 9, 4, tzinfo=MSK)) == ""
    # период, накрывающий разрыв 6–19 сентября, покрытым считаться не должен
    note = coverage_note(engine, "sys", datetime(2026, 9, 1, tzinfo=MSK), datetime(2026, 9, 26, tzinfo=MSK))
    assert note and "06.09.2026" in note, note


def test_failed_load_before_success_is_not_reported(engine):
    """Обрыв, случившийся ДО успешной загрузки той же системы, уже неактуален: его частичные строки перезаписаны.

    Живой случай 17.09.2026: у визитов 11 обрывов на кабинет (живой сбор этапа 3), последняя загрузка успешна,
    но два обрыва имели ту же дату съёма — и метрика вечно помечала числа «оценкой», ссылаясь на давно
    перекрытые прогоны. Сравнивать надо по времени прогона, а не только по дате съёма."""
    from datacore.schema.migrate import migrate
    from datacore.serve.metrics import state_of
    migrate(engine)
    day = date(2026, 9, 16)
    early = Provenance("sys", "C", "L-failed-early", datetime(2026, 9, 16, 9, 0, tzinfo=MSK))
    register_load(engine, early, day)
    finish_load(engine, early.load_id, rows_read=10, rows_written=0, status="failed")
    good = Provenance("sys", "C", "L-ok", datetime(2026, 9, 16, 12, 0, tzinfo=MSK))
    register_load(engine, good, day)
    finish_load(engine, good.load_id, rows_read=10, rows_written=10, status="ok")
    state, note = state_of(engine, ("sys",))
    assert state == day
    assert note == "", note                      # обрыв до успеха не поминается


def test_failed_load_after_success_is_reported(engine):
    """А обрыв ПОСЛЕ успеха — поминается: его частичные строки могли остаться в фактах."""
    from datacore.schema.migrate import migrate
    from datacore.serve.metrics import state_of
    migrate(engine)
    day = date(2026, 9, 16)
    good = Provenance("sys", "C", "L-ok", datetime(2026, 9, 16, 9, 0, tzinfo=MSK))
    register_load(engine, good, day)
    finish_load(engine, good.load_id, rows_read=10, rows_written=10, status="ok")
    late = Provenance("sys", "C", "L-failed-late", datetime(2026, 9, 16, 15, 0, tzinfo=MSK))
    register_load(engine, late, day)
    finish_load(engine, late.load_id, rows_read=10, rows_written=5, status="failed")
    state, note = state_of(engine, ("sys",))
    assert state == day and "не завершена" in note


def test_failed_load_of_never_succeeded_system_is_not_hidden_by_global_state(engine):
    """Ревью Codex этапа 5 (п.4): обрыв системы, у которой успеха не было ни разу, отсекался глобальной датой.

    Пример: API CRM ни разу не загрузился успешно и оставил частичные строки за 10.09, зеркало успешно
    загружено за 17.09. Общая дата съёма — 17.09, и обрыв за 10.09 не проходил условие «as_of >= состояние»:
    ядро молчало о незагруженной системе, хотя её строки могли попасть в факты."""
    from datacore.schema.migrate import migrate
    from datacore.serve.metrics import state_of
    migrate(engine)
    mirror = Provenance("mirror", "D", "L-mirror-ok", datetime(2026, 9, 17, 9, tzinfo=MSK))
    register_load(engine, mirror, date(2026, 9, 17))
    finish_load(engine, mirror.load_id, rows_read=10, rows_written=10, status="ok")
    api = Provenance("api", "D", "L-api-failed", datetime(2026, 9, 10, 9, tzinfo=MSK))
    register_load(engine, api, date(2026, 9, 10))
    finish_load(engine, api.load_id, rows_read=10, rows_written=5, status="failed")
    state, note = state_of(engine, ("mirror", "api"))
    assert "api" in note and "не завершена" in note, note
