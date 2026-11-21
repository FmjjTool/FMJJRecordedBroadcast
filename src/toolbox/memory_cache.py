"""有字节预算的进程内 LRU；仅保存可重建缓存，不操作持久化数据。"""

from collections import OrderedDict
from collections.abc import MutableMapping
from threading import RLock


class ByteLRU(MutableMapping):
    def __init__(self, max_bytes, *, size_of=len, max_items=256):
        self.max_bytes = max(0, int(max_bytes))
        self.max_items = max(0, int(max_items))
        self._size_of = size_of
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    @property
    def bytes_used(self):
        with self._lock:
            return self._bytes

    def __getitem__(self, key):
        with self._lock:
            value, _size = self._items[key]
            self._items.move_to_end(key)
            return value

    def __setitem__(self, key, value):
        size = max(0, int(self._size_of(value)))
        with self._lock:
            if key in self._items:
                self._bytes -= self._items.pop(key)[1]
            if size > self.max_bytes or not self.max_items:
                return
            self._items[key] = value, size
            self._bytes += size
            while self._bytes > self.max_bytes or len(self._items) > self.max_items:
                _key, (_value, removed) = self._items.popitem(last=False)
                self._bytes -= removed

    def __delitem__(self, key):
        with self._lock:
            self._bytes -= self._items.pop(key)[1]

    def __iter__(self):
        with self._lock:
            return iter(tuple(self._items))

    def __len__(self):
        with self._lock:
            return len(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0
