import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app import metrics
from app.api.v1.links import router as links_router
from app.api.v1.redirect import router as redirect_router
from app.exceptions import AliasConflict, LinkExpired, LinkNotFound, Unauthorized
from app.logging_config import configure_logging
from app.middleware.metrics import MetricsMiddleware
from app.middleware.request_id import RequestIDMiddleware

configure_logging()
logger = structlog.get_logger(__name__)

app = FastAPI(title="短链接服务")
app.add_middleware(RequestIDMiddleware)
app.add_middleware(MetricsMiddleware)


@app.exception_handler(LinkNotFound)
async def link_not_found_handler(request: Request, exc: LinkNotFound):
    return JSONResponse(status_code=404, content={"detail": "短链接不存在"})


@app.exception_handler(LinkExpired)
async def link_expired_handler(request: Request, exc: LinkExpired):
    return JSONResponse(status_code=410, content={"detail": "短链接已过期"})


@app.exception_handler(AliasConflict)
async def alias_conflict_handler(request: Request, exc: AliasConflict):
    return JSONResponse(status_code=409, content={"detail": "短码已被占用"})


@app.exception_handler(Unauthorized)
async def unauthorized_handler(request: Request, exc: Unauthorized):
    return JSONResponse(status_code=401, content={"detail": "API Key 无效"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = structlog.contextvars.get_contextvars().get("request_id", "")
    logger.exception("unhandled_exception")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "request_id": request_id},
    )


# 注意：静态路由必须注册在 /{code} 之前，否则会被跳转路由吃掉。
@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    from app.db import engine
    from app.services.cache import l1_cache

    try:
        metrics.set_gauge("db_pool_size", engine.pool.size())
        metrics.set_gauge("db_pool_checked_out", engine.pool.checkedout())
    except Exception:  # noqa: BLE001 某些池实现没有这些方法
        pass
    metrics.set_gauge("l1_cache_size", l1_cache.size)
    metrics.set_gauge("l1_cache_hits_total", l1_cache.hits)
    metrics.set_gauge("l1_cache_misses_total", l1_cache.misses)
    return Response(
        content=metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


app.include_router(links_router, prefix="/api/v1")
app.include_router(redirect_router)
