"""Тесты проверки сценария: python -m pytest test_script_check.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import script_check as sc  # noqa: E402

HEAD = "| Время | Говорит | В кадре | Текст на экране | Заметка |\n|---|---|---|---|---|\n"

GOOD = HEAD + (
    "| 0:00–0:05 | Разберём одну заявку от начала до конца. | Экран с заявкой крупно | Одна заявка | |\n"
    "| 0:05–2:00 | Сначала три слова, без них дальше непонятно. | Рисую схему на доске | Ставка | |\n"
    "| 2:00–5:40 | Остановите видео и ответьте: какую машину ищем? | Показываю площадку, обвожу поле | | пауза-вопрос |\n"
    "| 5:40–6:00 | Следующий шаг — запись на разговор. | Лицо, взгляд в камеру | | призыв |\n"
)


def run(text, kind="lesson", max_sec=None, sound=True):
    rep, rows = sc.run(text, kind, max_sec, sound)
    return rep.errors, rows


def test_good_lesson_passes():
    errors, rows = run(GOOD)
    assert errors == []
    assert len(rows) == 4 and rows[-1].end == 360


def test_no_table():
    errors, _ = run("просто текст")
    assert any("нет таблицы" in e for e in errors)


def test_gap_and_too_long():
    bad = GOOD.replace("2:00–5:40", "2:10–5:40").replace("5:40–6:00", "5:40–6:30")
    errors, _ = run(bad)
    assert any("разрыв" in e for e in errors)
    assert any("больше предела" in e for e in errors)


def test_empty_frame():
    errors, _ = run(GOOD.replace("Рисую схему на доске", " "))
    assert any("пустая колонка «В кадре»" in e for e in errors)


def test_greeting_in_opening():
    errors, _ = run(GOOD.replace("Разберём одну заявку", "Всем привет, меня зовут Анна"))
    assert any("первых 30 с" in e for e in errors)


def test_promises():
    bad = GOOD.replace("Следующий шаг — запись на разговор.",
                       "Гарантируем трудоустройство, зарабатывать от 100 000 ₽, осталось 3 места")
    errors, _ = run(bad)
    text = " ".join(errors)
    assert "обещание работы" in text and "обещание дохода" in text and "срочность" in text


def test_redundancy_and_short_caption():
    bad = GOOD.replace("| Ставка |", "| Сначала три слова без них дальше непонятно |")
    errors, _ = run(bad)
    assert any("повторяет речь" in e for e in errors)
    errors_nosound, _ = run(bad, sound=False)
    assert not any("повторяет речь" in e for e in errors_nosound)


def test_music_and_pause():
    bad = GOOD.replace("пауза-вопрос", "фоновая музыка").replace("Остановите видео и ответьте: какую машину ищем?",
                                                               "Смотрим площадку.")
    errors, _ = run(bad)
    assert any("музыки" in e for e in errors)
    assert any("паузы-вопроса" in e for e in errors)


def test_placeholder_and_shotlist(tmp_path):
    text = "# Сценарий — проба\n\n" + GOOD.replace("| призыв |", "| [нужно от проекта: цена] |")
    errors, rows = run(text)
    assert any("пробелы фактов" in e for e in errors)
    f = tmp_path / "s.md"
    f.write_text(text, encoding="utf-8")
    out = tmp_path / "лист.md"
    assert sc.main([str(f), "--type", "lesson", "--shotlist", str(out)]) == 1
    sheet = out.read_text(encoding="utf-8")
    assert sheet.count("\n### ") == 4 and "Вопросы до съёмки" in sheet and "цена" in sheet


def test_short_skips_lesson_rules():
    short = HEAD + ("| 0:00–0:02 | Где ищут машины под груз? | Экран площадки | Где ищут машины под груз | |\n"
                    "| 0:02–0:20 | Вот здесь. Ссылка в профиле. | Палец на кнопке | | |\n")
    errors, _ = run(short, kind="short")
    assert errors == []
