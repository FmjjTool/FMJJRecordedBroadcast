"""原子配置写入与可恢复的实时 JSONL；不把 flush 当成断电保证。"""
import json
import os
import tempfile
import threading
import time
from collections import deque
from pathlib import Path


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
