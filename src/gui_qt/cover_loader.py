"""封面图加载器（共享实例）。

旧实现是「每行一个原生线程 + 一次 requests + 一次全尺寸解码」：
一次歌单/歌手结果动辄上百行，就会同时起上百个线程、上百个 HTTP 请求，
主线程被 GIL/线程创建/布局刷新拖住，表现为界面卡顿、CPU 与内存占用很高。

这里统一成：
- 固定 4 个 daemon worker 的队列（并发受控，软件退出不会被请求拖住）
- 同 URL + 尺寸的在途请求合并，加载完成的 QPixmap 进 LRU 缓存（翻页/重渲染直接复用）
- 用 QImageReader 按目标尺寸解码，不再把 800x800 原图完整解码后再缩放
- 调用方可传 stale() 判定：结果已被换掉/页面已清空时不再发请求、也不再落图
"""

from __future__ import annotations

import queue
import re
import threading
import weakref
from collections import OrderedDict

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from gui_qt.common import post_to_main

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_HOST_RE = re.compile(r"https?://([^/]+)/")


def _alive(widget) -> bool:
    """C++ 对象是否还活着（行被 deleteLater 后弱引用可能仍可解引用）。"""
    if widget is None:
        return False
    try:
        from shiboken6 import isValid
        return bool(isValid(widget))
    except Exception:
        return True


def _stale(check) -> bool:
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return True


class _WorkerPool:
    """固定数量的 daemon 线程池（ThreadPoolExecutor 的线程非 daemon，会拖慢退出）。"""

    def __init__(self, workers: int):
        self._queue: queue.Queue = queue.Queue()
        self._workers = max(1, int(workers))
        for i in range(self._workers):
            threading.Thread(target=self._loop, name=f"cover-{i}", daemon=True).start()

    def submit(self, fn, *args):
        self._queue.put((fn, args))

    def _loop(self):
        while True:
            fn, args = self._queue.get()
            try:
                fn(*args)
            except Exception:
                pass  # 单张封面失败不影响其余任务
            finally:
                self._queue.task_done()


def _decode(data: bytes, size: int):
    """按目标尺寸解码：小封面不把原图整张解出来。"""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf)
    try:
        reader.setAutoTransform(True)
        orig = reader.size()
        if orig.isValid() and orig.width() > size and orig.height() > size:
            reader.setScaledSize(orig.scaled(QSize(size, size), Qt.KeepAspectRatio))
        img = reader.read()
    finally:
        buf.close()
    if img.isNull():
        img = QImage.fromData(data)
        if img.isNull():
            return None
        img = img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return img


class CoverLoader:
    """共享封面加载器；所有公开方法都只在主线程调用。"""

    def __init__(self, workers: int = 4, cache_size: int = 256):
        self._pool = _WorkerPool(workers)
        self._cache_size = max(8, int(cache_size))
        self._lock = threading.Lock()
        self._cache: OrderedDict = OrderedDict()
        self._waiters: dict = {}
        self._local = threading.local()

    # ---------- 对外 ----------

    def request(self, url: str, size: int, label, stale=None):
        """给 label 请求封面；缓存命中时立即设置，否则排队加载。"""
        if not url or size <= 0 or not _alive(label):
            return
        key = (url, int(size))
        cached = self._cache_get(key)
        if cached is not None:
            self._apply(label, cached, stale)
            return
        with self._lock:
            if _stale(stale):
                return
            waiters = self._waiters.setdefault(key, [])
            waiters.append((weakref.ref(label), stale))
            first = len(waiters) == 1
        if first:
            self._pool.submit(self._work, key)

    # ---------- 队列 ----------

    def _work(self, key):
        url, size = key
        with self._lock:
            waiters = self._waiters.get(key) or []
            if not any(_alive(ref()) and not _stale(check) for ref, check in waiters):
                self._waiters.pop(key, None)
                return
        img = self._fetch(url, size)
        post_to_main(self._deliver, key, img)

    def _fetch(self, url: str, size: int):
        try:
            import requests
            session = getattr(self._local, "session", None)
            if session is None:
                session = requests.Session()
                session.headers.update({"User-Agent": _UA})
                self._local.session = session
            headers = {}
            host = _HOST_RE.match(url)
            if host and host.group(1).endswith("126.net"):
                headers["Referer"] = "https://music.163.com/"
            resp = session.get(url, timeout=(5, 10), headers=headers)
            if resp.status_code != 200 or not resp.content:
                return None
            return _decode(resp.content, size)
        except Exception:
            return None

    def _deliver(self, key, img):
        with self._lock:
            waiters = self._waiters.pop(key, [])
        if img is None or img.isNull() or not waiters:
            return
        pixmap = QPixmap.fromImage(img)
        if pixmap.isNull():
            return
        self._cache_put(key, pixmap)
        for ref, stale in waiters:
            self._apply(ref(), pixmap, stale)

    @staticmethod
    def _apply(label, pixmap, stale=None):
        if not _alive(label) or _stale(stale):
            return
        try:
            label.setPixmap(pixmap)
        except RuntimeError:
            pass

    # ---------- 缓存 ----------

    def _cache_get(self, key):
        with self._lock:
            pixmap = self._cache.get(key)
            if pixmap is not None:
                self._cache.move_to_end(key)
            return pixmap

    def _cache_put(self, key, pixmap):
        with self._lock:
            self._cache[key] = pixmap
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)


_LOADER = CoverLoader()


def load_cover(url: str, size: int, label, stale=None):
    """共享加载器入口：主线程调用，返回 None。"""
    _LOADER.request(url, size, label, stale)
