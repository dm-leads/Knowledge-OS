"""Контракт операций (шаг 4 дорожной карты): что делает каждая команда машины — для навыка, людей и сервера MCP.

Контракт живёт в выпуске пакета — файл `contracts.yaml` рядом с кодом — и печатается командой `content-engine catalog`
того выпуска, которым идёт работа: навык и сервер читают смысл именно установленной версии, канон его не подменяет.
Схема аргументов берётся из парсера (обход подкоманд), смысл — из `contracts.yaml`. Тест пакета держит их вместе: у
каждой подкоманды есть контракт и нет лишних; эффект «платный вызов» ⇔ у подкоманды есть `--confirm-spend`; эффект,
который ждёт `--apply`, ⇔ у подкоманды есть `--apply`; у каждой операции с деньгами или записью названы тесты,
которые это доказывают (`proof`), и они существуют.

Эффекты — перечислением с условием (`when`), а не «платно/бесплатно»: `queue take` берёт выдачу из кэша бесплатно и
платит только при отказе основного источника.
"""
from __future__ import annotations

import argparse
from importlib import resources

import yaml

from .errors import EngineError

CATALOG_SCHEMA_VERSION = 1
EFFECTS = ("filesystem_read", "filesystem_write", "external_read", "external_write", "paid_external_call",
           "agent_task_required", "human_approval_required")
REQUIRED = ("operation_id", "name", "phrases", "module", "requires", "produces", "side_writes", "effects",
            "human", "exit_codes", "next")
OPTIONAL = ("agent_step", "price", "write", "secrets", "repeatable", "locks", "proof", "note", "input_constraints")
CODES = ("0", "1", "2", "3")
WRITE_FIELDS = ("right", "apply", "snapshot", "rollback")


def load() -> dict:
    text = resources.files("content_engine").joinpath("contracts.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    ops = data.get("operations") or {}
    if not isinstance(ops, dict):
        raise EngineError("contracts.yaml: operations — словарь «команда → контракт»")
    return ops


def validate(command: str, c: dict) -> list[str]:
    """Ошибки контракта одной операции: поля, эффекты, коды выхода."""
    problems = [f"{command}: нет поля {f}" for f in REQUIRED if f not in c]
    problems += [f"{command}: неизвестное поле {f}" for f in c if f not in REQUIRED + OPTIONAL]
    for e in c.get("effects") or []:
        if not isinstance(e, dict) or e.get("type") not in EFFECTS:
            problems.append(f"{command}: эффект {e!r} — нужен type из {EFFECTS}")
    for code in (c.get("exit_codes") or {}):
        if str(code) not in CODES:
            problems.append(f"{command}: код выхода {code} не из {CODES}")
    types = {e.get("type") for e in c.get("effects") or [] if isinstance(e, dict)}
    if "paid_external_call" in types and not c.get("price"):
        problems.append(f"{command}: платный вызов без поля price")
    if bool(c.get("human")) != ("human_approval_required" in types):
        problems.append(f"{command}: поле human заполняется тогда и только тогда, когда есть human_approval_required")
    if c.get("side_writes") and "filesystem_write" not in types:
        problems.append(f"{command}: побочная запись без эффекта filesystem_write")
    problems += [f"{command}: write — неизвестное поле {f}" for f in (c.get("write") or {}) if f not in WRITE_FIELDS]
    if not (c.get("phrases") or []):
        problems.append(f"{command}: нет фраз человека")
    return problems


def _arg(action) -> dict:
    kind = "boolean" if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)) else \
        "array" if isinstance(action, argparse._AppendAction) else \
        "number" if action.type in (int, float) else "string"
    out = {"type": kind, "description": action.help or ""}
    if action.choices:
        out["enum"] = list(action.choices)
    if action.default not in (None, argparse.SUPPRESS, False, []):
        out["default"] = action.default
    return out


def leaves(parser: argparse.ArgumentParser, path=()) -> list[tuple[str, argparse.ArgumentParser]]:
    """Подкоманды-листья парсера: («queue take», парсер)."""
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        return [(" ".join(path), parser)] if path else []
    out = []
    for action in subs:
        for name, sub in action.choices.items():
            out += leaves(sub, path + (name,))
    return out


