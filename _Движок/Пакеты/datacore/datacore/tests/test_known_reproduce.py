from datetime import date

from datacore.checks.known import KnownAnswer, reproduce
from datacore.schema.config import load_config
from datacore.tests.helpers import SYNTHETIC
from datacore.tests.test_metrics_ask import seed

CFG = load_config(SYNTHETIC)


def ka(id, scope, value, source, stage=2, metric="new_first_sql"):
    return KnownAnswer(id=id, metric=metric, level="new_first_sql", scope=scope, flow="all", period_start=date(2026, 8, 1),
                       period_end=date(2026, 9, 1), value=value, unit="шт", status="факт", source=source, as_of=date(2026, 9, 14), stage=stage)


def test_reproduce_exact_for_own_system_tolerance_for_bridge_and_skip_for_later_stage(engine):
    seed(engine)
    answers = [ka("own_ok", "b1", 1, "D:crm_mirror:flag"),
               ka("own_off", "b1", 2, "D:crm_mirror:flag"),
               ka("bridge_ok", "company", 3, "C:tracker:analytics"),        # 2 против 3: |Δ| = 1 ≤ 2 — в допуске
               ka("bridge_off", "company", 9, "C:tracker:analytics"),
               ka("later", "company", 5, "C:tracker:x", stage=5, metric="visits")]
    checks = {c.id: c for c in reproduce(engine, CFG, answers, stage=2)}
    assert checks["own_ok"].ok and not checks["own_off"].ok
    assert checks["bridge_ok"].ok and not checks["bridge_off"].ok
    assert checks["later"].ok and "этап" in checks["later"].note


# --- «намеренно красный» эталон — только объявленно (стандарт, раздел 8; ревью методологии 28.09.2026) --------------

def test_declared_divergence_is_reported_apart_and_does_not_stop(engine, capsys):
    """Эталон, красный намеренно, со ссылкой на разбор — отдельная строка отчёта, код 0. Необъявленное
    расхождение рядом — по-прежнему стоп, код 1."""
    from dataclasses import replace
    from datacore.checks.known import report
    seed(engine)
    explained = replace(ka("red_on_purpose", "b1", 2, "D:crm_mirror:flag"),
                        explained_divergence="План — Этап 5.md#два-определения")
    checks = {c.id: c for c in reproduce(engine, CFG, [explained], stage=2)}
    c = checks["red_on_purpose"]
    assert not c.ok and c.explained and not c.stops
    assert report(list(checks.values())) == 0
    out = capsys.readouterr().out
    assert "🟨" in out and "План — Этап 5.md#два-определения" in out
    assert "объявленных расхождений (эталон красный намеренно, со ссылкой на разбор): 1" in out

    both = reproduce(engine, CFG, [explained, ka("silent_red", "b1", 2, "D:crm_mirror:flag")], stage=2)
    assert report(both) == 1
    assert "расхождений 1" in capsys.readouterr().out


def test_declared_divergence_that_matches_again_says_so(engine):
    """Эталон с пометкой снова совпал — отчёт предлагает снять пометку, чтобы она не прятала будущий сбой."""
    from dataclasses import replace
    seed(engine)
    matched = replace(ka("was_red", "b1", 1, "D:crm_mirror:flag"), explained_divergence="разбор.md")
    c = reproduce(engine, CFG, [matched], stage=2)[0]
    assert c.ok and not c.explained and "можно снять" in c.note


def test_declared_divergence_needs_a_reference(tmp_path):
    """Пометка без ссылки на разбор — не объявление, а способ спрятать красный эталон: файл не загружается."""
    import pytest
    from datacore.checks.known import load_known_answers
    bad = tmp_path / "k.yaml"
    bad.write_text("answers:\n  - {id: x, metric: m, level: l, scope: s, flow: all, period_start: 2026-08-01, "
                   "period_end: 2026-09-01, value: 1, unit: шт, status: факт, source: 'D:sys:q', as_of: 2026-09-01, "
                   "stage: 2, explained_divergence: '   '}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="explained_divergence"):
        load_known_answers(bad)
