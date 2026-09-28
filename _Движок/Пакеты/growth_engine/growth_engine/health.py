"""Гейт инстанса Движка роста (этап 6, страж 13): одна команда перед каждым циклом, только чтение.

Разделы по порядку: конфигурация → карта источников → источники (пробы) → известные ответы → хранилище → модель →
пересмотры → реестр → листы хранилища → лист карты источников. Каждая строка — номер стража, итог и что проверено;
логику проверок держит `core/health.py`. Каждый раздел ловит свои нарушения, поэтому один прогон показывает все.

- Известные ответы (`health.known_answers`): адаптер обязан воспроизводить сверенное раньше число; вне допуска — 🟥
  страж 2, а если адаптер умеет — с номерами записей окна, изменённых после даты сверки. Раздела нет — 🟧.
- Пересмотры (`health.revisions`): число роли денежной модели за последние закрытые месяцы — сегодняшний съём денежной
  системы против последнего более раннего съёма в хранилище (Т6, запаздывание флага New First). Раздела нет — 🟧;
  отключить можно только явно: `off: <причина>`.
- Хранилище читается из конфигурации (`storage.adapter: lark_sheets` и `storage.book`) или из папки markdown (`--out`);
  не прочитано хранилище — модель, пересмотры, реестр и листы помечаются «не проверено», а не «чисто».

Гейт ничего не пишет: ни в источники, ни в хранилище, ни в файлы. Платная проба и платный известный ответ без
`--confirm-paid` пропускаются и код не валят. Каждая строка вывода проходит чистку: строка запроса ссылок, адреса почты и
длинные строки с буквами и цифрами скрываются. Код возврата 0 — нет ни одной строки 🟥 структуры или 🟧 покрытия.

Запуск из Скрипты/:
  py -3 -m growth_engine.health --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env"
"""
from __future__ import annotations

import argparse
import re
import sys
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

import yaml

from .adapters import FACTORIES
from .core.arithmetic import add
from .core.config import parse_config
from .core.economy import Tolerance
from .core.errors import GuardViolation
from .core.health import (Check, Outcome, check_artifacts, check_known_answer, check_model, check_registry,
                          check_revisions, check_source_map, check_sources_sheet, closed_months, summary, verdict,
                          violation)
from .core.source_map import class_status, load_source_map
from .core.storage import SHEETS
from .probes import probe_source
from .sources.base import Query, SourceError, load_secrets
from .sources_sheet import env_names
from .storage.selection import open_store, store_target

SECTION_CONFIG = "конфигурация"
SECTION_MAP = "карта источников"
SECTION_PROBES = "источники"
SECTION_KNOWN = "известные ответы"
SECTION_STORE = "хранилище"
SECTION_MODEL = "модель"
SECTION_TAKES = "пересмотры"
SECTION_REGISTRY = "реестр"
SECTION_ARTIFACTS = "артефакты"
AFTER_STORE = (SECTION_MODEL, SECTION_TAKES, SECTION_REGISTRY, SECTION_ARTIFACTS)
KNOWN_KEYS = ("system", "metric", "scope", "flow", "period", "expected", "abs_units", "rel", "checked")
REVISION_KEYS = ("role", "months", "abs_units", "rel")
CHANGED_SHOWN = 30
_QUERY = re.compile(r"\?[^\s\"'<>)]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}"
                    r"(?![A-Za-z0-9_\-])")


def safe(text: str) -> str:
    """Строка вывода гейта без строки запроса ссылок, адресов почты и длинных строк с буквами и цифрами (похожих на
    токены); имена ключей конфигурации из букв и подчёркиваний не трогаются."""
    return _TOKEN.sub("…", _EMAIL.sub("…", _QUERY.sub("?…", text)))


def _rows(store, sheet: str) -> int | None:
    if store.backend.header(sheet) is None:
        return None
    return sum(1 for row in store.backend.read_rows(sheet) if row.get("id", "") != "")


def _period(entry: dict) -> tuple[date, date]:
    start, end = (date.fromisoformat(str(value)) for value in entry["period"])
    return start, end


def _one(numbers, what: str):
    """Единственное число ответа источника; пустой ответ — покрытие, а не падение гейта."""
    if not numbers:
        raise GuardViolation(13, f"{what}: источник вернул пустой ответ — числа нет", GuardViolation.COVERAGE)
    return numbers[0]


