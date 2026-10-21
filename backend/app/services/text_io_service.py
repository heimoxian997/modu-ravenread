"""文本文件的安全命名、编码探测、解码与规范化。"""

import re
import unicodedata
from pathlib import Path

from charset_normalizer import from_bytes

from app.common.exceptions import BusinessException

# UTF-8 / UTF-8-SIG 优先，失败后回退 GB18030；最后再用 charset-normalizer 兜底。
_ENCODING_LABELS = {
    "utf_8": "UTF-8",
    "utf-8": "UTF-8",
    "utf_8_sig": "UTF-8-SIG",
    "utf-8-sig": "UTF-8-SIG",
    "gb18030": "GB18030",
    "gbk": "GB18030",
    "gb2312": "GB18030",
}
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(filename: str | None) -> str:
    """去掉路径与非法字符，阻止路径穿越，并保留 .txt 后缀。"""
    if not filename:
        raise BusinessException(message="文件名不能为空", code=4000)

    # 兼容浏览器传来的 Windows / POSIX 路径，只取最后一段。
    name = unicodedata.normalize("NFC", filename).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        char for char in name if char >= " " and char not in '<>:"/\\|?*'
    ).strip(" .")
    if not name or name in {".", ".."}:
        name = "未命名.txt"

    path = Path(name)
    suffix = path.suffix.lower()
    stem = path.stem.strip(" .") or "未命名"
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    # 控制文件名长度，避免超过常见文件系统限制。
    stem = stem[:120]
    if suffix != ".txt":
        suffix = ".txt"
    return f"{stem}{suffix}"


def _normalize_encoding_label(label: str) -> str:
    return _ENCODING_LABELS.get(label.lower().replace(" ", "_"), label.upper())


def decode_txt(raw: bytes) -> tuple[str, str]:
    """返回 (文本, 编码)。

    优先 UTF-8-SIG / UTF-8，其次 GB18030；仍失败时使用 charset-normalizer
    探测一次，以兼容 BIG5 等少见编码。所有解码均为严格模式，避免静默乱码。
    """
    if not raw:
        raise BusinessException(message="文件内容为空，请选择有效的 txt 小说", code=4001)

    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            # 某些文件可能重复写入 BOM，这里一并清掉。
            return raw.decode("utf-8-sig").lstrip("\ufeff"), "UTF-8-SIG"
        except UnicodeDecodeError:
            pass

    try:
        return raw.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        pass

    try:
        return raw.decode("gb18030"), "GB18030"
    except UnicodeDecodeError:
        pass

    matches = from_bytes(raw)
    best = matches.best()
    if best is not None:
        try:
            text = str(best)
            return text, _normalize_encoding_label(best.encoding)
        except (UnicodeError, TypeError):
            pass

    raise BusinessException(
        message="无法识别文件编码，请将文件另存为 UTF-8 或 GBK 后重试",
        code=4001,
    )


def normalize_text(text: str) -> str:
    """去 BOM、统一换行、全角空格转半角、压缩连续空行、去首尾空白。"""
    text = text.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ")
    # 3 个以上换行（2 个以上空行）压缩为 2 个换行（1 个空行）。
    text = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", text)
    text = text.strip()
    if not text:
        raise BusinessException(message="文件正文为空，请选择有效的小说文件", code=4001)
    return text


def prepare_text(raw: bytes) -> tuple[str, str]:
    """解码并规范化上传内容。"""
    text, encoding = decode_txt(raw)
    text = normalize_text(text)
    # UTF-16 等被误判时可能出现大量 NUL；直接给出可操作提示。
    if text.count("\x00") > max(10, len(text) // 100):
        raise BusinessException(
            message="文件疑似不是纯文本，请另存为 UTF-8 或 GBK 编码的 txt",
            code=4001,
        )
    return text, encoding


def count_text_chars(text: str) -> int:
    """字符数按 Unicode 码点统计，和 Python 切片偏移保持一致。"""
    return len(text)
