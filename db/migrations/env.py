from alembic import context

from assistant import models  # noqa: F401
from assistant.config import Settings
from assistant.db import Base, make_database

config = context.config
settings = Settings().prepare()
target_metadata = Base.metadata


def offline():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def online():
    engine, _ = make_database(settings)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    offline()
else:
    online()
