# -*- coding: utf-8 -*-
"""Проверка текста редактором: стоп-слова, приёмы манипуляции, типографика, длины полей.

    python stopwords_check.py "<файл.txt|.md>"            # текст из файла
    python stopwords_check.py --text "Заголовок" "Текст"   # строки прямо в команде, каждая — отдельное поле
    python stopwords_check.py --text "…" --limit 40        # и предел длины для каждой строки

Словари — в references/stop-words.md (блок ```stopwords) и references/offers.md (блок ```manipulation).
Слово со звёздочкой в словаре — основа: «инновационн*» найдёт «инновационное», «инновационных».
Блок ```allow в stop-words.md — законные термины («профессиональная переподготовка»): их скрипт не проверяет.
Находка — повод посмотреть, а не приговор: у многих слов есть законное значение («простой», «может»).
Выход: 0 — находок нет, 1 — есть находки, 2 — ошибка запуска.
"""
import argparse, pathlib, re, sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent.parent / "references"


def load(name: str, block: str) -> dict[str, list[str]]:
    """Словарь «класс → слова» из блока ```block в справочнике."""
    text = (HERE / name).read_text(encoding="utf-8")
    m = re.search(rf"```{block}\n(.*?)```", text, re.S)
    if not m:
        raise SystemExit(f"в {name} нет блока ```{block}")
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            cls, words = line.split(":", 1)
            # ё приводим к е и убираем повторы: «надёжный» и «надежный» — одно слово, одна находка
            items = [w.strip().lower().replace("ё", "е") for w in words.split(",") if w.strip()]
            out[cls.strip()] = list(dict.fromkeys(items))
    return out


def load_allow() -> list[str]:
    """Законные словосочетания из блока ```allow — одна строка через запятую."""
    text = (HERE / "stop-words.md").read_text(encoding="utf-8")
    m = re.search(r"```allow\n(.*?)```", text, re.S)
    if not m:
        return []
    return [w.strip().lower().replace("ё", "е") for w in m.group(1).replace("\n", ",").split(",") if w.strip()]


TYPO = [  # (что ищем, что не так)
    (r'"[^"]*"', "прямые кавычки — нужны «ёлочки»"),
    (r"\s-\s", "дефис вместо тире между частями предложения — нужно « — »"),
    # диапазон «70-110», но не телефон «8-800-555-35-35»
    (r"(?<![\d-])\d{1,4}\s?-\s?\d{1,4}(?![\d-])", "дефис в диапазоне чисел — нужно короткое тире «–» без пробелов"),
    (r"!+", "восклицательный знак — спокойная констатация убедительнее"),
    # сумма или количество, но не индекс, ИНН, артикул
    (r"(?<!\d)(?<!\d\s)\d{5,7}(?=\s*(?:₽|руб|р\.|тыс|человек|чел\.|км|шт|м²|м\b))",
     "число от 5 знаков без пробела между разрядами: 25 000"),
    (r"[A-ZА-ЯЁ]{6,}", "капслок"),
]


def pattern(w: str) -> str:
    """Регулярка для слова или фразы словаря; «*» на конце — основа слова."""
    # фраза в словаре пишется без запятых; в тексте между словами допустимы запятая и любые пробелы
    parts = [re.escape(x.rstrip("*")) + (r"\w*" if x.endswith("*") else "") for x in w.split()]
    return r"(?<![\w-])" + r"[\s,]+".join(parts) + r"(?![\w-])"


def find(text: str, dicts: dict[str, dict[str, list[str]]], allow: list[str] = ()) -> list[str]:
    low = text.lower().replace("ё", "е")
    for w in allow:   # законные термины закрываем пробелами той же длины — позиции находок не сдвигаются
        low = re.sub(pattern(w), lambda m: " " * len(m.group(0)), low)
    hits = []
    for kind, classes in dicts.items():
        for cls, words in classes.items():
            taken: list[tuple[int, int]] = []   # уже найденные места: «более того» не считается ещё и «более»
            for w in sorted(words, key=len, reverse=True):
                for m in re.finditer(pattern(w), low):
                    if any(a < m.end() and m.start() < b for a, b in taken):
                        continue
                    taken.append((m.start(), m.end()))
                    ctx = text[max(0, m.start() - 25):m.end() + 25].replace("\n", " ")
                    hits.append(f"{kind} · {cls}: «{text[m.start():m.end()]}» — …{ctx}…")
    for pat, why in TYPO:
        for m in re.finditer(pat, text):
            hits.append(f"типографика: {why} — «{m.group(0)}»")
    return hits


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Найти стоп-слова, манипуляции и ошибки типографики.")
    ap.add_argument("file", nargs="?", help="файл с текстом")
    ap.add_argument("--text", nargs="+", help="строки текста вместо файла")
    ap.add_argument("--limit", type=int, help="предел длины каждой строки --text, знаков")
    a = ap.parse_args(argv)
    if not a.file and not a.text:
        ap.print_help()
        return 2
    try:
        parts = a.text if a.text else [pathlib.Path(a.file).read_text(encoding="utf-8")]
    except OSError as e:
        print(f"не прочитать файл: {e}", file=sys.stderr)
        return 2
    dicts = {"стоп-слово": load("stop-words.md", "stopwords"), "манипуляция": load("offers.md", "manipulation")}
    allow = load_allow()
    total = 0
    for i, part in enumerate(parts, 1):
        hits = find(part, dicts, allow)
        if a.limit and len(part) > a.limit:
            hits.append(f"длина {len(part)} > {a.limit}")
        head = f"[{i}] {part[:60]}{'…' if len(part) > 60 else ''}" if a.text else f"[{a.file}]"
        print(head, "— чисто" if not hits else f"— находок {len(hits)}")
        for h in hits:
            print("   ", h)
        total += len(hits)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
