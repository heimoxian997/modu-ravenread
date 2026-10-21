from types import SimpleNamespace

import pytest

from app.common.exceptions import BusinessException
from app.services.boundary_service import validate_boundaries


def _chapter(start, end, title="", kind="main"):
    return SimpleNamespace(start=start, end=end, title=title, kind=kind)


def test_valid_contiguous_boundaries_and_auto_title():
    chapters = validate_boundaries(
        [_chapter(5, 20), _chapter(20, 50, "后半", "extra")],
        text_length=50,
    )
    assert chapters[0]["title"] == "第1章"
    assert chapters[0]["word_count"] == 15
    assert chapters[1]["title"] == "后半"
    assert chapters[1]["kind"] == "extra"


def test_overlap_rejected():
    with pytest.raises(BusinessException) as exc_info:
        validate_boundaries([_chapter(0, 20), _chapter(15, 30)], 30)
    errors = exc_info.value.data["errors"]
    assert any("重叠" in item["message"] for item in errors)


def test_gap_rejected():
    with pytest.raises(BusinessException) as exc_info:
        validate_boundaries([_chapter(0, 10), _chapter(12, 30)], 30)
    errors = exc_info.value.data["errors"]
    assert any("缺口" in item["message"] for item in errors)


def test_out_of_bounds_rejected():
    with pytest.raises(BusinessException):
        validate_boundaries([_chapter(0, 31)], 30)
    with pytest.raises(BusinessException):
        validate_boundaries([_chapter(-1, 30)], 30)


def test_start_must_be_before_end():
    with pytest.raises(BusinessException):
        validate_boundaries([_chapter(10, 10)], 30)


def test_tail_must_be_covered():
    with pytest.raises(BusinessException) as exc_info:
        validate_boundaries([_chapter(0, 20)], 30)
    assert any("末尾" in item["message"] for item in exc_info.value.data["errors"])


def test_strictly_increasing_starts():
    with pytest.raises(BusinessException):
        validate_boundaries([_chapter(0, 15), _chapter(0, 15)], 15)


def test_empty_list_rejected():
    with pytest.raises(BusinessException):
        validate_boundaries([], 10)
