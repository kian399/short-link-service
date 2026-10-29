import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app import metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录每个请求的计数与耗时；/metrics 自身不计入，避免抓取污染数据。"""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", None) or request.url.path
            if path != "/metrics":
                elapsed = time.perf_counter() - start
                labels = {"method": request.method, "path": path, "status": str(status)}
                metrics.inc("http_requests_total", labels)
                metrics.observe(
                    "http_request_duration_seconds",
                    elapsed,
                    {"method": request.method, "path": path},
                )
