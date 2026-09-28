"""Адаптер Ядра данных (класс D): числа читаются из собственного хранилища фактов, а не из витрины поставщика.

Чем этот источник отличается от остальных: он **не ходит по сети**. Ядро — отдельная система со своим хранилищем,
и адаптер открывает её базу только на чтение. Поэтому здесь нет ни белого списка эндпоинтов, ни пауз между
запросами, ни секретов — нечему утекать и нечего ограничивать.

Второе отличие: ядро уже отдаёт числа с происхождением, статусом и датой съёма — тем же контрактом, что требует
движок. Адаптер поэтому тонкий: он переводит запрос в вызов метрики ядра и переносит поля один в один, ничего не
пересчитывая. Пересчёт здесь был бы вторым определением метрики, а их должно быть ровно одно (Я12).

⚠️ Ядро подписывает число датой съёма СВОИХ фактов, и она обычно старше даты прогона движка: загрузки идут своим
расписанием. Число с чужой датой съёма подменять нельзя (П3), поэтому адаптер отдаёт дату ядра и называет разрыв
в пояснении — иначе движок сложил бы числа разных съёмов молча.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, SourceError

SYSTEM = "datacore"

# Статусы ядра и движка называются одинаково, но это разные перечисления: соответствие задаётся явно, чтобы
# переименование в одной системе ломало здесь, а не тихо превращало «нет данных» в «факт».
_STATUS = {"факт": Status.FACT, "оценка": Status.ESTIMATE, "proxy": Status.PROXY, "нет данных": Status.NO_DATA}



def _denominator_note(row_value, denominator: str | None, denominator_value, unit: str = "") -> str:
    """Знаменатель числа словами. У числа движка нет поля под его величину, а без неё доля непроверяема:
    «23,6 %» ничего не говорит, «439 из 1 857 лидов» проверяется умножением (ревью Codex этапа 5, п.4).

    У доли пишется «часть из целого», у среднего — «на сколько поделено». Для среднего «часть из целого»
    было бы бессмыслицей: прибыль на лид, умноженная на лиды, даёт прибыль, а не долю."""
    if not denominator_value or row_value is None:
        return ""
    whole = f"{denominator_value:,.0f}".replace(",", " ")
    if unit == "доля":
        part = f"{row_value * denominator_value:,.0f}".replace(",", " ")
        return f"знаменатель: {part} из {whole} {denominator or ''}".strip()
    return f"знаменатель: на {whole} {denominator or ''}".strip()


class DatacoreAdapter:
    """Числа Ядра данных как источник движка.

    `section` конфигурации: `path_root` — папка `Скрипты/` ядра, `metrics` — какие метрики ядра отвечают на какие
    метрики движка, `scope_map` — соответствие кабинетов, если имена различаются."""

    def __init__(self, section: dict, cfg=None, secrets: dict | None = None, storage_url: str | None = None):
        self.section = section
        self.cfg = cfg
        self._storage_url = storage_url
        self._core = None
        self._published = None

    # --- чтение опубликованного файла ----------------------------------------
    #
    # Основной способ. Ядро живёт своим окружением (DuckDB, dlt, SQLMesh), и тянуть его зависимости в движок
    # значило бы склеить две системы в одну. Вместо этого ядро публикует числа файлом тем же контрактом, что
    # отдаёт своей команде `ask`, а движок читает файл — без единой общей библиотеки.
    #
    # Файл — снимок на дату съёма, а не живая связь: число, подписанное датой, нельзя незаметно пересчитать
    # задним числом, и потребитель всегда видит, насколько оно свежее.

    def _load_published(self) -> dict:
        if self._published is not None:
            return self._published
        path = Path(self.section["published"])
        if not path.exists():
            raise SourceError(f"файл публикации ядра не найден: {path}; "
                              f"ядро публикует его командой `python -m datacore.serve.publish --out <файл>`")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceError(f"файл публикации ядра не читается: {type(exc).__name__}: {exc}") from exc
        index = {}
        for row in payload.get("numbers") or []:
            key = (row.get("metric"), row.get("scope"), row.get("segment") or "",
                   row.get("period_start"), row.get("period_end"))
            index[key] = row
        self._published = {"payload": payload, "index": index}
        return self._published

    def _from_published(self, query: Query, name: str) -> Number:
        got = self._load_published()
        key = (name, self._scope_name(query.scope), query.breakdown or "",
               query.period_start.isoformat(), query.period_end.isoformat())
        row = got["index"].get(key)
        if row is None:
            published_at = (got["payload"].get("published_at") or "")[:10]
            raise SourceError(f"в публикации ядра нет числа «{name}» для «{query.scope}» за "
                              f"{query.period_start}–{query.period_end}"
                              + (f" (файл собран {published_at})" if published_at else ""))
        return self._row_to_number(row, query, name)

    def _row_to_number(self, row: dict, query: Query, name: str) -> Number:
        """Строка публикации → число движка. Поля переносятся один в один, пересчёта нет."""
        status = _STATUS.get(row.get("status"))
        if status is None:
            raise SourceError(f"статус «{row.get('status')}» в публикации ядра не из канона движка")
        as_of = date.fromisoformat(row["as_of"]) if row.get("as_of") else query.as_of
        notes = [row["missing"]] if row.get("missing") else []
        share_note = _denominator_note(row.get("value"), row.get("denominator"),
                                      row.get("denominator_value"), row.get("unit") or "")
        if share_note:
            notes.insert(0, share_note)
        if as_of != query.as_of:
            notes.append(f"дата съёма ядра {as_of:%d.%m.%Y}, прогон движка {query.as_of:%d.%m.%Y}")
        return Number(metric=query.metric, level=row.get("level") or "money", scope=query.scope,
                      flow=query.flow, period_start=query.period_start, period_end=query.period_end,
                      source=f"D:{SYSTEM}:{name}", status=status, value=row.get("value"),
                      denominator=row.get("denominator"), unit=row.get("unit") or "шт",
                      segment=row.get("segment") or "", as_of=as_of, missing="; ".join(notes))

    # --- прямой доступ к базе ядра -------------------------------------------
    #
    # Запасной способ: работает, только если движок запущен в окружении с зависимостями ядра. Нужен для отладки
    # и для случая, когда число требуется свежее последней публикации.

    def _load_core(self):
        """Модули ядра с диска. Ядро живёт своей папкой и в зависимостях движка не числится: это отдельная
        система, а не библиотека."""
        if self._core is not None:
            return self._core
        root = Path(self.section["path_root"]).resolve()
        if not (root / "datacore").is_dir():
            raise SourceError(f"ядро данных не найдено: {root / 'datacore'}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from datacore.schema.config import load_config
            from datacore.schema.engine import connect
            from datacore.serve.metrics import REGISTRY
        except ImportError as exc:
            raise SourceError(f"ядро данных не загружается: {exc}") from exc
        config_path = Path(self.section["config"]) if self.section.get("config") else \
            root.parent / "Ядро данных" / "Конфигурация инстанса.yaml"
        core_cfg = load_config(config_path)
        url = self._storage_url or f"duckdb:///{core_cfg.duckdb_path.as_posix()}"
        self._core = (REGISTRY, core_cfg, connect, url)
        return self._core

    def _metric_name(self, metric: str) -> str:
        """Метрика ядра, отвечающая на метрику движка. Соответствие живёт в конфигурации, а не в коде: имена —
        часть инстанса, и адаптер их не знает (граница К9 ядра, страж 9 движка)."""
        names = self.section.get("metrics") or {}
        if metric not in names:
            raise GuardViolation(9, f"метрика «{metric}» не объявлена в соответствии с метриками ядра")
        return names[metric]

    def _scope_name(self, scope: str) -> str:
        return (self.section.get("scope_map") or {}).get(scope, scope)

    # --- выдача чисел --------------------------------------------------------

    def fetch(self, query: Query) -> list[Number]:
        name = self._metric_name(query.metric)
        if self.section.get('published'):
            return [self._from_published(query, name)]
        registry, core_cfg, connect, url = self._load_core()
        fn = registry.get(name)
        if fn is None:
            raise GuardViolation(9, f"метрики «{name}» нет в реестре ядра; есть: {', '.join(sorted(registry))}")
        # Разрез передаётся ядру как есть: язык разрезов у них общий («измерение=значение»).
        kwargs = {"segment": query.breakdown} if query.breakdown else {}
        engine = connect(url, read_only=True)
        try:
            got = fn(engine, core_cfg, self._scope_name(query.scope), query.period_start, query.period_end,
                     **kwargs)
        except Exception as exc:
            # Отказ ядра — это отказ источника, а не «данных нет»: разница важна для стража 9.
            raise SourceError(f"{SYSTEM}:{name}: {type(exc).__name__}: {exc}") from exc
        finally:
            engine.close()
        return [self._translate(got, query, name)]

    def _translate(self, got, query: Query, name: str) -> Number:
        """Число ядра → число движка. Поля переносятся один в один; пересчёта здесь нет и быть не может."""
        status = _STATUS.get(getattr(got.status, "value", got.status))
        if status is None:
            raise SourceError(f"статус ядра «{got.status}» не из канона движка")
        notes = [got.missing] if got.missing else []
        share_note = _denominator_note(got.value, got.denominator,
                                      getattr(got, "denominator_value", None), got.unit or "")
        if share_note:
            notes.insert(0, share_note)
        # Дата съёма ядра старше прогона движка — обычное дело, но молчать о ней нельзя: иначе числа разных
        # съёмов сложатся незаметно (П3).
        if got.as_of and got.as_of != query.as_of:
            notes.append(f"дата съёма ядра {got.as_of:%d.%m.%Y}, прогон движка {query.as_of:%d.%m.%Y}")
        return Number(metric=query.metric, level=got.level, scope=query.scope, flow=query.flow,
                      period_start=query.period_start, period_end=query.period_end,
                      source=f"D:{SYSTEM}:{name}", status=status, value=got.value,
                      denominator=got.denominator, unit=got.unit, segment=got.segment or "",
                      as_of=got.as_of or query.as_of, missing="; ".join(notes))

    def totals(self, scope: str, metrics, start: date, end: date) -> dict:
        """Итоги нескольких метрик за окно. Каждая метрика считается своим определением — складывать их нельзя,
        и адаптер этого не делает."""
        if self.section.get('published'):
            got = self._load_published()
            return {metric: (got['index'].get((self._metric_name(metric), self._scope_name(scope), '',
                                               start.isoformat(), end.isoformat())) or {}).get('value')
                    for metric in metrics}
        registry, core_cfg, connect, url = self._load_core()
        out = {}
        engine = connect(url, read_only=True)
        try:
            for metric in metrics:
                name = self._metric_name(metric)
                fn = registry.get(name)
                if fn is None:
                    raise GuardViolation(9, f"метрики «{name}» нет в реестре ядра")
                got = fn(engine, core_cfg, self._scope_name(scope), start, end)
                out[metric] = got.value
        finally:
            engine.close()
        return out

    # --- проба ---------------------------------------------------------------

    def probe(self) -> list[ProbeResult]:
        """Доступно ли ядро и отвечает ли оно числом. Проба не выдумывает период: берётся прошлый полный месяц."""
        if self.section.get('published'):
            try:
                got = self._load_published()
            except SourceError as exc:
                return [ProbeResult(SYSTEM, False, None, str(exc))]
            payload, index = got['payload'], got['index']
            no_data = sum(1 for r in index.values() if r.get('value') is None)
            return [ProbeResult(SYSTEM, len(index) > 0, None,
                                f"публикация «{payload.get('instance')}» от "
                                f"{(payload.get('published_at') or '')[:10]}: чисел {len(index)}, "
                                f"без данных {no_data}, съём фактов {payload.get('as_of') or 'не указан'}")]
        try:
            registry, core_cfg, connect, url = self._load_core()
        except SourceError as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        today = date.today()
        end = date(today.year, today.month, 1)
        start = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
        results = []
        for scope in (self.section.get("scopes") or [next(iter(core_cfg.scopes), "company")]):
            try:
                engine = connect(url, read_only=True)
                try:
                    name = self._metric_name(next(iter(self.section.get("metrics") or {"": ""})))
                    got = registry[name](engine, core_cfg, self._scope_name(scope), start, end)
                finally:
                    engine.close()
            except Exception as exc:
                results.append(ProbeResult(SYSTEM, False, None, f"{scope}: {type(exc).__name__}: {exc}"))
                continue
            ok = got.value is not None
            results.append(ProbeResult(SYSTEM, ok, None,
                                       f"{scope}: {name} за {start:%m.%Y} = {got.value} ({got.status.value})"
                                       if ok else f"{scope}: {got.missing or 'нет данных'}"))
        return results
