# -*- coding: utf-8 -*-
"""百度网盘 Web API 客户端（Cookie 直连，纯 Python）。

与夸克同一套引擎接口（`list_index` / `ensure_dir` / `upload_file` / `delete_files`），
所以备份引擎不需要区分后端。上传链路：

1. `POST /api/precreate`  申请上传，返回 `uploadid`；`return_type=2` 表示秒传成功
2. `POST d.pcs.baidu.com/rest/2.0/pcs/superfile2`  逐块上传（表单字段 `file`）
3. `POST /api/create`     提交文件（`uploadid` + `block_list`）

要点（实测得出）：
- `superfile2` **只认 `app_id`**，传 `access_token=<bdstoken>` 会报 `31024 app id is empty`；
- 目录、删除都按**路径**走，不用 fid，所以 `list_index` 里的 `fid` 直接给路径；
- 百度不允许上传 0 字节文件；
- 分块固定 4 MB，`block_list` 是每块的 md5（大文件不是整文件 md5）。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

BASE = "https://pan.baidu.com"
PCS = "https://d.pcs.baidu.com"
APP_ID = 250528
BLOCK_SIZE = 4 * 1024 * 1024
TIMEOUT = 60
PART_TIMEOUT = 150
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_AUTH_ERRNOS = {-6, -3, 2, 111, 116, 4}


class BaiduError(RuntimeError):
    def __init__(self, errno, message: str = "", path: str = ""):
        self.errno = errno
        self.message = message or "未知错误"
        self.path = path
        super().__init__(f"[{errno}] {self.message}")

    @property
    def is_auth(self) -> bool:
        return int(self.errno or 0) in _AUTH_ERRNOS


def file_digests(path: Path, block_size: int = BLOCK_SIZE) -> tuple[str, list[str]]:
    """一次读完同时算：整文件 md5 + 每 4 MB 块的 md5 列表。

    - 整文件 md5 给状态库做「内容是否变化」判断
    - 块 md5 列表是百度 `block_list` 的口径（大文件必须按块给）
    3 GB 视频只读一遍盘，不做二次遍历。
    """
    whole = hashlib.md5()
    blocks: list[str] = []
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(block_size)
            if not chunk:
                break
            whole.update(chunk)
            blocks.append(hashlib.md5(chunk).hexdigest())
    return whole.hexdigest(), (blocks or [hashlib.md5(b"").hexdigest()])


class BaiduClient:
    """一个登录态一个实例；`bdstoken` 首次用时取。"""

    def __init__(self, cookie: str, user_agent: str = UA):
        cookie = (cookie or "").strip()
        if not cookie:
            raise BaiduError(-6, "百度网盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({
            "Cookie": self.cookie,
            "User-Agent": self.user_agent,
            "Referer": f"{BASE}/disk/main",
            "Accept": "application/json, text/plain, */*",
        })
        self._bdstoken = ""
        self._dir_cache: dict[str, str] = {"": "/", "/": "/"}
        self._index_cache: dict[str, dict] = {}
        # (路径, 大小, mtime) → (整文件 md5, 4MB 块 md5 列表)
        self._digest_cache: dict[tuple, tuple[str, list[str]]] = {}

    def digest(self, path) -> tuple[str, str]:
        """引擎只要 (md5, 附加)：百度这里返回 (整文件 md5, 首块 md5)，块列表内部缓存。"""
        md5, blocks = self._digests(path)
        return md5, blocks[0]

    def _digests(self, path) -> tuple[str, list[str]]:
        p = Path(path)
        stat = p.stat()
        key = (str(p), stat.st_size, stat.st_mtime)
        if key not in self._digest_cache:
            self._digest_cache[key] = file_digests(p)
        return self._digest_cache[key]

    # ==================== 基础 ====================

    def _common(self) -> dict:
        return {"app_id": APP_ID, "channel": "chunlei", "clienttype": 0, "web": 1}

    @property
    def bdstoken(self) -> str:
        if self._bdstoken:
            return self._bdstoken
        resp = self.session.get(
            f"{BASE}/api/gettemplatevariable",
            params={"clienttype": 0, "app_id": APP_ID, "web": 1,
                    "fields": '["bdstoken"]'}, timeout=TIMEOUT)
        data = self._json(resp, "/api/gettemplatevariable")
        token = str((data.get("result") or {}).get("bdstoken") or "")
        if not token:
            raise BaiduError(data.get("errno", -1), "未取到 bdstoken（登录可能已失效）")
        self._bdstoken = token
        return token

    @staticmethod
    def _json(resp, path: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise BaiduError(-1, f"接口返回非 JSON（HTTP {resp.status_code}）", path)
        if not isinstance(data, dict):
            raise BaiduError(-1, "接口返回结构异常", path)
        return data

    def _check(self, data: dict, path: str) -> dict:
        errno = int(data.get("errno") or 0)
        if errno not in (0,):
            raise BaiduError(errno, data.get("error_msg") or data.get("errmsg") or "", path)
        return data

    # ==================== 目录 ====================

    def list_dir(self, remote_path: str) -> list[dict]:
        items: list[dict] = []
        start = 0
        while True:
            resp = self.session.get(f"{BASE}/api/list", params={
                **self._common(), "dir": remote_path or "/", "order": "name",
                "start": start, "limit": 100, "bdstoken": self.bdstoken}, timeout=TIMEOUT)
            data = self._check(self._json(resp, "/api/list"), "/api/list")
            batch = data.get("list") or []
            items.extend(batch)
            if len(batch) < 100:
                break
            start += 100
        return items

    def list_entries(self, remote_path: str = "/") -> list[dict]:
        """下载页使用的统一结构。

        `fid` 用路径（进入目录靠路径），`download_id` 用 fs_id
        （百度 filemetas 的 targets 对中文路径会报 errno 2，必须用 fsids）。
        """
        entries: list[dict] = []
        for item in self.list_dir(remote_path or "/"):
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            path = str(item.get("path") or "")
            entries.append({
                "file_name": str(item.get("server_filename") or ""),
                "fid": path,
                "download_id": str(item.get("fs_id") or ""),
                "dir": str(item.get("isdir")) == "1",
                "size": size,
                "updated_at": item.get("server_mtime") or item.get("local_mtime") or "",
            })
        return entries

    def download_links(self, fsids: list[str]) -> dict[str, str]:
        """按 fs_id 批量取 dlink；返回值 `{fs_id: dlink}`。"""
        ids: list[int] = []
        for value in fsids:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        if not ids:
            return {}
        resp = self.session.get(f"{BASE}/api/filemetas", params={
            **self._common(), "fsids": json.dumps(ids), "dlink": 1,
            "bdstoken": self.bdstoken}, timeout=TIMEOUT)
        data = self._check(self._json(resp, "/api/filemetas"), "/api/filemetas")
        links: dict[str, str] = {}
        for index, item in enumerate(data.get("info") or []):
            fid = str(item.get("fs_id") or (ids[index] if index < len(ids) else ""))
            url = str(item.get("dlink") or "")
            if fid and url:
                links[fid] = url
        if not links:
            raise BaiduError(-1, "未取到下载直链", "/api/filemetas")
        return links

    def download_headers(self) -> dict:
        """直链下载头；UA 必须是 `pan.baidu.com`，浏览器 UA 会被 31326 拒绝。"""
        return {
            "Cookie": self.cookie,
            "User-Agent": "pan.baidu.com",
            "Referer": f"{BASE}/",
            "Accept": "*/*",
        }

    def list_index(self, remote_dir: str) -> dict[str, dict]:
        """`文件名 → {fid: 路径, size}`（百度按路径删除，fid 直接放路径）。"""
        remote_dir = (remote_dir or "").strip("/")
        if remote_dir in self._index_cache:
            return self._index_cache[remote_dir]
        path = "/" + remote_dir if remote_dir else "/"
        index: dict[str, dict] = {}
        try:
            items = self.list_dir(path)
        except BaiduError as exc:
            if int(exc.errno or 0) == -9:      # 目录还不存在，等价于空目录
                self._index_cache[remote_dir] = index
                return index
            raise
        for item in items:
            if int(item.get("isdir") or 0):
                continue
            index[str(item.get("server_filename"))] = {
                "fid": str(item.get("path") or ""),
                "size": int(item.get("size") or 0),
            }
        self._index_cache[remote_dir] = index
        return index

    def ensure_dir(self, remote_dir: str) -> str:
        """逐级建目录，返回最终路径（百度用路径而不是 fid）。"""
        remote_dir = (remote_dir or "").strip("/")
        if not remote_dir:
            return "/"
        current = ""
        for part in [p for p in remote_dir.split("/") if p.strip()]:
            current = f"{current}/{part}"
            if current in self._dir_cache:
                continue
            resp = self.session.post(f"{BASE}/api/create", data={
                "path": current, "isdir": 1, "block_list": "[]",
                "bdstoken": self.bdstoken, **self._common()}, timeout=TIMEOUT)
            data = self._json(resp, "/api/create")
            # errno -8 = 目录已存在
            if int(data.get("errno") or 0) not in (0, -8):
                raise BaiduError(data.get("errno"), data.get("error_msg") or "建目录失败", current)
            self._dir_cache[current] = current
        return "/" + remote_dir

    def delete_files(self, fids: list[str]) -> bool:
        """按路径删到回收站（引擎传入的是 list_index 给的路径）。"""
        paths = [str(f) for f in fids if f]
        if not paths:
            return True
        try:
            resp = self.session.post(
                f"{BASE}/api/filemanager", params={"opera": "delete"},
                data={"filelist": json.dumps(paths), "async": 1,
                      "bdstoken": self.bdstoken, **self._common()}, timeout=TIMEOUT)
            data = self._json(resp, "/api/filemanager")
            return int(data.get("errno") or 0) == 0
        except (BaiduError, requests.RequestException):
            return False

    # ==================== 上传 ====================

    def upload_file(self, local_path, remote_dir: str, on_progress=None,
                    on_stage=None, hashes=None, resume=None) -> dict:
        """上传一个文件；`resume` 为 `state.ResumeHandle` 时支持断点续传。

        百度按 `partseq` 顺序传分片，所以断点只需记住「已传到第几片」；
        `uploadid` 过期时 create/precreate 会报错，这里丢掉断点重来一次。
        """
        path = Path(local_path)
        size = path.stat().st_size
        if size <= 0:
            raise BaiduError(-1, "百度网盘不允许上传 0 字节文件")
        remote_dir = (remote_dir or "").strip("/")
        target_dir = self.ensure_dir(remote_dir)
        remote_path = f"{target_dir.rstrip('/')}/{path.name}"

        if on_stage is not None:
            on_stage("hash", path.name, size)
        file_md5, blocks = self._digests(path)

        saved = resume.load() if resume is not None else {}
        uploadid = ""
        done = 0
        if (saved.get("md5") == file_md5 and int(saved.get("size") or -1) == size
                and saved.get("uploadid")):
            uploadid = str(saved["uploadid"])
            done = max(0, int(saved.get("done") or 0))
        used_resume = bool(uploadid)

        def _persist() -> None:
            if resume is not None:
                resume.save({"md5": file_md5, "size": size,
                             "uploadid": uploadid, "done": done})

        try:
            if not uploadid:
                resp = self.session.post(f"{BASE}/api/precreate", data={
                    "path": remote_path, "size": size, "isdir": 0,
                    "block_list": json.dumps(blocks), "autoinit": 1, "rtype": 3,
                    "bdstoken": self.bdstoken, **self._common()}, timeout=TIMEOUT)
                pre = self._check(self._json(resp, "/api/precreate"), "/api/precreate")
                uploadid = str(pre.get("uploadid") or "")
                if int(pre.get("return_type") or 0) == 2 or not uploadid:
                    if resume is not None:
                        resume.clear()
                    self._index_cache.pop(remote_dir, None)
                    return {"fid": remote_path, "instant": True,
                            "md5": file_md5, "size": size}
                _persist()

            if on_stage is not None:
                on_stage("upload", path.name, size)
            total_parts = max(1, -(-size // BLOCK_SIZE))
            sent = min(done * BLOCK_SIZE, size)
            if on_progress is not None and sent:
                try:
                    on_progress(sent, size)
                except Exception:  # noqa: BLE001
                    pass

            with open(path, "rb") as handle:
                handle.seek(done * BLOCK_SIZE)
                partseq = done
                while True:
                    blob = handle.read(BLOCK_SIZE)
                    if not blob:
                        break
                    last_error = None
                    for attempt in range(2):
                        try:
                            r = self.session.post(
                                f"{PCS}/rest/2.0/pcs/superfile2",
                                params={"method": "upload", "type": "tmpfile",
                                        "path": remote_path, "uploadid": uploadid,
                                        "partseq": partseq, "app_id": APP_ID},
                                files={"file": ("blob", blob)}, timeout=PART_TIMEOUT)
                            if r.status_code == 200:
                                last_error = None
                                break
                            last_error = BaiduError(r.status_code, r.text[:160], "superfile2")
                            if r.status_code < 500:
                                break
                        except (requests.RequestException, OSError) as exc:
                            last_error = exc
                        if attempt == 0:
                            time.sleep(1.5)
                    if last_error is not None:
                        raise last_error if isinstance(last_error, BaiduError) else BaiduError(
                            -1, f"分块 {partseq} 上传失败：{last_error}", "superfile2")
                    sent = min(sent + len(blob), size)
                    partseq += 1
                    done = partseq
                    _persist()
                    if on_progress is not None:
                        try:
                            on_progress(sent, size)
                        except Exception:  # noqa: BLE001
                            pass

            resp = self.session.post(f"{BASE}/api/create", data={
                "path": remote_path, "size": size, "isdir": 0,
                "block_list": json.dumps(blocks), "uploadid": uploadid,
                "bdstoken": self.bdstoken, **self._common()}, timeout=TIMEOUT * 2)
            created = self._check(self._json(resp, "/api/create"), "/api/create")
            if resume is not None:
                resume.clear()
            self._index_cache.pop(remote_dir, None)
            return {"fid": str(created.get("path") or remote_path), "instant": False,
                    "md5": file_md5, "size": size}
        except Exception:
            if used_resume and resume is not None:
                resume.clear()
                return self.upload_file(local_path, remote_dir, on_progress=on_progress,
                                        on_stage=on_stage, hashes=(file_md5, ""), resume=None)
            raise
