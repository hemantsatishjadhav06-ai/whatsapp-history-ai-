"""Render release migrations and bounded worker schema readiness.

This manages SQL startup only. It never enables a connector or external sending.
"""
import argparse
from pathlib import Path
import time

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from assistant.config import Settings
from assistant.db import make_database

# Stable session lock, separate from short workspace application transactions.
MIGRATION_LOCK = 584913470125844


def migration_config():
    # Frozen container wheels live under .venv; repository data lives at WORKDIR.
    root = Path.cwd()
    if not (root / 'alembic.ini').is_file():
        raise RuntimeError('Run release startup from the repository work directory')
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'db/migrations'))
    return config


def migrate(settings, config):
    engine, _ = make_database(settings)
    try:
        if engine.dialect.name == 'postgresql':
            # Keep a dedicated session lock while Alembic uses its own connection.
            # Database statement_timeout bounds waiting; no indefinite lock wait.
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': MIGRATION_LOCK})
                try:
                    command.upgrade(config, 'head')
                finally:
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': MIGRATION_LOCK})
        else:
            command.upgrade(config, 'head')
    finally:
        engine.dispose()


def schema_ready(engine, expected):
    try:
        with engine.connect() as connection:
            current = set(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
            return current == expected
    except SQLAlchemyError:
        return False


def wait_schema(settings, config, timeout=300, interval=2, *, clock=time.monotonic, sleep=time.sleep):
    expected = set(ScriptDirectory.from_config(config).get_heads())
    if not expected:
        raise RuntimeError('No release migration head exists')
    engine, _ = make_database(settings)
    deadline = clock() + timeout
    try:
        while True:
            if schema_ready(engine, expected):
                return
            remaining = deadline - clock()
            if remaining <= 0:
                raise RuntimeError('The database has not reached the expected release schema')
            sleep(min(interval, remaining))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['migrate', 'wait-schema'])
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 900:
        parser.error('timeout must be between 1 and 900 seconds')
    try:
        settings = Settings().prepare()
        config = migration_config()
        if args.operation == 'migrate':
            migrate(settings, config)
        else:
            wait_schema(settings, config, args.timeout)
    except Exception:
        # Provider URLs, database credentials and SQL are not release log fields.
        raise SystemExit('Database release startup failed; inspect protected service diagnostics') from None
    print('Database release startup confirmed')


if __name__ == '__main__':
    main()
