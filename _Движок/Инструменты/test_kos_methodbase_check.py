"""Проверка методобазы на игрушечном Knowledge-OS: ссылки и разделы, frontmatter, карты, навыки, словарь хозяев,
база нарушений, режим --changed, коды возврата."""
import json
import os
import subprocess
from pathlib import Path

import pytest

import kos_methodbase_check as mb

FM = """---
type: {type}
status: {status}
description: |
  Тестовый документ.
author: тест
created: 2026-11-01
updated: 2026-11-01
review_cycle: {cycle}
last_reviewed: 2026-11-01
tags:
  - тест

---
"""
PASSPORT = "\n## Место в системе\n\n- **Отвечает на:** тест.\n"
FOOTER = "\n# Связанные документы\n\n- [Карта](<{map}>)\n"
MAP = mb.MAP_FILE
OFFERS = "Методология — Офферы.md"


def doc(type_="methodology", status="active", cycle="6m", body="", passport=True, footer_to=MAP):
    text = FM.format(type=type_, status=status, cycle=cycle) + "\n# Заголовок\n" + (PASSPORT if passport else "")
    return text + body + (FOOTER.format(map=footer_to) if footer_to else "")


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def map_text(rows: str) -> str:
    return doc("reference", body=(
        "\n## Карта\n\n| Задача | Документ-хозяин | Навык | Зрелость | Чего нет |\n|---|---|---|---|---|\n" + rows +
        "\nПрочее: [Офферы](<" + OFFERS + ">), [Папка](<Папка/_manifest.md>).\n"), footer_to=OFFERS)


@pytest.fixture
def kos(tmp_path):
    """Чистый игрушечный Knowledge-OS: ошибок нет."""
    root = tmp_path / "kos"
    m = root / "Методологии"
    write(m / "_manifest.md", doc("manifest", body=(
        f"\n- [Офферы](<{OFFERS}>)\n- [Карта](<{MAP}>)\n- [Папка](<Папка/_manifest.md>)\n"), footer_to=None))
    write(m / OFFERS, doc(body=(
        "\n## 1. Рынок\n\nТекст.\n\n## 5. Скидки и срочность\n\nСкидка не афишируется, срочность точечно.\n"
        "\n| Шаг | Что |\n|---|---|\n| 7 | строка таблицы |\n\n1. Пункт списка.\n2. Ещё пункт.\n")))
    write(m / "Папка" / "_manifest.md", doc("manifest", body="\n- [Справочник](<Справочник — Тест.md>)\n",
                                             footer_to=None))
    write(m / "Папка" / "Справочник — Тест.md", doc("reference", status="draft", footer_to="../" + MAP))
    write(m / MAP, map_text(
        f"| Оффер | [Офферы](<{OFFERS}>) | `offer` | основа | — |\n"
        "| Папка | [Папка](<Папка/_manifest.md>) | — | черновик | — |\n"
        "| Спрос | навыки | `keysso` | основа | — |\n"))
    skills = root / "_Движок" / "Исходники скиллов"
    for name in ("offer", "keysso", "growth-route"):
        write(skills / name / "SKILL.md", f"---\nname: {name}\ndescription: Навык {name}.\n---\n\n# {name}\n")
    write(root / "CLAUDE.md", "# CLAUDE\n\n## Скиллы\n\n- `offer` — офферы.\n- Прочие: `growth-*`, `keysso`.\n\n"
                              "## Дальше\n")
    write(root / "_Движок" / "Канон" / "Стандарт — Тест.md", "# x\n")
    home = tmp_path / "skills-home"
    home.mkdir()
    for name in ("offer", "keysso", "growth-route"):
        link_dir(skills / name, home / name)
    owners = tmp_path / "owners.json"
    owners.write_text(json.dumps({"concepts": [{
        "key": "скидки", "owner": f"Методологии/{OFFERS}", "section": "5", "anchor": "(?i)скидк",
        "pattern": "(?i)не\\s+афиш", "aliases": ["Офферы"]}]}, ensure_ascii=False), encoding="utf-8")
    return {"root": root, "m": m, "home": home, "owners": owners, "skills": skills, "tmp": tmp_path}


def link_dir(source: Path, link: Path):
    """Ссылка на папку навыка: символическая, а без права на неё в Windows — junction."""
    try:
        os.symlink(source, link, target_is_directory=True)
    except OSError:
        import _winapi
        _winapi.CreateJunction(str(source), str(link))


