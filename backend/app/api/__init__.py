"""API 路由汇总。"""

from fastapi import APIRouter

from app.api.agent import router as agent_router
from app.api.books import router as books_router
from app.api.chunks import router as chunks_router
from app.api.drafts import router as drafts_router
from app.api.entities import router as entities_router
from app.api.notes import router as notes_router
from app.api.progress import router as progress_router
from app.api.settings import router as settings_router
from app.api.summaries import router as summaries_router
from app.api.text_index import router as text_index_router

api = APIRouter(prefix="/api")

api.include_router(drafts_router)
api.include_router(books_router)
api.include_router(agent_router)
api.include_router(progress_router)
api.include_router(chunks_router)
api.include_router(settings_router)
api.include_router(text_index_router)
api.include_router(summaries_router)
api.include_router(entities_router)
api.include_router(notes_router)

from app.api.reading_notes import router as reading_notes_router
api.include_router(reading_notes_router)

from app.api.corrections import router as corrections_router
api.include_router(corrections_router)
