"""Сборка страницы (В6, Т20.2–Т20.5): `content-engine assemble build <id> | contract`.

`build <id>` — запрет в коде: нужен утверждённый бриф и отчёт гейта статьи `gate_article.json` с итогом «пропуск» и
суммой, равной SHA-256 текущего `article.md` («пропуск» от прежнего текста не годится). Сборщик — по
`assemble.adapter` (встроенный или из инстанса). Автопроверки Т20.4 с уровнями `assemble.levels`: битые внутренние
якоря, якоря оглавления = разделы, целые таблицы, обязательные элементы, граница у каждого раздела. Итог проверок —
в манифесте (`checks`, `ready`): провал проверки уровня block — пакет «не готов», публиковать его нельзя.

`contract` — синтетическая статья пакета через сборщик профиля: манифест валиден, файлы на месте, суммы совпадают.
Сборщик проекта со своим форматом текста может отдать свою синтетическую статью методом `contract_input()`.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from ..assemblers.base import MANIFEST, Package, check_manifest, load_assembler, write_manifest
from ..core import approval
from ..core.errors import GateRefused
from ..core.htmldoc import HtmlDoc, comments_left, strip_comments
from ..core.markdown_html import convert

CONTRACT_ARTICLE = """---
title: Синтетическая статья для проверки сборщика
---

# Как ухаживать за садовым инвентарём

Инвентарь служит дольше, если его чистить после работы и хранить сухим.

## Чистка после работы

Землю смывают водой, металл вытирают насухо. Тупые края правят напильником.

### Что взять

- щётку;
- ветошь;
- масло для металла.

## Хранение зимой

| Инструмент | Как хранить |
|---|---|
| Лопата | на стене, лезвием вниз |
| Секатор | в чехле, смазанным |

## Частые вопросы

**Как часто точить секатор?**
Раз в сезон и после обрезки толстых веток.

