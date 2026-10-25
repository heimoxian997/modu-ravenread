"""Log route templates and durations, never questions, source text or request bodies."""
import logging
import re
import time
import uuid


def install(app):
    logger = logging.getLogger("uvicorn.error")

    @app.middleware("http")
    async def trace(request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if re.fullmatch(r"[a-zA-Z0-9-]{8,64}", supplied) else uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            logger.info("request id=%s method=%s route=%s elapsed_ms=%d", request_id,
                request.method, route, int((time.monotonic()-started)*1000))
