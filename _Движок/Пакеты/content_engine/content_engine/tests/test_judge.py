"""Судья спорного (Т27, П39–П40): выбирает только из пятёрки, цитата — из текста раздела, без эмбеддингов —
лексическая пятёрка, эмбеддинги API — через бюджет-гейт; задание судье при пополнении запаса."""
import json

import pytest

from content_engine.cli import main
from content_engine.core import store
from content_engine.core.instance import Instance
from content_engine.steps import judge
from content_engine.steps import queue as queue_mod
from content_engine.steps import reservations

from .conftest import TODAY, set_profile
from .test_brief import TID, inst  # noqa: F401


def disputed(instance):
    report = instance.root / "gate-report.json"
    report.write_text(json.dumps({"stopped_by": {"page": "zametki-oktyabr"}, "layers": []}, ensure_ascii=False),
                      encoding="utf-8")
    topics = queue_mod.load(instance)
    topics[0].update(status=queue_mod.CANDIDATE, gate={"date": TODAY.isoformat(), "verdict": "спорно",
                                                       "complete": True, "report": str(report)})
    queue_mod.save(instance, topics)


def answer(instance, **fields):
    (instance.brief_dir(TID) / judge.ANSWER).write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")


def test_lexical_five_task_and_free_with_angle_reserves(inst):  # noqa: F811
    disputed(inst)
    out = judge.make_task(inst, TID)
    assert out["kind"] == "лексическая пятёрка" and 0 < out["sections"] <= 5
    assert out["risk"].endswith("zametki-oktyabr")
    task = (inst.brief_dir(TID) / "task_judge.md").read_text(encoding="utf-8")
    assert "Статья-риск целиком" in task and "Роль: judge" in task
    context = json.loads((inst.brief_dir(TID) / judge.CONTEXT).read_text(encoding="utf-8"))
    first = context["sections"][0]
    quote = " ".join(first["text"].split()[:6])
    answer(inst, verdict="free_with_angle", section_id=first["id"], quote=quote.replace("—", "—"),
           angle="сроки по температуре почвы")
    dry = judge.check(inst, TID, dry_run=True)
    assert dry["accepted"] and not inst.file("judgements").exists()
    out = judge.check(inst, TID)
    assert out["accepted"] and "gated с углом" in out["queue"]
    topic = queue_mod.load(inst)[0]
    assert topic["status"] == queue_mod.GATED and topic["angle"] == "сроки по температуре почвы"
    assert reservations.load(inst)[0]["queue_id"] == TID
    row = store.read_jsonl(inst.file("judgements"))[0]
    assert row["pentad"] == "лексическая пятёрка" and row["verdict"] == "free_with_angle"


@pytest.mark.parametrize("fields, problem", [
    ({"verdict": "closed_by", "section_id": "чужой#0", "quote": "x"}, "не из пятёрки"),
    ({"verdict": "closed_by", "section_id": None, "quote": "этого текста в разделе нет вовсе"}, "цитаты нет"),
    ({"verdict": "free_with_angle", "section_id": None, "quote": None, "angle": ""}, "без угла"),
    ({"verdict": "может быть", "section_id": None, "quote": None}, "verdict"),
])
def test_answer_outside_five_or_fake_quote_not_accepted(inst, project, fields, problem):  # noqa: F811
    disputed(inst)
    judge.make_task(inst, TID)
    first = json.loads((inst.brief_dir(TID) / judge.CONTEXT).read_text(encoding="utf-8"))["sections"][0]
    fields = {k: (first["id"] if k == "section_id" and v is None else
                  " ".join(first["text"].split()[:5]) if k == "quote" and v is None else v) for k, v in fields.items()}
    answer(inst, **fields)
    out = judge.check(inst, TID)
    assert not out["accepted"] and any(problem in p for p in out["problems"]), out["problems"]
    assert main(["judge", "check", TID, "--profile", str(project)]) == 1
    assert queue_mod.load(inst)[0]["status"] == queue_mod.CANDIDATE


def test_closed_rejects_and_disputed_goes_to_human(inst, project):  # noqa: F811
    disputed(inst)
    judge.make_task(inst, TID)
    first = json.loads((inst.brief_dir(TID) / judge.CONTEXT).read_text(encoding="utf-8"))["sections"][0]
    answer(inst, verdict="disputed", section_id=first["id"], quote=" ".join(first["text"].split()[:4]))
    assert main(["judge", "check", TID, "--profile", str(project)]) == 2
    assert queue_mod.load(inst)[0]["status"] == queue_mod.CANDIDATE
    assert main(["judge", "accept", TID, "--who", "Человек", "--verdict", "closed", "--profile", str(project)]) == 0
    topic = queue_mod.load(Instance(project, today=TODAY))[0]
    assert topic["status"] == queue_mod.REJECTED and topic["judge"]["by"] == "Человек"


EMB = '''
class Provider:
    calls = 0

    def __init__(self, instance):
        pass

    def price(self, texts):
        return 1.5

    def embed(self, texts):
        Provider.calls += 1
        return [[float(len(t)), 1.0] for t in texts]
'''


def test_api_embeddings_need_budget_yes(inst, project):  # noqa: F811
    (project.parent / "adapters").mkdir()
    (project.parent / "adapters" / "emb.py").write_text(EMB, encoding="utf-8")
    set_profile(project, lambda d: (d["judge"]["embeddings"].update(kind="api", model="module:adapters/emb.py:Provider",
                                                                     key_env="EMB_KEY", price_per_1k_rub=0.5),
                                    d["budget"].update(run_limit_rub=10, month_limit_rub=100)))
    instance = Instance(project, today=TODAY)
    disputed(instance)
    assert main(["judge", "task", TID, "--profile", str(project)]) == 2             # без «да» — ноль вызовов
    assert not (instance.root / "cache" / "embeddings").exists() or not any((instance.root / "cache" / "embeddings").iterdir())
    assert main(["judge", "task", TID, "--confirm-spend", "1.5", "--profile", str(project)]) == 0
    context = json.loads((instance.brief_dir(TID) / judge.CONTEXT).read_text(encoding="utf-8"))
    assert context["kind"] == "эмбеддинги API"
    assert main(["judge", "task", TID, "--profile", str(project)]) == 0            # из кэша — без нового вызова


def test_refill_gives_judge_task_for_disputed(inst, project, monkeypatch):  # noqa: F811
    topics = queue_mod.load(inst)
    topics[0].update(status=queue_mod.CANDIDATE)
    queue_mod.save(inst, topics)
    report_path = inst.root / "gate-report.json"
    report_path.write_text(json.dumps({"stopped_by": {"page": "zametki-oktyabr"}, "layers": []}), encoding="utf-8")

    def fake_gate(instance, cand, max_quota=None):
        return {"date": TODAY.isoformat(), "verdict": "спорно", "complete": True, "serp_requested": 0,
                "stopped_by": {"page": "zametki-oktyabr"}, "serp_type": {"reason": ""}, "topic": cand.topic}
    import content_engine.steps.gate_topic as gate_topic
    monkeypatch.setattr(gate_topic, "gate", fake_gate)
    monkeypatch.setattr(gate_topic, "save_report", lambda instance, report: str(report_path))
    out = queue_mod.refill(inst, count=1)
    assert any("задание судье" in v for _, v in out["results"]), out["results"]
    assert (inst.brief_dir(TID) / "task_judge.md").is_file()
