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

# 测试临时目录放在仓库内，避免受系统临时目录权限限制。
_TEST_ROOT = BACKEND_DIR / "tests" / ".pytest-data"
_TEST_ROOT.mkdir(parents=True, exist_ok=True)
_TEST_DATA_DIR = _TEST_ROOT / uuid.uuid4().hex
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ.pop("DATABASE_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


SAMPLE_NOVEL = (
    Path(__file__).parent / "fixtures" / "sample_novel.txt"
).read_text(encoding="utf-8")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_dir():
    yield
    engine.dispose()
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试使用空数据库和空运行时目录。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    for folder in (_TEST_DATA_DIR / "drafts", _TEST_DATA_DIR / "uploads"):
        if folder.exists():
            for child in folder.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
    yield


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
