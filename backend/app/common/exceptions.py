"""业务异常与全局异常处理器。

约定：业务错误返回 HTTP 200 + code != 200，前端按 code 判断；
请求参数校验错误仍返回 HTTP 422。
"""

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.response import Response


class BusinessException(Exception):
    def __init__(self, message: str, code: int = 500, data: Any = None):
        self.message = message
        self.code = code
        self.data = data
        super().__init__(message)


async def bussiness_exception_handler(request: Request, exc: BusinessException):
    return JSONResponse(
        status_code=200,
        content=Response.error(
            code=exc.code, message=exc.message, data=exc.data
        ).model_dump(),
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=Response.error(code=exc.status_code, message=str(exc.detail)).model_dump(),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验异常处理器，返回字段级错误信息。"""
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(item) for item in error.get("loc", [])),
                "message": error.get("msg", "参数不合法"),
                "type": error.get("type", "value_error"),
            }
        )
    return JSONResponse(
        status_code=422,
        content=Response.error(
            code=422, message="请求参数校验错误", data={"errors": errors}
        ).model_dump(),
    )


async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=Response.error(code=500, message="服务器内部错误").model_dump(),
    )
