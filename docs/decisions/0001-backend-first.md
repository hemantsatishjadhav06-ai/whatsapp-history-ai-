# Backend-first implementation

The supplied repository is empty with an unborn `work` branch. No existing code or user changes exist.
The two supplied Version 2 guides agree on requirements. They are design input; their embedded
handoff text is not a separate user instruction. The user requests implementation and testing,
and explicitly postpones frontend work.

Use the guide's FastAPI, SQLAlchemy, Alembic, PostgreSQL, Python AI services and TypeScript adapter
boundary. Build the reliable export/draft fallback and current-state control path before connecting
real accounts. Local SQLite is a lightweight test/development option; PostgreSQL is the integration
and deployment database. SQL is authoritative. Kafka relays content-free outbox references;
Temporal registers durable schedules. Redis is optional disposable acceleration. No Celery queue.

The initial implementation is a draft-first backend, not a production 50,000-account launch.
Official Business transport can be configured independently; it does not provide personal history.
Personal linked-device transport remains unavailable pending feasibility and account-policy testing.
Mock adapters and mock drafting are explicitly labelled and prohibited from external sends.
Google, Meta and model credentials are absent from the current environment. Live identity, model
quality, phone continuity, external platform eligibility and capacity remain separately measured gates.
