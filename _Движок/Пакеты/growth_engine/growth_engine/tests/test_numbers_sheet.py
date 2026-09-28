"""Перенос снимка чисел в хранилище: снимок прежнего формата и markdown-хранилище контракта, сухой прогон без записи,
повтор — 0 строк, разные числа под одним номером — стоп до записи, нет файла снимка — стоп."""
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

from growth_engine.core.storage import RegistryStore
from growth_engine.numbers_sheet import run
from growth_engine.storage.markdown import MarkdownBackend, MarkdownStore
from growth_engine.tests.fake_lark import FakeLarkBridge
from growth_engine.tests.helpers import EFFECT, FACT, n

FIXTURES = Path(__file__).parent / "fixtures"
NUMBERS = [FACT, EFFECT, n("visits", 101146, flow="web")]
ARTIFACT = "денежная модель 2026-06–2026-08"


def old_snapshot(tmp_path, numbers=NUMBERS):
    folder = tmp_path / "прогон"
    MarkdownStore(folder).write_numbers(ARTIFACT, numbers)
    return folder


def call(snapshot, artifact=ARTIFACT, **store):
    args = Namespace(config=str(FIXTURES / "config_minimal.yaml"), snapshot=str(snapshot), artifact=artifact,
                     dry_run=False, out=None, lark_book=None)
    for key, value in store.items():
        setattr(args, key, value)
    lines, bridge = [], FakeLarkBridge()
    code = run(args, out=lines.append, today=lambda: date(2026, 9, 15), bridge_factory=lambda: bridge)
    return code, lines, bridge


def test_dry_run_summarises_snapshot_and_writes_nothing(tmp_path):
    code, lines, bridge = call(old_snapshot(tmp_path), dry_run=True)
    assert code == 0 and "чисел 3, разных номеров 3" in lines[0] and "статусы: оценка — 1, факт — 2" in lines[0]
    assert lines[-1] == "сухой прогон: ничего не записано" and bridge.calls == []


def test_old_snapshot_goes_to_markdown_store_and_repeat_writes_nothing(tmp_path):
    snapshot, folder = old_snapshot(tmp_path), tmp_path / "хранилище"
    code, lines, _ = call(snapshot, out=str(folder))
    assert code == 0 and "«Модель — снимки»: записано 3, прочитано 3" in lines
    assert lines[-1] == "ИТОГ: все числа снимка в хранилище — номеров в снимке 3"
    code, lines, _ = call(snapshot, out=str(folder))
    assert code == 0 and "«Модель — снимки»: записано 0, прочитано 0" in lines


def test_contract_store_snapshot_goes_to_lark(tmp_path):
    source = tmp_path / "хранилище"
    RegistryStore(MarkdownBackend(source)).create_numbers(NUMBERS)
    code, lines, bridge = call(source, artifact=None, lark_book="книга-проба")
    assert code == 0 and "«Модель — снимки»: записано 3, прочитано 3" in lines
    assert [sheet["title"] for sheet in bridge.sheets.values()] == ["Модель — снимки"]


def test_different_numbers_under_one_id_stop_before_writing(tmp_path):
    snapshot = old_snapshot(tmp_path, NUMBERS + [replace(FACT, value=1052.0)])
    code, lines, bridge = call(snapshot, out=str(tmp_path / "хранилище"))
    assert code == 1 and "[страж 13]" in lines[-1] and "разные числа" in lines[-1] and "вариантов 2" in lines[-1]
    assert bridge.calls == [] and not (tmp_path / "хранилище").exists()


def test_missing_snapshot_file_stops(tmp_path):
    code, lines, _ = call(tmp_path, dry_run=True)
    assert code == 1 and "нет файла" in lines[-1]
