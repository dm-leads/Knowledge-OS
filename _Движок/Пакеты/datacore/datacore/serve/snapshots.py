"""Снимки своих чисел: ядро считает метрики и сохраняет их на дату съёма в facts.snapshot_number (ADR-0002 контура).

Зачем. Источники дописывают данные задним числом — месяц визитов в снимках трёх дат давал три разных числа. Поэтому
вопрос «что мы знали такого-то числа» нельзя решить пересчётом по сегодняшним фактам: ответ будет другим, а подпись
под ним — ложной. Снимок — единственный честный способ ответить на такой вопрос (решение владельца 16.09.2026).

Снимок хранит число целиком: значение, статус, пояснение, знаменатель и дату съёма самого числа. Дата съёма числа —
не день прогона: при упавшем источнике число остаётся на прошлой дате, и подписать его днём прогона значило бы
солгать. Из снимка возвращается тот статус, с которым число снято: «оценка» при чтении не становится «фактом».

Снимается только число, чья дата съёма совпадает с днём прогона. Число, отставшее из-за упавшего или отстающего
источника, не снимается: снимок его даты был снят в тот день, когда она была свежей, а сейчас в фактах уже могут быть
более поздние строки другой системы или частичные строки оборванной загрузки — под старой подписью лежали бы новые
данные (ревью переноса 28.09.2026). Такие числа названы в отчёте шага («отстают»).

Снимки копятся только вперёд: снимок одной даты съёма пишется один раз и не перезаписывается. Даты, в которые снимков
не снимали, восстановить неоткуда.

Снимки — отдельный шаг ночного прогона с кодом возврата, после загрузок, от которых числа зависят:
`python -m datacore.serve.snapshots --as-of <день>`. Сбой снимка — сбой прогона, а не побочная заметка загрузчика.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

SNAPSHOT_COLUMNS = ("metric", "scope", "flow", "segment", "period_start", "period_end", "as_of", "value", "unit",
                    "status", "missing", "denominator_value", "taken_on",
                    "source_system", "source_class", "load_id", "loaded_at")
KEY = ("metric", "scope", "flow", "segment", "period_start", "period_end", "as_of")
LEGACY_NOTE = ("снимок снят до версии схемы 13: статус и дата съёма числа не сохранены, подпись — по дню прогона")
CONFIG = Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml"


def _months_back(today: date, count: int) -> list[tuple[date, date]]:
    """Последние полные месяцы плюс текущий: (начало, исключающая правая граница)."""
    out, year, month = [], today.year, today.month
    for _ in range(count + 1):
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1)
        out.append((start, end))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return out


def periods_to_snapshot(cfg, today: date, stage: int | None = None) -> list[tuple[str, str, date, date, str]]:
    """Что снимать: периоды известных ответов (всех этапов, если `stage` не задан) — чтобы эталоны воспроизводились
    буквально, — и последние месяцы по каждой метрике реестра — чтобы история копилась сама.
    Возвращает (метрика, кабинет, начало, конец, разрез)."""
    from datacore.checks.known import load_known_answers
    from datacore.serve.metrics import REGISTRY
    periods: list[tuple[str, str, date, date, str]] = []

    for metric in REGISTRY:
        if metric == "new_first_sql_by_channel":
            continue                       # разрезы снимаются только по известным ответам: групп каналов десятки
        for scope in cfg.scopes:
            for start, end in _months_back(today, 3):
                periods.append((metric, scope, start, end, ""))

    answers_file = cfg.duckdb_path.parent / "known_answers.yaml"
    if answers_file.exists():
        for a in load_known_answers(answers_file):
            if (stage is None or a.stage <= stage) and a.metric in REGISTRY:
                periods.append((a.metric, a.scope, a.period_start, a.period_end, a.segment))

    seen, unique = set(), []
    for p in periods:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


@dataclass
class SnapshotReport:
    """Итог шага снимков: сколько записано и прочитано обратно, сколько уже было, сколько не посчитано и почему."""
    taken_on: date
    periods: int
    saved: int = 0                  # новых строк записано и прочитано обратно
    kept: int = 0                   # снимок той даты съёма уже был — не перезаписан
    behind: int = 0                 # дата съёма числа старше дня прогона — источник отстаёт, не снимается
    no_data: int = 0                # «нет данных» — не снимается: снимок не место для выдумок
    refused: dict[str, int] = field(default_factory=dict)   # код правила → сколько чисел не строится

    def render(self) -> str:
        refused = ", ".join(f"{code} — {n}" for code, n in sorted(self.refused.items())) or "нет"
        return (f"снимки на {self.taken_on:%d.%m.%Y}: периодов {self.periods}; записано и прочитано обратно "
                f"{self.saved}, уже были {self.kept}, отстают от дня прогона {self.behind}, без данных {self.no_data}, "
                f"не строятся по правилу: {refused}")


def snapshot_run(engine, cfg, taken_on: date, periods=None, stage: int | None = None,
                 load_id: str | None = None) -> SnapshotReport:
    """Считает числа и сохраняет новые снимки. Число, которое по правилу не строится (нет кабинета, сложение без
    доказательства, нет разреза), пропускается с кодом правила в отчёте; любая другая ошибка поднимается — шаг
    падает с кодом возврата."""
    from datacore.schema.errors import RuleViolation
    from datacore.serve.metrics import REGISTRY, SOURCE_SYSTEM

    periods = periods if periods is not None else periods_to_snapshot(cfg, taken_on, stage)
    report = SnapshotReport(taken_on, len(periods))
    load_id = load_id or f"snapshot-{taken_on:%Y%m%d}"
    now = datetime.now(timezone.utc)
    existing = {tuple(r) for r in engine.fetchall(
        f"SELECT {', '.join(KEY)} FROM facts.snapshot_number WHERE source_system = ?", (SOURCE_SYSTEM,))}
    rows, new_keys = [], set()
    for metric, scope, start, end, segment in periods:
        kwargs = {"segment": segment} if segment else {}
        try:
            number = REGISTRY[metric](engine, cfg, scope, start, end, **kwargs)
        except RuleViolation as exc:
            report.refused[exc.code] = report.refused.get(exc.code, 0) + 1
            continue
        if number.value is None:
            report.no_data += 1
            continue
        if number.as_of != taken_on:
            report.behind += 1
            continue
        key = (metric, scope, number.flow, segment, start, end, number.as_of)
        if key in existing or key in new_keys:
            report.kept += 1
            continue
        new_keys.add(key)
        rows.append((*key[:6], number.as_of, float(number.value), number.unit, number.status.value,
                     number.missing or "", number.denominator_value, taken_on,
                     SOURCE_SYSTEM, number.source_class, load_id, now))
    if rows:
        marks = ", ".join("?" for _ in SNAPSHOT_COLUMNS)
        engine.executemany(
            f"INSERT INTO facts.snapshot_number ({', '.join(SNAPSHOT_COLUMNS)}) VALUES ({marks}) "
            "ON CONFLICT (metric, scope, flow, segment, period_start, period_end, as_of, source_system) DO NOTHING",
            rows)
        engine.commit()
        stored = {tuple(r) for r in engine.fetchall(
            f"SELECT {', '.join(KEY)} FROM facts.snapshot_number WHERE source_system = ? AND load_id = ?",
            (SOURCE_SYSTEM, load_id))}
        report.saved = len(new_keys & stored)
        if report.saved != len(new_keys):
            raise RuntimeError(f"снимки: записано {len(new_keys)}, прочитано обратно {report.saved} — молчаливый провал")
    return report


def take_snapshots(engine, cfg, as_of: date, periods=None, stage: int | None = None, load_id: str | None = None) -> int:
    """Снять снимки прогона `as_of`; возвращает число записанных строк."""
    return snapshot_run(engine, cfg, as_of, periods, stage, load_id).saved


def read_snapshot(engine, metric: str, scope: str, start: date, end: date, as_of: date,
                  segment: str = "") -> dict | None:
    """Снимок числа на дату съёма: значение, статус, пояснение, знаменатель. None — снимка той даты нет."""
    from datacore.serve.metrics import SOURCE_SYSTEM
    row = engine.fetchone(
        "SELECT value, status, missing, denominator_value, taken_on FROM facts.snapshot_number "
        "WHERE metric = ? AND scope = ? AND segment = ? AND period_start = ? AND period_end = ? AND as_of = ? "
        "AND source_system = ?",
        (metric, scope, segment, start, end, as_of, SOURCE_SYSTEM))
    if not row or row[0] is None:
        return None
    value, status, missing, denominator_value, taken_on = row
    return {"value": float(value), "status": status, "missing": missing or "",
            "denominator_value": None if denominator_value is None else float(denominator_value),
            "taken_on": taken_on}


def main(argv=None) -> int:
    """Шаг ночного прогона: снять снимки своих чисел. Код 1 — сбой или ни одного числа этой даты не снято и не было
    (все числа не строятся или отстают — загрузки дня не состоялись)."""
    from datacore.schema.config import load_config
    from datacore.schema.engine import connect
    from datacore.serve.storage import storage_url

    ap = argparse.ArgumentParser(description="Снимки своих чисел ядра на дату съёма")
    ap.add_argument("--as-of", required=True, type=date.fromisoformat, help="день прогона (дата загрузок)")
    ap.add_argument("--stage", type=int, default=None,
                    help="до какого этапа брать периоды известных ответов; по умолчанию — все этапы")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None, help="адрес базы; по умолчанию — из окружения, затем файл DuckDB")
    ap.add_argument("--dry-run", action="store_true", help="показать, что будет снято, и ничего не записывать")
    a = ap.parse_args(argv)

    cfg = load_config(Path(a.config) if a.config else CONFIG)
    periods = periods_to_snapshot(cfg, a.as_of, a.stage)
    if a.dry_run:
        print(f"к снятию на {a.as_of:%d.%m.%Y}: {len(periods)} периодов")
        for metric, scope, start, end, segment in periods[:20]:
            print(f"  {metric} · {scope}{' · ' + segment if segment else ''} · {start:%d.%m.%Y}–{end:%d.%m.%Y}")
        if len(periods) > 20:
            print(f"  … и ещё {len(periods) - 20}")
        return 0

    try:
        engine = connect(storage_url(cfg, a.db))
        try:
            report = snapshot_run(engine, cfg, a.as_of, periods=periods)
        finally:
            engine.close()
    except Exception as exc:                          # noqa: BLE001 — любой сбой шага виден кодом возврата
        print(f"🟥 снимки не сняты: {type(exc).__name__}: {exc}")
        return 1
    print(report.render())
    if periods and not (report.saved or report.kept):
        print("🟥 ни одного числа этого дня не снято и не было — числа не строятся или отстают от дня прогона")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
