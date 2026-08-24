from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    service: str
    status: str


app = FastAPI(
    title="Synora Agent Runtime",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(service="synora-agent-runtime", status="ok")
