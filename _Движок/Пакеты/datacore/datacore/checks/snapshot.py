"""Снять снимки чисел на дату вручную: `python -m datacore.checks.snapshot --as-of 2026-09-16`.

Прежняя точка входа, оставлена для ручного запуска. Снимки — отдельный шаг ночного прогона
`python -m datacore.serve.snapshots`; эта команда вызывает тот же шаг, чтобы два пути не разошлись. Ручной запуск
нужен, когда загрузка в тот день не шла, а снимок нужен."""
from __future__ import annotations

import sys

from datacore.serve.snapshots import main

if __name__ == "__main__":
    sys.exit(main())
