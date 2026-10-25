"""Signed opaque references: clients never calculate offsets or pagination indexes."""
import base64
import hashlib
import hmac
import json
import os
import threading
from pathlib import Path
from app import config


_key_lock = threading.Lock()


def _key():
    with _key_lock:
        return _load_key()


def _load_key():
    path = Path(config.DATA_DIR) / "tool-token.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as f:
        key = os.urandom(32)
        f.write(key)
    return key


def encode(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_key(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + raw).decode().rstrip("=")


def decode(token: str) -> dict:
    try:
        if len(token) > 12000:
            raise ValueError()
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if not hmac.compare_digest(raw[:32], hmac.new(_key(), raw[32:], hashlib.sha256).digest()):
            raise ValueError()
        result = json.loads(raw[32:])
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except Exception as exc:
        raise ValueError("引用或 cursor 无效，请重新查询") from exc
