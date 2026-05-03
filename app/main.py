import logging

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
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

    from app.api.health import router as health_router
    from app.api.session import router as session_router
    from app.api.simli import router as simli_router
    from app.api.ws_interview import router as ws_router

    app = FastAPI(
        title="BehavioralDummy",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["content-type"],
    )

    app.include_router(health_router)
    app.include_router(session_router, prefix="/session")
    app.include_router(simli_router, prefix="/simli")
    app.include_router(ws_router)

    return app


app = create_app()
