from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from app.observability import HTTP_REGISTRY, metrics_response
from app.core.config import settings
from app.api.v1.jobs import router as jobs_router

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(jobs_router, prefix="/api/v1/jobs", tags=["Jobs"])


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
