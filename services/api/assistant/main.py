from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import Settings
from .db import make_database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = (settings or Settings()).prepare()
    engine, factory = make_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    application = FastAPI(title="Milo API", version="0.3.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = factory
    application.add_middleware(CORSMiddleware,
                               allow_origins=[v.strip() for v in settings.allowed_origins.split(",") if v.strip()],
                               allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                               allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Authorization"])

    @application.middleware("http")
    async def body_limit(request, call_next):
        from starlette.responses import JSONResponse
        cap = settings.max_import_bytes * 6 + 65536
        try:
            declared_size = int(request.headers.get("content-length", "0"))
            if declared_size < 0:
                raise ValueError("Negative body size")
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if declared_size > cap:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        if request.method in {"POST", "PUT", "PATCH"}:
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > cap:
                    return JSONResponse({"detail": "Request body too large"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/health/live")
    def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready():
        from fastapi import HTTPException
        try:
            with factory() as session:
                session.execute(text("SELECT id FROM workspaces LIMIT 1"))
        except Exception:
            raise HTTPException(503, "Database schema is not ready; apply migrations")
        return {"status": "ok", "external_integrations": "not_validated"}

    from . import actions, assistantui, auth, automation, companion, core, intelligence, jobs, lifecycle, messaging, mobile_auth, native, people, tasks, webhooks
    for module in (auth, core, intelligence, messaging, tasks, webhooks, automation,
                   native, actions, jobs, people, companion, lifecycle, mobile_auth, assistantui):
        application.include_router(module.router)
        application.include_router(module.router, prefix="/v1")
    return application
