"""Новые источники спроса (Т31, П46) и запасной источник выдачи (Н6): вопросы клиентов без персональных данных,
Wordstat — Keys.so, при отказе облако через бюджет-гейт, подсказки без частоты не входят, у числа по подстроке —
примеры; Yandex Search API отвечает, когда Keys.so отказал."""
import base64

import pytest

from content_engine.adapters.client_questions import ClientQuestions
from content_engine.adapters.yandex_search import FallbackSerp, YandexSearchSerp
from content_engine.cli import main
from content_engine.core.errors import AccessDenied, BudgetRefused, EngineError
from content_engine.core.instance import Instance
from content_engine.steps.demand import collect, render

from .conftest import TODAY, set_profile
from .keysso_transport import Response


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for name in ("KEYSO_API_KEY", "YANDEX_SEARCH_API_KEY", "YANDEX_FOLDER_ID"):
        monkeypatch.setenv(name, "x")


def questions(project, text):
    path = project.parent / "questions.csv"
    path.write_text(text, encoding="utf-8")
    set_profile(project, lambda d: d["sources"]["client_questions"].update(enabled=True, path="questions.csv"))
    return path


def test_client_questions_reject_personal_data(project):
    path = questions(project, "question;count;name\nКак утеплить теплицу?;3;Иван\n")
    with pytest.raises(EngineError, match="ровно"):
        ClientQuestions(path, TODAY.isoformat()).read()
    path.write_text("question;count\nПерезвоните на +79001112233?;1\n", encoding="utf-8")
    with pytest.raises(EngineError, match="телефон"):
        ClientQuestions(path, TODAY.isoformat()).read()
    path.write_text("question;count\nПишите на a.b@mail.example?;1\n", encoding="utf-8")
    with pytest.raises(EngineError, match="почта"):
        ClientQuestions(path, TODAY.isoformat()).read()


def test_answered_questions_go_to_report_others_become_demand(project):
    questions(project, "question;count\nКакой полив теплицы зимой?;7\nЧем подкормить клубнику весной?;4\n")
    report = collect(Instance(project, today=TODAY))
    item = report["sources"]["client_questions"]
    assert item["status"] == "ок" and item["rows"] == 1
    assert any("теплиц" in a["question"] for a in item["already"])
    assert "уже отвечено" in render(report)


class Cloud:
    """Подмена облака Wordstat и Keys.so."""

    def __init__(self, keysso_status=401):
        self.keysso_status = keysso_status
        self.cloud_calls = 0

    def __call__(self, method, url, params=None, json=None, headers=None, timeout=None):
        if "api.keys.so" in url:
            if self.keysso_status != 200:
                return Response(self.keysso_status, {})
            if "similarkeys" in url:
                return Response(200, {"data": [{"word": "теплица из поликарбоната", "ws": 900},
                                               {"word": "теплица своими руками отзывы", "ws": None}]})
            return Response(200, [{"ws": 5400, "wsk": 300}])
        self.cloud_calls += 1
        if url.endswith("topRequests"):
            results = [{"phrase": f"теплица вариант {i}", "count": 100 - i} for i in range(20)]
            return Response(200, {"results": [{"phrase": json["phrase"], "count": 5000}] + results,
                                  "associations": [{"phrase": "парник", "count": 300}, {"phrase": "шум", "count": 0}]})
        return Response(200, {"results": [{"date": "2026-01-01T00:00:00Z", "count": 4000},
                                          {"date": "2026-02-01T00:00:00Z", "count": 4200}]})


def wordstat(project, order, price=0):
    set_profile(project, lambda d: (d["sources"]["wordstat"].update(order=order, cloud_price_rub=price),
                                    d["sources"]["suggest"].update(enabled=True, limit=10),
                                    d["sources"]["keysso"].update(enabled=True),
                                    d["directions"].__setitem__(slice(None), [d["directions"][0]])))


def test_wordstat_keysso_frequency_and_suggest_without_frequency_dropped(project):
    wordstat(project, ["keysso", "cloud"], price=0.02)
    site = Cloud(keysso_status=200)
    report = collect(Instance(project, today=TODAY, transport=site))
    item = report["sources"]["wordstat"]
    assert item["source"] == "keysso" and item["dropped_no_frequency"] == 1 and site.cloud_calls == 0
    assert item["requests"] >= 2                                              # расход Keys.so виден в отчёте


def test_wordstat_falls_back_to_cloud_through_budget_gate(project):
    wordstat(project, ["keysso", "cloud"], price=0.5)
    site = Cloud(keysso_status=401)
    with pytest.raises(BudgetRefused):
        collect(Instance(project, today=TODAY, transport=site))
    assert site.cloud_calls == 0                                              # без «да» — ноль платных вызовов
    set_profile(project, lambda d: d["budget"].update(run_limit_rub=10, month_limit_rub=100))
    report = collect(Instance(project, today=TODAY, transport=site), confirm=1.0)
    item = report["sources"]["wordstat"]
    assert item["source"] == "cloud" and any("keysso" in f for f in item["fallback"])
    sample = item["samples"]["теплица"]
    assert 10 <= len(sample) <= 15
    rows = {r["query"]: r for r in __import__("content_engine.steps.demand", fromlist=["load"]).load(
        Instance(project, today=TODAY))}
    assert rows["теплица"]["history"][0] == {"month": "2026-01", "count": 4000.0}
    assert "шум" not in rows and item["dropped_no_frequency"] == 1
    assert "примеры для проверки чтением" in render(report)


def xml(urls):
    body = "<yandexsearch>" + "".join(f"<doc><url>{u}</url></doc>" for u in urls) + "</yandexsearch>"
    return base64.b64encode(body.encode("utf-8")).decode()


def test_yandex_search_answers_when_keysso_refuses(project):
    set_profile(project, lambda d: (d["sources"].update(serp_order=["keysso", "yandex_search"]),
                                    d["sources"]["keysso"].update(enabled=True),
                                    d["budget"].update(run_limit_rub=10, month_limit_rub=100),
                                    d["sources"]["yandex_search"].update(price_rub=0.4)))
    calls = []

    def site(method, url, params=None, json=None, headers=None, timeout=None):
        calls.append(url)
        if "api.keys.so" in url:
            return Response(401, {})
        return Response(200, {"rawData": xml([f"https://site{i}.example/a" for i in range(12)])})
    instance = Instance(project, today=TODAY, transport=site)
    serp = instance.serp()
    assert isinstance(serp, FallbackSerp)
    with pytest.raises(BudgetRefused):
        serp.top(["отопление теплицы"], "msk")
    instance.confirm_spend = 1.0
    result = serp.top(["отопление теплицы"], "msk")["отопление теплицы"]
    assert serp.last_source == "yandex_search" and len(result.top) == 10 and result.top[0][1] == 1
    assert any("keysso отказал" in n for n in serp.notes)
    with pytest.raises(Exception, match="не подключено"):
        YandexSearchSerp(instance).site_keywords("example.ru", "msk", 10)
