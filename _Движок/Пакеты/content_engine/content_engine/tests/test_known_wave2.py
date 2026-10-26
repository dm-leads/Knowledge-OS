"""Известные ответы части 1 (Т25.1, П37), пункты самопроверки (Т25.2) и сверка с .gitignore без git (Т42.4)."""
import yaml

from content_engine.cli import main
from content_engine.cli import open_instance as REAL_OPEN
from content_engine.core.ignore import Ignore
from content_engine.core.instance import Instance
from content_engine.steps.check import run_check

from .conftest import TODAY
from .site_fixture import Page
from .test_verify import Site, old_article_page

CASES = {"cases": [
    {"name": "старая статья доехала", "kind": "publish", "id": "otoplenie-teplicy",
     "url": "https://example.ru/blog/otoplenie-teplicy", "expect": "доехало"},
    {"name": "подменённый адрес не доехал", "kind": "publish", "id": "otoplenie-teplicy",
     "url": "https://example.ru/blog/net-takoj", "expect": "не доехало"},
    {"name": "сборка без брифа", "kind": "refuse", "action": "assemble_unapproved"},
    {"name": "второй прогон", "kind": "refuse", "action": "second_run_locked"},
    {"name": "публикация выше ступени", "kind": "refuse", "action": "above_rights", "area": "publish",
     "level": "publish"},
    {"name": "запись без снимка", "kind": "refuse", "action": "no_snapshot"},
]}


def patched(monkeypatch, site):
    import content_engine.cli as cli
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: REAL_OPEN(args, today=TODAY, transport=site))


def test_wave2_cases_from_separate_file(project, monkeypatch, capsys):
    site = Site()
    site.pages["example.ru/blog/otoplenie-teplicy"] = Page(200, old_article_page())
    patched(monkeypatch, site)
    (project.parent / "known_answers.wave2.yaml").write_text(yaml.safe_dump(CASES, allow_unicode=True),
                                                             encoding="utf-8")
    assert main(["known-answers", "--profile", str(project), "--cases", "known_answers.wave2.yaml"]) == 0
    out = capsys.readouterr().out
    assert "совпало 6 из 6" in out
    assert not (project.parent / "data" / "publications.jsonl").exists()          # известные ответы не пишут
    assert not list((project.parent / "data").glob("briefs/*"))


def test_refusal_that_passes_is_mismatch(project, monkeypatch, capsys):
    from .conftest import set_profile
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    patched(monkeypatch, Site())
    cases = {"cases": [CASES["cases"][4]]}
    (project.parent / "ka.yaml").write_text(yaml.safe_dump(cases, allow_unicode=True), encoding="utf-8")
    assert main(["known-answers", "--profile", str(project), "--cases", "ka.yaml"]) == 1
    assert "отказа нет" in capsys.readouterr().out


def test_check_shows_wave2_items(project):
    items = {i["item"]: i for i in run_check(Instance(project, today=TODAY, transport=Site()))}
    for name in ("ступени прав", "сайт на чтение", "файл ключа IndexNow", "браузер", "контракт сборщика",
                 "контракт публикатора", "роли моделей", "правила игнорирования"):
        assert name in items, name
    assert items["контракт сборщика"]["light"] == "зелёный"
    assert items["файл ключа IndexNow"]["light"] == "не подключено"
    assert items["сайт на чтение"]["light"] == "красный"            # синтетический сайт не отдаёт главную


def tree(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    # как у потребителя: правило *.json — в .gitignore раздела, исключения и рабочие папки — в .gitignore инстанса
    (root / ".gitignore").write_text("*.json\ncache/\n/root.txt\n**/tmp\n", encoding="utf-8")
    inst = root / "blog" / "instance"
    for rel in ("profile.yaml", "data/queue.json", "data/bets.json", "data/publications.jsonl", "cache/pages/a.json",
                "data/briefs/T1/brief.yaml", "data/briefs/T1/keep.json", "root.txt", "blog/root.txt",
                "a/b/tmp/x.txt", "sub/keep.json", "sub/drop.json"):
        path = (inst / rel) if not rel.startswith(("root", "blog/", "a/", "sub/")) else (root / rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (root / "sub" / ".gitignore").write_text("!keep.json\n", encoding="utf-8")
    (root / "blog" / "instance" / ".gitignore").write_text("data/briefs/\n!data/bets.json\n!data/briefs/T1/keep.json\n",
                                                            encoding="utf-8")
    return root, inst


def test_gitignore_rules_without_git(tmp_path):
    root, inst = tree(tmp_path)
    ig = Ignore(inst)
    assert ig.root == root.resolve()

    def ignored(path):
        return ig.ignored(path)[0]
    assert not ignored(inst / "profile.yaml")
    assert ignored(inst / "data" / "queue.json") and not ignored(inst / "data" / "bets.json")
    assert not ignored(inst / "data" / "publications.jsonl")
    assert ignored(inst / "cache" / "pages" / "a.json")
    assert ignored(inst / "data" / "briefs" / "T1" / "brief.yaml")
    assert ignored(inst / "data" / "briefs" / "T1" / "keep.json")        # из игнорируемой папки не вернуть
    assert ignored(root / "root.txt") and not ignored(root / "blog" / "root.txt")
    assert ignored(root / "a" / "b" / "tmp" / "x.txt")
    assert not ignored(root / "sub" / "keep.json") and ignored(root / "sub" / "drop.json")
    assert "*.json" in ig.ignored(inst / "data" / "queue.json")[1]
