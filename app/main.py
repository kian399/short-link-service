from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.api.v1.links import router as links_router
from app.api.v1.redirect import router as redirect_router
from app.exceptions import LinkNotFound, LinkExpired, AliasConflict, Unauthorized

app = FastAPI(title="短链接服务")


# 注册异常处理器
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
    return JSONResponse(status_code=401, content={"detail": "API Key无效"})


# 注册路由
app.include_router(links_router, prefix="/api/v1")
app.include_router(redirect_router)


@app.get("/healthz")
def health():
    return {"status": "ok"}
