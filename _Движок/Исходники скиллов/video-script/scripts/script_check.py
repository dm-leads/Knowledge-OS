"""Проверка сценария видео по методике «Сценарии видео и бесплатного урока». Только чтение.

Зачем: правило в промпте нарушается, правило в коде — нет. Скрипт ловит то, что видно по таблице сценария:
хронометраж, пустую картинку, вступление с приветствием, обещания и давление, дублирование речи текстом на экране,
фоновую музыку в учебной части, отсутствие паузы-вопроса в уроке, незакрытые пробелы фактов. Тон и словарь
конкретного проекта проверяет скрипт проекта (у «Учебного центра» — 06 — Инструменты/scripts/tone_check.py).

    python script_check.py <сценарий.md> --type lesson [--max 6:00] [--no-sound] [--shotlist <лист.md>]

Типы: short (лента), lesson (урок-модуль), series (ролик серии), sales (продающее видео или эфир),
family (ролик для второго человека в решении). Код выхода: 0 — замечаний нет, 1 — есть замечания, 2 — не прочитать.
Формат таблицы — references/scenario-format.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TYPES = ("short", "lesson", "series", "sales", "family")
# Длина по умолчанию, секунд: урок-модуль — до 6 мин (Гуо), ролик для второго человека — до 3 мин (гипотеза методики)
DEFAULT_MAX = {"short": 60, "lesson": 360, "family": 180}
PLACEHOLDER = re.compile(r"\[нужно от проекта[^\]]*\]", re.I)

OPENERS = re.compile(r"\b(привет\w*|здравствуйте|добр\w+ (?:день|утро|вечер)|меня зовут|добро пожаловать|"
                     r"заставк\w*|логотип\w*|интро\b)", re.I)

# Обещания и давление — переносимые запреты методики (разд. 5 и 7) и «Воронок», разд. 11
PROMISES = [
    (r"гарант\w*", "гарантия — только то, что записано в договоре; проверить формулировку"),
    (r"трудоустро\w*|устроим на работу|найдём вам работу", "обещание работы"),
    (r"(?:зарабат\w*|доход\w*|зарплат\w*)[^.!?]{0,40}?\d[\d\s]*(?:тыс|000|₽|руб)", "сумма заработка — обещание дохода"),
    (r"\d[\d\s]*(?:тыс\.?|000)\s*(?:₽|руб\w*)?\s*в месяц[^.!?]{0,30}?(?:зарабат|доход|получа)", "сумма заработка — обещание дохода"),
    (r"осталось\s+\d+\s+мест|только сегодня|успейте|последний шанс|пока не поздно", "искусственная срочность"),
    (r"\b100\s*%", "«100 %» — абсолютное обещание"),
    (r"без риска|никакого риска|точно получится|у всех получается", "обещание результата"),
    (r"зачёркнут\w*|вместо\s+\d[\d\s]*₽", "зачёркнутая цена — только с реальной основой"),
]

MUSIC = re.compile(r"музык\w*|саундтрек|\bбит\b", re.I)
PAUSE = re.compile(r"пауз\w*|останов\w*|поставьте на паузу|ответьте|запишите|попробуйте", re.I)
WORD = re.compile(r"[а-яёa-z0-9]+", re.I)
TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})\s*$")


@dataclass
class Row:
    line: int
    start: int
    end: int
    say: str
    frame: str
    screen: str
    note: str

    @property
    def text(self) -> str:
        return " ".join((self.say, self.frame, self.screen, self.note))


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    checks: int = 0

    def check(self, ok: bool, msg: str) -> None:
        self.checks += 1
        if not ok:
            self.errors.append(msg)


def mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def parse_seconds(value: str) -> int:
    m = re.fullmatch(r"(\d{1,3}):(\d{2})", value.strip())
    if m:
        return int(m[1]) * 60 + int(m[2])
    return int(value)


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_table(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Первая таблица Markdown с колонкой «Время»: (заголовки, [(номер строки файла, ячейки)])."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|") and "время" in line.lower():
            header = [h.lower() for h in split_row(line)]
            rows = []
            for j in range(i + 2, len(lines)):
                if not lines[j].lstrip().startswith("|"):
                    break
                rows.append((j + 1, split_row(lines[j])))
            return header, rows
    return [], []


def col(header: list[str], *names: str) -> int | None:
    for idx, h in enumerate(header):
        if any(n in h for n in names):
            return idx
    return None


def load_rows(text: str, rep: Report) -> list[Row]:
    header, raw = parse_table(text)
    rep.check(bool(header), "нет таблицы сценария с колонкой «Время» (формат — references/scenario-format.md)")
    if not header:
        return []
    idx = {k: col(header, *n) for k, n in {"t": ("время",), "say": ("говорит", "речь", "текст диктора"),
                                           "frame": ("в кадре", "кадр", "картинк"),
                                           "screen": ("на экране", "титр", "подпис"), "note": ("заметк", "примеч")}.items()}
    missing = [n for n, k in (("Говорит", "say"), ("В кадре", "frame")) if idx[k] is None]
    rep.check(not missing, f"в таблице нет колонок: {', '.join(missing)}")
    rows = []
    for ln, cells in raw:
        def get(k: str) -> str:
            i = idx[k]
            return cells[i] if i is not None and i < len(cells) else ""
        m = TIME.match(get("t"))
        if not m:
            rep.check(False, f"строка {ln}: время «{get('t')}» не в формате м:сс–м:сс")
            continue
        start, end = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
        rows.append(Row(ln, start, end, get("say"), get("frame"), get("screen"), get("note")))
    rep.check(len(rows) >= 2, "в сценарии меньше двух строк с временем")
    return rows


def empty(cell: str) -> bool:
    return cell.strip(" -—–.") == ""


def check_timing(rows: list[Row], rep: Report, max_sec: int | None) -> None:
    if not rows:
        return
    rep.check(rows[0].start == 0, f"строка {rows[0].line}: сценарий начинается не с 0:00")
    for r in rows:
        rep.check(r.end > r.start, f"строка {r.line}: конец {mmss(r.end)} не позже начала {mmss(r.start)}")
    for a, b in zip(rows, rows[1:]):
        rep.check(a.end == b.start, f"строка {b.line}: разрыв или наложение — {mmss(a.end)} → {mmss(b.start)}")
    if max_sec:
        total = rows[-1].end
        rep.check(total <= max_sec, f"длина {mmss(total)} больше предела {mmss(max_sec)}")


def check_frames(rows: list[Row], rep: Report) -> None:
    for r in rows:
        rep.check(not empty(r.frame), f"строка {r.line}: пустая колонка «В кадре» — картинку снимают по ней")


def check_opening(rows: list[Row], rep: Report, kind: str) -> None:
    limit = 3 if kind == "short" else 30
    for r in rows:
        if r.start >= limit:
            break
        found = OPENERS.search(r.say + " " + r.frame)
        rep.check(not found, f"строка {r.line}: «{found and found[0]}» в первых {limit} с — начинать с сути")


def check_promises(rows: list[Row], rep: Report) -> None:
    for r in rows:
        for pat, why in PROMISES:
            m = re.search(pat, r.say + " " + r.screen, re.I)
            rep.check(not m, f"строка {r.line}: «{m and m[0]}» — {why}")


def words(s: str) -> list[str]:
    return [w.lower() for w in WORD.findall(s)]


def check_redundancy(rows: list[Row], rep: Report) -> None:
    for r in rows:
        scr = words(r.screen)
        if len(scr) <= 4:
            continue
        said = set(words(r.say))
        share = sum(w in said for w in scr) / len(scr)
        rep.check(share < 0.6, f"строка {r.line}: текст на экране повторяет речь ({share:.0%} слов) — "
                               "оставить 2–4 слова или термин")


def check_music(rows: list[Row], rep: Report) -> None:
    for r in rows:
        m = MUSIC.search(r.text)
        rep.check(not m, f"строка {r.line}: «{m and m[0]}» — в учебной части без фоновой музыки (Майер, d = 0,95, 2 опыта)")


def check_pause(rows: list[Row], rep: Report) -> None:
    rep.check(any(PAUSE.search(r.text) for r in rows),
              "нет паузы-вопроса: «остановите видео и ответьте…» — генеративная активность и вопросы в ролике")


def check_tail(rows: list[Row], rep: Report) -> None:
    if rows:
        last = rows[-1]
        rep.check(last.end - last.start >= 10, f"строка {last.line}: последняя сцена короче 10 с — "
                                               "нет места под призыв и конечные рекомендации")


def check_placeholders(text: str, rep: Report) -> list[str]:
    found = PLACEHOLDER.findall(text)
    rep.check(not found, f"незакрытые пробелы фактов: {len(found)} — со съёмки такой сценарий не уходит")
    return found


def run(text: str, kind: str, max_sec: int | None, sound: bool) -> tuple[Report, list[Row]]:
    rep = Report()
    rows = load_rows(text, rep)
    check_timing(rows, rep, max_sec if max_sec is not None else DEFAULT_MAX.get(kind))
    check_frames(rows, rep)
    check_opening(rows, rep, kind)
    check_promises(rows, rep)
    if sound and kind != "short":
        check_redundancy(rows, rep)
    if kind in ("lesson", "series", "sales"):
        check_music(rows, rep)
    if kind == "lesson":
        check_pause(rows, rep)
    if kind != "short":
        check_tail(rows, rep)
    check_placeholders(text, rep)
    return rep, rows


SHOOT_RULES = """## Правила съёмки (методика, разд. 4)

