"""Аудит недели (модуль Д3, Т35, П52–П54): ошибки находятся со своим уровнем, в отчёте — дельта, пересечения — тем же
кодом, что гейт темы, задачи на склейку — только ниже порога ложных тревог, выпавшие — в плане."""
import json

import pytest

from content_engine.cli import main
from content_engine.core import audit_store, store
from content_engine.core.errors import GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import audit, overlaps
from content_engine.steps.gate_topic import Candidate, gate

from .conftest import GREENHOUSE, TODAY, set_profile, write_article
from .site_fixture import Page

D = "https://example.ru"


def html(title="Отопление теплицы зимой: чем топить", desc="Чем топить теплицу зимой: печь, газ, электричество, "
         "солнце — сравнение по цене и удобству для дачи", canonical=None, body="", h1="Заголовок",
         links=(), heads=("Печь",), url=""):
    canon = f'<link rel="canonical" href="{canonical if canonical is not None else url}">' if canonical != "" else ""
    a = "".join(f'<a href="{l}">ссылка</a>' for l in links)
    h = "".join(f"<h2>{x}</h2><p>текст раздела</p>" for x in heads)
    schema = '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article"}</script>'
    return (f"<html><head><title>{title}</title><meta name=\"description\" content=\"{desc}\">{canon}{schema}"
            f"</head><body><article><h1>{h1}</h1>{h}<p>{body}</p>{a}</article></body></html>")


class Site:
    def __init__(self, pages):
        self.pages = pages

    def __call__(self, method, url, **kw):
        return self.pages.get(url.rstrip("/"), Page(404, "нет"))


def site_pages():
    u = {k: f"{D}/blog/{k}" for k in "abcdef"}
    return {
        u["a"]: Page(200, html(url=u["a"], links=[u["b"], u["c"], u["f"]])),
        u["b"]: Page(200, html(title="", url=u["b"], links=[u["a"]])),
        u["c"]: Page(200, html(url=u["c"], canonical="https://other.example/c", links=[u["a"]])),
        u["d"]: Page(301, "", location=f"{D}/blog/d2"),
        f"{D}/blog/d2": Page(301, "", location=f"{D}/blog/d3"),
        f"{D}/blog/d3": Page(200, html(url=f"{D}/blog/d3")),
        u["e"]: Page(200, html(url=u["e"], links=[f"{D}/blog/a?utm_source=x"])),
        u["f"]: Page(200, html(url=u["f"], heads=("Заказать теплицу",), links=[u["a"]])),
        f"{D}/robots.txt": Page(200, "User-agent: *\nDisallow: /admin\n"),
    }


@pytest.fixture
def audited(project):
    (project.parent / "sitemap.txt").write_text("\n".join(f"{D}/blog/{k}" for k in "abcdef"), encoding="utf-8")
    set_profile(project, lambda d: (d["site"].update(sitemap_file="sitemap.txt"),
                                    d["gate_article"].update(cta_markers=["заказать"])))
    instance = Instance(project, today=TODAY)
    print(audit.write_checklist(instance, apply=True))
    return project


def _run(project, pages, today=TODAY):
    instance = Instance(project, today=today, transport=Site(pages))
    out = audit.run_audit(instance)
    return instance, out


def test_planted_errors_found_with_levels(audited):
    _, out = _run(audited, site_pages())
    got = {(f["check"], f["url"].rsplit("/", 1)[-1]): f["level"] for f in out["findings"]}
    assert got[("title_empty", "b")] == "critical"
    assert got[("canonical_foreign", "c")] == "critical"
    assert got[("redirect_chain", "d")] == "high"
    assert got[("expert_link_params", "e")] == "low"
    assert got[("orphan", "e")] == "medium"                          # на e никто не ссылается
    assert got[("expert_cta_heading", "f")] == "medium"
    assert ("orphan", "a") not in got


def test_second_run_reports_only_delta(audited):
    instance, out = _run(audited, site_pages())
    audit_store.write(instance, "full", out["pages"], out["findings"])
    from datetime import timedelta
    later = TODAY + timedelta(days=7)
    instance2, out2 = _run(audited, site_pages(), later)
    d = audit.delta(out2["previous"], out2["findings"])
    assert d["base"] is False and d["new"] == [] and d["gone"] == []
    pages = site_pages()
    pages[f"{D}/blog/a"] = Page(200, html(title="", url=f"{D}/blog/a", links=[f"{D}/blog/b"]))
    _, out3 = _run(audited, pages, later)
    d = audit.delta(out3["previous"], out3["findings"])
    assert ("title_empty", f"example.ru/blog/a") in {(f["check"], f["url"]) for f in d["new"]}


