"""Вердикт дня (модуль Д1, Т34.3): первое сработавшее правило, пороги — в профиле (`monitor`).

1. сайт не отвечает (главная или последняя статья — не 200, или сеть) → красный;
2. публикаций нет дольше `silence_intervals` интервалов ритма (интервал = 1 / `queue.per_day` дней) → красный;
3. критичный дрифт или критичные находки последнего аудита → красный;
4. запас тем меньше чем на `stock_yellow_days` дней → жёлтый;
5. аудит старше `audit_stale_days` или аудита нет → жёлтый;
6. иначе зелёный.
Правило без данных не срабатывает: оно пишется в «не проверено» рядом с цветом — панель и сводка показывают его,
чтобы зелёный с непроверенным не читался как «всё проверено».
У каждого правила — что сделать (команда и файл), чтобы агент чинил по готовому заданию.
"""
from __future__ import annotations

from datetime import date

RED, YELLOW, GREEN = "красный", "жёлтый", "зелёный"


def decide(instance, facts: dict) -> dict:
    """facts: site_ok (bool|None), site_why, last_publication (str|None), drift_critical (int|None),
    audit_critical (int|None), audit_age (int|None), stock_days (float|None)."""
    m, per_day = instance.p["monitor"], instance.p["queue"]["per_day"]
    unchecked = []

    def out(color, rule, why, todo):
        return {"color": color, "rule": rule, "why": why, "todo": todo, "unchecked": unchecked}

    if facts.get("site_ok") is False:
        return out(RED, 1, f"сайт не отвечает: {facts.get('site_why', '')}",
                   "открыть сайт в браузере; если отвечает — повторить `pulse run` (сеть устройства)")
    if facts.get("site_ok") is None:
        unchecked.append("сайт не проверялся")
    unchecked += list(facts.get("unchecked") or [])
    if per_day > 0:
        limit = m["silence_intervals"] / per_day
        last = facts.get("last_publication")
        if last is None:
            unchecked.append("публикаций машины в журнале выпуска нет")
        else:
            idle = (instance.today - date.fromisoformat(last)).days
            if idle > limit:
                return out(RED, 2, f"публикаций нет {idle} дн. (порог {limit:g} дн. при ритме {per_day:g} в день)",
                           "`content-engine status` → взять тему `queue take`, выпустить по маршруту статьи")
    drift_c, audit_c = facts.get("drift_critical"), facts.get("audit_critical")
    if drift_c:
        return out(RED, 3, f"критичный дрифт: {drift_c}", "открыть панель — блок «дрифт», чинить страницу; "
                                                           "эталон: data/snapshots/live/")
    if audit_c:
        return out(RED, 3, f"критичные находки аудита: {audit_c}", "отчёт аудита reports/<дата>/audit.md")
    stock = facts.get("stock_days")
    if stock is None:
        unchecked.append("запас тем не прочитан")
    elif stock < m["stock_yellow_days"]:
        return out(YELLOW, 4, f"запаса тем на {stock:g} дн. (меньше {m['stock_yellow_days']:g})",
                   "`content-engine queue refill`")
    age = facts.get("audit_age")
    if age is None or age > m["audit_stale_days"]:
        return out(YELLOW, 5, "аудита нет" if age is None else f"аудит {age} дн. назад (старше "
                                                                 f"{m['audit_stale_days']})", "`content-engine audit run`")
    return out(GREEN, 6, "правила красного и жёлтого не сработали", "")
