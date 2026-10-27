"""Ставка из гипотезы Движка роста (Т26, П38): хранилище Markdown и карточка файлом, только чтение, без импорта
`growth_engine`; `require_bet: true` без связи — предупреждение самопроверки."""
from datetime import timedelta

import pytest
import yaml

from content_engine.adapters import growth
from content_engine.cli import main
from content_engine.core.errors import EngineError, GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import check as check_mod
from content_engine.steps import queue as queue_mod

from .conftest import TODAY, set_profile
from .test_brief import TID, inst  # noqa: F401

HYP_COLUMNS = ["id", "formulation", "status", "version", "effect_goal_units_id", "effect_rub_id",
               "failure_criterion", "window_days", "mechanic", "main_metric"]
NUM_COLUMNS = ["id", "metric", "level", "scope", "flow", "segment", "period_start", "period_end", "as_of", "source",
               "status", "value", "denominator", "unit", "missing"]


def sheet(path, name, columns, rows):
    def line(values):
        return "|" + "|".join(f" {v} " for v in values) + "|"
    text = [f"# {name}", "", line(columns), "|" + "|".join("---" for _ in columns) + "|"]
    text += [line([row.get(c, "") for c in columns]) for row in rows]
    path.write_text("\n".join(text) + "\n", encoding="utf-8")


@pytest.fixture
def store_dir(tmp_path):
    folder = tmp_path / "growth_store"
    folder.mkdir()
    sheet(folder / "Гипотезы.md", "Гипотезы", HYP_COLUMNS, [
        {"id": "H-7", "formulation": "Если выпустить статью &#124; про грядки, то заявки вырастут&#10;потому что спрос",
         "status": "candidate", "version": "1", "effect_goal_units_id": "n1", "effect_rub_id": "n2",
         "failure_criterion": "'=ноль заявок за 90 дней", "window_days": "90"},
        {"id": "H-8", "formulation": "старая", "status": "архив"}])
    sheet(folder / "Модель — снимки.md", "Модель — снимки", NUM_COLUMNS, [
        {"id": "n1", "metric": "leads", "level": "сайт", "scope": "кабинет-1", "period_start": "2026-02-01",
         "period_end": "2026-02-28", "as_of": "2026-03-01", "source": "аналитика", "status": "estimate",
         "value": "4.0", "unit": "заявки"},
        {"id": "n2", "metric": "effect_rub", "level": "сайт", "scope": "кабинет-1", "period_start": "2026-02-01",
         "period_end": "2026-02-28", "as_of": "2026-03-01", "source": "модель", "status": "estimate",
         "value": "12000.0", "unit": "₽"}])
    return folder


def test_markdown_store_bet_decoded_and_read_only(store_dir):
    src = growth.MarkdownGrowth(store_dir)
    bet = src.bet("H-7")
    assert bet["hypothesis"] == "Если выпустить статью | про грядки, то заявки вырастут\nпотому что спрос"
    assert bet["fail_threshold"] == "=ноль заявок за 90 дней"            # ведущий апостроф снят
    assert [n["value"] for n in bet["expectation_numbers"]] == [4.0, 12000.0]
    assert bet["expectation_numbers"][0]["status"] == "оценка" and "кабинет кабинет-1" in bet["expectation"]
    assert bet["windows"] == [90] and src.writes == 0 and src.reads == 1
    with pytest.raises(GateRefused, match="в архиве"):
        src.bet("H-8")
    with pytest.raises(EngineError, match="H-9"):
        src.bet("H-9")


def test_missing_number_reference_is_error(store_dir):
    text = (store_dir / "Гипотезы.md").read_text(encoding="utf-8").replace(" n2 ", " n404 ")
    (store_dir / "Гипотезы.md").write_text(text, encoding="utf-8")
    with pytest.raises(EngineError, match="n404"):
        growth.MarkdownGrowth(store_dir).bet("H-7")


def card(folder, **change):
    data = yaml.safe_load(growth.card_template())
    data.update({"id": "H-13", "exported_at": TODAY.isoformat(), **change})
    folder.mkdir(exist_ok=True)
    (folder / "H-13.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return data


def test_bet_from_card_written_to_queue_without_breaking_old_fields(inst, project):  # noqa: F811
    set_profile(project, lambda d: d["sources"]["growth"].update(kind="file", path="data/growth"))
    instance = Instance(project, today=TODAY)
    card(instance.root / "data" / "growth")
    assert main(["queue", "bet", TID, "--hypothesis-id", "H-13", "--profile", str(project)]) == 0
    bet = queue_mod.load(instance)[0]["bet"]
    assert isinstance(bet["expectation"], str) and bet["expectation"].startswith("около 20 заявок")
    assert bet["hypothesis_id"] == "H-13" and bet["unit"] == "direction" and bet["planned_count"] == 5
    assert bet["fail_threshold"] and bet["expectation_numbers"][0]["status"] == "оценка"
    src = growth.FileGrowth(instance.root / "data" / "growth", TODAY)
    src.bet("H-13")
    assert src.writes == 0 and src.reads == 1                 # карточка только читается


def test_card_rules(inst, project, tmp_path):  # noqa: F811
    folder = tmp_path / "cards"
    card(folder, status="архив")
    with pytest.raises(GateRefused, match="в архиве"):
        growth.FileGrowth(folder, TODAY).bet("H-13")
    card(folder, fail_threshold="")
    with pytest.raises(EngineError, match="fail_threshold"):
        growth.FileGrowth(folder, TODAY).bet("H-13")
    card(folder, exported_at=(TODAY - timedelta(days=45)).isoformat())
    assert any("старше 30 дней" in n for n in growth.FileGrowth(folder, TODAY).bet("H-13")["notes"])


def test_manual_bet_needs_all_three_and_not_mixed(inst, project):  # noqa: F811
    assert main(["queue", "bet", TID, "--hypothesis", "статья даст заявки", "--profile", str(project)]) == 3
    assert main(["queue", "bet", TID, "--hypothesis-id", "H-1", "--hypothesis", "x", "--profile", str(project)]) == 3
    assert main(["queue", "bet", TID, "--hypothesis", "статья даст заявки", "--expectation", "2",
                 "--fail-threshold", "0", "--profile", str(project)]) == 0


def test_require_bet_without_link_is_check_warning(project):
    set_profile(project, lambda d: d["queue"].update(require_bet=True))
    item = check_mod._growth(Instance(project, today=TODAY))
    assert item["light"] == check_mod.YELLOW and "запас очереди останется нулём" in item["detail"]


def test_card_template_command(capsys):
    assert main(["queue", "card"]) == 0
    assert "expectation_numbers" in capsys.readouterr().out
