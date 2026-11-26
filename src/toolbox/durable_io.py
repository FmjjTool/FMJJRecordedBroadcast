"""原子配置写入与可恢复的实时 JSONL；不把 flush 当成断电保证。"""
import json
import os
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

_file_locks_guard = threading.Lock()
_file_locks = {}


@contextmanager
def exclusive_file_lock(path, timeout=10):
    """协作写入方共用的线程/进程锁；锁文件保留，OS 锁随退出释放。

    仅用于受控的非凭据文件更新。调用方指定锁文件，不删除它来推测锁状态，
    避免另一进程仍持有旧文件时产生两把锁。
    """
    path = Path(path).absolute()
    key = os.path.normcase(str(path))
    with _file_locks_guard:
        local = _file_locks.setdefault(key, threading.Lock())
    if not local.acquire(timeout=max(0, timeout)):
        raise TimeoutError("写入操作正在进行，请稍后重试")
    stream, locked = None, False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(path):
            raise OSError("写入锁路径不安全")
        stream = open(path, "a+b")
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + max(0, timeout)
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("写入操作正在进行，请稍后重试") from exc
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        if stream is not None:
            if locked:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                finally:
                    try:
                        stream.close()
                    finally:
                        local.release()
            else:
                try:
                    stream.close()
                finally:
                    local.release()
        else:
            local.release()


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class JsonlJournal:
    """逐条 flush，最多每秒 fsync，关闭时同步；重开截掉末尾半条记录。"""
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_sync = 0
        self._stream = open(path, "a+b")
        self._stream.seek(0, os.SEEK_END)
        end = self._stream.tell()
        if end:
            self._stream.seek(end - 1)
            if self._stream.read(1) != b"\n":
                position = end
                while position:
                    start = max(0, position - 8192)
                    self._stream.seek(start)
                    block = self._stream.read(position - start)
                    split = block.rfind(b"\n")
                    if split >= 0:
                        self._stream.truncate(start + split + 1)
                        break
                    position = start
                else:
                    self._stream.truncate(0)
        self._stream.seek(0, os.SEEK_END)

    def write(self, record):
        data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self._stream.write(data)
            self._stream.flush()
            if time.monotonic() - self._last_sync >= 1:
                os.fsync(self._stream.fileno())
                self._last_sync = time.monotonic()

    def close(self):
        with self._lock:
            if not self._stream.closed:
                self._stream.flush()
                os.fsync(self._stream.fileno())
                self._stream.close()


def read_jsonl_tail(path, limit=4000):
    """只读末尾最多 8 MB，忽略末尾损坏记录，避免大场次恢复占满内存。"""
    with open(path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        start = max(0, stream.tell() - 8 * 1024 * 1024)
        stream.seek(start)
        if start:
            stream.readline()
        records = deque(maxlen=limit)
        for line in stream:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(item)
            except (ValueError, UnicodeError):
                continue
    return list(records)
