"""Команда миграций (этап 8): на сервере схему применяет человек и должен видеть результат."""
from datetime import datetime, timezone

from datacore.schema.engine import connect
from datacore.schema.migrate import all_versions
from datacore.serve.migrate_cli import main as migrate_main


def test_cli_applies_and_reports(tmp_path, capsys):
    """Этап 8: миграции запускает человек на сервере, поэтому у команды должен быть внятный отчёт.
    Молчаливый успех здесь уже обманул однажды — модуль без точки входа возвращал 0, ничего не сделав.
    С явным адресом базы конфигурация инстанса не нужна."""
    db = tmp_path / "cli.duckdb"
    url = f"duckdb:///{db.as_posix()}"

    assert migrate_main(["--db", url]) == 0
    first = capsys.readouterr().out
    assert f"Применено миграций: {len(all_versions())}" in first
    assert f"Версия схемы в базе: {max(all_versions())}" in first

    assert migrate_main(["--db", url]) == 0                   # повтор ничего не применяет
    again = capsys.readouterr().out
    assert "Новых миграций нет" in again and f"Версия схемы в базе: {max(all_versions())}" in again

    with connect(url, read_only=True) as e:
        assert [v for (v,) in e.fetchall("SELECT version FROM meta.schema_version ORDER BY version")] == all_versions()


def test_cli_prints_version_from_the_database_not_from_disk(tmp_path, capsys):
    """Версия — из meta.schema_version, а не номер последнего файла на диске (ревью методологии 28.09.2026).
    База, чья версия не совпадает с выпуском, — код 1: ночной прогон останавливается до загрузок."""
    url = f"duckdb:///{(tmp_path / 'ahead.duckdb').as_posix()}"
    assert migrate_main(["--db", url]) == 0
    capsys.readouterr()
    with connect(url) as e:                                   # база ушла вперёд выпуска: версия, которой на диске нет
        e.execute("INSERT INTO meta.schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                  (9999, "future", datetime.now(timezone.utc)))
    assert migrate_main(["--db", url]) == 1
    out = capsys.readouterr().out
    assert "Версия схемы в базе: 9999" in out and "🟥" in out
