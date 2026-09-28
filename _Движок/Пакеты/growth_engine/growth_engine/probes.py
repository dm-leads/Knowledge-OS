"""Живые пробы всех источников инстанса одной командой (ворота этапа 2; основа гейта этапа 6).

Для каждого раздела sources конфигурации создаётся адаптер и вызывается его probe(). Платная проба без
--confirm-paid не выполняется и печатается как пропущенная. Код возврата 0 — все выполненные пробы успешны.
Секреты в выводе — только имена переменных.

Запуск из Скрипты/:
  py -3 -m growth_engine.probes --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" [--confirm-paid]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .adapters import FACTORIES
from .core.config import parse_config
from .sources.base import ProbeResult, SourceError, load_secrets


def _failure(name: str, what: str, exc: Exception) -> ProbeResult:
    # Текст SourceError и KeyError несёт только имена переменных; у прочих исключений печатается лишь тип.
    detail = str(exc) if isinstance(exc, (SourceError, KeyError)) else type(exc).__name__
    return ProbeResult(name, False, None, f"{what}: {detail}")


def probe_source(name, section, cfg, factories, secrets, args) -> list[ProbeResult]:
    """Пробы одного источника конфигурации; сбой сборки адаптера или пробы — неуспешный результат, а не исключение."""
    factory = factories.get(name)
    if factory is None:
        return [ProbeResult(name, False, None, "в движке нет адаптера для этого источника")]
    try:
        source_secrets = secrets if secrets is not None else load_secrets(args.secrets, section.get("secret_env", []))
        adapter = factory(section, cfg, source_secrets, args.confirm_paid)
    except Exception as exc:   # один несобранный адаптер не останавливает пробы остальных
        return [_failure(name, "адаптер не создан", exc)]
    try:
        return adapter.probe()
    except Exception as exc:
        return [_failure(name, "проба упала", exc)]


def run(args, out=print, factories=FACTORIES, secrets=None) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    results, skipped = [], []
    for name, section in raw["sources"].items():
        if section.get("paid") and not args.confirm_paid:
            skipped.append(name)
            out(f"⏭ {name}: проба платная — не выполнена без --confirm-paid")
            continue
        for result in probe_source(name, section, cfg, factories, secrets, args):
            results.append(result)
            status = "" if result.http_status is None else f" · HTTP {result.http_status}"
            out(f"{'✅' if result.ok else '❌'} {result.source}{status} · {result.detail}")
    passed = sum(result.ok for result in results)
    out(f"проб {len(results)}, успешных {passed}, пропущено платных {len(skipped)}")
    return 0 if results and passed == len(results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Живые пробы всех источников инстанса Движка роста")
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--confirm-paid", action="store_true", help="выполнить и платные пробы")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
