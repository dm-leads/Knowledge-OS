"""Справка команд печатается на любой консоли (дефект приёмочного прогона 7б).

`py -3 -m growth_engine.registry --help` на русской консоли (cp1251) падал с `UnicodeEncodeError` на знаке `₽`:
`main()` чинит кодировку потока, но ПОСЛЕ `parse_args()`, а справка печатается и завершает процесс внутри неё.
Первое, что делает агент с незнакомой командой, — получал трассировку вместо списка подкоманд.

Проверяется подпроцессом: внутри одного процесса кодировку потока уже не подделать так, как это делает консоль.
Кодировка задаётся только дочернему процессу — иначе тест ломается о то же, что измеряет.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

COMMANDS = Path(__file__).resolve().parents[1]
# Команда — модуль с `main()`; `adapters.py` и подобные ему фабрики справки не имеют, и требовать её от них значило бы
# проверять форму вместо дела.
MODULES = sorted(path.stem for path in COMMANDS.glob("*.py")
                 if path.stem != "__init__" and "def main(" in path.read_text(encoding="utf-8"))


def help_of(module: str, encoding: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING=encoding)
    return subprocess.run([sys.executable, "-m", f"growth_engine.{module}", "--help"],
                          capture_output=True, text=True, errors="replace", env=env,
                          cwd=str(COMMANDS.parent))


@pytest.mark.parametrize("module", MODULES)
def test_help_prints_on_a_cp1251_console(module):
    """Справка обязана печататься там, где живёт агент, а не только в UTF-8."""
    result = help_of(module, "cp1251")
    assert result.returncode == 0, f"{module}: справка не напечатана — {(result.stderr or '').strip()[-200:]}"
    assert "usage" in (result.stdout or "").lower()


def test_registry_help_lists_every_subcommand():
    """Список подкоманд — то, ради чего агент зовёт справку; он должен быть виден и на cp1251."""
    from growth_engine.registry import SUBCOMMANDS
    result = help_of("registry", "cp1251")
    assert result.returncode == 0, (result.stderr or "")[-200:]
    missing = [name for name in SUBCOMMANDS if name not in result.stdout]
    assert not missing, f"в справке нет подкоманд: {missing}"
