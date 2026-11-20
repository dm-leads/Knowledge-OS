"""Проверка раскладки папок репозитория: без куч, снимки на своём месте, чистый корень, манифест с «Читать первым».

Зачем: порядок, который держит память агента, разваливается за месяц — один проект за полгода дошёл до 118 и 135
файлов в двух папках инструментов и 300 файлов снимков среди рабочих документов. Порядок держит проверка в коде:
перед коммитом и в CI. Правила — «Стандарт — Конвенция метаданных и _manifest», раздел 6.

Правила (номера как в стандарте):
- Р1 — в папке не больше DOCS_MAX документов (`.md`, кроме `_manifest.md` и `TASKS.md`);
- Р2 — не больше FILES_MAX файлов (без вложенных);
- Р3 — без порога: папки картинок (картинок не меньше половины, документов и кода нет) и архивы;
- Р4 — снимки на дату (папка с «снимк»/«снимок»/`snapshots` в имени) — только в папке ресурсов вне git;
- Р5 — в корне только белый список;
- Р6 — манифест у папки, где есть документ или скрипт; папку данных упоминает манифест родителя;
- Р7 — в манифесте строки «Читать первым:» и «Не трогать:»;
- Р8 — исключение только записью «папка | правило | причина | дата» в файле исключений.

Настройки проекта — `.kos-layout.json` в корне репозитория (все поля необязательны):
    {"docs_max": 25, "files_max": 40, "root_files": [...], "root_dirs": [...], "root_dir_pattern": "^0\\d? — ",
     "exceptions": "путь/к/исключениям.txt", "archive_parts": ["Архив", "Исполненные"]}

Запуск:
    python kos_layout_check.py <корень репозитория>            # отчёт; код выхода 1 — есть нарушения
    python kos_layout_check.py <корень> --strict              # то же для CI
Проект с собственной проверкой (repo_check) может импортировать `layout_issues` отсюда.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULTS = {
    "docs_max": 25,
    "files_max": 40,
    "root_files": ["AGENTS.md", "CLAUDE.md", "README.md", "_manifest.md", ".gitignore", ".gitattributes"],
    "root_dirs": ["_канон", ".github", ".githooks", ".claude", ".agents"],
    "root_dir_pattern": r"^0\d? — ",
    "exceptions": "",
    "archive_parts": ["Архив", "Исполненные"],
}
MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".mp4", ".pdf"}
NOT_DOCS = {"_manifest.md", "TASKS.md"}
SNAPSHOT = re.compile(r"(?i)снимк|снимок|^snapshots$")


def load_config(root: Path) -> dict:
    cfg = dict(DEFAULTS)
    p = root / ".kos-layout.json"
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


def tracked(root: Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", "ls-files"], capture_output=True)
    return [f for f in r.stdout.decode("utf-8").splitlines() if f]


def tree_of(files: list[str]) -> dict[str, list[str]]:
    tree: dict[str, list[str]] = {}
    for f in files:
        parts = f.split("/")
        for i in range(len(parts)):
            tree.setdefault("/".join(parts[:i]), [])
        tree["/".join(parts[:-1])].append(f)
    return tree


def exceptions(root: Path, cfg: dict) -> set[tuple[str, str]]:
    out = set()
    p = root / cfg["exceptions"] if cfg["exceptions"] else None
    if p and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 3 and parts[0] and not parts[0].startswith("#") and parts[2]:
                out.add((parts[0].rstrip("/"), parts[1]))
    return out


def kind(d: str, own: list[str], cfg: dict) -> str:
    parts = d.split("/")
    if any(x.startswith(".") for x in parts) or parts[0] == "_канон":
        return "скрытая"
    if set(cfg["archive_parts"]) & set(parts):
        return "архив"
    if any(SNAPSHOT.search(x) for x in parts):
        return "снимки"
    no_docs = not any(f.endswith(".py") or (f.endswith(".md") and f.split("/")[-1] not in NOT_DOCS) for f in own)
    if own and no_docs and sum(Path(f).suffix.lower() in MEDIA for f in own) / len(own) >= 0.5:
        return "картинки"
    return ""


def layout_issues(root: Path, files: list[str] | None = None, only: set[str] | None = None,
                  added: set[str] | None = None, cfg: dict | None = None) -> list[tuple[str, str, str, bool]]:
    """Нарушения: (папка, правило, текст, жёсткое). Жёсткое — новый файл в корне вне списка или в папке снимков."""
    cfg = cfg or load_config(root)
    files = tracked(root) if files is None else files
    added = added or set()
    tree = tree_of(files)
    exc = exceptions(root, cfg)
    root_dir_rx = re.compile(cfg["root_dir_pattern"])
    out: list[tuple[str, str, str, bool]] = []

    def add(d, rule, text, hard=False):
        if (d, rule) not in exc:
            out.append((d, rule, text, hard))

    for f in tree.get("", []):
        if f not in cfg["root_files"] and not f.startswith("."):
            add("", "Р5", f"«{f}» в корне — вне белого списка", f in added)
    for d in tree:
        if d and "/" not in d and d not in cfg["root_dirs"] and not root_dir_rx.match(d):
            add("", "Р5", f"папка «{d}/» в корне — вне белого списка", any(f.startswith(d + "/") for f in added))
    for d, own in sorted(tree.items()):
        if not d or not own or (only is not None and d not in only):
            continue
        k = kind(d, own, cfg)
        if k == "скрытая":
            continue
        if k == "снимки":
            add(d, "Р4", f"снимки на дату в git ({len(own)} файлов) — место им в папке ресурсов вне git",
                any(f in added for f in own))
            continue
        if k in ("архив", "картинки"):
            continue
        docs = [f for f in own if f.endswith(".md") and f.split("/")[-1] not in NOT_DOCS]
        if len(docs) > cfg["docs_max"]:
            add(d, "Р1", f"{len(docs)} документов > {cfg['docs_max']} — развести по смысловым блокам")
        if len(own) > cfg["files_max"]:
            add(d, "Р2", f"{len(own)} файлов > {cfg['files_max']} — развести по подпапкам (код — по системам)")
        names = {f.split("/")[-1] for f in own}
        if "_manifest.md" not in names:
            if docs or any(f.endswith(".py") for f in own):
                add(d, "Р6", "нет _manifest.md")
            else:
                parent = d.rsplit("/", 1)[0] if "/" in d else ""
                pm = root / parent / "_manifest.md"
                leaf = d.split("/")[-1]
                if pm.is_file() and leaf not in pm.read_text(encoding="utf-8", errors="replace"):
                    add(d, "Р6", f"папку данных не упоминает манифест родителя — допишите «{leaf}»")
            continue
        text = (root / d / "_manifest.md").read_text(encoding="utf-8", errors="replace").lower()
        miss = [s for s in ("Читать первым:", "Не трогать:") if s.lower() not in text]
        if miss:
            add(d, "Р7", "в _manifest.md нет строк " + ", ".join(f"«{m}»" for m in miss))
    return out


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in argv if not a.startswith("--")]
    root = Path(args[0] if args else ".").resolve()
    issues = layout_issues(root)
    for d, rule, text, _ in issues:
        print(f"{d or '(корень)'}: {rule} {text}")
    print(f"kos_layout_check: нарушений {len(issues)}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
