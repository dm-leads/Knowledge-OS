"""«Что с машиной» (шаг 4, К2): `status` только читает — ни одного нового или изменённого файла, даже при истёкшей брони."""
import hashlib
from datetime import date

from content_engine.cli import main
from content_engine.core.instance import Instance
from content_engine.steps import queue as queue_mod
from content_engine.steps import reservations


def _tree(root) -> dict:
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir")
            for p in root.rglob("*")}


def test_status_writes_nothing(project, capsys):
    past = Instance(project, today=date(2020, 1, 1))     # бронь давно истекла по сегодняшнему дню
    topic = {"id": "T0001", "topic": "отопление теплицы газом", "status": queue_mod.GATED, "direction": "теплицы",
             "formulations": ["отопление теплицы газом"], "reservation_id": None, "gate": None}
    record = reservations.create(past, topic=topic["topic"], key=[], cluster_id=None, who="тест", queue_id="T0001")
    topic["reservation_id"] = record["id"]
    queue_mod.save(past, [topic])
    before = _tree(project.parent)
    assert main(["status", "--profile", str(project)]) == 0
    assert main(["status", "--profile", str(project), "--topic", "T0001"]) == 0
    out = capsys.readouterr().out
    assert _tree(project.parent) == before
    assert "Тема T0001" in out and "рабочей папки темы нет" in out
    assert reservations.load(past)[0]["status"] == reservations.ACTIVE
