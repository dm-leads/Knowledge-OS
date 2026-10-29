"""ИИ-видимость (Д5, Т37, П56): балл пригодности по признакам, закрытые ИИ-краулеры — находка, robots недоступен —
«нет данных», цитирование: «не подключено», предел квоты до запроса, адреса в процентной кодировке, трекер только на
чтение."""
import json
from urllib.parse import quote

import pytest

from content_engine.adapters.keysso import KeyssoAi
from content_engine.core.errors import EndpointNotAllowed, GateRefused
from content_engine.core.instance import Instance
from content_engine.core.urls import norm_url
from content_engine.steps import ai_visibility as ai

from .conftest import TODAY, set_profile, write_article
from .keysso_transport import Response
from .site_fixture import Page

ANSWER = " ".join(["Теплицу зимой топят печью или тёплым полом, держат 18 градусов днём и 12 ночью."] * 4)
GOOD = (f"{ANSWER}\n\n## Чем топить\n\nПечь греет 20 м² за 2 часа, источник — https://minstroy.example.org/norma.\n\n"
        "## Сколько стоит\n\nОт 3 тысяч рублей в месяц.\n")
BAD = "## Раздел\n\nКороткий текст без чисел.\n"


def corpus(project):
    texts = project.parent / "texts"
    for f in texts.glob("*.md"):
        f.unlink()
    write_article(texts, "horoshaya", "Хорошая статья", GOOD)
    write_article(texts, "plohaya", "Плохая статья", BAD)


def robots(text, status=200):
    def transport(method, url, **kw):
        assert url.endswith("/robots.txt")
        return Page(status, text)
    return transport


def by_url(out):
    return {r["url"].rsplit("/", 1)[1]: r for r in out["rows"]}


def test_score_answer_first_and_without(project):
    corpus(project)
    out = ai.score(Instance(project, today=TODAY, transport=robots("User-agent: *\nDisallow: /admin\n")))
    rows = by_url(out)
    good, bad = rows["horoshaya"], rows["plohaya"]
    assert "прямой ответ в начале" not in good["missing"] and "прямой ответ в начале" in bad["missing"]
    assert "конкретика в первой трети" in bad["missing"] and "источник" in bad["missing"]
    assert good["score"] > bad["score"] and good["max"] == 5 and "свежая дата" in good["no_data"]
    assert bad in out["weak"]


def test_closed_ai_bots_are_finding(project):
    corpus(project)
    text = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
    out = ai.score(Instance(project, today=TODAY, transport=robots(text)))
    assert out["robots"]["blocked"] == ["GPTBot"]
    assert "ИИ-краулеры открыты" in by_url(out)["horoshaya"]["missing"]
    assert ai.blocked_bots("User-agent: *\nDisallow: /\n", ["ClaudeBot"]) == ["ClaudeBot"]
    assert ai.blocked_bots("User-agent: *\nDisallow: /\n\nUser-agent: ClaudeBot\nAllow: /\n", ["ClaudeBot"]) == []


def test_robots_down_is_no_data_not_zero(project):
    corpus(project)
    out = ai.score(Instance(project, today=TODAY, transport=robots("", status=500)))
    good = by_url(out)["horoshaya"]
    assert out["robots"]["status"] == "нет данных" and "ИИ-краулеры открыты" in good["no_data"]
    assert "ИИ-краулеры открыты" not in good["missing"] and good["max"] == 4      # нет даты и robots
    offline = ai.score(Instance(project, today=TODAY), offline=True)
    assert offline["robots"]["status"] == "нет данных"


