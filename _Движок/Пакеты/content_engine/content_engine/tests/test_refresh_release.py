"""Правка страницы до конца пути (П55, Ч2): гейт статьи командой держит пустое обновление кодом 1; правка проходит
«доехало», а записи дня выкладки не создают новую страницу в карте и новую запись книги — только отметку правки."""
import json
from datetime import timedelta

from content_engine.cli import main
from content_engine.core import bets, store
from content_engine.core.instance import Instance
from content_engine.core.urls import norm_url, slug_of
from content_engine.steps import queue as queue_mod

from .conftest import GREENHOUSE, TODAY
from .site_fixture import Page
from .test_brief import TID, inst  # noqa: F401
from .test_refresh import Site as RefreshSite
from .test_refresh import pos, serp_ok, taken  # noqa: F401
from .test_verify import URL, Site, built


def test_gate_command_refuses_empty_refresh_with_code_1(project, serp_ok, capsys):  # noqa: F811
    out = taken(project, RefreshSite())
    folder = Instance(project, today=TODAY).brief_dir(out["id"])
    (folder / "brief.yaml").write_text(json.dumps({"h1": "Отопление теплицы зимой", "title": "Отопление теплицы зимой",
                                                   "description": "о", "slug": "otoplenie-teplicy",
                                                   "author": "Автор"}, ensure_ascii=False), encoding="utf-8")
    (folder / "article.md").write_text(GREENHOUSE + "\n\nОбновлено сегодня.\n", encoding="utf-8")
    assert main(["gate", "article", "--topic", out["id"], "--offline", "--profile", str(project)]) == 1
    report = json.loads((folder / "gate_article.json").read_text(encoding="utf-8"))
    assert any(r["check"] == "refresh_change" for r in report["reasons"])


def test_refresh_arrives_and_records_only_mark(inst, project):  # noqa: F811
    page_html = built(Instance(project, today=TODAY))
    topics = queue_mod.load(inst)
    topics[0].update(type="refresh", page=norm_url(URL), slug=slug_of(URL))
    queue_mod.save(inst, topics)
    store.write_json(inst.file("bets"), "bets", [{"id": "T-OLD", "url": norm_url(URL), "published": "2025-01-10",
                                                  "measure": {}, "result": None}])
    pages_before = len(store.read_json(inst.file("demand_map"), "demand_map", missing_ok=True)["records"])
    site = Site()
    site.pages[norm_url(URL)] = Page(200, page_html)
    day = TODAY + timedelta(days=1)
    import content_engine.cli as cli
    real = cli.open_instance
    cli.open_instance = lambda args, **kw: real(args, today=day, transport=site)
    try:
        assert main(["publish", "verify", TID, "--url", URL, "--no-browser", "--profile", str(project)]) == 0
    finally:
        cli.open_instance = real
    after = Instance(project, today=day)
    assert queue_mod._find(queue_mod.load(after), TID)["status"] == queue_mod.PUBLISHED
    book = bets.load(after)
    assert len(book) == 1 and book[0]["refreshed"] == [{"day": day.isoformat(), "id": TID}]
    data = store.read_json(after.file("demand_map"), "demand_map", missing_ok=True)["records"]
    assert len(data) == pages_before and not any(p.get("source") == "запись выпуска" for p in data)
