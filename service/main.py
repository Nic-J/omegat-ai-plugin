import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from router import router

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)

log = structlog.get_logger()

app = FastAPI(title="OmegaT AI Translation Service")
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    log.error("validation_error", errors=exc.errors(), body=body.decode(errors="replace"))
    return JSONResponse(status_code=422, content={"detail": exc.errors()})
