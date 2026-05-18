import logging

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.auth import AccessPasscodeMiddleware
from app.core.lifespan import lifespan


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def create_app() -> FastAPI:
    _configure_logging()

    from app.api.admin import router as admin_router
    from app.api.auth import router as auth_router
    from app.api.health import router as health_router
    from app.api.session import router as session_router
    from app.api.avatar import router as avatar_router
    from app.api.ws_interview import router as ws_router

    app = FastAPI(
        title="BehavioralDummy",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    # Middleware execution order is reverse of registration: CORS first
    # registered → innermost; AccessPasscode last registered → outermost,
    # so unauthenticated requests are short-circuited before hitting any router.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["content-type"],
    )
    app.add_middleware(AccessPasscodeMiddleware)

    app.include_router(health_router)
    app.include_router(auth_router, prefix="/auth")
    app.include_router(session_router, prefix="/session")
    app.include_router(avatar_router, prefix="/avatar")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(ws_router)

    return app


app = create_app()
