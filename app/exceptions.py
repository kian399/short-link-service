class LinkNotFound(Exception):
    pass


class LinkExpired(Exception):
    pass


class AliasConflict(Exception):
    pass


class Unauthorized(Exception):
    pass


class RateLimited(Exception):
    """超过限流阈值。携带 Retry-After 秒数，供 429 响应头使用。"""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry after {retry_after}s")