def findings(kos):
    report, _ = mb.run(kos["root"], kos["owners"], kos["home"])
    return report.findings


def rules(kos, level=None):
    return sorted(f.rule for f in findings(kos) if level is None or f.level == level)


def cli(kos, *args):
    base = ["--root", str(kos["root"]), "--owners", str(kos["owners"]), "--skills-home", str(kos["home"]),
            "--baseline", str(kos["tmp"] / "baseline.json")]
    return mb.main(base + list(args))


def test_clean_repo_has_no_findings(kos):
    assert findings(kos) == []
    assert cli(kos, "check") == 0


def test_links_broken_wiki_absolute_but_not_in_code(kos):
    write(kos["m"] / "Методология — Ссылки.md", doc(body=(
        "\n[нет](<Нет такого.md>) и [[вики]] и [абс](D:/Knowledge-OS/x.md)\n"
        "`[в коде](<Нет.md>)` и\n```\n[в блоке](<Нет2.md>) [[тоже]]\n```\n")))
    write(kos["m"] / "_manifest.md", (kos["m"] / "_manifest.md").read_text(encoding="utf-8") + "Методология — Ссылки\n")
    got = [(f.rule, f.essence) for f in findings(kos) if f.path.endswith("Ссылки.md")]
    assert ("link-broken", "Нет такого.md") in got and ("link-wiki", "[[вики]]") in got
    assert ("link-absolute", "D:/Knowledge-OS/x.md") in got
    assert len(got) == 3 + 1   # + map-owner: новый хозяин не назван в карте


def test_section_references(kos):
    body = (f"\n[О](<{OFFERS}>), раздел 5. [О](<{OFFERS}>), раздел «Скидки и срочность». [О](<{OFFERS}>), шаг 7."
            f" [О](<{OFFERS}>), разделы 1 и 2. [О](<{OFFERS}>), раздел 9. [О](<{OFFERS}>), раздел «Нет такого»."
            f" [О](<{OFFERS}>)\n  в разделе «Платный трафик» — это папка, не раздел.\n")
    write(kos["m"] / "Методология — Разделы.md", doc(body=body))
    bad = sorted(f.essence for f in findings(kos) if f.rule == "section-missing")
    assert bad == sorted([f"{OFFERS} | раздел «Нет такого»", f"{OFFERS} | раздел 9"])


def test_link_outside_canon_only_from_core(kos):
    write(kos["root"] / "Проект" / "Документ.md", "# проект\n")
    write(kos["m"] / "Методология — Вовне.md", doc(body="\n[проект](<../Проект/Документ.md>)\n"))
    write(kos["m"] / "_исходные-заметки" / "Выжимка — Вовне.md", "[проект](<../../Проект/Документ.md>)\n")
    out = [f for f in findings(kos) if f.rule == "link-outside-canon"]
    assert [f.path for f in out] == ["Методологии/Методология — Вовне.md"]


def test_frontmatter_rules(kos):
    m = kos["m"]
    write(m / "Методология — Статус.md", doc(status="archived", cycle="12m"))
    write(m / "Методология — Цикл.md", doc(cycle="2w"))
    write(m / "Методология — Тип.md", doc(type_="reference"))
    write(m / "План — Волна.md", doc(type_="plan", cycle="1m"))
    write(m / "Постановка — Устав.md", doc(type_="charter"))
    write(m / "Постановка — Требования.md", doc(type_="requirements"))
    write(m / "Справочник — Без полей.md", "---\ntype: reference\nstatus: active\n---\n# x\n")
    write(m / "Справочник — Кривой.md", "---\ntype: reference\ndescription: a: b: c\n---\n# x\n")
    write(m / "_исходные-заметки" / "Выжимка — Что угодно.md", doc(type_="note", status="сырьё"))
    got = {(f.path.split("/")[-1], f.rule) for f in findings(kos) if f.rule.startswith(("fm-", "type-"))}
    assert ("Методология — Статус.md", "fm-status") in got
    assert ("Методология — Статус.md", "fm-review-cycle") not in got          # 12m — в закрытом списке
    assert ("Методология — Цикл.md", "fm-review-cycle") in got
    assert ("Методология — Тип.md", "type-prefix") in got
    assert not any(name.startswith(("План", "Постановка")) for name, _ in got)
    assert ("Справочник — Без полей.md", "fm-field") in got
    assert ("Справочник — Кривой.md", "fm-yaml") in got
    assert not any("Выжимка" in name for name, _ in got)