def test_score_rerun_same_day_not_duplicated(project):
    corpus(project)
    i = Instance(project, today=TODAY)
    ai.score(i, offline=True)
    ai.score(i, offline=True)
    rows = [json.loads(x) for x in (project.parent / "data" / "ai_visibility.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2


def test_citations_not_connected(project):
    out = ai.citations(Instance(project, today=TODAY))
    assert out["status"] == "не подключено" and "keysso" in out["why"]


class Keysso:
    def __init__(self, rows, total=None):
        self.rows, self.total, self.calls = rows, total, []

    def __call__(self, method, url, params=None, **kw):
        self.calls.append(url)
        return Response(200, {"current_page": 1, "last_page": 1, "total": self.total or len(self.rows),
                              "data": self.rows})


@pytest.fixture
def keysso_on(project, monkeypatch):
    monkeypatch.setenv("KEYSO_API_KEY", "ключ")
    set_profile(project, lambda d: d["ai_visibility"].update(citations="keysso"))
    return project


def test_citations_without_quota_limit_refused_no_call(keysso_on):
    api = Keysso([])
    with pytest.raises(GateRefused, match="квота"):
        ai.citations(Instance(keysso_on, today=TODAY), transport=api)
    assert api.calls == []


def test_citations_percent_encoded_host_counted(keysso_on):
    host = quote("сад-огород.рус")
    set_profile(keysso_on, lambda d: d["site"].update(domains=["сад-огород.рус"]))
    api = Keysso([{"word": "отопление теплицы", "ws": 100, "source_urls": ["https://other.ru/a", host],
                   "created_at": "2026-02-01"},
                  {"word": "чужое", "ws": 50, "source_urls": ["https://other.ru/b"], "created_at": "2026-02-01"},
                  {"word": "полив", "ws": 40, "source_urls": [f"https://{host}/blog/poliv"], "created_at": ""}],
                 total=36)
    out = ai.citations(Instance(keysso_on, today=TODAY), max_quota=3, transport=api)
    assert out["cited"] == 2 and out["total"] == 36 and len(api.calls) == 1
    assert dict(out["pages"])[norm_url("https://сад-огород.рус/blog/poliv")] == 1


def test_citations_unattended_uses_profile_limit_only(keysso_on):
    i = Instance(keysso_on, today=TODAY)
    i.unattended = True
    api = Keysso([])
    with pytest.raises(GateRefused):
        ai.citations(i, max_quota=5, transport=api)               # предел из командной строки без человека не действует
    assert api.calls == []
    set_profile(keysso_on, lambda d: d["ai_visibility"].update(max_quota=2))
    i = Instance(keysso_on, today=TODAY)
    i.unattended = True
    assert ai.citations(i, transport=api)["status"] == "ок" and len(api.calls) == 1


def test_citations_dynamics_against_previous_snapshot(keysso_on):
    from datetime import timedelta
    set_profile(keysso_on, lambda d: d["ai_visibility"].update(max_quota=1))
    ai.citations(Instance(keysso_on, today=TODAY - timedelta(days=30)), transport=Keysso([], total=10))
    out = ai.citations(Instance(keysso_on, today=TODAY), transport=Keysso([], total=14))
    assert out["dynamics"]["was"] == 10 and out["dynamics"]["now"] == 14 and out["dynamics"]["days"] == 30


def test_tracker_whitelist_has_no_create():
    client = KeyssoAi("ключ", tracker_id="77", transport=lambda *a, **k: Response(200, {}))
    assert client.client.allowed == {"report/simple/organic/ai-answers", "limits/all", "ai_tracker/77/citation"}
    for endpoint in ("ai_tracker", "ai_tracker/77/start", "ai_tracker/77/prompts"):
        with pytest.raises(EndpointNotAllowed):
            client.client.call("POST", endpoint)
    with pytest.raises(EndpointNotAllowed):
        client.client.call("DELETE", "ai_tracker/77/citation")


def test_tracker_percent_read(project, monkeypatch):
    monkeypatch.setenv("KEYSO_API_KEY", "ключ")
    set_profile(project, lambda d: d["ai_visibility"].update(citations="keysso_tracker", tracker_id="77", max_quota=1))

    def api(method, url, **kw):
        assert url.endswith("ai_tracker/77/citation") and method == "GET"
        return Response(200, {"percent": 17, "delta_percent": 4, "total_matched": 3, "total_with_sources": 18,
                              "chart": {"2026-03-01": 17}})
    out = ai.citations(Instance(project, today=TODAY), transport=api)
    assert out["percent"] == 17 and out["delta_percent"] == 4


def test_unmarked_corpus_sections_and_source_no_data(project):
    from content_engine.adapters.corpus import Page, Section
    page = Page(slug="a", url="https://example.ru/blog/a", title="", split="toc",
                sections=[Section("", "Источник фото: фотобанк"), Section("Раздел", ANSWER + "\n\n" + "слово " * 900)])
    f = ai.page_features(Instance(project, today=TODAY), page)
    assert f["sections"] is None and f["source"] is None       # оглавление снимка — структура и ссылки не видны
    assert f["answer"] is True                                  # подпись фото пропущена, ответ — следующий абзац


def test_weak_by_share_of_measured():
    assert ai.weak({"score": 3, "max": 4}, 4) is False           # 3 из 4 измеренных ≥ 4 из 6
    assert ai.weak({"score": 2, "max": 4}, 4) is True
    assert ai.weak({"score": 0, "max": 0}, 4) is False           # ничего не измерено — не «слабая»
