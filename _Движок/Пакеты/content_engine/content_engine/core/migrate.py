"""Перевод профиля на одну версию вверх (Т15.3): `content-engine profile migrate --profile <путь> [--apply]`.

YAML не пересобирается — иначе пропали бы комментарии и порядок, по которым проект калибровал профиль. Новые строки
берутся из шаблона пакета и вставляются текстом тремя видами:
- новый раздел верхнего уровня — блок шаблона в конец файла;
- новое поле существующего раздела — строки шаблона сразу после последней строки раздела, с его отступом;
- поле каждого элемента списка (`directions[].volume`) — строка после последнего поля каждого элемента.
Значение меняется заменой одной строки (`schema_version`); поле, которое проект уже выставил, не трогается. Второй
ключ с тем же именем не появляется никогда: PyYAML молча взял бы последний и стёр калибровку. После вставки — разбор
YAML без повторных ключей, обход дерева (все старые значения на месте) и проверка схемой новой версии. Реестры волны 1
перевод не трогает.

Шаг «версия N → N+1» — запись в `STEPS`: следующая версия добавляется строкой, а не переделкой.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import profile as profile_mod
from .errors import ProfileError

TOP = re.compile(r"^([A-Za-z_][\w]*)\s*:")


@dataclass(frozen=True)
class Step:
    to: int
    sections: tuple = ()                      # новые разделы верхнего уровня
    fields: tuple = ()                        # (путь раздела через точку, поле)
    item_fields: tuple = ()                   # (список верхнего уровня, поле каждого элемента)
    keep: tuple = ()                          # поля, у которых в новом шаблоне другое значение, а проект держит своё
    notes: dict = field(default_factory=dict)


STEPS = {
    1: Step(to=2, sections=("models", "prepare", "brief", "assemble", "publish", "indexing", "records"),
            fields=(("budget", "run_limit_rub"), ("budget", "month_limit_rub")),
            item_fields=(("directions", "volume"),),
            keep=(("queue", "require_bet"),)),
}


class _UniqueLoader(yaml.SafeLoader):
    """Разбор без повторных ключей: второй ключ с тем же именем — ошибка, а не тихая замена."""


def _mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ProfileError(f"повторный ключ «{key}» (строка {key_node.start_mark.line + 1})")
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse(text: str) -> dict:
    try:
        return yaml.load(text, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise ProfileError(f"YAML не читается — {exc}") from exc


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank(line: str) -> bool:
    return not line.strip()


def _comment(line: str) -> bool:
    return line.strip().startswith("#")


def _top_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Раздел верхнего уровня → (строка ключа, строка после раздела)."""
    starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := TOP.match(line))]
    spans = {}
    for n, (i, name) in enumerate(starts):
        spans[name] = (i, starts[n + 1][0] if n + 1 < len(starts) else len(lines))
    return spans


def _find_key(lines: list[str], start: int, stop: int, indent: int, key: str) -> int | None:
    rx = re.compile(r"^ {%d}%s\s*:" % (indent, re.escape(key)))
    for i in range(start, stop):
        if rx.match(lines[i]):
            return i
    return None


def _child_span(lines: list[str], start: int, stop: int) -> tuple[int, int, int]:
    """Тело блока под строкой `start`: (отступ детей, последняя строка тела включительно, конец просмотра)."""
    own = _indent(lines[start])
    child, last, end = None, start, stop
    for i in range(start + 1, stop):
        line = lines[i]
        if _blank(line):
            continue
        if _indent(line) <= own:              # строка или комментарий на уровне ключа — блок кончился
            end = i
            break
        if child is None and not _comment(line):
            child = _indent(line)
        last = i
    return (child if child is not None else own + 2), last, end