def test_catalog_and_map_owner(kos):
    write(kos["m"] / "Папка" / "Справочник — Сирота.md", doc("reference"))
    write(kos["m"] / "Методология — Вне карты.md", doc())
    write(kos["m"] / "_manifest.md", (kos["m"] / "_manifest.md").read_text(encoding="utf-8") +
          "- Методология — Вне карты\n")
    got = {(f.rule, f.path.split("/")[-1]) for f in findings(kos)}
    assert ("manifest-catalog", "Справочник — Сирота.md") in got     # папка в карте, но манифест его не знает
    assert ("map-owner", "Справочник — Сирота.md") not in got        # в карте через манифест папки
    assert ("map-owner", "Методология — Вне карты.md") in got
    assert ("manifest-catalog", "Методология — Вне карты.md") not in got


def test_passport_block_required_for_owner(kos):
    write(kos["m"] / "Методология — Без паспорта.md", doc(passport=False))
    write(kos["m"] / "_manifest.md", (kos["m"] / "_manifest.md").read_text(encoding="utf-8") +
          "- Методология — Без паспорта\n")
    assert "passport-block" in rules(kos, "error")


def test_map_maturity_check_and_write_preserves_bytes(kos):
    m = kos["m"]
    path = m / MAP
    text = path.read_text(encoding="utf-8").replace("| `offer` | основа |", "| `offer` | черновик |") \
        .replace("| — | черновик |", "| — |  основа  |")
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))   # CRLF должен сохраниться
    bad = [f for f in findings(kos) if f.rule == "map-maturity"]
    assert len(bad) == 2
    before = path.read_bytes()
    assert cli(kos, "write") == 0
    after = path.read_bytes()
    expected = before.replace("| `offer` | черновик |".encode(), "| `offer` | основа |".encode()) \
        .replace("| — |  основа  |".encode(), "| — |  черновик  |".encode())
    assert after == expected
    assert not [f for f in findings(kos) if f.rule == "map-maturity"]


def test_skills_rules(kos):
    skills, home = kos["skills"], kos["home"]
    write(skills / "sales-dept" / "SKILL.md", "---\nname: sales-dept\ndescription: |\n  " + "д" * 1100 +
          "\n---\n\nАбсолютный путь — норма: [карта](<D:/где-то/вне/репозитория.md>)\n\n## Границы\n\n- `offer`\n")
    write(skills / "ads-vk" / "SKILL.md", "---\nname: ads-vk\ndescription: x: y: z\n---\n# ads\n")
    write(kos["m"] / MAP, (kos["m"] / MAP).read_text(encoding="utf-8")
          .replace("`keysso` |", "`keysso`, `no-such-skill`, MCP `analytics-*` |"))
    got = {(f.rule, f.essence if f.rule == "skill-unknown" else f.path.split("/")[-2]) for f in findings(kos)}
    assert ("skill-description", "sales-dept") in got
    assert ("skill-not-in-claude", "sales-dept") in got
    assert ("skill-not-in-claude", "ads-vk") in got            # ads-* в CLAUDE.md нет, growth-* есть
    assert ("skill-not-in-claude", "growth-route") not in got
    assert ("skill-not-linked", "sales-dept") in got
    assert ("skill-yaml", "ads-vk") in got
    assert ("skill-neighbors", "sales-dept") in got
    assert ("skill-unknown", "no-such-skill") in got
    assert not any(r == "skill-unknown" and e.startswith("analytics") for r, e in got)
    assert not any(r.startswith("link-") for r, _ in got)


def test_owner_copy_warning(kos):
    m = kos["m"]
    write(m / "Методология — Копия.md", doc(body=(
        "\nСкидка не афишируется, честно.\n"
        f"Скидка не афишируется — [Офферы](<{OFFERS}>), раздел 5.\n"
        "Скидка не афишируется — „Офферы“, раздел 5.\n")))
    write(m / "_manifest.md", (m / "_manifest.md").read_text(encoding="utf-8") + "- Методология — Копия\n")
    copies = [f for f in findings(kos) if f.rule == "owner-copy"]
    assert len(copies) == 1 and "честно" in copies[0].message and copies[0].level == "warning"


