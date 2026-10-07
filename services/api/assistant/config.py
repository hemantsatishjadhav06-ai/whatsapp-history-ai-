from pathlib import Path

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "sqlite:///.local/assistant.db"
    encryption_key: str = ""
    allow_dev_auth: bool = False
    session_secure: bool = True
    session_ttl_seconds: int = 86400
    google_client_id: str = ""
    google_android_client_id: str = ""
    google_ios_client_id: str = ""
    native_session_ttl_seconds: int = 3600
    native_refresh_ttl_seconds: int = 2592000
    allowed_origins: str = "http://localhost:3000"
    internal_service_token: str = ""
    model_provider: str = "disabled"
    model_api_key: str = ""
    model_name: str = ""
    model_api_url: str = "https://api.openai.com/v1"
    model_timeout_seconds: int = 30
    model_max_input_chars: int = 24000
    model_processing_region: str = "unconfigured"
    model_data_use_configuration: str = "unconfigured"
    model_pricing_verified: bool = False
    model_pricing_model_name: str = ""
    model_input_cost_microusd_per_million: int | None = None
    model_output_cost_microusd_per_million: int | None = None
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v23.0"
    enable_external_sends: bool = False
    connector_gateway_url: str = ""
    connector_gateway_token: str = ""
    connector_gateway_timeout_seconds: int = 10
    max_import_bytes: int = 2_000_000
    max_import_records: int = 20000
    request_limits_mode: str = "auto"
    request_max_inflight: int = 32
    request_control_reserve: int = 4
    request_thread_tokens: int = 48
    request_default_body_bytes: int = 65536
    request_body_timeout_seconds: float = 10
    request_json_max_depth: int = 64
    request_rate_window_seconds: int = 60
    request_rate_auth_issues: int = 30
    request_rate_auth_exchanges: int = 30
    request_rate_api: int = 600
    request_rate_source_api: int = 6000
    request_rate_control: int = 120
    request_rate_source_control: int = 120
    request_rate_global_control: int = 6000
    request_rate_global_auth: int = 6000
    request_rate_global_api: int = 60000
    request_rate_memory_keys: int = 10000
    request_rate_namespace: str = "milo-request-v1"
    trusted_proxy_key: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_seconds: float = 3
    db_pool_recycle_seconds: int = 1800
    db_statement_timeout_ms: int = 15000
    kafka_bootstrap_servers: str = "localhost:9092"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    workflow_task_queue: str = "assistant-schedules"
    redis_url: str = "redis://localhost:6379/0"

    def prepare(self) -> "Settings":
        # Railway and other PostgreSQL providers supply these URI prefixes;
        # select the installed psycopg 3 driver without parsing or logging secrets.
        for prefix in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefix):
                self.database_url = "postgresql+psycopg://" + self.database_url.removeprefix(prefix)
                break
        if not 300 <= self.native_session_ttl_seconds <= 86400:
            raise ValueError("Native sessions must expire between five minutes and 24 hours")
        if not self.native_session_ttl_seconds <= self.native_refresh_ttl_seconds <= 2592000:
            raise ValueError("Native refresh sessions must expire within 30 days and after the access session")
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("Unknown environment")
        bounds = {
            "session_ttl_seconds": (300, 2592000), "max_import_bytes": (1, 20000000),
            "max_import_records": (1, 100000), "request_max_inflight": (2, 4096),
            "request_control_reserve": (1, 128), "request_default_body_bytes": (1, 2097152),
            "request_thread_tokens": (8, 4100),
            "request_body_timeout_seconds": (0.001, 60), "request_json_max_depth": (4, 128),
            "request_rate_window_seconds": (1, 3600), "request_rate_auth_issues": (1, 10000),
            "request_rate_auth_exchanges": (1, 10000), "request_rate_api": (1, 100000),
            "request_rate_source_api": (1, 1000000), "request_rate_control": (1, 10000),
            "request_rate_source_control": (1, 1000000), "request_rate_global_control": (1, 1000000),
            "request_rate_global_auth": (1, 1000000), "request_rate_global_api": (1, 10000000),
            "request_rate_memory_keys": (10, 100000), "db_pool_size": (1, 100),
            "db_max_overflow": (0, 100), "db_pool_timeout_seconds": (0.01, 30),
            "db_pool_recycle_seconds": (30, 86400), "db_statement_timeout_ms": (100, 120000),
        }
        for field, (minimum, maximum) in bounds.items():
            value = getattr(self, field)
            import math
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{field} is outside its supported bounds")
        if self.request_control_reserve >= self.request_max_inflight:
            raise ValueError("Request admission must retain ordinary and control capacity")
        if self.request_thread_tokens < self.request_max_inflight + 4:
            raise ValueError("Thread capacity must exceed request admission to avoid database dependency starvation")
        if self.request_limits_mode == "auto":
            self.request_limits_mode = {"test": "off", "development": "memory", "production": "redis"}[self.environment]
        if self.request_limits_mode not in {"off", "memory", "redis"}:
            raise ValueError("Unknown request rate-limit backend")
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.request_rate_namespace):
            raise ValueError("Invalid rate-limit namespace")
        if self.trusted_proxy_key and len(self.trusted_proxy_key.encode()) < 32:
            raise ValueError("Trusted proxy signing key must contain at least 32 bytes")
        if self.connector_gateway_url:
            from urllib.parse import urlsplit
            endpoint = urlsplit(self.connector_gateway_url)
            if (endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                    or endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                    or (endpoint.scheme == "http" and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError("Connector gateway requires HTTPS or a loopback HTTP endpoint")
            if not self.connector_gateway_token:
                raise ValueError("Connector gateway requires its service token")
        if not 1 <= self.connector_gateway_timeout_seconds <= 60:
            raise ValueError("Connector gateway timeout must be between 1 and 60 seconds")
        for rate in (self.model_input_cost_microusd_per_million, self.model_output_cost_microusd_per_million):
            if rate is not None and not 0 <= rate <= 2_000_000_000:
                raise ValueError("Model pricing must be a bounded nonnegative micro-USD rate")
        if self.environment == "production":
            if self.allow_dev_auth or not self.session_secure:
                raise ValueError("Production requires secure sessions and forbids development authentication")
            if self.database_url.startswith("sqlite") or not self.encryption_key:
                raise ValueError("Production requires PostgreSQL and an externally managed encryption key")
            if self.model_provider == "mock":
                raise ValueError("Mock model is unavailable in production")
            if self.request_limits_mode != "redis":
                raise ValueError("Production requires shared Redis request limits")
            from urllib.parse import urlsplit
            origins = [value.strip() for value in self.allowed_origins.split(",") if value.strip()]
            if not origins:
                raise ValueError("Production requires an explicit HTTPS application origin")
            for value in origins:
                origin = urlsplit(value)
                if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                        or origin.query or origin.fragment or origin.path not in {"", "/"} or "*" in value):
                    raise ValueError("Production origins must be exact HTTPS origins")
            redis_endpoint = urlsplit(self.redis_url)
            if redis_endpoint.scheme not in {"redis", "rediss"} or not redis_endpoint.hostname:
                raise ValueError("Production requires a configured Redis endpoint")
        Path(".local").mkdir(exist_ok=True, mode=0o700)
        if not self.encryption_key:
            key_path = Path(".local/encryption.key")
            if not key_path.exists():
                with key_path.open("xb") as stream:
                    stream.write(Fernet.generate_key())
                key_path.chmod(0o600)
            self.encryption_key = key_path.read_text().strip()
        Fernet(self.encryption_key.encode())
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return self
