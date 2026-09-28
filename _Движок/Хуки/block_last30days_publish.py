# -*- coding: utf-8 -*-
"""Хук PreToolUse (Bash): не даёт last30days выгрузить отчёт на внешний хостинг ht-ml.app.

Флаги --publish / --publish-html публикуют результат исследования в открытый доступ.
Исследования по нашим проектам наружу не уходят без явного согласия владельца, поэтому
запрет стоит здесь, в коде, а не только в инструкции. Код выхода 2 блокирует команду,
текст из stderr видит агент.
"""
import json
import sys

try:
    cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
except Exception:
    sys.exit(0)  # непонятный ввод — не мешаем остальным командам

if "last30days" in cmd and "--publish" in cmd:
    sys.stderr.write(
        "Заблокировано настройками устройства: last30days с --publish выкладывает отчёт "
        "на внешний хостинг ht-ml.app в открытый доступ. Сохрани результат локально "
        "(--save-dir / --output); публикация — только по явному решению владельца.\n"
    )
    sys.exit(2)
sys.exit(0)
