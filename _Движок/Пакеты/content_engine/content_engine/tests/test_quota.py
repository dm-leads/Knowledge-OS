"""Бюджет квоты и кэш выдачи (Т7.2, Т7.5, П4)."""
from datetime import timedelta

from content_engine.adapters.serp import OK, SerpResult
from content_engine.core.errors import QuotaExhausted
from content_engine.core.instance import Instance
from content_engine.core.quota import fetch_serp

from .conftest import TODAY, set_profile


class CountingSerp:
    """Выдача на любую фразу; считает запрошенные фразы; квота кончается после `fail_after` фраз."""
    name = "счётчик"

    def __init__(self, remaining: int, fail_after: int | None = None):
        self.left = remaining
        self.fail_after = fail_after
        self.asked: list[str] = []
        self.calls_after_refusal = 0
        self.refused = False

    def remaining(self):
        return self.left

    def top(self, phrases, base):
        if self.refused:
            self.calls_after_refusal += 1
        if self.fail_after is not None and len(self.asked) + len(phrases) > self.fail_after:
            self.refused = True
            raise QuotaExhausted("квота выбрана")
        self.asked.extend(phrases)
        return {p: SerpResult(p, base, OK, [(f"site{i}.example/{len(p)}", i) for i in range(1, 11)]) for p in phrases}


PHRASES = [f"фраза {i}" for i in range(10)]


def make(project, serp, **profile):
    if profile:
        set_profile(project, lambda d: d["budget"].update(profile))
    return Instance(project, today=TODAY, serp=serp)


def test_enough_quota_all_requested(project):
    serp = CountingSerp(100)
    out, plan = fetch_serp(make(project, serp), PHRASES)
    assert len(out["msk"]) == 10 and plan.requested == 10 and plan.left_for_tomorrow == 0


def test_not_enough_exactly_what_fits_and_rest_named(project):
    serp = CountingSerp(4)
    _, plan = fetch_serp(make(project, serp), PHRASES)
    assert serp.asked == PHRASES[:4]                 # самые частотные — первыми, ровно сколько помещается
    assert plan.left_for_tomorrow == 6 and "осталось на завтра 6" in plan.line()


def test_reserve_is_not_touched(project):
    serp = CountingSerp(10)
    _, plan = fetch_serp(make(project, serp, reserve=7), PHRASES)
    assert len(serp.asked) == 3 and plan.budget == 3


def test_max_quota_caps_run(project):
    serp = CountingSerp(100)
    _, plan = fetch_serp(make(project, serp), PHRASES, max_quota=2)
    assert len(serp.asked) == 2 and plan.left_for_tomorrow == 8


def test_no_requests_after_quota_refusal(project):
    serp = CountingSerp(1000, fail_after=0)
    instance = make(project, serp)
    _, plan = fetch_serp(instance, [f"ф {i}" for i in range(250)])
    assert plan.stopped_by_quota and plan.requested == 0
    assert serp.calls_after_refusal == 0


def test_cache_reused_then_expires(project):
    serp = CountingSerp(100)
    instance = make(project, serp)
    fetch_serp(instance, PHRASES[:3])
    _, plan = fetch_serp(instance, PHRASES[:3])
    assert plan.cached == 3 and plan.requested == 0
    later = Instance(project, today=TODAY + timedelta(days=46), serp=serp)
    _, plan = fetch_serp(later, PHRASES[:3])
    assert plan.requested == 3


def test_fresh_only_requires_today(project):
    serp = CountingSerp(100)
    fetch_serp(make(project, serp), PHRASES[:2])
    next_day = Instance(project, today=TODAY + timedelta(days=1), serp=serp)
    _, plan = fetch_serp(next_day, PHRASES[:2], fresh_only=True)
    assert plan.requested == 2
