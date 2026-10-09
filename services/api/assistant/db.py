from datetime import UTC, datetime
import json
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import Request
from sqlalchemy import Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = False

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        cipher = getattr(dialect, "assistant_cipher", None)
        if cipher is None:
            raise RuntimeError("Encryption is not initialized")
        return cipher.encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        cipher = getattr(dialect, "assistant_cipher", None)
        if cipher is None:
            raise RuntimeError("Encryption is not initialized")
        return cipher.decrypt(value.encode()).decode()


class EncryptedJSON(EncryptedText):
    """Protect free-form arrays containing owner preferences or missing facts."""
    cache_ok = False

    def process_bind_param(self, value, dialect):
        return super().process_bind_param(json.dumps(value) if value is not None else None, dialect)

    def process_result_value(self, value, dialect):
        plain = super().process_result_value(value, dialect)
        return json.loads(plain) if plain is not None else None


def make_database(settings):
    # Keep SQL parameters (identifiers, ciphertext) out of exception text and logs.
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if settings.database_url.endswith(":memory:"):
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
                      pool_timeout=settings.db_pool_timeout_seconds, pool_recycle=settings.db_pool_recycle_seconds,
                      connect_args={"connect_timeout": 5,
                                    "options": f"-c statement_timeout={settings.db_statement_timeout_ms}"})
    engine = create_engine(settings.database_url, **kwargs)
    engine.dialect.assistant_cipher = Fernet(settings.encryption_key.encode())
    if settings.database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine, sessionmaker(engine, expire_on_commit=False)


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
