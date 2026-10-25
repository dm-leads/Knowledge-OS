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
Совпадение слоя: слой с этим номером дал «пересекается» на этой странице (первым он быть не обязан). «Спорно»
пересечением не считается. Печать — «совпало N из M»; любое расхождение — код 1.

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


def load_cases(instance) -> list[dict]:
    path = instance.file("known_answers")
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
    raise EngineError(f"случай «{case.get('name')}»: неизвестный вид {kind}")


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
    cases = load_cases(instance)
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
