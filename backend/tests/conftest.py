"""pytest 全局配置：导入应用前指定临时 DATA_DIR，避免污染真实书库。"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 所有文件资源位于有限槽位；数据库默认在内存，跨线程使用独立连接。
import sqlite3
from resource_manager import TestResources
_resources = TestResources(BACKEND_DIR / "tests" / ".pytest-data" / "runs")
_TEST_DATA_DIR = _resources.path / "data"
_TEST_DATA_DIR.mkdir()
_memory_name = "rrtest_" + uuid.uuid4().hex
_memory_anchor = sqlite3.connect(f"file:{_memory_name}?mode=memory&cache=shared", uri=True)
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///file:{_memory_name}?mode=memory&cache=shared&uri=true"


def pytest_configure(config):
    config.option.basetemp = str(_resources.path / "pytest")
    config.addinivalue_line("markers", "disk_db: test actual on-disk persistence")


from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


SAMPLE_NOVEL = (
    Path(__file__).parent / "fixtures" / "sample_novel.txt"
).read_text(encoding="utf-8")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_dir():
    yield
    from app.services import text_index_service, text_window_service, secret_service
    text_index_service.reset_client()
    text_window_service.clear_cache()
    secret_service.reset_cache()
    engine.dispose()
    _memory_anchor.close()
    _resources.close()


@pytest.fixture(autouse=True)
def _reset_state(request, monkeypatch):
    """Fresh tables per test; disk fixtures retain genuine reopen semantics."""
    from sqlalchemy import create_engine
    from app import database
    from app.services import embedding_service, text_window_service
    active_engine = engine
    if request.node.get_closest_marker("disk_db"):
        active_engine = create_engine(
            "sqlite:///" + (_TEST_DATA_DIR / "persistence.db").as_posix(),
            connect_args={"check_same_thread": False},
        )
        from sqlalchemy import event
        event.listen(active_engine, "connect", database._set_sqlite_pragma)
        monkeypatch.setattr(database, "engine", active_engine)
        if hasattr(request.module, "engine"):
            monkeypatch.setattr(request.module, "engine", active_engine)
        database.SessionLocal.configure(bind=active_engine)
    # API startup must not import GPU runtimes during ordinary fake-model tests.
    monkeypatch.setattr(embedding_service, "warmup_runtime", lambda: None)
    from app.services import note_embedding_service
    # Ordinary tests do not run a real GPU indexer. Worker tests opt in explicitly.
    monkeypatch.setattr(note_embedding_service, "start", lambda: None)
    monkeypatch.setattr(note_embedding_service, "stop", lambda: None)
    monkeypatch.setattr(note_embedding_service, "available", lambda: False)
    text_window_service.clear_cache()
    Base.metadata.drop_all(bind=active_engine)
    Base.metadata.create_all(bind=active_engine)
    for folder in (_TEST_DATA_DIR / "drafts", _TEST_DATA_DIR / "uploads"):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
    try:
        yield
    finally:
        import threading
        for thread in threading.enumerate():
            if thread.name.startswith(("summary-job-", "text-index-job-")):
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError(f"测试后台任务未退出，保留资源以便排查：{thread.name}")
        if active_engine is not engine:
            active_engine.dispose()
            database.SessionLocal.configure(bind=engine)
            (_TEST_DATA_DIR / "persistence.db").unlink(missing_ok=True)


@pytest.fixture()
def mcp_server():
    """Only SDK integration tests require the optional MCP dependency.

    Skip on the missing SDK itself, not on arbitrary imports inside app.mcp_server,
    so application import bugs still fail when the SDK is installed.
    """
    pytest.importorskip("mcp", reason="可选 MCP 测试需安装 requirements-mcp.txt", exc_type=ModuleNotFoundError)
    from app import mcp_server as server
    return server


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def sample_novel():
    return SAMPLE_NOVEL


@pytest.fixture()
def upload_novel(client):
    def _upload(text: str, filename: str = "测试小说.txt", encoding: str = "utf-8"):
        response = client.post(
            "/api/drafts",
            files={"file": (filename, text.encode(encoding), "text/plain")},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["code"] == 200, payload
        return payload["data"]

    return _upload
