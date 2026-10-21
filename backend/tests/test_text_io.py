from app.common.exceptions import BusinessException
from app.services.text_io_service import (
    decode_txt,
    normalize_text,
    prepare_text,
    safe_filename,
)

import pytest


def test_decode_utf8():
    text, encoding = decode_txt("第一章 初见\n正文".encode("utf-8"))
    assert text.startswith("第一章")
    assert encoding == "UTF-8"


def test_decode_utf8_sig():
    raw = "\ufeff第一章 初见\n正文".encode("utf-8-sig")
    text, encoding = decode_txt(raw)
    assert encoding == "UTF-8-SIG"
    assert text.startswith("第一章")
    assert not text.startswith("\ufeff")


def test_decode_gbk():
    text, encoding = decode_txt("第一章 初见\n中文正文".encode("gbk"))
    assert text == "第一章 初见\n中文正文"
    assert encoding == "GB18030"


def test_decode_gb18030():
    text, encoding = decode_txt("第一章 初见\n𠀀 生僻字".encode("gb18030"))
    assert "𠀀" in text
    assert encoding == "GB18030"


def test_normalize_text_rules():
    raw = "\ufeff第一行\r\n\r\n\r\n\r\n第二行\r第三章\u3000开始  "
    assert normalize_text(raw) == "第一行\n\n第二行\n第三章 开始"


def test_prepare_text_rejects_empty():
    with pytest.raises(BusinessException):
        prepare_text(b"")


def test_safe_filename_removes_path():
    assert safe_filename("../../危险小说.txt") == "危险小说.txt"
    assert safe_filename(r"C:\Users\me\小说.TXT") == "小说.txt"
    assert safe_filename("a<b>c?.txt") == "abc.txt"
    assert safe_filename("..") == "未命名.txt"
    assert safe_filename("CON.txt") == "_CON.txt"
