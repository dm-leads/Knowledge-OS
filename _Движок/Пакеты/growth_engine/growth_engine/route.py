"""Черновик маршрута цикла (этап 4, задача 4.7): цель × форма бизнеса × карта источников → «Маршрут цикла».

Печатает черновик для согласования с владельцем; согласованный маршрут пишется в хранилище любым ключом общего выбора
хранилища (`--out`, книга — после «да» владельца книги) и читается обратно. Класс главной
метрики — `--main-class` или класс системы денежной модели (роль `economy.money_system`), если цель — метрика
денежной модели. Продажи ли цель — по роли `sales` денежной модели или из `--sales-goal`. Любой страж — код
возврата 1.

Запуск из Скрипты/:
  py -3 -m growth_engine.route --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --source-map "../Планирование/Движок роста/Карта источников.yaml" --task "объём лидов" --cycle Ц-1
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from .core.config import parse_config
from .core.errors import GuardViolation
from .core.router import STEP_CLASSES, route_draft
from .core.source_map import class_status, load_source_map
from .storage.selection import STORE_KEYS_TEXT, add_store_arguments, given_store_keys, open_store


def main_metric_class(raw: dict, goal_metric: str, override: str | None) -> str:
    """Класс источника главной метрики: явно из аргумента или из роли системы денежной модели."""
    if override:
        return override
    economy = raw.get("economy") or {}
    system = economy.get("money_system")
    if not system or goal_metric not in (economy.get("money_metrics") or {}).values():
        raise GuardViolation(9, f"класс главной метрики не определён: цель «{goal_metric}» не из денежной модели — "
                                "укажите --main-class")
    section = (raw.get("sources") or {}).get(system) or {}
    if "class" not in section:
        raise GuardViolation(9, f"система денежной модели «{system}» не описана в sources с классом источника")
    return section["class"]


def goal_is_sales(raw: dict, goal_metric: str, declared: str | None) -> bool:
    """Продажи ли цель цикла: по роли `sales` денежной модели, если цель — её метрика; иначе — только объявлено явно."""
    roles = (raw.get("economy") or {}).get("money_metrics") or {}
    stated = None if declared is None else declared == "да"
    if goal_metric in roles.values():
        derived = roles.get("sales") == goal_metric
        if stated is not None and stated != derived:
            raise GuardViolation(9, f"цель «{goal_metric}» по ролям денежной модели — "
                                    f"{'продажи' if derived else 'не продажи'}, а --sales-goal = {declared}")
        return derived
    if stated is None:
        raise GuardViolation(9, f"не определено, продажи ли цель «{goal_metric}»: её нет в ролях денежной модели — "
                                "укажите --sales-goal да или нет")
    return stated


def run(args, out=print, today=date.today, bridge_factory=None, google_factory=None) -> int:
    try:
        return _run(args, out, today, bridge_factory, google_factory)
    except GuardViolation as exc:
        out(f"❌ маршрут не построен: {exc}")
        return 1


def _run(args, out, today, bridge_factory, google_factory) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    status = class_status(load_source_map(args.source_map))
    main = main_metric_class(raw, cfg.goal_metric, args.main_class)
    sales = goal_is_sales(raw, cfg.goal_metric, args.sales_goal)
    route = route_draft(args.cycle, f"окно {cfg.goal_metric}", args.task, cfg.business_form, status, main, today(),
                        sales)
    out(f"Маршрут цикла {route.cycle_id} — черновик от {route.written_on:%d.%m.%Y}; цель: {route.goal}; "
        f"бизнес-задача: {args.task}; класс главной метрики: {route.main_class}; цель — продажи: "
        f"{'да' if route.sales_goal else 'нет'}")
    for s in route.steps:
        name = STEP_CLASSES[s.step].name
        if not s.classes:
            out(f"шаг {s.step} «{name}»: пропуск — {s.skipped_reason}")
            continue
        parts = [", ".join(f"{cls} — {decision}" for cls, decision in s.classes.items())]
        parts += [f"{label}: {text}" for label, text in (("заменитель", s.substitute),
                                                         ("цена добычи", s.extraction_cost),
                                                         ("почему нет данных", s.no_data_reason)) if text]
        out(f"шаг {s.step} «{name}»: " + "; ".join(parts))
    for note in route.notes:
        out(f"• {note}")
    out(f"ожидаемая уверенность вывода: {route.expected_confidence}")
    if not given_store_keys(args):
        out(f"маршрут не записан: черновик согласуется с владельцем; согласованный маршрут пишется ключом "
            f"{STORE_KEYS_TEXT}")
        return 0
    with open_store(args, cfg.storage_link_domains, today, bridge_factory, google_factory) as (store, label):
        report, = store.create(route, class_status=status)
        stored = store.read("routes", class_status=status, cycle_id=route.cycle_id)
    out(f"хранилище: {label}; «{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")
    same = stored == [route]
    out(f"ИТОГ: {'маршрут записан и прочитан' if same else 'маршрут в хранилище НЕ совпадает с черновиком'}")
    return 0 if same else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Черновик маршрута цикла; согласованный маршрут — в хранилище")
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-map", required=True)
    parser.add_argument("--task", required=True, help="бизнес-задача из библиотеки стартовых маршрутов")
    parser.add_argument("--cycle", required=True, help="идентификатор цикла, например Ц-1")
    parser.add_argument("--main-class", default=None,
                        help="класс источника главной метрики A–F, если цель не из денежной модели")
    parser.add_argument("--sales-goal", choices=("да", "нет"), default=None,
                        help="продажи ли цель цикла, если цель не из ролей денежной модели")
    add_store_arguments(parser, required=False)
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