def test_no_checklist_is_not_connected(project):
    instance = Instance(project, today=TODAY)
    with pytest.raises(Exception, match="не подключено"):
        audit.run_audit(instance)


def twin(project):
    write_article(project.parent / "texts", "otoplenie-teplicy-zimoj", "Отопление теплицы зимой — как топить",
                  GREENHOUSE)


def test_pair_found_by_audit_and_gate_alike(project):
    twin(project)
    instance = Instance(project, today=TODAY)
    page = next(p for p in instance.corpus()[0] if p.slug == "otoplenie-teplicy-zimoj")
    found = overlaps.live_overlaps(instance, [page])
    assert any("otoplenie-teplicy" in " ".join(o["pair"]) and "zimoj" in " ".join(o["pair"]) for o in found)
    heads = [h for h in page.headings if h][:2]
    formulations = list(dict.fromkeys([page.title] + heads + [page.title.lower()]))[:3]
    report = gate(Instance(project, today=TODAY), Candidate(page.title, formulations,
                                                            skip_slugs=frozenset({page.slug})), offline=True)
    hit = {l["page"] for l in report["layers"] if l["outcome"] == "пересекается" and l.get("page")}
    assert any("otoplenie-teplicy" in h for h in hit)


@pytest.mark.parametrize("rate, allowed", [(-1, False), (0.3, False), (0.1, True)])
def test_merge_tasks_only_below_threshold(project, rate, allowed):
    set_profile(project, lambda d: d["audit"]["false_alarm"].update(rate=rate, as_of="2026-02-20" if rate != -1
                                                                    else "", source="П10" if rate != -1 else ""))
    instance = Instance(project, today=TODAY)
    assert overlaps.merge_tasks_allowed(instance)[0] is allowed
    audit_store.write(instance, "full", 1, [{"id": "overlap_article|x ↔ y", "url": "x", "check": "overlap_article",
                                             "level": "high", "detail": "", "first_seen": TODAY.isoformat()}])
    if allowed:
        assert "утверждено" in overlaps.accept(instance, "overlap_article|x ↔ y", "Дмитрий")
        assert overlaps.approved(instance)[0]["id"] == "overlap_article|x ↔ y"
    else:
        with pytest.raises(GateRefused):
            overlaps.accept(instance, "overlap_article|x ↔ y", "Дмитрий")


def test_dropped_page_in_plan_and_limit_before_request(project, monkeypatch):
    calls = []

    class Fake:
        def inspect(self, url):
            calls.append(url)
            return {"indexed": False}

    import content_engine.adapters.gsc_inspect as gi
    monkeypatch.setattr(gi.UrlInspection, "from_profile", classmethod(lambda cls, inst: Fake()))
    set_profile(project, lambda d: (d["sources"]["gsc"].update(enabled=True), d["audit"].update(inspect_limit=2)))
    instance = Instance(project, today=TODAY)
    path = audit_store.folder(instance) / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"example.ru/blog/a": {"google": True}}), encoding="utf-8")
    gone = overlaps.dropped(instance, ["example.ru/blog/a", "example.ru/blog/b", "example.ru/blog/c"])
    assert gone == [{"url": "example.ru/blog/a", "engine": "google"}]
    assert len(calls) == 2                                               # предел — до запроса


def test_advice_checked_against_audit(project):
    instance = Instance(project, today=TODAY)
    audit_store.write(instance, "full", 1, [{"id": "orphan|example.ru/blog/a", "url": "example.ru/blog/a",
                                             "check": "orphan", "level": "medium", "detail": "",
                                             "first_seen": TODAY.isoformat(), "candidates": ["example.ru/blog/b"]}])
    good = {"advice": [{"id": "orphan|example.ru/blog/a", "what": "связать", "link_to": "example.ru/blog/b"}]}
    bad = {"advice": [{"id": "orphan|example.ru/blog/a", "what": "связать", "link_to": "example.ru/blog/z"},
                      {"id": "нет такой", "what": "x"}]}
    assert overlaps.check_advice(instance, good) == []
    assert len(overlaps.check_advice(instance, bad)) == 2


def test_audit_via_main(audited, monkeypatch, capsys):
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, transport=Site(site_pages()),
                                                                      **kw))
    assert main(["audit", "run", "--profile", str(audited)]) == 0
    out = capsys.readouterr().out
    assert "база снята" in out and "Пересечения живых" in out
    assert audit_store.latest(Instance(audited, today=TODAY))["findings"]
