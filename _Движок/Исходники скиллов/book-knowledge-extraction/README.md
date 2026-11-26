# Book Knowledge Extraction (Knowledge-OS + Heroes Camp)

Переносимый навык для превращения книг, научных статей, методичек и текстовых курсов в применимую базу знаний.
Объединяет формат карточек Ильи Красинского ([Heroes Camp](https://heroes.camp), 2026) и стандарт извлечения знаний
Knowledge-OS (пайплайн Ф0–Ф6, реестр покрытия, разметка `[факт]`/`[вывод]`/`[гипотеза]`, сверка с каноном Ф4).

Пакет автономен (`detachable: true`): работает внутри `D:\Knowledge-OS\_Движок\Исходники скиллов\book-knowledge-extraction\`
и устанавливается в любую другую AI-систему без внешних зависимостей.

## Установка в другие системы

### Codex
```bash
mkdir -p ~/.codex/skills
cp -R "D:\Knowledge-OS\_Движок\Исходники скиллов\book-knowledge-extraction" ~/.codex/skills/book-knowledge-extraction
```

### Claude Code
```bash
mkdir -p ~/.claude/skills
cp -R "D:\Knowledge-OS\_Движок\Исходники скиллов\book-knowledge-extraction" ~/.claude/skills/book-knowledge-extraction
```

## Состав пакета

- `SKILL.md` — правила, два режима работы (Knowledge-OS и автономный), 11 категорий извлечения, чек-лист качества.
- `templates/knowledge-item-template.md` — шаблон карточки знания с адресом, разметкой и практическим применением.
- `skill.yaml` — манифест переносимого пакета.
- `LICENSE.md` — авторство и условия использования (© Илья Красинский, 2026, https://heroes.camp).
