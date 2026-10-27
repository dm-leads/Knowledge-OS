"""Пересечения фраз включения навыка с описаниями соседних навыков — список спорных пар для разбора.

Берёт фразы «…» после слова «Триггеры:» в описании навыка и описания всех навыков в указанных папках (`*/SKILL.md`, поле `description`).
Пара спорная, если у фразы и чужого описания два общих слова и больше (по основе — первые пять букв, без служебных
слов) или чужое описание содержит фразу целиком. Ничего не пишет; печатает пары и число.

Запуск: py -3 trigger_overlap.py [--skill <SKILL.md>] <папка навыков> [<папка навыков> …]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STOP = {"для", "что", "как", "или", "это", "при", "без", "под", "над", "все", "его", "она", "они", "так", "уже",
        "статья", "статьи", "статью"}


def description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    match = re.search(r"^description:\s*(?:>-?|\|)?\s*\n?(.*?)(?=^\w[\w-]*:|\Z)", head, re.S | re.M)
    return " ".join((match.group(1) if match else "").split())


def stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{3,}", text.lower()) if w not in STOP}


def overlaps(skill_md: Path, folders: list[Path]) -> list[tuple[str, str, list[str]]]:
    own = description(skill_md)
    triggers = own.split("Триггеры:", 1)[-1].split(". ", 1)[0]     # фразы включения — после «Триггеры:» до точки
    phrases = re.findall(r"«([^»]+)»", triggers)
    rows = []
    for folder in folders:
        for other in sorted(folder.glob("*/SKILL.md")):
            if other.resolve() == skill_md.resolve():
                continue
            text = description(other)
            words = stems(text)
            for phrase in phrases:
                common = sorted(stems(phrase) & words)
                if phrase.lower() in text.lower() or len(common) >= 2:
                    rows.append((phrase, other.parent.name, common))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Пересечения фраз включения навыка с соседями")
    parser.add_argument("--skill", default=str(Path(__file__).resolve().parents[1] / "SKILL.md"))
    parser.add_argument("folders", nargs="+", help="папки навыков (в каждой — */SKILL.md)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = overlaps(Path(args.skill), [Path(f) for f in args.folders])
    for phrase, other, common in rows:
        print(f"«{phrase}» ↔ {other}: {', '.join(common) or 'фраза целиком'}")
    print(f"спорных пар: {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