def run(args, out=print, today=date.today, factories=FACTORIES, secrets=None, bridge_factory=None) -> int:
    checks = []

    def emit(*new):
        for check in new:
            checks.append(check)
            out(safe(check.render()))

    def section(name, produce):
        try:
            emit(*produce())
        except GuardViolation as exc:
            emit(violation(name, exc))
        except SourceError as exc:
            emit(Check(name, 13, Outcome.COVERAGE, f"источник не ответил — {exc}"))

    day = today()
    try:
        raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
        cfg = parse_config(raw)
        sources = raw["sources"]
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError, GuardViolation) as exc:
        emit(Check(SECTION_CONFIG, 13, Outcome.STRUCTURE,
                   f"конфигурация не читается ({type(exc).__name__}: {str(exc)[:200]}) — адаптеры не собрать"))
        out(summary(checks))
        return 1
    health = raw.get("health") or {}
    economy = raw.get("economy") or {}
    confirm_paid = bool(getattr(args, "confirm_paid", False))
    out(f"Гейт инстанса «{raw.get('company', '—')}» — {day:%d.%m.%Y}")
    emit(Check(SECTION_CONFIG, 13, Outcome.PASSED, f"прочитана: источников {len(sources)}, метрик {len(cfg.metrics)}"))

    adapters = {}

    def adapter(name):
        if name not in adapters:
            if name not in sources:
                raise GuardViolation(9, f"система «{name}» не объявлена в sources конфигурации")
            if name not in factories:
                raise GuardViolation(9, f"для системы «{name}» в движке нет адаптера")
            part = sources[name]
            source_secrets = secrets if secrets is not None else load_secrets(args.secrets, part.get("secret_env", []))
            adapters[name] = factories[name](part, cfg, source_secrets, confirm_paid)
        return adapters[name]

    # --- карта источников ---
    entries = None

    def source_map_checks():
        nonlocal entries
        if not health.get("source_map"):
            raise GuardViolation(9, "в разделе health конфигурации не указан файл карты источников (source_map)")
        entries = load_source_map(Path(args.config).parent / health["source_map"])
        configured = {name: str((part or {}).get("class", "")) for name, part in sources.items()}
        return check_source_map(entries, configured, env_names(args.secrets))

    try:
        section(SECTION_MAP, source_map_checks)
    except (OSError, yaml.YAMLError) as exc:
        emit(Check(SECTION_MAP, 13, Outcome.STRUCTURE, f"карта источников не читается ({type(exc).__name__})"))

    # --- источники ---
    probe_args = Namespace(secrets=args.secrets, confirm_paid=confirm_paid)
    for name, part in sources.items():
        if part.get("paid") and not confirm_paid:
            emit(Check(SECTION_PROBES, 13, Outcome.SKIPPED, f"{name}: проба платная — не выполнена без --confirm-paid"))
            continue
        for result in probe_source(name, part, cfg, factories, secrets, probe_args):
            status = "" if result.http_status is None else f" · HTTP {result.http_status}"
            hint = f" — синхронизируйте: {part['sync_command']}" if not result.ok and part.get("sync_command") else ""
            emit(Check(SECTION_PROBES, 13, Outcome.PASSED if result.ok else Outcome.COVERAGE,
                       f"{result.source}{status} · {result.detail}{hint}"))

    # --- известные ответы ---
    def known_answers():
        declared = health.get("known_answers") or []
        if not declared:
            return [Check(SECTION_KNOWN, 13, Outcome.COVERAGE,
                          "в разделе health конфигурации нет известных ответов — адаптеры не сверены с проверенными "
                          "числами")]
        results = []
        for entry in declared:
            label = f"{entry.get('system', '—')} · {entry.get('metric', '—')}"
            missing = [key for key in KNOWN_KEYS if key not in entry]
            if missing:
                results.append(Check(SECTION_KNOWN, 9, Outcome.STRUCTURE,
                                     f"{label}: в известном ответе не задано: {', '.join(missing)}"))
                continue
            if (sources.get(entry["system"]) or {}).get("paid") and not confirm_paid:
                results.append(Check(SECTION_KNOWN, 13, Outcome.SKIPPED,
                                     f"{label}: система платная — не сверено без --confirm-paid"))
                continue
            try:
                start, end = _period(entry)
                query = Query(metric=entry["metric"], scope=str(entry["scope"]), flow=entry["flow"], period_start=start,
                              period_end=end, breakdown=None, as_of=day)
                source = adapter(entry["system"])
                check = check_known_answer(_one(source.fetch(query), label), float(entry["expected"]),
                                           Tolerance(float(entry["abs_units"]), float(entry["rel"])), str(entry["checked"]))
                if check.outcome is Outcome.STRUCTURE and entry.get("changes_since") and hasattr(source, "changed_since"):
                    since = date.fromisoformat(str(entry["changes_since"]))
                    changed = source.changed_since(query, since)
                    shown = ", ".join(map(str, changed[:CHANGED_SHOWN])) + (" …" if len(changed) > CHANGED_SHOWN else "")
                    check = replace(check, text=f"{check.text}; записей окна, изменённых с {since:%d.%m.%Y}: "
                                                f"{len(changed)}" + (f" — номера: {shown}" if changed else ""))
                results.append(check)
            except GuardViolation as exc:
                results.append(violation(SECTION_KNOWN, exc, label))
            except SourceError as exc:
                results.append(Check(SECTION_KNOWN, 13, Outcome.COVERAGE, f"{label}: источник не ответил — {exc}"))
            except (ValueError, TypeError) as exc:
                results.append(Check(SECTION_KNOWN, 9, Outcome.STRUCTURE,
                                     f"{label}: известный ответ записан неверно ({type(exc).__name__})"))
        return results

    section(SECTION_KNOWN, known_answers)

    # --- хранилище ---
    try:
        with open_store(store_target(args, raw), cfg.storage_link_domains, lambda: day, bridge_factory) as (store, label):
            sheets = {sheet: _rows(store, sheet) for sheet in SHEETS.values()}
            emit(Check(SECTION_STORE, 13, Outcome.PASSED,
                       f"{label}: листов прочитано {sum(rows is not None for rows in sheets.values())} из {len(sheets)}"))
            cache = {}

            def stored_numbers():
                if "numbers" not in cache:
                    cache["numbers"] = store.read("numbers")
                return cache["numbers"]

            def model_checks():
                if "model_snapshot_max_days" not in health:
                    raise GuardViolation(9, "в разделе health конфигурации нет предела возраста снимка модели "
                                            "(model_snapshot_max_days)")
                if not economy.get("money_system"):
                    raise GuardViolation(9, "в разделе economy конфигурации не указана денежная система (money_system)")
                return check_model(stored_numbers(), economy.get("money_metrics") or {}, cfg, day,
                                   int(health["model_snapshot_max_days"]), str(economy["money_system"]))

            def revision_checks():
                spec = health.get("revisions")
                if not spec:
                    return [Check(SECTION_TAKES, 13, Outcome.COVERAGE,
                                  "в разделе health конфигурации нет пересмотров (revisions) — пересмотры задним числом "
                                  "не проверены; отключить можно только явно: off с причиной")]
                if spec.get("off"):
                    return [Check(SECTION_TAKES, 13, Outcome.NOT_CHECKED,
                                  f"пересмотры отключены правилом конфигурации: {spec['off']}")]
                missing = [key for key in REVISION_KEYS if key not in spec]
                roles = economy.get("money_metrics") or {}
                system = economy.get("money_system")
                if missing or not system or spec.get("role") not in roles:
                    raise GuardViolation(9, "пересмотры: не задано — " + (", ".join(missing) if missing else
                                                                          "денежная система или роль в economy"))
                scopes = list((sources.get(system) or {}).get("scopes") or [])
                if not scopes:
                    raise GuardViolation(9, f"пересмотры: у системы «{system}» не объявлены кабинеты (scopes)")
                fresh = []
                for start, end in closed_months(day, int(spec["months"])):
                    parts = [_one(adapter(system).fetch(Query(metric=roles[spec["role"]], scope=scope, flow="all",
                                                              period_start=start, period_end=end, breakdown=None,
                                                              as_of=day)), f"{system} · {roles[spec['role']]}")
                             for scope in scopes]
                    fresh.append(add(parts, cfg) if len(parts) > 1 else parts[0])
                return check_revisions(stored_numbers(), fresh, Tolerance(float(spec["abs_units"]), float(spec["rel"])))

            def registry_checks():
                if entries is None:
                    routes, statuses = [], {}
                    emit(Check(SECTION_REGISTRY, 9, Outcome.NOT_CHECKED, "маршруты: карта источников не прочитана"))
                else:
                    routes, statuses = store.read("routes", verify=False), class_status(entries)
                return check_registry(store.read("hypotheses", verify=False), store.read("knowledge"),
                                      store.read("decisions"), store.read("trees", verify=False), routes, statuses, day)

            section(SECTION_MODEL, model_checks)
            section(SECTION_TAKES, revision_checks)
            section(SECTION_REGISTRY, registry_checks)
            emit(*check_artifacts(sheets))
            if entries is not None and sheets[SHEETS["sources"]] is not None:
                section(SECTION_MAP, lambda: [check_sources_sheet(entries, store.read("sources"))])
    except (GuardViolation, OSError) as exc:
        if isinstance(exc, GuardViolation):
            emit(violation(SECTION_STORE, exc))
        else:
            emit(Check(SECTION_STORE, 13, Outcome.COVERAGE, f"хранилище не открыто ({type(exc).__name__})"))
        emit(*(Check(name, 13, Outcome.NOT_CHECKED, "хранилище не прочитано — см. строку раздела «хранилище»")
               for name in AFTER_STORE))

    out(summary(checks))
    return verdict(checks)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Гейт инстанса Движка роста: проверки перед циклом, только чтение")
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True, help="файл секретов: пробы источников и сверка имён переменных")
    parser.add_argument("--out", default=None, help="проверить markdown-хранилище вместо хранилища из конфигурации")
    parser.add_argument("--confirm-paid", action="store_true", help="выполнить и платные пробы")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
