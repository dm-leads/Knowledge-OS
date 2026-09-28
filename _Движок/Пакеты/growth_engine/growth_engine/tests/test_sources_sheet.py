"""Выгрузка карты источников на лист (К7): сверка имён секретов, сухой прогон без записи, запись и обновление снимка,
строки листа не удаляются, запись в Lark (модель моста)."""
from argparse import Namespace
from datetime import date
from pathlib import Path

import yaml

from growth_engine.sources_sheet import env_names, run
from growth_engine.tests.fake_lark import FakeLarkBridge

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_MAP = FIXTURES / "source_map_minimal.yaml"


def call(tmp_path, source_map=SOURCE_MAP, env="API_KEY_X=x\nWEB_TOKEN_Y=y\n", **store):
    secrets = tmp_path / "секреты.env"
    if env is not None:
        secrets.write_text(env, encoding="utf-8")
    args = Namespace(config=str(FIXTURES / "config_minimal.yaml"), source_map=str(source_map), secrets=str(secrets),
                     dry_run=False, out=None, lark_book=None)
    for key, value in store.items():
        setattr(args, key, value)
    lines, bridge = [], FakeLarkBridge()
    code = run(args, out=lines.append, today=lambda: date(2026, 9, 15), bridge_factory=lambda: bridge)
    return code, lines, bridge


def test_dry_run_prints_sources_and_writes_nothing(tmp_path):
    code, lines, bridge = call(tmp_path, dry_run=True)
    assert code == 0 and "| аналитика-x | C | подключён | 2021-04 | заявки и вход в продажи по каналам |" in lines
    assert sum(line.startswith("| ") for line in lines) == 4 and lines[-1] == "сухой прогон: ничего не записано"
    assert bridge.calls == [] and sorted(p.name for p in tmp_path.iterdir()) == ["секреты.env"]


def test_secret_name_absent_from_secrets_file_stops(tmp_path):
    code, lines, _ = call(tmp_path, env="API_KEY_X=x\n", dry_run=True)
    assert code == 1 and "[страж 12]" in lines[-1] and "WEB_TOKEN_Y" not in lines[-1]
    code, lines, _ = call(tmp_path / "нет", env=None, dry_run=True)
    assert code == 1 and "файла секретов нет" in lines[-1]


def test_sheet_follows_the_map_and_keeps_rows_absent_from_it(tmp_path):
    folder = tmp_path / "хранилище"
    code, lines, _ = call(tmp_path, out=str(folder))
    assert code == 0 and "«Карта источников»: записано 3, прочитано 3" in lines
    assert call(tmp_path, out=str(folder))[1][-2] == "«Карта источников»: записано 0, прочитано 0"
    raw = yaml.safe_load(SOURCE_MAP.read_text(encoding="utf-8"))
    raw["sources"][0]["truth_point"] = "заявки по каналам и страницам"
    del raw["sources"][2]
    changed = tmp_path / "карта.yaml"
    changed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    code, lines, _ = call(tmp_path, source_map=changed, out=str(folder))
    assert code == 0 and "«Карта источников»: записано 1, прочитано 1" in lines
    assert "на листе есть источники, которых нет в карте (строки не удаляются): диалоги-z" in lines
    assert lines[-1] == "ИТОГ: лист совпадает с картой — источников в карте 2, на листе 3"


def test_lark_write_goes_through_bridge(tmp_path):
    code, lines, bridge = call(tmp_path, lark_book="книга-проба")
    assert code == 0 and "хранилище: Lark, книга книга-проба" in lines
    assert [sheet["title"] for sheet in bridge.sheets.values()] == ["Карта источников"]


def test_pii_in_source_map_stops_before_printing(tmp_path):
    raw = yaml.safe_load(SOURCE_MAP.read_text(encoding="utf-8"))
    raw["sources"][0]["truth_point"] = "звонить +7 999 123-45-67"
    changed = tmp_path / "карта.yaml"
    changed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    code, lines, _ = call(tmp_path, source_map=changed, dry_run=True)
    assert code == 1 and "[страж 12]" in lines[-1] and not any("123-45-67" in line for line in lines)


def test_secret_names_are_taken_without_values(tmp_path):
    secrets = tmp_path / "секреты.env"
    secrets.write_text("# комментарий\nexport API_KEY_X=\"значение = с равно\"\n  WEB_TOKEN_Y = y\nне переменная\n",
                       encoding="utf-8")
    assert env_names(secrets) == ("API_KEY_X", "WEB_TOKEN_Y")
