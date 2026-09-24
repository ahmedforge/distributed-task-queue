from fastapi import FastAPI
from app.core.config import settings
from app.api.v1.jobs import router as jobs_router

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(jobs_router, prefix="/api/v1/jobs", tags=["Jobs"])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}