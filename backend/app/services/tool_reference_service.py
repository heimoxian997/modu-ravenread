"""Bounded process-local handles for search passages; signed legacy refs still work."""
from collections import OrderedDict
import secrets
import threading
import time

MAX_REFERENCES = 2048
TTL_SECONDS = 24 * 60 * 60
_references = OrderedDict()
_lock = threading.Lock()


def create(payload):
    key = "p_" + secrets.token_urlsafe(12)
    with _lock:
        _references[key] = (time.monotonic() + TTL_SECONDS, payload)
        while len(_references) > MAX_REFERENCES:
            _references.popitem(last=False)
    return key


def resolve(key):
    with _lock:
        value = _references.get(key)
        if value is None or value[0] < time.monotonic():
            _references.pop(key, None)
            raise ValueError("片段引用已失效（服务重启、过期或缓存淘汰），请重新搜索")
        _references.move_to_end(key)
        return dict(value[1])
