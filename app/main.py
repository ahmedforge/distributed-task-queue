import asyncio
from contextlib import asynccontextmanager, suppress
from app.dispatch import dispatch_loop
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from app.observability import HTTP_REGISTRY, metrics_response
from app.core.config import settings
from app.api.v1.jobs import router as jobs_router
from app.api.v1.accounts import router as accounts_router
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import request_validation_exception_handler


@asynccontextmanager
async def lifespan(app):
    dispatcher = asyncio.create_task(dispatch_loop())
    try:
        yield
    finally:
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.include_router(accounts_router, prefix="/api/v1/account", tags=["Account"])


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith("/api/v1/account/"):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Use a username of 3–32 letters, digits or underscores starting with a letter, and a password of 12–128 characters."
            },
        )
    return await request_validation_exception_handler(request, exc)


app.include_router(jobs_router, prefix="/api/v1/jobs", tags=["Jobs"])


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/v1/jobs", "/api/v1/account")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return await metrics_response()


Instrumentator(
    registry=HTTP_REGISTRY,
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    excluded_handlers=["/metrics"],
).instrument(app)
