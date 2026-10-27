"""Известные ответы (Т14.2) и ложные тревоги (Т14.3): `content-engine known-answers [--audit <файл>]`.

`known_answers.yaml` инстанса — случаи с ожиданием:

    cases:
      - name: тема закрыта разделом чужой статьи
        kind: gate                       # гейт темы: вердикт и, если задано, слой и страница
        topic: "..."
        formulations: ["...", "...", "..."]
        exclude: []                      # slug статей, которые не считать (тема против корпуса без них)
        bet: {}                          # поля ставки, если профиль её требует
        expect: {verdict: пересекается, layer: 2, page: <slug>}
      - name: две живые статьи на одном спросе
        kind: pair                       # «пересечение живых» в карте спроса ИЛИ «пересекается» в гейте темы
        pages: [<slug A>, <slug B>]
        gate: {topic: "...", formulations: [...], exclude: [<slug A>]}   # необязательно
      - name: статья доехала
        kind: publish                    # «доехало» по ответу сайта (Т25.1): адрес или подменённый адрес
        id: <id темы или slug статьи корпуса>
        url: https://…
        expect: доехало                  # или «не доехало»
      - name: сборка без утверждённого брифа
        kind: refuse                     # действие должно получить отказ; проверяется без записи вовне
        action: assemble_unapproved      # assemble_unapproved | second_run_locked | above_rights | no_snapshot
        area: publish                    # для above_rights: область и ступень, которой у проекта нет
        level: publish
Совпадение слоя: слой с этим номером дал «пересекается» на этой странице (первым он быть не обязан). «Спорно»
пересечением не считается. Печать — «совпало N из M»; любое расхождение — код 1.

`--cases <файл>` — другой файл случаев (по умолчанию `known_answers.yaml`): новые виды у проекта живут отдельным
файлом, пока прежний выпуск, который падает на незнакомом виде, ещё в работе. «Доехало» в известных ответах только
читает сайт: день выкладки, снимок и записи не пишутся. Отказы проверяются в песочнице — временном каталоге с копией
профиля; ни одного запроса записи.

`pending_until: ГГГГ-ММ-ДД` и `pending_reason` — случай ждёт данных (например, статья вышла вчера и её ещё нет в
источниках). До этой даты расхождение печатается «отложен» с причиной и кода 1 не даёт; ожидание случая не меняется.
После даты случай снова обязателен.

`--audit <файл>` — CSV решений гейта с колонкой «ложная тревога (да/нет)»: доля срабатываний, которые человек
отметил ложными, и размер выборки.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from ..core.errors import EngineError
from ..core.urls import slug_of
from .gate_topic import OVERLAP, Candidate, gate

AUDIT_COLUMN = "ложная тревога (да/нет)"


def load_cases(instance, cases: str | None = None) -> list[dict]:
    path = instance.file("known_answers")
    if cases:
        path = Path(cases)
        path = path if path.is_absolute() else instance.root / path
    if not path.is_file():
        raise EngineError(f"известных ответов нет: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases") or []
    if not cases:
        raise EngineError(f"{path.name}: список cases пуст")
    return cases


def _same(page: str, expected: str) -> bool:
    return bool(page) and (slug_of(page) == expected or page == expected)


def _gate_case(instance, case, offline):
    cand = Candidate(case["topic"], list(case["formulations"]), bet=dict(case.get("bet") or {}),
                     skip_slugs=frozenset(case.get("exclude") or []))
    return gate(instance, cand, offline=offline)


def run_case(instance, case: dict, offline: bool) -> tuple[bool, str]:
    kind = case.get("kind", "gate")
    if kind == "gate":
        report = _gate_case(instance, case, offline)
        exp = case["expect"]
        got = report["verdict"]
        if got != exp["verdict"]:
            return False, f"вердикт «{got}», ожидался «{exp['verdict']}»" + _why(report)
        if "layer" in exp:
            layer = next(l for l in report["layers"] if l["layer"] == exp["layer"])
            if layer["outcome"] != OVERLAP:
                return False, f"слой {exp['layer']} — «{layer['outcome']}», ожидалось «пересекается»"
            if exp.get("page") and not _same(layer["page"], exp["page"]):
                return False, f"слой {exp['layer']} указал {layer['page'] or '—'}, ожидалась {exp['page']}"
            return True, f"слой {exp['layer']}: {layer['page']}" + (f", раздел «{layer['section']}»"
                                                                      if layer["section"] else "")
        return True, f"вердикт «{got}»" + _why(report)
    if kind == "pair":
        a, b = case["pages"]
        path = instance.file("demand_map")
        if path.is_file():
            from ..core import store
            for c in store.read_json(path, "demand_map")["clusters"]:
                top10 = {k for k, v in c["positions"].items() if v["position"] <= 10}
                if c["live_overlap"] and {a, b} <= top10:
                    return True, f"пересечение живых в карте спроса: кластер «{c['head']}» ({c['id']})"
        if case.get("gate"):
            report = _gate_case(instance, case["gate"], offline)
            hit = next((l for l in report["layers"] if l["outcome"] == OVERLAP and _same(l["page"], b)), None)
            if report["verdict"] == OVERLAP and hit:
                return True, f"гейт: слой {hit['layer']} «{hit['name']}» на {b}"
            return False, f"ни пересечения живых, ни «пересекается» на {b}: гейт «{report['verdict']}»" + _why(report)
        return False, "пересечения живых этих страниц в карте спроса нет"
    if kind == "publish":
        from .verify import check_page, reference
        ref = reference(instance, str(case["id"]), case.get("url"))
        items = check_page(instance, ref, case["url"])["items"]
        got = "доехало" if all(i["ok"] for i in items) else "не доехало"
        why = "; ".join(f"{i['check']}: {i['detail']}" for i in items if not i["ok"])
        return got == case["expect"], f"«{got}», ожидалось «{case['expect']}»" + (f" ({why})" if why else "")
    if kind == "refuse":
        return refuse_case(instance, case)
    if kind == "judge":                         # Т33.2: ответ судьи вне пятёрки или с чужой цитатой не принят
        from .judge import validate
        problems, _ = validate(case["sections"], case["answer"])
        got = "не принят" if problems else "принят"
        return got == case["expect"], f"ответ судьи {got}, ожидалось «{case['expect']}»" + (
            f" ({'; '.join(problems)})" if problems else "")
    if kind == "review":                        # Т33.2: блокер вычитки держит черновик
        from .review import open_claims
        left = open_claims(case["claims"], case.get("decisions") or {})
        got = "черновик" if left else "пропуск"
        return got == case["expect"], f"вычитка: «{got}», ожидалось «{case['expect']}»" + (
            f" ({'; '.join(left)})" if left else "")
    if kind == "facts":                         # Т33.2: число, совпавшее с просроченным фактом, — черновик
        from datetime import date
        from ..core.facts import valid
        fact = case["fact"]
        stale = not valid(fact, date.fromisoformat(case.get("today") or instance.today.isoformat()))
        hit = str(fact["value"]) in case["text"]
        got = "черновик" if stale and hit else "пропуск"
        return got == case["expect"], f"факт «{fact['fact']}»: «{got}», ожидалось «{case['expect']}»"
    raise EngineError(f"случай «{case.get('name')}»: неизвестный вид {kind}")


REFUSALS = ("assemble_unapproved", "second_run_locked", "above_rights", "no_snapshot",
            "writer_without_brief", "paid_without_budget", "factcheck_with_context")


def refuse_case(instance, case: dict) -> tuple[bool, str]:
    """Действие должно получить отказ кода — проверка в песочнице, без записи вовне (Т25.1)."""
    import shutil
    import tempfile
    from ..core import actions as actions_mod
    from ..core import locks, rights
    from ..core.errors import BudgetRefused, GateRefused, LockBusy, RightsRefused, SnapshotMissing
    from ..core.instance import Instance
    action = case.get("action")
    if action not in REFUSALS:
        raise EngineError(f"случай «{case.get('name')}»: действие отказа «{action}» не из {REFUSALS}")
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(instance.profile_path, Path(tmp) / "profile.yaml")
        box = Instance(Path(tmp) / "profile.yaml", today=instance.today)
        expected, run = {
            "writer_without_brief": (GateRefused, lambda: _writer_without_brief(box)),
            "paid_without_budget": (BudgetRefused, lambda: _paid_without_budget(box)),
            "factcheck_with_context": (EngineError, lambda: _factcheck_with_context(box)),
            "assemble_unapproved": (GateRefused, lambda: _assemble_unapproved(box)),
            "second_run_locked": (LockBusy, lambda: _second_run(box, locks)),
            "above_rights": (RightsRefused, lambda: rights.require(box, case.get("area", "publish"),
                                                                    case.get("level", "publish"))),
            "no_snapshot": (SnapshotMissing, lambda: _no_snapshot(box, actions_mod)),
        }[action]
        try:
            run()
        except expected as exc:
            return True, f"отказ: {exc}"
        except EngineError as exc:
            return False, f"отказ другого вида: {type(exc).__name__}: {exc}"
        return False, "отказа нет — действие прошло бы"


def _writer_without_brief(box):
    """Т28.1: задание писателю без утверждённого брифа — отказ до чтения темы."""
    from .write import make_tasks
    make_tasks(box, "KA-WRITE")


def _paid_without_budget(box):
    """Т17.2: платный вызов без «да» человека — отказ, ноль вызовов (вызова в песочнице нет вовсе)."""
    from ..core import budget
    calls = []
    budget.paid_call(box, "KA-PAID", "известный ответ", 1.0, lambda: calls.append(1), confirm=None,
                     unattended=False, what="платный вызов без «да»", source="известный ответ")
    if calls:
        raise AssertionError("платный вызов ушёл без «да»")


def _factcheck_with_context(box):
    """Т29.1: задание вычитки с путём к контексту автора не пишется."""
    from .review import factcheck_task
    folder = box.brief_dir("KA-FACT")
    (folder / "article.md").write_text("# Статья\n\nСм. brief.yaml и task_write.md.\n", encoding="utf-8")
    factcheck_task(box, "KA-FACT")


def _assemble_unapproved(box):
    from .assemble import build
    folder = box.brief_dir("KA0001")
    (folder / "brief.yaml").write_text("h1: проверка\n", encoding="utf-8")
    build(box, "KA0001")


def _second_run(box, locks):
    with locks.held(box, "KA0001", "publish"):
        locks.acquire(box, "KA0001", "publish")


def _no_snapshot(box, actions_mod):
    box.profile["records"]["allow"] = "write"        # в песочнице право есть — проверяется именно снимок «до»
    done = []
    action = actions_mod.Action("db_row", "база проекта", "POST", "проверка", "read_back", ("records", "write"),
                                do=lambda: done.append(1), verify=lambda r: (True, ""), snapshot=None)
    try:
        actions_mod.run(box, "KA0001", [action], apply=True, echo=lambda s: None)
    finally:
        if done:
            raise EngineError("запись выполнена без снимка «до»")


def _why(report) -> str:
    s = report.get("stopped_by")
    return f" (слой {s['layer']}: {s['reason']} — {s['page']})" if s else ""


def audit(path: Path) -> dict:
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig"), delimiter=";"))
    if not rows or AUDIT_COLUMN not in rows[0]:
        raise EngineError(f"{Path(path).name}: нет колонки «{AUDIT_COLUMN}»")
    marked = [r for r in rows if (r[AUDIT_COLUMN] or "").strip().lower() in ("да", "нет")]
    alarms = [r for r in marked if (r.get("вердикт") or "") != "свободно"]
    false = [r for r in alarms if r[AUDIT_COLUMN].strip().lower() == "да"]
    share = len(false) / len(alarms) if alarms else None
    return {"sample": len(rows), "marked": len(marked), "alarms": len(alarms), "false": len(false),
            "share": round(share, 3) if share is not None else None}


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("known-answers", help="прогон известных ответов; --audit — доля ложных тревог")
    add_profile(p)
    p.add_argument("--audit", help="CSV решений гейта с колонкой «ложная тревога (да/нет)»")
    p.add_argument("--offline", action="store_true", help="без запросов к выдаче")
    p.add_argument("--cases", help="файл случаев вместо known_answers.yaml (путь от каталога инстанса)")
    from ..core.budget import add_flags
    add_flags(p)                                 # случаи гейта снимают выдачу; при отказе Keys.so — платный источник
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.audit:
        out = audit(Path(args.audit))
        share = "нет срабатываний" if out["share"] is None else f"{out['share']:.1%}"
        print(f"Ложные тревоги: {out['false']} из {out['alarms']} срабатываний — {share}; выборка {out['sample']} "
              f"решений, размечено {out['marked']}")
        return 0
    cases = load_cases(instance, args.cases)
    lines, ok, pending = [], 0, 0
    for case in cases:
        good, detail = run_case(instance, case, args.offline)
        until = str(case.get("pending_until") or "")
        if not good and until and instance.today.isoformat() < until:
            pending += 1
            lines.append(f"  … {case['name']}: отложен до {until} — {case.get('pending_reason', '')}; сейчас: {detail}")
            continue
        ok += good
        lines.append(f"  {'✓' if good else '✗'} {case['name']}: {detail}")
    total = len(cases) - pending
    text = "\n".join([f"Известные ответы: совпало {ok} из {total}" + (f", отложено {pending}" if pending else "")]
                     + lines)
    (instance.reports_dir() / "Известные ответы.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if ok == total else 1
