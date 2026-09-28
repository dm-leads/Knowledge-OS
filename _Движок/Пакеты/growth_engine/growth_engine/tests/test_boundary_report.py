"""Отчёт о границе вне ядра (28.09.2026): имя системы в пояснении и имя в логике считаются раздельно."""
from growth_engine.boundary_report import report, split_code_and_prose


def test_docstring_and_comment_go_to_prose_and_code_stays_code():
    text = ('"""Адаптер Roistat."""\n'
            'from .storage.lark_sheets import LarkBridge  # мост Lark\n'
            'def f():\n'
            '    """Пояснение про amocrm."""\n'
            '    return "roistat"\n')
    code, prose = split_code_and_prose(text)
    assert "lark_sheets" in code and "return \"roistat\"" in code
    assert "адаптер roistat" in prose and "мост lark" in prose and "amocrm" in prose
    assert "адаптер roistat" not in code and "мост lark" not in code


def test_report_names_modules_with_words_in_code():
    rows = {name: in_code for name, in_code, _ in report()}
    assert "sources/roistat.py" in rows and rows["sources/roistat.py"]
    assert all(not name.startswith("core/") for name in rows)
