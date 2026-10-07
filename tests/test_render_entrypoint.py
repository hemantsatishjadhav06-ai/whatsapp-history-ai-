"""Schema release startup is isolated from the shared application test database."""
from pathlib import Path
from types import SimpleNamespace

from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from assistant.config import Settings
from assistant import render_entrypoint as startup


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def execution_options(self, **options):
        self.engine.options.append(options)
        return self

    def execute(self, query, params=None):
        sql = str(query)
        self.engine.queries.append((sql, params))
        if "pg_advisory_lock" in sql:
            if self.engine.lock_error:
                raise SQLAlchemyError("synthetic database secret")
            self.engine.locked = True
        elif "pg_advisory_unlock" in sql:
            self.engine.locked = False
        else:
            if self.engine.query_error:
                raise SQLAlchemyError("no alembic_version exists")
            return SimpleNamespace(scalars=lambda: iter(self.engine.rows))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeEngine:
    def __init__(self, dialect="postgresql", rows=None):
        self.dialect = SimpleNamespace(name=dialect)
        self.rows = [] if rows is None else rows
        self.locked = False
        self.lock_error = False
        self.query_error = False
        self.queries = []
        self.options = []
        self.disposed = False
        self.connected = 0

    def connect(self):
        self.connected += 1
        return FakeConnection(self)

    def dispose(self):
        self.disposed = True


def database(monkeypatch, engine):
    monkeypatch.setattr(startup, "make_database", lambda settings: (engine, None))


def heads(monkeypatch, values=("synthetic-head",)):
    monkeypatch.setattr(startup.ScriptDirectory, "from_config",
                        lambda config: SimpleNamespace(get_heads=lambda: list(values)))


def test_postgres_upgrade_holds_dedicated_session_lock_and_releases_it(monkeypatch):
    engine = FakeEngine()
    database(monkeypatch, engine)
    upgrades = []

    def upgrade(config, revision):
        assert engine.locked
        assert not engine.disposed
        upgrades.append((config, revision))

    monkeypatch.setattr(startup.command, "upgrade", upgrade)
    config = object()
    startup.migrate(object(), config)
    assert upgrades == [(config, "head")]
    assert engine.connected == 1
    assert engine.options == [{"isolation_level": "AUTOCOMMIT"}]
    assert engine.queries == [("SELECT pg_advisory_lock(:key)", {"key": startup.MIGRATION_LOCK}),
                              ("SELECT pg_advisory_unlock(:key)", {"key": startup.MIGRATION_LOCK})]
    assert not engine.locked
    assert engine.disposed


def test_failed_upgrade_releases_session_lock_and_disposes_engine(monkeypatch):
    engine = FakeEngine()
    database(monkeypatch, engine)

    def upgrade(config, revision):
        assert engine.locked
        raise RuntimeError("synthetic failed migration")

    monkeypatch.setattr(startup.command, "upgrade", upgrade)
    with pytest.raises(RuntimeError, match="failed migration"):
        startup.migrate(object(), object())
    assert not engine.locked
    assert engine.disposed
    assert "pg_advisory_unlock" in engine.queries[-1][0]


def test_failed_lock_acquisition_never_runs_upgrade_and_disposes_engine(monkeypatch):
    engine = FakeEngine()
    engine.lock_error = True
    database(monkeypatch, engine)
    monkeypatch.setattr(startup.command, "upgrade", lambda *args: pytest.fail("upgrade must not run"))
    with pytest.raises(SQLAlchemyError):
        startup.migrate(object(), object())
    assert engine.disposed
    assert len(engine.queries) == 1


def test_sqlite_migration_has_no_postgres_lock_and_still_cleans_up(monkeypatch):
    engine = FakeEngine("sqlite")
    database(monkeypatch, engine)
    seen = []
    monkeypatch.setattr(startup.command, "upgrade", lambda config, revision: seen.append(revision))
    startup.migrate(object(), object())
    assert seen == ["head"]
    assert engine.queries == []
    assert engine.disposed


