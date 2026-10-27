"""Бюджет квоты источника выдачи (Т7.2): расход считается до запроса и не превышает остаток дня.

Расход — фразы без свежего кэша × базы. Бюджет — остаток, который вернул источник, минус `budget.reserve`, не больше
`--max-quota` и `budget.max_quota`. Помещается не всё — запрашиваются самые частотные фразы (порядок входа), кэш
сохраняется сразу после каждой пачки, печатается, сколько осталось на завтра. После отказа по квоте повторных
запросов нет: прогон останавливается и работает с тем, что уже есть.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import EngineError, QuotaExhausted
from .serp_cache import SerpCache

CHUNK = 100


@dataclass
class SerpPlan:
    needed: int = 0                 # фраз × баз без свежего кэша
    cached: int = 0
    remaining: int | None = None    # остаток дня у источника до прогона
    budget: int = 0
    requested: int = 0
    got: int = 0
    left_for_tomorrow: int = 0
    stopped_by_quota: bool = False
    source: str = ""                # кто ответил: keysso или запасной yandex_search (Н6)
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        text = (f"выдача: нужно {self.needed}, из кэша {self.cached}, бюджет {self.budget} "
                f"(остаток дня {self.remaining if self.remaining is not None else '—'}), запрошено {self.requested}, "
                f"получено {self.got}, осталось на завтра {self.left_for_tomorrow}")
        return text + ("; квота источника исчерпана — повторов нет" if self.stopped_by_quota else "")


def fetch_serp(instance, phrases: list[str], *, max_quota: int | None = None, fresh_only: bool = False,
               offline: bool = False) -> tuple[dict, SerpPlan]:
    """Выдача по фразам во всех базах профиля: {база: {фраза: SerpResult}} и отчёт о бюджете."""
    bases = instance.p["search"]["serp_bases"]
    cache = SerpCache(instance.root, instance.p["thresholds"]["serp_cache_days"], instance.today)
    phrases = list(dict.fromkeys(p.strip() for p in phrases if p.strip()))
    out: dict[str, dict] = {base: {} for base in bases}
    need: list[tuple[str, str]] = []
    plan = SerpPlan()
    for phrase in phrases:
        for base in bases:
            hit = cache.get(phrase, base, fresh_only=fresh_only)
            if hit is not None:
                out[base][phrase] = hit
                plan.cached += 1
            else:
                need.append((phrase, base))
    plan.needed = len(need)
    if not need:
        return out, plan
    if offline:
        plan.left_for_tomorrow = len(need)
        plan.notes.append("без запросов к выдаче (--offline)")
        return out, plan
    serp = instance.serp()
    plan.remaining = serp.remaining()
    budget = max(0, plan.remaining - instance.p["budget"]["reserve"])
    for cap in (max_quota, instance.p["budget"]["max_quota"]):
        if cap is not None:
            if cap < 0:
                raise EngineError(f"предел квоты {cap} < 0")
            budget = min(budget, cap)
    plan.budget = budget
    take = need[:budget]
    plan.left_for_tomorrow = len(need) - len(take)
    for base in bases:
        batch = [p for p, b in take if b == base]
        for start in range(0, len(batch), CHUNK):
            part = batch[start:start + CHUNK]
            try:
                results = serp.top(part, base)
            except QuotaExhausted:
                plan.stopped_by_quota = True
                done = plan.requested
                plan.left_for_tomorrow = len(need) - done
                return out, plan
            plan.requested += len(part)
            plan.source = getattr(serp, "last_source", "") or getattr(serp, "name", "")
            plan.notes += [n for n in getattr(serp, "notes", []) if n not in plan.notes]
            for phrase, result in results.items():
                cache.put(result)
                out[base][phrase] = result
                plan.got += 1
    return out, plan
