"""ASGI construction boundary: FastAPI factory, lifespan, and process launchers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI

from investigation_agent.api.problems import install_problem_handlers
from investigation_agent.api.routers.health import router as health_router
from investigation_agent.api.routers.investigations import router as investigations_router
from investigation_agent.api.routers.threads import router as threads_router
from investigation_agent.bootstrap.runtime import (
    Runtime,
    RuntimeFactories,
    build_runtime,
    observability_config,
)
from investigation_agent.config.secrets import InitializerSecrets, ServingSecrets
from investigation_agent.config.settings import Settings

type RuntimeBuilder = Callable[[], Awaitable[Runtime]]

_FULL_DETAIL_ENVIRONMENTS = frozenset({"local", "development", "dev", "staging", "test"})


@dataclass(frozen=True, slots=True)
class LifespanHooks:
    """Telemetry start/stop hooks; the default wires the shared observability library."""

    start: Callable[[], Any]
    stop: Callable[[], None]


def _default_hooks(settings: Settings) -> LifespanHooks:
    def start() -> Any:
        from observability import LoggingConfig, configure_logging, configure_observability
        from observability.genai_metrics import genai_metric_views

        providers = configure_observability(
            observability_config(settings), metric_views=genai_metric_views()
        )
        configure_logging(
            LoggingConfig(
                service_name=settings.otel_service_name,
                level=settings.log_level,
                exception_detail=_exception_detail(settings.environment_name),
                delivery=settings.log_export,
            ),
            logger_provider=providers.logger_provider,
        )
        logging.getLogger("langchain_aws").setLevel(logging.CRITICAL)
        return providers

    def stop() -> None:
        from observability import shutdown_observability

        shutdown_observability()

    return LifespanHooks(start=start, stop=stop)


def _exception_detail(environment: str) -> Literal["full", "safe"]:
    """Keep diagnostics rich outside production and content-safe in production."""
    return "full" if environment.lower() in _FULL_DETAIL_ENVIRONMENTS else "safe"


def create_app(
    settings: Settings,
    *,
    runtime_builder: RuntimeBuilder,
    hooks: LifespanHooks | None = None,
) -> FastAPI:
    """Build the ASGI application; the runtime is created once inside the lifespan."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_hooks = hooks or _default_hooks(settings)
        active_hooks.start()
        runtime: Runtime | None = None
        try:
            runtime = await runtime_builder()
            app.state.runtime = runtime
            yield
        finally:
            app.state.runtime = None
            if runtime is not None:
                await _drain(runtime)
                await runtime.close()
            active_hooks.stop()

    app = FastAPI(title="investigation-agent", lifespan=lifespan, docs_url=None, redoc_url=None)
    install_problem_handlers(app)
    app.include_router(health_router)
    app.include_router(investigations_router)
    app.include_router(threads_router)
    return app


async def _drain(runtime: Runtime) -> None:
    """Cancel active turns cooperatively and wait, bounded, for their leases to release."""

    await runtime.invoke_turn.cancel_active()
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(runtime.shutdown_timeout_s):
            while runtime.invoke_turn.active_count:
                await asyncio.sleep(0.05)


def run_serving(settings: Settings, secrets: ServingSecrets) -> int:
    """Start uvicorn with an application whose runtime is composed inside the lifespan."""

    import uvicorn

    async def build() -> Runtime:
        from observability import current_providers

        return await build_runtime(
            settings, secrets, factories=RuntimeFactories(telemetry=current_providers())
        )

    app = create_app(settings, runtime_builder=build)
    uvicorn.run(
        app,
        host=settings.investigation_agent_host,
        port=settings.investigation_agent_port,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=int(settings.shutdown_timeout_s),
    )
    return 0


def run_initializer(settings: Settings, secrets: InitializerSecrets) -> int:
    """Run the owner-only one-shot initializer after all configuration is validated."""

    from investigation_agent.adapters.postgres.initializer import initialize_database

    asyncio.run(
        initialize_database(
            owner_dsn=secrets.owner_database_url.get_secret_value(),
            reader_password=secrets.reader_password.get_secret_value(),
            writer_password=secrets.writer_password.get_secret_value(),
            expected_version=settings.expected_agent_initializer_version,
        )
    )
    return 0


__all__ = ["LifespanHooks", "create_app", "run_initializer", "run_serving"]