def input_schema(parser: argparse.ArgumentParser) -> dict:
    """JSON Schema аргументов подкоманды из парсера."""
    props, required, positional = {}, [], []
    for a in parser._actions:
        if a.dest == "help" or isinstance(a, argparse._SubParsersAction):
            continue
        name = a.option_strings[-1].lstrip("-") if a.option_strings else a.dest
        props[name] = _arg(a)
        if not a.option_strings:
            positional.append(name)
            if a.nargs not in ("?", "*"):
                required.append(name)
        elif a.required:
            required.append(name)
    return {"type": "object", "properties": props, "required": required, "positional": positional}


def flags(parser: argparse.ArgumentParser) -> set[str]:
    return {s for a in parser._actions for s in a.option_strings}


def catalog() -> dict:
    """Каталог операций выпуска: контракт из `contracts.yaml` плюс схема аргументов из парсера."""
    from .. import __version__
    from ..cli import build_parser
    contracts = load()
    ops, missing = [], []
    for command, parser in leaves(build_parser()):
        c = contracts.get(command)
        if c is None:
            missing.append(command)
            continue
        ops.append({"command": command, **c, "input_schema": input_schema(parser)})
    extra = sorted(set(contracts) - {cmd for cmd, _ in leaves(build_parser())})
    return {"catalog_schema_version": CATALOG_SCHEMA_VERSION, "engine_version": __version__, "operations": ops,
            "missing": missing, "extra": extra}


def markdown(cat: dict) -> str:
    """Каталог для людей: таблица операций и карточка каждой — генерируется, руками не правится."""
    lines = [f"# Каталог операций Контент-машины — выпуск {cat['engine_version']}", "",
             f"Схема каталога: {cat['catalog_schema_version']}. Сгенерировано командой `content-engine catalog --md`; "
             "руками не править.", "", "| Команда | Операция | Модуль | Эффекты | ✋ |", "|---|---|---|---|---|"]
    for op in cat["operations"]:
        effects = ", ".join(sorted({e["type"] for e in op["effects"]}))
        approval = any(e["type"] == "human_approval_required" for e in op["effects"])
        lines.append(f"| `{op['command']}` | {op['name']} | {op['module']} | {effects} | {'да' if approval else '—'} |")
    for op in cat["operations"]:
        lines += ["", f"## `{op['command']}` — {op['name']}", "",
                  f"- id: `{op['operation_id']}`; модуль: {op['module']}",
                  "- фразы: " + "; ".join(f"«{p}»" for p in op["phrases"]),
                  f"- нужно до: {op['requires']}"]
        if op.get("input_constraints"):
            lines.append(f"- условия на аргументы: {op['input_constraints']}")
        lines += [f"- получится: {op['produces']}",
                  f"- побочная запись: {op['side_writes'] or 'нет'}"]
        for e in op["effects"]:
            lines.append(f"- эффект `{e['type']}`" + (f" — {e['when']}" if e.get("when") else "")
                         + (" (только с `--apply`)" if e.get("requires_apply") else ""))
        if op.get("price"):
            lines.append(f"- цена: {op['price']}")
        if op.get("write"):
            lines.append(f"- запись вовне: {op['write']}")
        if op.get("agent_step"):
            lines.append(f"- шаг агента: {op['agent_step']}")
        lines.append(f"- ✋ человек: {op['human'] or 'нет'}")
        if op.get("note"):
            lines.append(f"- примечание: {op['note']}")
        if op.get("secrets"):
            lines.append("- секреты (имена переменных): " + ", ".join(op["secrets"]))
        lines.append("- коды выхода: " + "; ".join(f"{k} — {v}" for k, v in op["exit_codes"].items()))
        lines.append("- дальше: " + (", ".join(f"`{n}`" for n in op["next"]) or "—"))
        args = op["input_schema"]["properties"]
        if args:
            lines.append("- аргументы: " + ", ".join(f"`{k}`" for k in args))
    return "\n".join(lines) + "\n"
