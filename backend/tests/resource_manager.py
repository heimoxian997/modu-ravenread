"""Bounded test slots. A crashed process releases its OS lock; the next run reclaims it."""
from pathlib import Path
import os
import shutil


class TestResources:
    __test__ = False
    def __init__(self, root: Path, slots: int = 4):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = None
        for i in range(slots):
            fd = os.open(self.root / f"slot-{i}.lock", os.O_CREAT | os.O_RDWR, 0o600)
            lock = os.fdopen(fd, "r+b")
            if os.fstat(fd).st_size == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock.close()
                continue
            self.lock = lock
            self.path = self.root / f"slot-{i}"
            try:
                self.clean()
                self.path.mkdir()
            except Exception:
                lock.close()
                raise
            return
        raise RuntimeError("4 个测试槽位均被占用；请等待运行中的测试结束。")

    def clean(self):
        target = self.path.resolve()
        if target.parent != self.root or self.path.is_symlink() or getattr(self.path, "is_junction", lambda: False)():
            raise RuntimeError(f"拒绝清理异常测试路径：{self.path}")
        if target.exists():
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise RuntimeError(f"测试目录清理失败，停止创建新目录：{target}: {exc}") from exc

    def close(self):
        try:
            self.clean()
        finally:
            self.lock.close()
