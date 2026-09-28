"""Известные ответы инстанса (уровень 4 самопроверки): замороженные ответы воспроизводятся из фактов.

Расхождение с эталоном — стоп работы. Исключение одно и только объявленное: у ответа в файле пометка
`explained_divergence` — ссылка на разбор, почему эталон остаётся красным (два определения, незакрытый вопрос).
Такой ответ считается отдельной строкой отчёта и код возврата не валит; необъявленное расхождение — код 1.
Подгонка метрики под эталон запрещена (стандарт, раздел 8)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from datacore.schema.errors import RuleViolation
from datacore.schema.number import Number, Status


@dataclass(frozen=True)
class KnownAnswer:
    id: str
    metric: str
    level: str
    scope: str
    flow: str
    period_start: date
    period_end: date
    value: float | None
    unit: str
    status: str
    source: str
    as_of: date
    stage: int
    note: str = ""
    denominator: str | None = None
    segment: str = ""
    explained_divergence: str = ""     # ссылка на разбор: эталон красный намеренно и объявленно


def load_known_answers(path: Path) -> list[KnownAnswer]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    answers = [KnownAnswer(**a) for a in raw["answers"]]
    ids = [a.id for a in answers]
    if len(set(ids)) != len(ids):
        raise ValueError("id известных ответов повторяются")
    if any(a.stage not in range(2, 9) for a in answers):
        raise ValueError("stage известного ответа — от 2 до 8")
    for a in answers:
        if not isinstance(a.explained_divergence, str) or (a.explained_divergence and not a.explained_divergence.strip()):
            raise ValueError(f"{a.id}: explained_divergence — ссылка на разбор расхождения, строкой")
    return answers


def as_number(a: KnownAnswer) -> Number:
    return Number(metric=a.metric, level=a.level, scope=a.scope, flow=a.flow, period_start=a.period_start,
                  period_end=a.period_end, source=a.source, status=Status(a.status), value=a.value,
                  denominator=a.denominator, unit=a.unit, segment=a.segment, as_of=a.as_of)


@dataclass(frozen=True)
class Check:
    id: str
    expected: float | None
    got: float | None
    ok: bool
    note: str = ""
    explained: str = ""               # расхождение объявлено в файле ответов: ссылка на разбор

    @property
    def stops(self) -> bool:
        """Расхождение, которое останавливает работу: не совпало и не объявлено."""
        return not self.ok and not self.explained


def _own_system(source: str, cfg) -> bool:
    """Ответ своей системы (зеркало, API, само ядро) воспроизводится точно; чужой — в допуске моста."""
    system = source.split(":", 2)[1]
    return bool(cfg.crm) and system in (cfg.crm["system_mirror"], cfg.crm["system_api"], "datacore")


def reproduce(engine, cfg, answers: list[KnownAnswer], stage: int) -> list[Check]:
    from datacore.serve.metrics import REGISTRY
    tol_abs, tol_rel = cfg.known_answers_tolerance
    out = []
    for a in answers:
        if a.stage > stage or a.metric not in REGISTRY:
            out.append(Check(a.id, a.value, None, True, f"этап {a.stage}: метрика «{a.metric}» ещё не в реестре — пропуск"))
            continue
        kwargs = {"segment": a.segment} if a.segment else {}
        drift = ""
        try:
            # Сначала — на дату съёма ответа: если ядро хранит снимок той даты, ответ воспроизводится буквально
            # (ревью Codex этапа 3, 15.09.2026: иначе исторический ответ молча считался бы по сегодняшнему состоянию).
            got = REGISTRY[a.metric](engine, cfg, a.scope, a.period_start, a.period_end, as_of=a.as_of, **kwargs)
        except RuleViolation:
            # Снимка той даты нет — сверяем с текущим состоянием и говорим об этом вслух: источники дописывают данные
            # задним числом, поэтому такое сравнение всегда идёт по допуску моста, а не «точно».
            try:
                got = REGISTRY[a.metric](engine, cfg, a.scope, a.period_start, a.period_end, **kwargs)
            except RuleViolation as e:
                out.append(Check(a.id, a.value, None, False, f"число не строится: {e}"))
                continue
            drift = (f"эталон снят {a.as_of:%d.%m.%Y}, факты на {got.as_of:%d.%m.%Y} — сверка по допуску"
                     if got.as_of else f"эталон снят {a.as_of:%d.%m.%Y}, снимка той даты нет — сверка по допуску")
        if got.value is None or a.value is None:
            ok = got.value is None and a.value is None
            out.append(Check(a.id, a.value, got.value, ok, "нет данных" if ok else "«нет данных» против числа"))
            continue
        diff = abs((got.value or 0) - (a.value or 0))
        from_snap = "снимок" in (got.missing or "").lower()
        if from_snap:
            # Число воспроизведено буквально из снимка своей даты — допуск здесь не нужен, сравнение точное.
            ok, note = diff == 0, f"снимок на {a.as_of:%d.%m.%Y} — воспроизведено точно"
        elif _own_system(a.source, cfg) and not drift:
            ok, note = diff == 0, "точно (своя система)"
        else:
            ok = diff <= tol_abs or bool(a.value and diff / a.value <= tol_rel)
            note = f"|Δ| = {diff:g}, допуск {tol_abs:g} шт или {tol_rel:.0%}"
            note = f"{drift}; {note}" if drift else f"мост: {note}"
        note += f"; статус {got.status.value}" if got.status is not Status.FACT else ""
        if a.explained_divergence and ok:
            note += "; совпало — пометку «расхождение объяснено» можно снять"
        out.append(Check(a.id, a.value, got.value, bool(ok), note,
                         explained="" if ok else a.explained_divergence))
    return out


def main(argv=None) -> int:
    import argparse
    from datacore.schema.config import load_config
    from datacore.schema.engine import connect
    from datacore.serve.storage import storage_url
    ap = argparse.ArgumentParser(description="Известные ответы: воспроизвести из фактов")
    ap.add_argument("--stage", type=int, required=True)
    ap.add_argument("--db", default=None, help="адрес базы; по умолчанию — из окружения, затем файл DuckDB")
    ap.add_argument("--config", default=None)
    ap.add_argument("--answers", default=None, help="файл известных ответов; по умолчанию — рядом с базой инстанса")
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    cfg = load_config(Path(a.config) if a.config else root / "Ядро данных" / "Конфигурация инстанса.yaml")
    answers = load_known_answers(Path(a.answers) if a.answers else root / "Данные" / "datacore" / "known_answers.yaml")
    engine = connect(storage_url(cfg, a.db), read_only=True)
    try:
        checks = reproduce(engine, cfg, answers, a.stage)
    finally:
        engine.close()
    return report(checks)


def report(checks: list[Check]) -> int:
    """Печать проверки и код возврата: 1 — есть необъявленное расхождение."""
    for c in checks:
        got = "—" if c.got is None else format(c.got, "g")
        expected = "нет данных" if c.expected is None else format(c.expected, "g")
        mark = "✅" if c.ok else ("🟨" if c.explained else "🟥")
        tail = f"; расхождение объяснено: {c.explained}" if c.explained else ""
        print(f"{mark} {c.id}: ожидалось {expected}, получено {got} — {c.note}{tail}")
    stop = [c for c in checks if c.stops]
    explained = [c for c in checks if not c.ok and c.explained]
    print(f"проверено {len(checks)}, расхождений {len(stop)}")
    print(f"объявленных расхождений (эталон красный намеренно, со ссылкой на разбор): {len(explained)}")
    return 1 if stop else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