def _section_path(lines: list[str], path: str) -> tuple[int, int]:
    """Строка ключа раздела по пути «a.b» и граница поиска."""
    spans = _top_spans(lines)
    head, *rest = path.split(".")
    if head not in spans:
        raise ProfileError(f"раздела «{head}» нет в профиле")
    start, stop = spans[head]
    for key in rest:
        child, _, end = _child_span(lines, start, stop)
        found = _find_key(lines, start + 1, end, child, key)
        if found is None:
            raise ProfileError(f"раздела «{path}» нет в профиле")
        start, stop = found, end
    return start, stop


def _template_field(tpl: list[str], path: str, key: str) -> list[str]:
    """Строки поля из шаблона: сама строка и всё, что глубже неё (значение-блок и комментарии-продолжения)."""
    start, stop = _section_path(tpl, path)
    child, _, end = _child_span(tpl, start, stop)
    i = _find_key(tpl, start + 1, end, child, key)
    if i is None:
        raise ProfileError(f"в шаблоне пакета нет поля «{path}.{key}»")
    out = [tpl[i]]
    for line in tpl[i + 1:end]:
        if _blank(line) or _indent(line) <= child:
            break
        out.append(line)
    return out


def _reindent(lines: list[str], old: int, new: int) -> list[str]:
    out = []
    for line in lines:
        if _indent(line) >= old and not (_comment(line) and _indent(line) > old + 8):
            line = " " * new + line[old:]
        out.append(line)
    return out


def _template_section(tpl: list[str], name: str) -> list[str]:
    spans = _top_spans(tpl)
    if name not in spans:
        raise ProfileError(f"в шаблоне пакета нет раздела «{name}»")
    start, stop = spans[name]
    block = tpl[start:stop]
    while block and _blank(block[-1]):
        block.pop()
    return block


def _list_items(lines: list[str], name: str) -> list[tuple[int, int, int]]:
    """Элементы списка верхнего уровня: (строка «- », отступ полей элемента, последняя строка элемента)."""
    spans = _top_spans(lines)
    start, stop = spans[name]
    if lines[start].split(":", 1)[1].split("#", 1)[0].strip():
        raise ProfileError(f"«{name}» записан в одну строку — перевод вставкой его не правит; разверните список "
                           "по элементу на строку")
    items = []
    for i in range(start + 1, stop):
        m = re.match(r"^(\s*)- ", lines[i])
        if m and not _comment(lines[i]):
            items.append([i, len(m.group(1)) + 2, i])
    for n, item in enumerate(items):
        end = items[n + 1][0] if n + 1 < len(items) else stop
        for j in range(item[0] + 1, end):
            if not _blank(lines[j]) and _indent(lines[j]) >= item[1]:
                item[2] = j
    return [tuple(x) for x in items]


@dataclass
class Plan:
    version_from: int
    version_to: int
    text: str
    changes: list[str]


