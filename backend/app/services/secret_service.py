"""本地密钥加密服务。

使用 data/secret.key 中的 Fernet 密钥加密 API Key。密钥只在首次使用时生成；
换机器或删除密钥文件后，旧密文无法解密，需要用户重新填写 API Key。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app import config
from app.common.exceptions import BusinessException

_lock = threading.Lock()
_fernet: Fernet | None = None


def get_secret_path() -> Path:
    return Path(config.DATA_DIR) / "secret.key"


def reset_cache() -> None:
    """清理内存中的 Fernet 实例，供测试或密钥文件替换后使用。"""
    global _fernet
    with _lock:
        _fernet = None


def _load_or_create_fernet() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is not None:
            return _fernet

        path = get_secret_path()
        if path.exists():
            try:
                _fernet = Fernet(path.read_bytes().strip())
            except Exception as exc:
                raise BusinessException(
                    message="密钥文件损坏，请删除 data/secret.key 后重新填写 API Key。",
                    code=4000,
                ) from exc
            return _fernet

        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        _fernet = Fernet(key)
        return _fernet


def encrypt_api_key(text: str | None) -> str | None:
    if not text:
        return None
    token = _load_or_create_fernet().encrypt(text.encode("utf-8"))
    return token.decode("ascii")


def decrypt_api_key(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _load_or_create_fernet().decrypt(token.encode("ascii")).decode(
            "utf-8"
        )
    except BusinessException:
        raise
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise BusinessException(
            message="密钥无法解密，请重新填写 API Key。",
            code=4000,
        ) from exc
