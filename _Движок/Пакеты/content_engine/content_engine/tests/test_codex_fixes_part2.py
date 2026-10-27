"""Замечания второй модели (Codex) к части 2, принятые в З47: потолок и «да» — на весь прогон, цена — конечное
неотрицательное число, сумма вычитки учитывает alt, библиотека фактов — только из круга текущего текста, имена
устройств Windows, длина ответа эмбеддингов."""
import json

import pytest

from content_engine.adapters import embeddings
from content_engine.core import budget, facts
from content_engine.core.errors import BudgetRefused, EngineError, SourceError
from content_engine.core.instance import Instance
from content_engine.core.transfer import clean_name
from content_engine.steps import review

from .conftest import TODAY, set_profile
from .test_brief import TID, inst  # noqa: F401
from .test_schema_assemble import ready_topic


def limits(project, run=10, month=100):
    set_profile(project, lambda d: d["budget"].update(run_limit_rub=run, month_limit_rub=month))
    return Instance(project, today=TODAY)


def test_run_limit_and_yes_cover_whole_command(project):
    instance = limits(project)
    calls = []
    budget.paid_call(instance, "T", "шаг", 6, lambda: calls.append(1), confirm=12, unattended=False, what="а", source="s")
    with pytest.raises(BudgetRefused, match="потолка прогона"):
        budget.paid_call(instance, "T", "шаг", 6, lambda: calls.append(2), confirm=12, unattended=False, what="б",
                         source="s")
    instance = limits(project, run=20)
    budget.paid_call(instance, "T", "шаг", 6, lambda: calls.append(3), confirm=8, unattended=False, what="в", source="s")
    with pytest.raises(BudgetRefused, match="за прогон — 12.00"):
        budget.paid_call(instance, "T", "шаг", 6, lambda: calls.append(4), confirm=8, unattended=False, what="г",
                         source="s")
    assert calls == [1, 3]


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1.0])
def test_bad_price_refused(project, price):
    instance = limits(project)
    with pytest.raises(BudgetRefused, match="не конечное"):
        budget.gate(instance, price, confirm=0, unattended=False, what="x")


def test_alt_change_resets_factcheck_digest():
    a = "---\ntitle: т\nimage: https://cdn.example.ru/1.png\n---\n# H\n\n![Срок службы 50 лет](https://cdn.example.ru/a.png)\n"
    b = a.replace("50 лет", "100 лет")
    c = a.replace("https://cdn.example.ru/a.png", "https://cdn.example.ru/b.png").replace("1.png", "2.png")
    assert review.normalized_text(a) != review.normalized_text(b)          # alt — часть утверждений
    assert review.normalized_text(a) == review.normalized_text(c)          # адрес картинки — нет
    assert "image: обещание" in review.normalized_text(a + "\nimage: обещание\n")   # строка в теле — текст


def test_stale_round_does_not_feed_fact_library(inst, project):  # noqa: F811
    ready_topic(inst)
    review.factcheck_task(inst, TID)
    folder = review.factcheck_dir(inst, TID)
    (folder / "claims.jsonl").write_text(json.dumps({"n": 1, "claim": "зубок сажают на 5 см", "type": "number",
                                                     "source": "https://example.org/x", "result": "confirmed"},
                                                    ensure_ascii=False) + "\n", encoding="utf-8")
    article = inst.brief_dir(TID) / "article.md"
    article.write_text(article.read_text(encoding="utf-8") + "\nНовое утверждение.\n", encoding="utf-8")
    out = review.check(inst, TID)
    assert not out["clean"] and "прежнему тексту" in out["library"] and facts.read(inst) == []


def test_windows_device_names_and_empty():
    assert clean_name("CON") == "_CON" and clean_name("nul.txt") == "_nul.txt"
    with pytest.raises(EngineError, match="пустое"):
        clean_name('???')


def test_embeddings_short_answer_not_cached(project):
    set_profile(project, lambda d: d["budget"].update(run_limit_rub=10, month_limit_rub=100))
    instance = Instance(project, today=TODAY)

    class Short:
        def price(self, texts):
            return 1.0

        def embed(self, texts):
            return [[1.0, 0.0]]

    with pytest.raises(SourceError, match="векторов"):
        embeddings.vectors(instance, ["a", "b"], tid="T", confirm=1.0, unattended=False, provider=Short())
    assert not list((instance.root / "cache" / "embeddings").glob("*.json"))
