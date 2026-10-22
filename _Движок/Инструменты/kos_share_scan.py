"""Скан папки перед отправкой наружу: секреты, персональные данные, названия наших проектов, локальные пути.

Запуск: py -3 kos_share_scan.py "<папка>" [--allow "слово" ...]
Печатает находки «файл:строка: класс: фрагмент» (значения похожих на ключи строк маскируются) и итог.
Код возврата 1, если есть находки, не покрытые --allow.
"""
import argparse
import pathlib
import re
import sys

# Свои названия проектов, заказчиков и людей — то, что не должно уйти к посторонним.
OUR_NAMES = [
    "viklimat", "iklimat", "бризекс", "breezeks", "knowledge-os", "knowledge os", "kos_", "kos-",
    "second brain", "второй мозг", "виталий", "заичкин", "дмитри", "lark", "roistat", "amocrm",
    "porg-", "105977811", "timeweb", "tmweb", "dm-leads", "dm_leads", "aleksandrg01", "_движок",
    "канон knowledge", "ai-hub", "кайа", "kaia", "гончаров",
]

PATTERNS = [
    ("путь", re.compile(r"(?i)\b[A-Z]:[\\/]")),
    ("путь", re.compile(r"(?i)/Users/[A-Za-z]|\\Users\\[A-Za-z]|/home/[a-z]")),
    ("секрет", re.compile(r"(?i)\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}")),
    ("секрет", re.compile(r"\b(ghp|gho|glpat|xox[bap])[-_][A-Za-z0-9_\-]{10,}")),
    ("секрет", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("секрет", re.compile(r"(?i)\b[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|PASS)[A-Z0-9_]*\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{12,}")),
    ("секрет", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{20,}")),
    ("ПДн-почта", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ПДн-телефон", re.compile(r"(?<!\d)(\+7|8)[\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")),
]

TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".csv", ".yaml", ".yml", ".toml", ".js", ".ts", ".html", ""}


def mask(fragment: str) -> str:
    return re.sub(r"[A-Za-z0-9_\-\.]{12,}", lambda m: m.group(0)[:4] + "…", fragment)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--allow", action="append", default=[], help="фрагмент, который разрешён (регистр не важен)")
    args = ap.parse_args()
    root = pathlib.Path(args.folder)
    allow = [a.lower() for a in args.allow]
    findings, files = [], 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        for name in OUR_NAMES:
            if name in rel.lower():
                findings.append((rel, 0, "название в имени файла", name))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            findings.append((rel, 0, "не текст — проверить глазами", path.suffix))
            continue
        files += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for no, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            for name in OUR_NAMES:
                if name in low:
                    findings.append((rel, no, "наше название", name))
            for cls, rx in PATTERNS:
                for m in rx.finditer(line):
                    findings.append((rel, no, cls, mask(m.group(0))))
    open_ = []
    for rel, no, cls, frag in findings:
        allowed = any(a in frag.lower() for a in allow)
        mark = "разрешено" if allowed else "НАХОДКА"
        print(f"{mark}  {rel}:{no}: {cls}: {frag}")
        if not allowed:
            open_.append(rel)
    print(f"Итог: файлов прочитано {files}, находок {len(findings)}, не разрешено {len(open_)}")
    return 1 if open_ else 0


if __name__ == "__main__":
    sys.exit(main())