def plan(path) -> Plan:
    path = Path(path)
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    old = parse("\n".join(lines))
    if not isinstance(old, dict):
        raise ProfileError(f"{path.name}: верхний уровень — не раздел")
    version = old.get("schema_version")
    if version == profile_mod.SCHEMA_VERSION:
        raise ProfileError(f"{path.name}: профиль уже версии {version}, переводить нечего")
    if version not in STEPS:
        raise ProfileError(f"{path.name}: версия {version!r} — перевода с неё в пакете нет")
    profile_mod.validate(old, version)
    step = STEPS[version]
    tpl = profile_mod.template_text().split("\n")
    changes = []
    # (в) поле каждого элемента списка — снизу вверх, чтобы номера строк выше не сдвигались
    for list_name, key in step.item_fields:
        source = _template_field_from_list(tpl, list_name, key)
        items = _list_items(lines, list_name)
        added = []
        for n, (head, inner, last) in reversed(list(enumerate(items))):
            if key in old[list_name][n]:
                continue
            lines[last + 1:last + 1] = _reindent(source["lines"], source["indent"], inner)
            added.append(f"поле {list_name}[{n}].{key}: {source['value']}")
        changes += reversed(added)
    # (б) новые поля существующих разделов
    for section, key in step.fields:
        node = old
        for part in section.split("."):
            node = node[part]
        if key in node:
            continue
        start, stop = _section_path(lines, section)
        child, last, _ = _child_span(lines, start, stop)
        source = _template_field(tpl, section, key)
        tstart, tstop = _section_path(tpl, section)
        tchild = _child_span(tpl, tstart, tstop)[0]
        lines[last + 1:last + 1] = _reindent(source, tchild, child)
        changes.append(f"поле {section}.{key}: {_value_of(source[0])}")
    # (а) новые разделы — в конец файла
    while lines and _blank(lines[-1]):
        lines.pop()
    for name in step.sections:
        if name in old:
            continue
        lines += [""] + _template_section(tpl, name)
        changes.append(f"раздел {name} (значения шаблона пакета)")
    lines.append("")
    # смена значения — одной строкой
    for i, line in enumerate(lines):
        m = re.match(r"^schema_version\s*:\s*(\d+)(.*)$", line)
        if m:
            lines[i] = f"schema_version: {step.to}{m.group(2)}"
            changes.append(f"schema_version: {version} → {step.to}")
            break
    template_values = parse("\n".join(tpl))
    for section, key in step.keep:
        mine = old[section][key]
        theirs = template_values[section][key]
        if mine != theirs:
            changes.append(f"{section}.{key}: оставлено значение проекта ({_yaml(mine)}); в шаблоне версии "
                           f"{step.to} — {_yaml(theirs)}")
    text = newline.join(lines)
    new = parse(text)
    _same_old_values(old, new)
    profile_mod.validate(new, step.to)
    return Plan(version, step.to, text, changes)


def _template_field_from_list(tpl: list[str], list_name: str, key: str) -> dict:
    items = _list_items(tpl, list_name)
    if not items:
        raise ProfileError(f"в шаблоне пакета пустой список «{list_name}»")
    head, inner, last = items[0]
    for i in range(head, last + 1):
        line = tpl[i]
        bare = line[inner:] if line.startswith(" " * inner) else line.lstrip(" -")
        if line.startswith(" " * inner) and re.match(r"%s\s*:" % re.escape(key), bare):
            return {"lines": [line], "indent": inner, "value": _value_of(line)}
    raise ProfileError(f"в шаблоне пакета нет поля «{list_name}[].{key}»")


def _value_of(line: str) -> str:
    return line.split(":", 1)[1].split("  #", 1)[0].strip()


def _yaml(value) -> str:
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).strip().removesuffix("...").strip()


def _same_old_values(old, new, path="") -> None:
    """Все значения старого профиля на месте (кроме версии) — обход дерева."""
    if isinstance(old, dict):
        if not isinstance(new, dict):
            raise ProfileError(f"перевод испортил раздел «{path or 'профиль'}»")
        for key, value in old.items():
            name = f"{path}.{key}" if path else str(key)
            if name == "schema_version":
                continue
            if key not in new:
                raise ProfileError(f"перевод потерял поле «{name}»")
            _same_old_values(value, new[key], name)
        return
    if isinstance(old, list) and old and all(isinstance(x, dict) for x in old):
        if not isinstance(new, list) or len(new) != len(old):
            raise ProfileError(f"перевод изменил список «{path}»")
        for n, (a, b) in enumerate(zip(old, new)):
            _same_old_values(a, b, f"{path}[{n}]")
        return
    if old != new:
        raise ProfileError(f"перевод изменил значение «{path}»: было {old!r}, стало {new!r}")


def apply(path, migration: Plan) -> str:
    """Записать через временный файл, прочитать обратно и проверить схемой новой версии."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(migration.text)
    os.replace(tmp, path)
    back = path.read_bytes().decode("utf-8")
    if back != migration.text:
        raise ProfileError(f"{path.name}: записано {len(migration.text)} знаков — прочитано {len(back)}")
    profile_mod.load(path)
    lines = migration.text.count("\n")
    return (f"{path.name}: записано строк {lines} — прочитано {back.count(chr(10))}; профиль версии "
            f"{migration.version_to} проходит проверку")
