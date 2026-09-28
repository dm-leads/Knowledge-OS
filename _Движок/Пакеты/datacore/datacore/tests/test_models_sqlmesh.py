"""Спайк Р5 → постоянный контрактный тест моделей (Я10, Я12): одна модель, два движка, аудит ловит ложь."""
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from datacore.schema.engine import connect
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts

MODELS = Path(__file__).resolve().parents[1] / "models"
MSK = timezone(timedelta(hours=3))
PROV = Provenance("crm", "D", "L-spike", datetime(2026, 9, 14, 12, tzinfo=MSK))
SQLMESH = Path(sys.executable).parent / ("sqlmesh.exe" if os.name == "nt" else "sqlmesh")


def seed(engine):
    migrate(engine)
    register_load(engine, PROV, date(2026, 9, 14))
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1", created_at=datetime(2026, 8, 1, tzinfo=MSK))], PROV)
    write_facts(engine, "deal", [dict(deal_id=i, contact_id=1, brand="b1" if i < 3 else "b2", pipeline="p", status="s",
                                      created_at=datetime(2026, 8, i, 10, tzinfo=MSK)) for i in (1, 2, 3)], PROV)


def sqlmesh(*args, env):
    """Запуск движка моделей. Кэш и журналы — во временную папку рядом с состоянием: в проекте models/ лежит в
    выпуске с паспортом, и лишний файл там краснит тест паспорта."""
    runtime = Path(env["DATACORE_STATE_PATH"]).parent
    env = {"DATACORE_SQLMESH_CACHE": str(runtime / "sqlmesh_cache"), **env}
    return subprocess.run([str(SQLMESH), "--log-file-dir", str(runtime / "logs"), *args], cwd=MODELS,
                          env={**os.environ, "PYTHONUTF8": "1", **env},
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_unit_tests_green(tmp_path):
    r = sqlmesh("test", env={"DATACORE_STATE_PATH": str(tmp_path / "state.duckdb")})
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("gateway", ["duckdb", "postgres"])
def test_same_model_materializes_on_both_engines(gateway, request, tmp_path):
    env = {"DATACORE_STATE_PATH": str(tmp_path / "state.duckdb")}   # своё состояние на каждый прогон
    if gateway == "duckdb":
        db = tmp_path / "spike.duckdb"
        url = f"duckdb:///{db.as_posix()}"
        env["DATACORE_DUCKDB_PATH"] = str(db)
    else:
        postgres_url = request.getfixturevalue("postgres_url")   # лениво: вариант duckdb Postgres не требует
        p = urlparse(postgres_url)
        url = postgres_url
        env.update({"DATACORE_PG_HOST": p.hostname, "DATACORE_PG_PORT": str(p.port),
                    "DATACORE_PG_USER": p.username, "DATACORE_PG_DATABASE": p.path.lstrip("/")})
        if p.password:   # на сервере вход по паролю, на машине разработчика — доверенный
            env["DATACORE_PG_PASSWORD"] = p.password
    with connect(url) as e:
        if gateway == "postgres":
            for schema in ("meta", "raw", "facts", "metrics"):
                e.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        seed(e)
    r = sqlmesh("--gateway", gateway, "plan", "--auto-apply", "--no-prompts", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    with connect(url) as e:
        got = e.fetchall("SELECT brand, month, deals FROM metrics.deals_by_month ORDER BY brand")
    assert got == [("b1", date(2026, 8, 1), 2), ("b2", date(2026, 8, 1), 1)]


def test_audit_fails_on_planted_row(tmp_path):
    db = tmp_path / "spike_audit.duckdb"
    env = {"DATACORE_DUCKDB_PATH": str(db), "DATACORE_STATE_PATH": str(tmp_path / "state.duckdb")}
    with connect(f"duckdb:///{db.as_posix()}") as e:
        seed(e)
        write_facts(e, "deal", [dict(deal_id=9, contact_id=1, brand="", pipeline="p", status="s",
                                     created_at=datetime(2026, 8, 9, tzinfo=MSK))], PROV)
    r = sqlmesh("--gateway", "duckdb", "plan", "--auto-apply", "--no-prompts", env=env)
    assert r.returncode != 0 and "brand_not_blank" in (r.stdout + r.stderr)


def test_models_leave_no_runtime_files_in_the_code_folder(tmp_path):
    """После прогона движка моделей в models/ нет ни кэша, ни журналов: иначе выпуск в проекте «правили руками»."""
    before = {p.relative_to(MODELS) for p in MODELS.rglob("*")}
    r = sqlmesh("test", env={"DATACORE_STATE_PATH": str(tmp_path / "state.duckdb")})
    assert r.returncode == 0, r.stdout + r.stderr
    extra = sorted(str(p) for p in {p.relative_to(MODELS) for p in MODELS.rglob("*")} - before)
    assert extra == [], f"движок моделей оставил файлы в каталоге кода: {extra}"
