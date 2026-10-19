"""Точка входа: один процесс отдаёт API, статику Mini App и держит бота."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api
from .bot import BotRunner
from .config import Config, load_config
from .db import Database
from .errors import ApiError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

log = logging.getLogger(__name__)


def create_app(config: Config | None = None, database: Database | None = None) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = config or load_config()
    database = database or Database(config.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        applied = database.migrate()
        log.info(
            "База %s готова (применено миграций: %d)", database.path, len(applied)
        )

        runner: BotRunner | None = None
        if config.bot_enabled:
            runner = BotRunner(config, database)
            await runner.start()
            log.info("Бот запущен (long polling)")
        else:
            log.warning("BOT_ENABLED=false — бот не запущен, команды доступа не работают")

        if config.webapp_url:
            log.info("Mini App ожидается по адресу %s", config.webapp_url)

        try:
            yield
        finally:
            if runner is not None:
                await runner.stop()

    app = FastAPI(
        title="Инвентаризация ноутбуков",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.db = database

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        field = ""
        message = "Проверь заполненные поля"
        if errors:
            location = [str(part) for part in errors[0].get("loc", []) if part != "body"]
            field = ".".join(location)
            message = errors[0].get("msg", message)
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": f"{field}: {message}" if field else message,
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        with database.session() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    app.include_router(api.router)

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index() -> FileResponse:
        """Точка входа отдаётся с обязательной перепроверкой.

        У css и js в ссылках есть ?v=N, а у самой страницы версии нет. Без
        Cache-Control WebView Telegram держит её в кеше по эвристике и после
        обновления показывает старую разметку с новым скриптом. no-cache не
        запрещает кеширование: браузер всё равно спросит сервер и чаще всего
        получит дешёвый 304 по ETag.
        """
        return FileResponse(
            WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    # Статика монтируется последней: все конкретные маршруты выше имеют приоритет.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app

