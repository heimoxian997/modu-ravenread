"""FastAPI 应用入口。

只监听本机回环地址，不包含任何外部服务依赖。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.middleware.cors import CORSMiddleware

from app.api import api
from app.common.exceptions import (
    BusinessException,
    bussiness_exception_handler,
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import (  # noqa: F401  (注册 ORM 模型)
    Book,
    Chapter,
    ChapterExtraction,
    ChapterSummary,
    Character,
    CharacterAlias,
    CharacterAliasObservation,
    CharacterCardOverride,
    CharacterChapterDelta,
    CharacterMention,
    Chunk,
    Draft,
    EntityIndexFlag,
    ExtractJob,
    IndexJob,
    LlmProvider,
    LoreFact,
    LoreOverride,
    SummaryIssue,
)
from app.services import (
    alias_check_service,
    draft_service,
    embedding_service,
    entity_override_service,
    llm_provider_service,
    summary_job_service,
    text_index_job_service,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """建表、补列并清理过期上传草稿。"""
    Base.metadata.create_all(bind=engine)
    entity_override_service.ensure_override_schema()
    llm_provider_service.ensure_provider_schema()
    alias_check_service.ensure_schema()
    summary_job_service.ensure_job_schema()
    db = SessionLocal()
    try:
        draft_service.cleanup_expired_drafts(db)
        text_index_job_service.mark_interrupted_jobs(db)
        summary_job_service.mark_interrupted_jobs(db)
    finally:
        db.close()
    # 预热 torch/CUDA 运行时信息，不加载模型，确保状态接口首请求 <1 秒。
    embedding_service.warmup_runtime()
    yield


app = FastAPI(
    title="墨读 · RavenRead",
    version="0.1.0",
    lifespan=lifespan,
)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api)

# 注册顺序有讲究：全局兜底必须放最后。
app.add_exception_handler(BusinessException, bussiness_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)


@app.get("/")
def root():
    return {
        "message": "RavenRead 本地服务运行中",
        "docs": "/docs",
        "privacy": "所有数据仅保存在本机",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )
