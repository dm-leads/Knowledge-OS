import os
import tempfile
from pathlib import Path

import pytest

from datacore.schema.engine import connect
from datacore.tests.pg_portable import PortablePostgres, bin_dir_of, ensure_binaries, pg_home


@pytest.fixture(scope="session")
def postgres_url():
    url = os.environ.get("DATACORE_TEST_PG_URL")
    if url:
        yield url
        return
    bin_dir = bin_dir_of(pg_home())
    if not (bin_dir / "pg_ctl.exe").exists():
        if os.environ.get("DATACORE_REQUIRE_PG") == "1":
            bin_dir = ensure_binaries()                 # ворота: скачать и поставить в ASCII-путь вне репозитория
        else:
            pytest.skip("нет Postgres: задайте DATACORE_TEST_PG_URL или DATACORE_REQUIRE_PG=1")
    server = PortablePostgres(bin_dir)
    try:
        yield server.start()
    finally:
        server.stop()


@pytest.fixture(params=["duckdb", "postgres"])
def engine(request):
    if request.param == "duckdb":
        path = Path(tempfile.mkdtemp(prefix="datacore-duck-")) / "t.duckdb"
        e = connect(f"duckdb:///{path.as_posix()}")
    else:
        e = connect(request.getfixturevalue("postgres_url"))   # лениво: вариант duckdb Postgres не трогает
        # чистый лист на каждый тест: свои схемы сносим
        for schema in ("meta", "raw", "facts", "metrics", "s1"):
            e.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        e.execute("DROP TABLE IF EXISTS t_pk")
        e.commit()
    yield e
    e.rollback()
    e.close()
