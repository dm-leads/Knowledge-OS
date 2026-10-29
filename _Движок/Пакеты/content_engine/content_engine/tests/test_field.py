"""«Поле шире освоенного» (А4, Т41.3, П60): гейт без сети — квота не тратится; отчёт по направлениям; ошибка формата
файла — с номером строки."""
import pytest

from content_engine.core.errors import EngineError
from content_engine.core.instance import Instance
from content_engine.steps import field

from .conftest import TODAY, set_profile

TOPICS = """# новые направления
пчёлы;пчеловодство на даче;пчеловодство на даче
пчёлы;пчеловодство на даче;как завести пчёл на участке
пчёлы;пчеловодство на даче;улей для начинающих

теплицы;отопление теплицы зимой;отопление теплицы зимой
теплицы;отопление теплицы зимой;чем топить теплицу зимой
теплицы;отопление теплицы зимой;печь в теплице зимой
"""


@pytest.fixture
def topics(project):
    (project.parent / "field.txt").write_text(TOPICS, encoding="utf-8")
    set_profile(project, lambda d: d["field"].update(file="field.txt"))
    return project


def test_field_spends_no_quota(topics, monkeypatch):
    def no_serp(self):
        raise AssertionError("выдача запрошена — квота тратится")
    monkeypatch.setattr(Instance, "serp", no_serp)
    out = field.probe(Instance(topics, today=TODAY))
    assert out["serp_requested"] == 0 and out["topics"] == 2


def test_field_report_by_direction(topics):
    out = field.probe(Instance(topics, today=TODAY))
    verdicts = {r["topic"]: r["verdict"] for r in out["rows"]}
    assert verdicts["отопление теплицы зимой"] == "пересекается"
    assert verdicts["пчеловодство на даче"] != "пересекается"
    assert set(out["by_direction"]) == {"пчёлы", "теплицы"}
    assert (topics.parent / "reports" / TODAY.isoformat() / "field.md").is_file()


def test_field_bad_line_number(topics):
    (topics.parent / "field.txt").write_text("пчёлы;пчеловодство\n", encoding="utf-8")
    with pytest.raises(EngineError, match="строка 1"):
        field.probe(Instance(topics, today=TODAY))


def test_field_not_connected(project):
    assert field.probe(Instance(project, today=TODAY))["status"] == "не подключено"
