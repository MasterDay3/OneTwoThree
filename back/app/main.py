import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.routers import health, me, meetings, participants
from app.services.errors import ConflictError, NotFoundError

if not settings.auth_enabled:
    logging.getLogger("uvicorn.error").warning(
        "COGNITO_USER_POOL_ID is not set: auth is disabled and every request acts as the local user"
    )

app = FastAPI(
    title="Meetings API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NotFoundError)
def handle_not_found(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
def handle_conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# The API's bare domain has nothing of its own; send visitors to the docs.
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/api/docs")


app.include_router(health.router, prefix="/api")
app.include_router(me.router, prefix="/api")
app.include_router(meetings.router, prefix="/api")
app.include_router(participants.router, prefix="/api")