@pytest.mark.parametrize("rows,ready", [(["head-a", "head-b"], True), (["head-a"], False),
                                      (["head-a", "head-b", "unknown-head"], False), ([], False)])
def test_schema_ready_requires_exact_release_head_set(rows, ready):
    engine = FakeEngine(rows=rows)
    assert startup.schema_ready(engine, {"head-a", "head-b"}) is ready


def test_schema_not_created_is_pending_readiness_instead_of_worker_startup():
    engine = FakeEngine()
    engine.query_error = True
    assert startup.schema_ready(engine, {"head"}) is False


def test_worker_waits_for_release_head_then_disposes_probe_engine(monkeypatch):
    engine = FakeEngine(rows=["older-head"])
    database(monkeypatch, engine)
    heads(monkeypatch)
    clock = Clock()

    def sleep(seconds):
        clock.sleep(seconds)
        engine.rows = ["synthetic-head"]

    startup.wait_schema(object(), object(), timeout=10, interval=2, clock=clock, sleep=sleep)
    assert clock.sleeps == [2]
    assert engine.connected == 2
    assert engine.disposed


def test_missing_schema_times_out_without_ever_upgrading_database(monkeypatch):
    engine = FakeEngine()
    engine.query_error = True
    database(monkeypatch, engine)
    heads(monkeypatch)
    monkeypatch.setattr(startup.command, "upgrade", lambda *args: pytest.fail("workers never migrate"))
    clock = Clock()
    with pytest.raises(RuntimeError, match="expected release schema"):
        startup.wait_schema(object(), object(), timeout=5, interval=2, clock=clock, sleep=clock.sleep)
    assert clock.sleeps == [2, 2, 1]
    assert engine.connected == 4
    assert engine.disposed


def test_worker_probe_unexpected_failure_also_disposes_engine(monkeypatch):
    engine = FakeEngine()
    database(monkeypatch, engine)
    heads(monkeypatch)
    monkeypatch.setattr(startup, "schema_ready", lambda *args: (_ for _ in ()).throw(RuntimeError("unexpected")))
    with pytest.raises(RuntimeError, match="unexpected"):
        startup.wait_schema(object(), object())
    assert engine.disposed


def test_no_release_head_fails_before_creating_database_engine(monkeypatch):
    heads(monkeypatch, ())
    monkeypatch.setattr(startup, "make_database", lambda settings: pytest.fail("must not connect without head"))
    with pytest.raises(RuntimeError, match="No release migration head"):
        startup.wait_schema(object(), object())


def test_migration_config_resolves_repository_data_from_work_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="repository work directory"):
        startup.migration_config()
    (tmp_path / "alembic.ini").write_text("[alembic]\n")
    config = startup.migration_config()
    assert config.config_file_name == str(tmp_path / "alembic.ini")
    assert config.get_main_option("script_location") == str(tmp_path / "db/migrations")


def test_real_sqlite_upgrade_and_worker_readiness_on_isolated_database(monkeypatch, tmp_path):
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo)
    url = f"sqlite:///{tmp_path / 'release-only.db'}"
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setenv("ENVIRONMENT", "test")
    config = startup.migration_config()
    settings = Settings(_env_file=None, environment="test", database_url=url, encryption_key=key).prepare()
    startup.migrate(settings, config)
    startup.migrate(settings, config)  # Repeated release hook is idempotent.
    startup.wait_schema(settings, config, timeout=1)
    engine = create_engine(url)
    try:
        expected = set(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection:
            assert set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) == expected
        assert {"users", "workspaces", "connectors", "outbox"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_cli_startup_error_does_not_expose_database_url_or_sql(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["render-entrypoint", "migrate"])
    monkeypatch.setattr(startup, "migration_config", lambda: object())
    monkeypatch.setattr(startup, "migrate", lambda *args: (_ for _ in ()).throw(
        RuntimeError("postgres://synthetic-user:synthetic-secret@private/service SQL UPDATE")))
    with pytest.raises(SystemExit) as error:
        startup.main()
    assert str(error.value) == "Database release startup failed; inspect protected service diagnostics"
    assert "synthetic-secret" not in capsys.readouterr().out
