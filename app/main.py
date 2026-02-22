import uuid
from contextlib import asynccontextmanager

import aioboto3
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.exceptions.trade_exceptions import TradeValidationError
from app.kafka import producer as kafka_producer_module
from app.logging_config import configure_logging, get_logger
from app.routers import health, trades
from app.routers import requests as requests_router

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("application_starting", environment=settings.environment)

    session = aioboto3.Session()
    async with session.client(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint_url or None,
    ) as dynamodb_client:
        app.state.dynamodb_client = dynamodb_client

        try:
            await kafka_producer_module.start_producer()
        except Exception as exc:
            logger.warning("kafka_producer_start_failed", error=str(exc))

        yield

        await kafka_producer_module.stop_producer()

    logger.info("application_stopped")


app = FastAPI(
    title="Trade Store API",
    description="REST API for storing and managing financial trades with Kafka back-pressure.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
    )
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


@app.exception_handler(TradeValidationError)
async def trade_validation_exception_handler(
    request: Request, exc: TradeValidationError
):
    return JSONResponse(status_code=400, content={"detail": exc.message})


app.include_router(trades.router)
app.include_router(requests_router.router)
app.include_router(health.router)
