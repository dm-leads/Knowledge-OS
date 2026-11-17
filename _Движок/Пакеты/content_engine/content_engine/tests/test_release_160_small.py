"""1.6.0, мелкие правки переноса (аудит 17.11.2026, правка 7): новый сайт с пустым корпусом — жёлтый, а не красный;
`queue refill` без кандидатов при запасе ниже нормы — код 1 с подсказкой."""
from datetime import timedelta

from content_engine.cli import main
from content_engine.core.instance import Instance
from content_engine.steps import check

from .conftest import TODAY, set_profile
from .site_fixture import Page


def _corpus_item(project):
    instance = Instance(project, today=TODAY, transport=lambda *a, **k: Page(200, "<html><body>сайт</body></html>"))
    return next(i for i in check.run_check(instance) if i["item"] == "корпус")


def _empty_corpus(project):
    folder = project.parent / "empty"
    folder.mkdir()
    set_profile(project, lambda d: d["corpus"]["folders"][0].update(path="empty"))


def test_new_site_with_empty_corpus_is_yellow(project):
    _empty_corpus(project)
    set_profile(project, lambda d: d["queue"]["pace"].update(domain_since=(TODAY - timedelta(days=20)).isoformat()))
    assert _corpus_item(project)["light"] == check.YELLOW


def test_empty_corpus_without_domain_date_is_red_with_hint(project):
    _empty_corpus(project)
    set_profile(project, lambda d: d["queue"]["pace"].update(domain_since=""))
    item = _corpus_item(project)
    assert item["light"] == check.RED and "corpus import" in item["hint"]


def test_refill_without_queue_exits_one(project, capsys):
    assert main(["queue", "refill", "--profile", str(project)]) == 1
    assert "queue build" in capsys.readouterr().out
