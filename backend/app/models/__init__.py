"""集中导入模型，确保 Base.metadata 包含所有表。"""

from app.models.agent_message import AgentMessage
from app.models.agent_session import AgentSession
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.chapter_summary import ChapterSummary
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_card_override import CharacterCardOverride
from app.models.character_alias_observation import CharacterAliasObservation
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.character_mention import CharacterMention
from app.models.chunk import Chunk
from app.models.draft import Draft
from app.models.entity_index_flag import EntityIndexFlag
from app.models.extract_job import ExtractJob
from app.models.index_job import IndexJob
from app.models.llm_provider import LlmProvider
from app.models.lore_fact import LoreFact
from app.models.lore_override import LoreOverride
from app.models.note import Issue, Note
from app.models.reader_progress import ReaderProgress
from app.models.bookmark import Bookmark
from app.models.summary_issue import SummaryIssue

__all__ = [
    "AgentMessage",
    "AgentSession",
    "Book",
    "Chapter",
    "ChapterExtraction",
    "ChapterSummary",
    "Character",
    "CharacterAlias",
    "CharacterAliasObservation",
    "CharacterCardOverride",
    "CharacterChapterDelta",
    "CharacterMention",
    "Chunk",
    "Draft",
    "EntityIndexFlag",
    "ExtractJob",
    "IndexJob",
    "LlmProvider",
    "LoreFact",
    "LoreOverride",
    "Issue",
    "Note",
    "ReaderProgress",
    "SummaryIssue",
]

from app.models.reading_note import ReadingNoteRevision, LegacyNoteImport
from app.models.note_embedding import NoteSearchDocument, NoteEmbedding