def test_owners_config_errors(kos):
    data = json.loads(kos["owners"].read_text(encoding="utf-8"))
    data["concepts"].append(dict(data["concepts"][0], key="пропал", owner="Методологии/Нет.md"))
    data["concepts"].append(dict(data["concepts"][0], key="раздел", section="Нет такого раздела"))
    kos["owners"].write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert [f.essence for f in findings(kos) if f.rule == "owners-config"] == \
        ["пропал: нет хозяина", "раздел: нет раздела Нет такого раздела"]
    kos["owners"].write_text("{битый", encoding="utf-8")
    assert cli(kos, "check") == 2


def test_baseline_fails_only_on_new(kos, capsys):
    m = kos["m"]
    write(m / "Методология — Старое.md", doc(body="\n[нет](<Нет.md>)\n"))
    write(m / "_manifest.md", (m / "_manifest.md").read_text(encoding="utf-8") +
          "- Методология — Старое\n- Методология — Новое\n")
    assert cli(kos, "check") == 1
    assert cli(kos, "baseline") == 0
    assert cli(kos, "check") == 0
    write(m / "Методология — Старое.md", doc(body="\n\n\n[нет](<Нет.md>)\n"))   # сдвиг строки — та же ошибка
    assert cli(kos, "check") == 0
    write(m / "Методология — Старое.md", doc(body="\n[нет](<Нет.md>)\n[нет](<Нет.md>)\n"))  # второе такое же — новое
    assert cli(kos, "check") == 1
    capsys.readouterr()
    write(m / "Методология — Старое.md", doc(body="\n[нет](<Нет.md>)\n"))
    write(m / "Методология — Новое.md", doc(body="\n[[вики]]\n"))
    assert cli(kos, "check", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    new = [f for f in data["findings"] if f["new"]]
    assert sorted(f["rule"] for f in new) == ["link-wiki", "map-owner"]
    assert data["summary"]["link-broken"] == {"level": "error", "total": 1, "new": 0, "known": 1}


def test_failures_return_2(kos, tmp_path):
    assert mb.main(["--root", str(tmp_path / "нет"), "--owners", str(kos["owners"]), "check"]) == 2
    empty = tmp_path / "пусто"
    (empty / "Методологии").mkdir(parents=True)
    assert mb.main(["--root", str(empty), "--owners", str(kos["owners"]), "check"]) == 2
    (kos["m"] / "Методология — Битая.md").write_bytes(b"\xff\xfe\x00 not utf8")
    assert cli(kos, "check") == 2
    (kos["m"] / "Методология — Битая.md").unlink()
    (kos["tmp"] / "baseline.json").write_text("[]", encoding="utf-8")
    assert cli(kos, "check") == 2


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_changed_checks_staged_and_referrers(kos, capsys):
    root, m = kos["root"], kos["m"]
    write(m / "Методология — Ссылается.md", doc(body="\n[цель](<Методология — Цель.md>)\n"))
    write(m / "Методология — Цель.md", doc())
    write(m / "Методология — Чужая.md", doc(body="\n[нет](<Нет.md>)\n"))   # чужая старая ошибка вне индекса
    write(m / "_manifest.md", (m / "_manifest.md").read_text(encoding="utf-8") +
          "- Методология — Ссылается\n- Методология — Цель\n- Методология — Чужая\n")
    write(m / MAP, (m / MAP).read_text(encoding="utf-8") +
          "\n- Методология — Ссылается\n- Методология — Цель\n- Методология — Чужая\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "старт")
    assert cli(kos, "check", "--changed") == 0          # в индексе пусто: чужая ошибка не мешает
    git(root, "mv", "Методологии/Методология — Цель.md", "Методологии/Методология — Цель новая.md")
    capsys.readouterr()
    assert cli(kos, "check", "--changed", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    paths = {f["path"].split("/")[-1] for f in data["findings"] if f["new"]}
    assert "Методология — Ссылается.md" in paths            # ссылается на переименованный путь
    assert "Методология — Чужая.md" not in paths
    assert "Методология — Цель новая.md" in paths           # новый файл: нет в манифесте и в карте