- Рабочее место вместо студии, крупный план, смотреть в камеру.
- Говорить своими словами, живо, не замедляться; паузы и «э-э» вырежут на монтаже.
- Рисовать или показывать экран в ту секунду, когда об этом говорите; указывать на то, о чём речь.
- Показ руками — снимать от первого лица.
- Без фоновой музыки в учебной части. Неподвижное фото спикера рядом со слайдами не ставить.
- Звук важнее картинки: петличка, тихое помещение.
"""


def shotlist(rows: list[Row], title: str, placeholders: list[str]) -> str:
    out = [f"# Съёмочный лист — {title}", "", SHOOT_RULES, "## Сцены", ""]
    for n, r in enumerate(rows, 1):
        out += [f"### {n}. {mmss(r.start)}–{mmss(r.end)}", "",
                f"- **В кадре:** {r.frame or '—'}",
                f"- **Сказать:** {r.say or '—'}",
                f"- **Текст на экране:** {r.screen or 'нет'}",
                f"- **Заметка:** {r.note or '—'}",
                "- **Подготовить заранее:** ", ""]
    if placeholders:
        out += ["## Вопросы до съёмки", ""] + [f"- {p}" for p in placeholders] + [""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Проверка сценария видео по методике")
    p.add_argument("file")
    p.add_argument("--type", choices=TYPES, required=True)
    p.add_argument("--max", help="предел длины, м:сс или секунды (по умолчанию: short 1:00, lesson 6:00, family 3:00)")
    p.add_argument("--no-sound", action="store_true", help="смотрят без звука — не проверять дублирование речи текстом")
    p.add_argument("--shotlist", help="записать съёмочный лист в этот файл")
    a = p.parse_args(argv)
    path = Path(a.file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"не прочитать {path}: {e}")
        return 2
    rep, rows = run(text, a.type, parse_seconds(a.max) if a.max else None, not a.no_sound)
    for e in rep.errors:
        print("ЗАМЕЧАНИЕ", e)
    print(f"проверок: {rep.checks}, замечаний: {len(rep.errors)}, сцен: {len(rows)}, "
          f"длина: {mmss(rows[-1].end) if rows else '—'}")
    if a.shotlist and rows:
        title = next((ln[1:].strip() for ln in text.splitlines() if ln.startswith("# ")), path.stem)
        Path(a.shotlist).write_text(shotlist(rows, title, PLACEHOLDER.findall(text)), encoding="utf-8")
        back = Path(a.shotlist).read_text(encoding="utf-8").count("\n### ")
        print(f"съёмочный лист: {a.shotlist}, сцен записано и прочитано обратно: {back}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
