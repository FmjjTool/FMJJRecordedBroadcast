"""B站资源批量下载：hdslb CDN 镜像回退 + 线程池并发。"""

from __future__ import annotations

import os
import posixpath
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0")
HDSLB_HOSTS = ["i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com"]


def get_mirror_urls(url: str) -> list[str]:
    """hdslb.com 资源生成 i0/i1/i2 互换镜像。"""
    if not url or url.startswith("data:"):
        return [url] if url else []
    p = urllib.parse.urlparse(url)
    if not (p.hostname or "").endswith("hdslb.com"):
        return [url]
    urls = [url]
    for host in HDSLB_HOSTS:
        if host != p.hostname:
            candidate = f"{p.scheme}://{host}{p.path}"
            if candidate not in urls:
                urls.append(candidate)
    return urls


def url_ext(url: str) -> str:
    """从 URL 提取扩展名（hdslb 图床常无后缀，按路径段猜）。"""
    path = urllib.parse.urlparse(url).path
    _, ext = posixpath.splitext(path)
    ext = ext.lstrip(".").lower()
    return {"emote": "png", "emotes": "png"}.get(ext, ext)


def download_one(url: str, save_path: str, *, referer: str = "https://www.bilibili.com/",
                 mirrors: list[str] | None = None) -> str:
    """下载单文件（失败依次尝试额外镜像 + CDN 节点互换），返回保存路径；全失败抛最后异常。"""
    candidates = list(dict.fromkeys([url, *(mirrors or []), *get_mirror_urls(url)]))
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    last_err: Exception | None = None
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA, "Referer": referer}) as client:
        for cand in candidates:
            try:
                with client.stream("GET", cand) as r:
                    r.raise_for_status()
                    tmp = save_path + ".part"
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(65536):
                            f.write(chunk)
                os.replace(tmp, save_path)
                return save_path
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
    raise last_err or RuntimeError(f"下载失败: {url}")


class DownloadTask:
    """一次批量下载任务：名称 + 文件列表 + 进度回调（线程安全）。"""

    def __init__(self, name: str, files: list[dict], base_dir: str,
                 on_progress=None, on_file_done=None, on_file_start=None):
        self.name = name
        self.files = files           # [{path, url}]
        self.base_dir = base_dir
        self.on_progress = on_progress       # (done, total)
        self.on_file_done = on_file_done     # (path, err)
        self.on_file_start = on_file_start   # (path) 下载开始（浮窗槽位绑定用）
        self._stop = threading.Event()
        self.failed: list[str] = []

    def stop(self):
        self._stop.set()

    def run(self, max_workers: int = 5) -> dict:
        root = os.path.join(self.base_dir, self.name)
        total = len(self.files)
        done = 0
        lock = threading.Lock()
        self.failed = []

        def _dl(item: dict):
            rel = item["path"]
            if self.on_file_start:
                try:
                    self.on_file_start(rel)
                except Exception:
                    pass
            ext = item.get("ext") or url_ext(item["url"])
            full = os.path.join(root, rel if not ext or rel.endswith(f".{ext}") else f"{rel}.{ext}")
            try:
                download_one(item["url"], full,
                             referer=item.get("referer", "https://www.bilibili.com/"),
                             mirrors=item.get("mirrors"))
                return rel, None
            except Exception as e:  # noqa: BLE001
                return rel, str(e)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = []
            for item in self.files:
                if self._stop.is_set():
                    break
                futures.append(pool.submit(_dl, item))
            for fut in as_completed(futures):
                rel, err = fut.result()
                with lock:
                    done += 1
                    if err:
                        self.failed.append(rel)
                    if self.on_file_done:
                        try:
                            self.on_file_done(rel, err)
                        except Exception:
                            pass
                    if self.on_progress:
                        try:
                            self.on_progress(done, total)
                        except Exception:
                            pass
        return {"total": total, "done": done, "failed": self.failed, "dir": root}
