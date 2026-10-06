"""Разбор числа: из чего оно сложилось и что из него выпало.

Отвечает на вопрос «почему столько, а не столько» без открывания чужих систем: показывает путь от всех
сделок к зачётному числу по шагам, называет каждую отсечённую группу и её объём.

Это не новая метрика — те же факты и то же правило, что у `new_first_sql`, только с раскрытыми промежуточными
величинами. Число в конце обязано совпасть с числом метрики, и тест это проверяет.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from datacore.schema.config import load_config
from datacore.schema.engine import connect
from datacore.serve.storage import storage_url
from datacore.schema.errors import RuleViolation
from datacore.serve.contour import contour_clause, contour_needs_visit
from datacore.serve.metrics import _crm_systems, _own_marker_join, window


def _join(cfg, contour: str) -> str:
    join = _own_marker_join(cfg)
    if contour_needs_visit(cfg, contour):
        join += (" LEFT JOIN facts.visit v ON v.visit_id = m.visit_id "
                 "AND v.source_system = m.source_system")
    return join


def ladder(engine, cfg, scope: str, start: date, end: date, contour: str) -> list[dict]:
    """Лестница отбора: сколько сделок остаётся после каждого шага правила.

    Шаги идут в том же порядке, что и в конфигурации набора: сначала база (какие сделки вообще берём),
    потом исключения по одному. Так видно цену каждого правила отдельно."""
    contours = cfg.contours or {}
    if contour not in contours:
        raise RuleViolation("К2", f"набор отбора «{contour}» не объявлен: {sorted(contours)}")
    rules = contours[contour].get("exclude") or []
    lo, hi = window(start, end, cfg.timezone)
    join = _join(cfg, contour)
    brand = "" if scope == "company" else " AND d.brand = ?"
    brand_args = () if scope == "company" else (scope,)
    common = (f"FROM facts.deal d {join} WHERE d.deleted_at IS NULL "
              f"AND d.created_at >= ? AND d.created_at < ?{brand}"
              " AND NOT EXISTS (SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal' "
              "AND e.entity_key = CAST(d.deal_id AS VARCHAR))")

    steps = []
    n = engine.fetchone(f"SELECT COUNT(DISTINCT d.deal_id) {common}", (lo, hi, *brand_args))[0]
    steps.append({"name": "все сделки периода", "value": n, "cut": 0})

    if (contours[contour].get("base") or {}).get("new_first_only"):
        prev = n
        n = engine.fetchone(f"SELECT COUNT(DISTINCT d.deal_id) {common} AND d.is_new_first",
                            (lo, hi, *brand_args))[0]
        steps.append({"name": "только новые первые квалифицированные", "value": n, "cut": prev - n})

    # Исключения применяются накопительно: каждое следующее считается поверх предыдущих. База набора уже
    # посчитана отдельным шагом, поэтому здесь она добавляется в запрос явно — иначе счётчик прыгал бы вверх.
    base_sql = " AND d.is_new_first" if (contours[contour].get("base") or {}).get("new_first_only") else ""
    if (contours[contour].get("base") or {}).get("first_per_contact"):
        from datacore.serve.contour import FIRST_PER_CONTACT_SQL
        base_sql += f" AND {FIRST_PER_CONTACT_SQL}"
        prev = n
        n = engine.fetchone(f"SELECT COUNT(DISTINCT d.deal_id) {common}{base_sql}", (lo, hi, *brand_args))[0]
        steps.append({"name": "одна сделка на контакт", "value": n, "cut": prev - n})
    applied, applied_params = [], []
    for i, rule in enumerate(rules, 1):
        single, single_params = contour_clause(_one_rule_cfg(cfg, contour, i), contour)
        applied.append(single)
        applied_params.extend(single_params)
        clause = " AND ".join(x for x in applied if x)
        prev = n
        n = engine.fetchone(f"SELECT COUNT(DISTINCT d.deal_id) {common}{base_sql} AND {clause}",
                            (lo, hi, *brand_args, *applied_params))[0]
        field = rule.get("field", "?")
        keep = " (часть возвращена по площадке)" if rule.get("keep_referrer") else ""
        steps.append({"name": f"исключение по полю «{field}»{keep}", "value": n, "cut": prev - n})
    return steps


def _one_rule_cfg(cfg, contour: str, upto: int):
    """Копия конфигурации, где у набора оставлены только первые `upto` правил и его база.

    Нужна, чтобы считать лестницу накопительно тем же кодом, что строит обычное условие: иначе разбор
    пришлось бы писать вторым способом, и он мог бы разойтись с метрикой."""
    import copy
    clone = copy.copy(cfg)
    contours = copy.deepcopy(cfg.contours or {})
    c = contours[contour]
    c["base"] = {}                                    # база уже посчитана отдельным шагом
    c["exclude"] = (c.get("exclude") or [])[upto - 1:upto]
    object.__setattr__(clone, "contours", contours)
    return clone


def breakdown(engine, cfg, scope: str, start: date, end: date, contour: str, field: str = "marker_level_1"):
    """Из чего состоит зачётное число: разбивка по источнику, каналу или техканалу."""
    from datacore.serve.contour import FIELD_SQL
    if field not in FIELD_SQL:
        raise RuleViolation("К2", f"разбивка по «{field}» не поддержана, только {sorted(FIELD_SQL)}")
    clause, params = contour_clause(cfg, contour)
    lo, hi = window(start, end, cfg.timezone)
    join = _join(cfg, contour)
    brand = "" if scope == "company" else " AND d.brand = ?"
    brand_args = () if scope == "company" else (scope,)
    where = f" AND {clause}" if clause else ""
    return engine.fetchall(
        f"SELECT COALESCE({FIELD_SQL[field]}, '(не заполнено)'), COUNT(DISTINCT d.deal_id) "
        f"FROM facts.deal d {join} WHERE d.deleted_at IS NULL "
        f"AND d.created_at >= ? AND d.created_at < ?{brand}"
        " AND NOT EXISTS (SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal' "
        f"AND e.entity_key = CAST(d.deal_id AS VARCHAR)){where} GROUP BY 1 ORDER BY 2 DESC",
        (lo, hi, *brand_args, *params))


def cut_reasons(engine, cfg, scope: str, start: date, end: date, contour: str):
    """Кого правило отсекло и по какой причине — поимённо по значениям полей."""
    from datacore.serve.contour import FIELD_SQL
    contours = cfg.contours or {}
    rules = (contours.get(contour) or {}).get("exclude") or []
    clause, params = contour_clause(cfg, contour)
    lo, hi = window(start, end, cfg.timezone)
    join = _join(cfg, contour)
    brand = "" if scope == "company" else " AND d.brand = ?"
    brand_args = () if scope == "company" else (scope,)
    out = []
    for rule in rules:
        column = FIELD_SQL[rule["field"]]
        values = [str(v) for v in rule.get("exclude") or []]
        if values:
            marks = ", ".join("?" for _ in values)
            rows = engine.fetchall(
                f"SELECT {column}, COUNT(DISTINCT d.deal_id) FROM facts.deal d {join} "
                f"WHERE d.deleted_at IS NULL AND d.is_new_first "
                f"AND d.created_at >= ? AND d.created_at < ?{brand} "
                f"AND {column} IN ({marks}) GROUP BY 1 ORDER BY 2 DESC",
                (lo, hi, *brand_args, *values))
            for value, n in rows:
                out.append({"field": rule["field"], "value": value, "leads": n})
        for prefix in (str(v) for v in rule.get("exclude_prefix") or []):
            rows = engine.fetchall(
                f"SELECT {column}, COUNT(DISTINCT d.deal_id) FROM facts.deal d {join} "
                f"WHERE d.deleted_at IS NULL AND d.is_new_first "
                f"AND d.created_at >= ? AND d.created_at < ?{brand} "
                f"AND {column} LIKE ? GROUP BY 1 ORDER BY 2 DESC",
                (lo, hi, *brand_args, prefix + "%"))
            for value, n in rows:
                out.append({"field": rule["field"], "value": f"{value} (по началу строки)", "leads": n})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Разбор числа: из чего сложилось (только чтение)")
    ap.add_argument("--scope", default="company")
    ap.add_argument("--contour", default="digital")
    ap.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    ap.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    ap.add_argument("--by", default="marker_level_1", help="поле разбивки зачётного числа")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)

    cfg = load_config(Path(a.config) if a.config else
                      Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml")
    url = storage_url(cfg, a.db)
    engine = connect(url, read_only=True)
    try:
        print(f"Разбор: {a.scope} · набор «{a.contour}» · "
              f"{a.start:%d.%m.%Y}–{a.end:%d.%m.%Y} (правая граница исключающая)\n")

        print("ЛЕСТНИЦА ОТБОРА — сколько остаётся после каждого шага:\n")
        steps = ladder(engine, cfg, a.scope, a.start, a.end, a.contour)
        width = max(len(s["name"]) for s in steps)
        for s in steps:
            cut = f"  −{s['cut']}" if s["cut"] else ""
            print(f"  {s['name']:{width}}  {s['value']:>6}{cut}")

        print("\n\nЧТО ОТСЕКЛИ — по значениям полей:\n")
        for row in cut_reasons(engine, cfg, a.scope, a.start, a.end, a.contour):
            print(f"  {row['field']:22} «{str(row['value'])[:34]:36}» {row['leads']:>5}")

        print(f"\n\nИЗ ЧЕГО СОСТОИТ ЗАЧЁТНОЕ ЧИСЛО — по полю «{a.by}»:\n")
        total = 0
        for value, n in breakdown(engine, cfg, a.scope, a.start, a.end, a.contour, a.by):
            total += n
            print(f"  {str(value)[:44]:46} {n:>5}")
        print(f"  {'ИТОГО':46} {total:>5}")
    except RuleViolation as exc:
        print(exc)
        return 1
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