**Чем смазать металл перед зимой?**
Подойдёт машинное масло тонким слоем.
"""
CONTRACT_BRIEF = {"id": "contract", "h1": "Как ухаживать за садовым инвентарём",
                  "title": "Синтетическая статья для проверки сборщика", "slug": "kontrakt-sborshchika",
                  "description": "Проверка контракта сборщика на синтетической статье пакета.",
                  "author": "Автор Примеров", "read_also": []}


def _gate_ok(instance, tid: str) -> dict:
    folder = instance.brief_dir(tid)
    article, report_path = folder / "article.md", folder / "gate_article.json"
    if not article.is_file():
        raise GateRefused(f"тема {tid}: текста нет — `{article}`")
    if not report_path.is_file():
        raise GateRefused(f"тема {tid}: гейта статьи нет — `gate article --topic {tid}`")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("verdict") != "пропуск":
        raise GateRefused(f"тема {tid}: гейт статьи — «{report.get('verdict')}», сборка не начинается")
    if report.get("offline"):
        raise GateRefused(f"тема {tid}: гейт статьи прошёл без проверки ссылок (--offline) — сборка не начинается")
    if report.get("article_sha256") != hashlib.sha256(article.read_bytes()).hexdigest():
        raise GateRefused(f"тема {tid}: текст изменён после гейта статьи — `gate article --topic {tid}` заново")
    return report


def checks(instance, package: Package) -> list[dict]:
    """Т20.4 — автопроверки собранной страницы с уровнями профиля."""
    levels = instance.p["assemble"]["levels"]
    main = package.main.read_text(encoding="utf-8")
    if package.main.suffix == ".md":
        from ..adapters.corpus import split_front_matter
        body_html, _ = convert(split_front_matter(main)[1])
        main = f"<html><body><article>{body_html}</article></body></html>"
    doc = HtmlDoc(main)
    ids = doc.ids()
    results = []

    def add(name, ok, detail):
        if levels[name] != "off":
            results.append({"check": name, "level": levels[name], "ok": bool(ok), "detail": detail})

    # нативные якоря страницы (окно консультации конструктора и т. п.) объявляет сборщик проекта в манифесте
    native = set(package.manifest.get("native_anchors") or [])
    broken = sorted({h[1:] for h in doc.links() if h.startswith("#") and len(h) > 1 and h[1:] not in ids
                     and h[1:] not in native})
    add("anchors", not broken, "битые якоря: " + ", ".join(broken) if broken else "внутренние якоря на месте")
    skip = [instance.p["gate_article"]["faq_heading"], *(package.manifest.get("toc_skip") or [])]
    ok, detail = _toc(doc, instance.p["corpus"]["toc_marker"], skip)
    add("toc", ok, detail)
    if "comments" in levels:                     # Н5: публикуемый HTML пакета без комментариев
        left = {name: n for name in package.manifest["files"] if name.endswith((".html", ".htm"))
                and (n := comments_left((package.folder / name).read_text(encoding="utf-8")))}
        add("comments", not left, "комментарии в публикуемом HTML: " + ", ".join(f"{k} — {v}" for k, v in left.items())
            if left else "комментариев в HTML пакета нет")
    bad = [n for n, rows in enumerate(doc.tables(), 1) if rows and len(set(rows)) > 1]
    add("tables", not bad, f"таблицы с разным числом ячеек в строках: {bad}" if bad else
        f"таблиц {len(doc.tables())}, все целые")
    # элементы, которые ставит сама страница (H1 — заголовок страницы конструктора), сборщик объявляет в манифесте
    outside = set(package.manifest.get("outside_blocks") or [])
    missing = [sel for sel in instance.p["assemble"]["required_elements"] if sel not in outside
               and not doc.select(sel)]
    add("required", not missing, "нет элементов: " + ", ".join(missing) if missing else "обязательные элементы на месте")
    ref = instance.p["thresholds"]["section_length_ref"]
    if ref is None:
        results.append({"check": "section_borders", "level": "off", "ok": False,
                        "detail": "не проверялось: thresholds.section_length_ref не задан"})
    else:
        long = []
        for title, length, has_sub in _sections(doc, instance):
            if length > ref and not has_sub:
                long.append(f"«{title}» ({length} основ)")
        add("section_borders", not long, "длинные разделы без подзаголовка: " + ", ".join(long) if long else
            f"у каждого раздела длиннее {ref} основ есть подзаголовок")
    return results


def _toc_links(doc: HtmlDoc, marker: str) -> list[str] | None:
    """Ссылки оглавления: блок `.toc` или `nav`, иначе первый список после строки-маркера (`corpus.toc_marker`)."""
    nodes = doc.select(".toc") or doc.select("nav")
    if nodes:
        return [a.attrs.get("href", "")[1:] for a in nodes[0].find_all("a") if a.attrs.get("href", "").startswith("#")]
    if not marker:
        return None
    order = list(doc.root.iter())
    want = marker.strip().strip(":").lower()
    start = next((i for i, n in enumerate(order) if n.tag not in ("#root", "html", "body")
                  and n.text().strip().strip(":").lower() == want), None)
    if start is None:
        return None
    lst = next((n for n in order[start + 1:] if n.tag in ("ul", "ol")), None)
    if lst is None:
        return None
    return [a.attrs.get("href", "")[1:] for a in lst.find_all("a") if a.attrs.get("href", "").startswith("#")]


def _toc(doc: HtmlDoc, marker: str, skip: list[str]) -> tuple[bool, str]:
    """Каждый раздел H2 есть в оглавлении по порядку, кроме частых вопросов (`gate_article.faq_heading`) и разделов,
    которые сборщик проекта объявил в манифесте (`toc_skip`, начало заголовка — например «Выводы»). id может стоять
    на H2 или на обёртке раздела: пункт оглавления ведёт на первый H2 от своей цели."""
    order = list(doc.root.iter())
    pos = {id(n): i for i, n in enumerate(order)}
    h2 = [n for n in order if n.tag == "h2"]
    skipped = [h for h in h2 if any(h.text().lower().startswith(s.lower()) for s in skip if s)]
    required = [h for h in h2 if h not in skipped]
    toc = _toc_links(doc, marker)
    if not toc:
        return len(required) < 2, "оглавления нет" + ("" if len(required) < 2 else f" при {len(required)} разделах H2")
    by_id = {n.attrs["id"]: n for n in order if n.attrs.get("id")}
    mapped = []
    for anchor in toc:
        target = by_id.get(anchor)
        if target is None:
            return False, f"пункт оглавления «#{anchor}» ведёт в никуда"
        nxt = next((h for h in h2 if pos[id(h)] >= pos[id(target)]), None)
        if nxt is None:
            return False, f"после цели «#{anchor}» нет раздела H2"
        mapped.append(nxt)
    if [pos[id(h)] for h in mapped] != sorted(pos[id(h)] for h in mapped) or len(set(map(id, mapped))) != len(mapped):
        return False, "порядок оглавления не совпадает с разделами"
    missing = [h.text() for h in required if h not in mapped]
    if missing:
        return False, "разделов нет в оглавлении: " + "; ".join(missing)
    return True, f"оглавление = {len(required)} разделов H2" + (
        f"; вне оглавления: {', '.join(h.text() for h in skipped)}" if skipped else "")


def _sections(doc: HtmlDoc, instance):
    """(заголовок H2, длина раздела в основах, есть ли H3) — по тексту между соседними H2."""
    node, _ = doc.main()
    out, current, parts, sub = [], None, [], False

    def close():
        if current is not None:
            out.append((current, len(instance.norm.tokens(" ".join(parts))), sub))

    def walk(n):
        nonlocal current, parts, sub
        for child in n.children:
            if isinstance(child, str):
                if current is not None:
                    parts.append(child)
                continue
            if child.tag == "h2":
                close()
                current, parts, sub = child.text(), [], False
                continue
            if child.tag == "h3" and current is not None:
                sub = True
            if child.tag in ("script", "style", "nav"):
                continue
            walk(child)
    walk(node)
    close()
    return out


def build(instance, tid: str) -> tuple[Package, list[dict]]:
    approval.require(instance, tid)
    _gate_ok(instance, tid)
    from .brief import load_brief
    folder = instance.brief_dir(tid)
    schema_path = folder / "schema.json"
    if not schema_path.is_file():
        raise GateRefused(f"тема {tid}: разметки нет — `gate article --topic {tid}` строит её из текста")
    out = folder / "package"
    if out.exists():
        shutil.rmtree(out)                       # пакет — производный: пересобирается целиком
    brief = {**load_brief(instance, tid), "id": tid}
    assembler = load_assembler(instance)(instance, out)
    package = assembler.build(folder / "article.md", json.loads(schema_path.read_text(encoding="utf-8")), [], brief)
    notes = strip_package_comments(package)
    package = write_manifest(out, {k: v for k, v in package.manifest.items()
                                   if k not in ("files", "schema_version", "kind")}, list(package.manifest["files"]))
    results = checks(instance, package)
    ready = not any(r["level"] == "block" and not r["ok"] for r in results)
    meta = {k: v for k, v in package.manifest.items() if k not in ("files", "schema_version", "kind")}
    meta.update(checks=results, ready=ready, notes=notes, brief_sha256=approval.digest(folder / "brief.yaml"),
                article_sha256=approval.digest(folder / "article.md"))
    package = write_manifest(out, meta, list(package.manifest["files"]))
    return package, results


def strip_package_comments(package: Package) -> list[dict]:
    """Н5: снять комментарии со всех HTML-файлов пакета, которые идут на сайт; их текст — в манифест (`notes`).

    Пояснения сборки нужны агентам и человеку — они попадают в манифест и в `paste.md`, а не в код страницы."""
    notes = []
    for name in package.manifest["files"]:
        if not name.endswith((".html", ".htm")):
            continue
        path = package.folder / name
        clean, removed = strip_comments(path.read_text(encoding="utf-8"))
        if removed:
            path.write_text(clean, encoding="utf-8", newline="\n")
            notes += [{"file": name, "comment": text[:300]} for text in removed]
    return notes


def contract(instance) -> list[str]:
    """Тест контракта сборщика профиля на синтетической статье (Т20.3); пусто — контракт выполнен."""
    from ..steps.schema import build as schema_build
    cls = load_assembler(instance)
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = tmp / "package"
        try:
            assembler = cls(instance, out)
            text, brief = (assembler.contract_input() if hasattr(assembler, "contract_input")
                           else (CONTRACT_ARTICLE, dict(CONTRACT_BRIEF)))
            article = tmp / "article.md"
            article.write_text(text, encoding="utf-8", newline="\n")
            schema = schema_build(text, brief, instance.p, instance.today.isoformat())
            package = assembler.build(article, schema, [], brief)
        except Exception as exc:                       # сборщик проекта упал — это и есть нарушение контракта
            return [f"сборщик {instance.p['assemble']['adapter']} упал: {type(exc).__name__}: {exc}"]
        if not isinstance(package, Package):
            return ["build вернул не Package"]
        problems += check_manifest(out, package.manifest)
        if not (out / MANIFEST).is_file():
            problems.append("манифеста нет в папке пакета")
        for field in ("title", "slug", "h1", "canonical"):
            if not package.manifest.get(field):
                problems.append(f"в манифесте пусто поле «{field}»")
        if package.manifest.get("slug") != brief["slug"]:
            problems.append("slug манифеста не из брифа")
        if not re.match(r"\d{4}-\d{2}-\d{2}$", str(package.manifest.get("built", ""))):
            problems.append("день сборки в манифесте не ГГГГ-ММ-ДД")
    return problems


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("assemble", help="сборка страницы: build | contract")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("build", help="собрать пакет темы (нужны утверждённый бриф и «пропуск» гейта статьи)")
    add_profile(p)
    p.add_argument("id")
    p.set_defaults(func=run)
    p = actions.add_parser("contract", help="тест контракта сборщика профиля на синтетической статье")
    add_profile(p)
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "contract":
        problems = contract(instance)
        for line in problems:
            print(f"  ✗ {line}")
        print(f"Контракт сборщика {instance.p['assemble']['adapter']}: " + ("выполнен" if not problems else
                                                                             f"нарушен ({len(problems)})"))
        return 1 if problems else 0
    package, results = build(instance, args.id)
    for r in results:
        mark = "✓" if r["ok"] else ("✗" if r["level"] == "block" else "!")
        print(f"  {mark} {r['check']} ({r['level']}): {r['detail']}")
    m = package.manifest
    print(f"Пакет {args.id}: {package.folder} — файлов {len(m['files'])}, "
          + ("ГОТОВ" if m["ready"] else "НЕ ГОТОВ (провал проверки уровня block)"))
    if not m["ready"]:
        raise GateRefused("сборка не готова: исправьте текст и соберите заново")
    return 0
