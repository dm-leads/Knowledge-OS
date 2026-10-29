"""«Поле шире освоенного» (А4, Т41.3): `content-engine field probe`.

Раз в месяц — пачка тем новых направлений через гейт темы без сети (`gate topic --offline`, квота не тратится). Вход —
`field.file` (путь от инстанса): UTF-8, строка «направление;тема;формулировка»; строки одной темы собираются вместе —
гейту нужно не меньше трёх формулировок на тему; пустые строки и строки с «#» пропускаются; строка без двух «;» —
ошибка с её номером. Тем — не больше `field.max`. Итог по направлению: сколько тем свободно, пересекается, спорно —
`reports/<день>/field.md` и `data/field/<день>.json`. Новое направление в профиль вписывает человек.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core import store
from ..core.errors import EngineError, GateRefused
from .gate_topic import COMMERCIAL, DISPUTED, FREE, OVERLAP, Candidate, gate

VERDICTS = (FREE, OVERLAP, DISPUTED, COMMERCIAL)


def read_file(path: Path) -> list[tuple[str, str, list[str]]]:
    topics: dict[tuple[str, str], list[str]] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(";")]
        if len(parts) != 3 or not all(parts):
            raise EngineError(f"{path.name}, строка {n}: нужно «направление;тема;формулировка» — «{line}»")
        topics.setdefault((parts[0], parts[1]), []).append(parts[2])
    return [(d, t, f) for (d, t), f in topics.items()]


def probe(instance, *, file: str | None = None) -> dict:
    rel = file or instance.p["field"]["file"]
    if not rel:
        return {"status": "не подключено", "why": "field.file пуст — файл тем новых направлений «направление;тема;"
                                                   "формулировка»"}
    path = Path(rel)
    path = path if path.is_absolute() else instance.root / path
    if not path.is_file():
        raise GateRefused(f"файла тем нет: {path}")
    topics = read_file(path)
    cap = instance.p["field"]["max"]
    skipped = max(0, len(topics) - cap)
    rows, requested = [], 0
    for direction, topic, formulations in topics[:cap]:
        try:
            report = gate(instance, Candidate(topic, formulations), offline=True)
        except EngineError as exc:
            rows.append({"direction": direction, "topic": topic, "verdict": "ошибка", "why": str(exc)})
            continue
        requested += report["serp_requested"]
        stop = report["stopped_by"] or {}
        rows.append({"direction": direction, "topic": topic, "verdict": report["verdict"],
                     "why": stop.get("reason", ""), "page": stop.get("page", "")})
    if requested:
        raise EngineError(f"«поле шире» запросило выдачу ({requested}) — гейт без сети нарушен")
    by_dir: dict[str, dict] = {}
    for r in rows:
        d = by_dir.setdefault(r["direction"], {v: 0 for v in VERDICTS + ("ошибка",)})
        d[r["verdict"]] = d.get(r["verdict"], 0) + 1
    out = {"status": "ок", "date": instance.today.isoformat(), "file": str(path), "topics": len(rows),
           "skipped_over_max": skipped, "by_direction": by_dir, "serp_requested": requested}
    out["write"] = store.write_json(instance.root / "data" / "field" / f"{instance.today.isoformat()}.json", "field",
                                    rows, meta={k: v for k, v in out.items() if k != "status"})
    out["rows"] = rows
    (instance.reports_dir() / "field.md").write_text(render(out) + "\n", encoding="utf-8", newline="\n")
    return out


def render(out: dict) -> str:
    if out["status"] != "ок":
        return f"Поле шире: {out['status']} — {out['why']}"
    lines = [f"Поле шире освоенного: тем {out['topics']} через гейт без сети (квота не тратилась)"
             + (f"; сверх field.max не проверено {out['skipped_over_max']}" if out["skipped_over_max"] else "")]
    for d, c in out["by_direction"].items():
        lines.append(f"- {d}: свободно {c[FREE]}, пересекается {c[OVERLAP]}, спорно {c[DISPUTED]}, "
                     f"коммерческая {c[COMMERCIAL]}" + (f", ошибка {c['ошибка']}" if c.get("ошибка") else ""))
    lines.append("новое направление в профиль вписывает человек (directions)")
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("field", help="поле шире освоенного: probe")
    actions = p.add_subparsers(dest="action", required=True)
    p = actions.add_parser("probe", help="темы новых направлений через гейт без сети")
    add_profile(p)
    p.add_argument("--file", help="файл тем вместо field.file")
    p.add_argument("--unattended", action="store_true", help="запуск по расписанию")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    out = probe(instance, file=args.file)
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render(out))
    return 0
