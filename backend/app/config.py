"""应用配置。

配置来源优先级：环境变量 > backend/.env > 默认值。
整个应用只允许绑定本机地址，数据目录默认位于仓库根目录的 data/。
"""

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 后端目录（backend/）与仓库根目录
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    """单用户本地应用配置。

    不包含任何密钥、鉴权或大模型配置。
    """

    DATA_DIR: Path = PROJECT_ROOT / "data"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DATABASE_URL: str | None = None
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB

    # 本地嵌入模型。权重默认放在 <DATA_DIR>/models/bge-m3，不进仓库。
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_MODEL_DIR: str = ""
    EMBEDDING_DEVICE: str = "auto"
    EMBEDDING_BATCH_SIZE: int = Field(default=8, ge=1)
    EMBEDDING_MAX_LENGTH: int = Field(default=1024, ge=1)
    EMBEDDING_USE_FP16: bool = True
    HF_ENDPOINT: str = ""

    # 本地 Qdrant 文本向量层
    QDRANT_PATH: str = ""
    TEXT_INDEX_COLLECTION: str = "novel_text"
    TEXT_INDEX_VERSION: int = Field(default=1, ge=1)
    SUMMARY_INDEX_COLLECTION: str = "novel_summary"
    SUMMARY_INDEX_VERSION: int = Field(default=1, ge=1)
    CHARACTER_INDEX_COLLECTION: str = "novel_character"
    LORE_INDEX_COLLECTION: str = "novel_lore"
    CHARACTER_INDEX_VERSION: int = Field(default=2, ge=1)
    LORE_INDEX_VERSION: int = Field(default=1, ge=1)

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("HOST")
    @classmethod
    def _loopback_only(cls, value: str):
        if value not in {"127.0.0.1", "localhost"}:
            raise ValueError("HOST 只允许 127.0.0.1 或 localhost，本应用不提供局域网访问")
        return value

    @field_validator("EMBEDDING_DEVICE")
    @classmethod
    def _embedding_device(cls, value: str):
        value = value.strip().lower()
        if value not in {"auto", "cuda", "cpu"}:
            raise ValueError("EMBEDDING_DEVICE 只允许 auto / cuda / cpu")
        return value

    @model_validator(mode="after")
    def _fill_database_url(self):
        """把相对 DATA_DIR 固定到 backend/ 下解析，再推导 SQLite 地址。"""
        data_dir = Path(self.DATA_DIR)
        if not data_dir.is_absolute():
            data_dir = (BASE_DIR / data_dir).resolve()
        else:
            data_dir = data_dir.resolve()
        self.DATA_DIR = data_dir

        if not self.DATABASE_URL:
            db_path = (data_dir / "app.db").resolve()
            # SQLite URL 在 Windows 上使用正斜杠，避免反斜杠被当作转义。
            self.DATABASE_URL = f"sqlite:///{db_path.as_posix()}"
        return self


settings = Settings()

# 运行时目录。启动时确保存在，但不把任何运行时内容提交到仓库。
DATA_DIR = Path(settings.DATA_DIR).resolve()
DRAFTS_DIR = DATA_DIR / "drafts"
UPLOADS_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = settings.MAX_FILE_SIZE
ALLOWED_EXTENSIONS = {".txt"}
DRAFT_TTL_HOURS = 24

# 正文切块参数（字符数按 Python 字符串码点计算）
PARENT_CHARS = 1500
CHILD_CHARS = 500
CHILD_OVERLAP = 100
CHILD_MAX_CHARS = 600

# 嵌入模型目录：相对路径以 data/ 为基准；留空使用默认目录。
_embedding_dir_value = settings.EMBEDDING_MODEL_DIR.strip()
if _embedding_dir_value:
    _embedding_dir = Path(_embedding_dir_value)
    if not _embedding_dir.is_absolute():
        _embedding_dir = DATA_DIR / _embedding_dir
    EMBEDDING_MODEL_DIR = _embedding_dir.resolve()
else:
    EMBEDDING_MODEL_DIR = (DATA_DIR / "models" / "bge-m3").resolve()

# 重排模型目录，默认 data/models/bge-reranker-v2-m3；缺失时检索自动降级回 RRF。
RERANK_MODEL_DIR = (DATA_DIR / "models" / "bge-reranker-v2-m3").resolve()

# 本地 Qdrant 目录；相对路径以 data/ 为基准。
_qdrant_path_value = settings.QDRANT_PATH.strip()
if _qdrant_path_value:
    _qdrant_path = Path(_qdrant_path_value)
    if not _qdrant_path.is_absolute():
        _qdrant_path = DATA_DIR / _qdrant_path
    QDRANT_PATH = _qdrant_path.resolve()
else:
    QDRANT_PATH = (DATA_DIR / "qdrant").resolve()
TEXT_INDEX_COLLECTION = settings.TEXT_INDEX_COLLECTION
TEXT_INDEX_VERSION = settings.TEXT_INDEX_VERSION
SUMMARY_INDEX_COLLECTION = settings.SUMMARY_INDEX_COLLECTION
SUMMARY_INDEX_VERSION = settings.SUMMARY_INDEX_VERSION
CHARACTER_INDEX_COLLECTION = settings.CHARACTER_INDEX_COLLECTION
LORE_INDEX_COLLECTION = settings.LORE_INDEX_COLLECTION
CHARACTER_INDEX_VERSION = settings.CHARACTER_INDEX_VERSION
LORE_INDEX_VERSION = settings.LORE_INDEX_VERSION
