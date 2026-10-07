from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.responses import JSONResponse

from .config import Settings
from .db import make_database
from .request_security import RequestRateLimiter, RequestSecurityMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = (settings or Settings()).prepare()
    engine, factory = make_database(settings)
    limiter = RequestRateLimiter(settings)

    @asynccontextmanager
    async def lifespan(app):
        import anyio
        thread_limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = thread_limiter.total_tokens
        thread_limiter.total_tokens = settings.request_thread_tokens
        try:
            yield
        finally:
            thread_limiter.total_tokens = previous_tokens
            await limiter.close()
            engine.dispose()

    application = FastAPI(title="Milo API", version="0.3.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = factory
    application.state.request_limiter = limiter
    application.add_middleware(CORSMiddleware,
                               allow_origins=[v.strip() for v in settings.allowed_origins.split(",") if v.strip()],
                               allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                               allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Authorization"])

    application.add_middleware(RequestSecurityMiddleware, settings=settings, limiter=limiter)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Validation diagnostics must never echo Google credentials, session
        # secrets or arbitrary private input into error bodies or access logs.
        errors = [{key: value for key, value in item.items() if key in {"type", "loc", "msg"}}
                  for item in error.errors()[:20]]
        return JSONResponse({"detail": errors}, status_code=422,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @application.exception_handler(PoolTimeout)
    async def pool_capacity_error(request, error):
        return JSONResponse({"detail": "Database capacity is busy; check current state before retrying"},
                            status_code=503, headers={"Retry-After": "2", "Cache-Control": "no-store"})

    @application.get("/health/live")
    async def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready():
        from fastapi import HTTPException
        try:
            import anyio
            def database_ready():
                with factory() as session:
                    session.execute(text("SELECT id FROM workspaces LIMIT 1"))
            await anyio.to_thread.run_sync(database_ready)
            await limiter.ready()
        except Exception:
            raise HTTPException(503, "Database schema or request-limit service is not ready")
        return {"status": "ok", "external_integrations": "not_validated"}

    from . import actions, assistantui, auth, automation, companion, core, intelligence, jobs, lifecycle, messaging, mobile_auth, native, people, tasks, webhooks
    for module in (auth, core, intelligence, messaging, tasks, webhooks, automation,
                   native, actions, jobs, people, companion, lifecycle, mobile_auth, assistantui):
        application.include_router(module.router)
        application.include_router(module.router, prefix="/v1")
    return application
