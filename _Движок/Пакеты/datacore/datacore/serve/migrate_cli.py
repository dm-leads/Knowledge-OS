"""Применение миграций командой человека.

Слой schema/ — переносимое ядро без интерфейсов: тест границы запрещает там argparse и sys
(попытка положить эту команду туда его и уронила). Поэтому командная строка живёт в serve/,
рядом с остальными командами — ask, explain, motivation, publish.

Нужна переносу на сервер: там схему создаёт не тест, а человек, и ему нужно видеть,
что именно применилось. Молчаливый успех здесь уже обманул однажды.

Версия печатается из базы (`meta.schema_version`), а не номером последнего файла на диске: успех миграции
доказывает состояние базы, а не содержимое папки (стандарт, раздел 2 и «Сервер и ночное обновление»). Если
в базе версия не последняя — код 1, и ночной прогон останавливается до загрузок.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datacore.schema.config import load_config
from datacore.schema.engine import connect
from datacore.schema.migrate import all_versions, applied_versions, migrate
from datacore.serve.storage import storage_url

CONFIG = Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Применить миграции схемы ядра данных")
    ap.add_argument("--db", default=None, help="адрес базы; по умолчанию — файл DuckDB из конфигурации")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)

    # С явным адресом базы конфигурация инстанса не нужна: адрес — всё, что команде надо знать.
    url = a.db or storage_url(load_config(Path(a.config) if a.config else CONFIG))

    engine = connect(url)
    try:
        applied = migrate(engine)
        in_db = applied_versions(engine)
    finally:
        engine.close()

    on_disk = all_versions()
    if applied:
        print(f"Применено миграций: {len(applied)} — {', '.join(str(v) for v in applied)}")
    else:
        print("Новых миграций нет.")
    db_version = max(in_db) if in_db else None
    print(f"Версия схемы в базе: {db_version if db_version is not None else 'нет'}")
    missing = sorted(set(on_disk) - set(in_db))
    if missing or db_version != max(on_disk):
        print(f"🟥 база не на последней версии: на диске миграции до {max(on_disk)}, "
              f"в базе не применены {missing or 'нет, но версия базы выше диска — выпуск старше базы'}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
