"""Хук не даёт last30days публиковать отчёт наружу и не мешает остальным командам."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).with_name("block_last30days_publish.py")


def run(payload) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("command", ["python last30days.py 'бризеры' --publish",
                                     "last30days тема --publish-html"])
def test_publish_is_blocked_with_a_reason(command):
    result = run({"tool_input": {"command": command}})
    assert result.returncode == 2 and "ht-ml.app" in result.stderr


@pytest.mark.parametrize("command", ["python last30days.py 'бризеры' --save-dir out", "git push --publish", "ls"])
def test_other_commands_pass(command):
    assert run({"tool_input": {"command": command}}).returncode == 0


def test_unreadable_input_does_not_block():
    assert run("не json").returncode == 0
