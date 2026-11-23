# -*- coding: utf-8 -*-
"""移动云盘（139）新版 Web API 客户端（Cookie + 签名直连）。

接口族（实测于 `personal-kd-njs.yun.139.com`）：
- `POST /hcy/file/list`              列目录（`parentFileId` 为 `/` 或目录 fileId）
- `POST /hcy/file/create`            建目录（type=folder）/ 申请上传（type=file）
- `POST /hcy/file/getUploadUrl`      追加分片上传地址（超过 100 片时）
- `PUT  <partInfo.uploadUrl>`        直传分片（不带 Cookie、不需要签名）
- `POST /hcy/file/complete`          合并完成
- `POST /hcy/recyclebin/batchTrash`  删除到回收站

鉴权要点（踩坑记录）：
- 必须带 `Authorization`（取 Cookie 里的 `authorization`），少了报 `04000005 认证失败`；
- 每个请求都要 `mcloud-sign`（见 `sign.py`）；
- `x-DeviceInfo` / `x-yun-client-info` 必须带设备指纹，格式不对报 `04000003 请求头不合法`；
- `/file/create` 的 `partInfos` 不能是空数组，要按 100 MB 切片申报，否则报
  `04000002 分片信息列表数量不符合标准`。
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import requests

from . import sign as ysign

HOST = "https://personal-kd-njs.yun.139.com"
BASE = f"{HOST}/hcy"
TIMEOUT = 60
PART_TIMEOUT = 1800
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PART_SIZE = 100 * 1024 * 1024
BIG_PART_SIZE = 512 * 1024 * 1024

# 设备指纹：服务端校验设备串格式，缺了会报「请求头不合法 00010003」
DEVICE_ID = "09dd2e466fce159c14ecc5ce24afee33"
DEVICE_INFO = f"||9|7.18.1|edge||{DEVICE_ID}||windows 10||zh-CN|||"
CLIENT_INFO = f"{DEVICE_INFO}ZWRnZQ==||"

_AUTH_HINTS = ("认证失败", "登录")


class _PartReader:
    """把文件的一段作为流式请求体，避免 100/512 MB 的分片整片读进内存。"""

    def __init__(self, handle, length: int):
        self._handle = handle
        self._left = max(0, int(length))
        self._length = self._left

    def __len__(self) -> int:
        return self._length

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        if size is None or size < 0 or size > self._left:
            size = self._left
        data = self._handle.read(size)
        self._left -= len(data)
        return data


class Yun139Error(RuntimeError):
    def __init__(self, code, message: str = "", path: str = ""):
        self.code = code
        self.message = message or "未知错误"
        self.path = path
        super().__init__(f"[{code}] {self.message}")

    @property
    def is_auth(self) -> bool:
        text = str(self.message)
        return any(hint in text for hint in _AUTH_HINTS)


class Yun139Client:
    """一个登录态一个实例（fileId 语义：`/` 为根，其余为字符串 id）。"""

    def __init__(self, cookie: str, user_agent: str = UA, lanes: int = 3):
        cookie = (cookie or "").strip()
        if not cookie:
            raise Yun139Error(-1, "移动云盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
        self.lanes = max(1, min(4, int(lanes or 3)))
        self.session = requests.Session()
        self._dir_cache: dict[str, str] = {"": "/", "/": "/"}
        self._index_cache: dict[str, dict] = {}
        self._digest_cache: dict[tuple, tuple[str, str]] = {}
        self._authz = ""
        for part in cookie.split(";"):
            if "=" in part:
                key, _, value = part.partition("=")
                if key.strip() == "authorization":
                    self._authz = value.strip()

    # ==================== 基础 ====================

    def _headers(self, body: str) -> dict:
        return {
            "Cookie": self.cookie,
            "Authorization": self._authz,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://yun.139.com/",
            "Origin": "https://yun.139.com",
            "User-Agent": self.user_agent,
            "mcloud-channel": "1000101",
            "mcloud-client": "10701",
            "mcloud-version": "7.18.1",
            "mcloud-route": "001",
            "x-m4c-caller": "PC",
            "x-m4c-src": "10002",
            "x-inner-ntwk": "2",
            "x-SvcType": "1",
            "x-yun-svc-type": "1",
            "x-yun-api-version": "v1",
            "x-DeviceInfo": DEVICE_INFO,
            "x-yun-client-info": CLIENT_INFO,
            "x-yun-module-type": "100",
            "x-yun-app-channel": "10000034",
            "x-yun-channel-source": "10000034",
            "x-huawei-channelSrc": "10000034",
            "CMS-DEVICE": "default",
            "INNER-HCY-ROUTER-HTTPS": "1",
            "mcloud-sign": ysign.make_sign(body),
        }

    def _post(self, path: str, body: dict, retries: int = 1) -> dict:
        data = ysign.json_body(body)
        url = f"{BASE}{path}"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(url, headers=self._headers(data),
                                         data=data.encode(), timeout=TIMEOUT)
                payload = resp.json()
                if payload.get("success") is True:
                    return payload.get("data") or {}
                raise Yun139Error(payload.get("code"), payload.get("message"), path)
            except Yun139Error:
                raise
            except (requests.RequestException, OSError, ValueError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(1.5)
        raise Yun139Error(-1, f"网络错误：{last_error}", path)

    # ==================== 目录 ====================

    def iter_dir(self, file_id: str = "/", page_size: int = 100):
        """逐页列出一个目录的子项；调用方可边翻页边展示。"""
        cursor = None
        while True:
            data = self._post("/file/list", {
                "pageInfo": {"pageSize": page_size, "pageCursor": cursor},
                "orderBy": "updated_at", "orderDirection": "DESC",
                "parentFileId": file_id or "/",
                "imageThumbnailStyleList": ["Small", "Large"],
            })
            batch = data.get("items") or []
            if batch:
                yield batch
            cursor = (data.get("pageInfo") or {}).get("nextPageCursor")
            if not cursor or not batch:
                break

    def list_dir(self, file_id: str = "/") -> list[dict]:
        return [item for batch in self.iter_dir(file_id) for item in batch]

    @staticmethod
    def _entry(item: dict) -> dict:
        """下载页使用的统一结构；`fid` 与 `download_id` 都是 fileId。"""
        is_dir = item.get("type") == "folder"
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        file_key = str(item.get("fileId") or "")
        return {
            "file_name": str(item.get("name") or ""),
            "fid": file_key,
            "download_id": file_key,
            "dir": is_dir,
            "size": 0 if is_dir else size,
            "updated_at": item.get("updatedAt") or item.get("createdAt") or "",
        }

    def iter_entries(self, file_id: str = "/", page_size: int = 100):
        """逐页产出下载页统一结构，首分页返回后即可渲染。"""
        for batch in self.iter_dir(file_id or "/", page_size):
            yield [self._entry(item) for item in batch]

    def list_entries(self, file_id: str = "/") -> list[dict]:
        return [entry for batch in self.iter_entries(file_id) for entry in batch]

    def download_links(self, file_ids: list[str]) -> dict[str, str]:
        """逐个取下载地址（接口一次只接受一个 fileId，返回 data.url）。"""
        links: dict[str, str] = {}
        for fid in [str(f) for f in file_ids if f]:
            data = self._post("/file/getDownloadUrl", {"fileId": fid})
            url = str(data.get("url") or "")
            if url:
                links[fid] = url
        if not links:
            raise Yun139Error(-1, "未取到下载直链", "/file/getDownloadUrl")
        return links

    def download_headers(self) -> dict:
        """直链下载头；139 直链需要登录态 Cookie。"""
        return {
            "Cookie": self.cookie,
            "User-Agent": self.user_agent or UA,
            "Referer": "https://yun.139.com/",
            "Accept": "*/*",
        }

    def list_index(self, remote_dir: str) -> dict[str, dict]:
        remote_dir = (remote_dir or "").strip("/")
        if remote_dir in self._index_cache:
            return self._index_cache[remote_dir]
        index: dict[str, dict] = {}
        try:
            fid = self.ensure_dir(remote_dir)
            for item in self.list_dir(fid):
                if item.get("type") == "folder":
                    continue
                index[str(item.get("name"))] = {
                    "fid": str(item.get("fileId") or ""),
                    "size": int(item.get("size") or 0),
                }
        except Yun139Error:
            index = {}
        self._index_cache[remote_dir] = index
        return index

    def ensure_dir(self, remote_dir: str) -> str:
        remote_dir = (remote_dir or "").strip("/")
        if not remote_dir:
            return "/"
        if remote_dir in self._dir_cache:
            return self._dir_cache[remote_dir]
        fid = "/"
        walked = ""
        for part in [p for p in remote_dir.split("/") if p.strip()]:
            walked = f"{walked}/{part}" if walked else part
            if walked in self._dir_cache:
                fid = self._dir_cache[walked]
                continue
            existing = ""
            for item in self.list_dir(fid):
                if item.get("type") == "folder" and item.get("name") == part:
                    existing = str(item.get("fileId") or "")
                    break
            if existing:
                fid = existing
            else:
                data = self._post("/file/create", {
                    "parentFileId": fid, "name": part, "description": "",
                    "type": "folder", "fileRenameMode": "force_rename"})
                fid = str(data.get("fileId") or "")
                if not fid:
                    raise Yun139Error(-1, f"创建目录失败：{part}", "/file/create")
            self._dir_cache[walked] = fid
        return fid

    def delete_files(self, fids: list[str]) -> bool:
        ids = [str(f) for f in fids if f]
        if not ids:
            return True
        try:
            self._post("/recyclebin/batchTrash", {"fileIds": ids})
            return True
        except Yun139Error:
            return False

    # ==================== 上传 ====================

    def digest(self, path) -> tuple[str, str]:
        """引擎要 (md5, 附加)：返回 (整文件 md5, sha256)。一次读完算两个。"""
        p = Path(path)
        stat = p.stat()
        key = (str(p), stat.st_size, stat.st_mtime)
        if key not in self._digest_cache:
            md5 = hashlib.md5()
            sha256 = hashlib.sha256()
            with open(p, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    md5.update(chunk)
                    sha256.update(chunk)
            self._digest_cache[key] = (md5.hexdigest(), sha256.hexdigest())
        return self._digest_cache[key]

    @staticmethod
    def _plan_parts(size: int) -> list[dict]:
        part_size = BIG_PART_SIZE if size > 30 * 1024 ** 3 else PART_SIZE
        plan: list[dict] = []
        offset = 0
        number = 1
        while True:
            chunk = min(part_size, size - offset) if size else 0
            plan.append({"partNumber": number, "partSize": chunk, "partOffset": offset})
            offset += chunk
            number += 1
            if offset >= size:
                break
        return plan

    def upload_file(self, local_path, remote_dir: str, on_progress=None,
                    on_stage=None, hashes=None, resume=None) -> dict:
        """上传文件；`resume` 为 `state.ResumeHandle` 时按分片续传。

        续传记录保存 `file_id` / `upload_id` / 分片计划 / 已完成分片列表。
        分片按 `lanes` 并发 PUT；上传地址（uploadUrl）有时效，失效时用
        `/file/getUploadUrl` 重新取。
        """
        path = Path(local_path)
        size = path.stat().st_size
        md5, sha256 = hashes if hashes else self.digest(path)
        if on_stage is not None:
            on_stage("upload", path.name, size)
        parent = self.ensure_dir(remote_dir)

        saved = resume.load() if resume is not None else {}
        file_id = ""
        upload_id = ""
        plan: list[dict] = []
        done = 0
        completed: set[int] = set()
        if (saved.get("sha256") == sha256 and int(saved.get("size") or -1) == size
                and saved.get("upload_id") and saved.get("plan")):
            file_id = str(saved.get("file_id") or "")
            upload_id = str(saved["upload_id"])
            plan = list(saved["plan"])
            done = max(0, int(saved.get("done") or 0))
            completed = {int(x) for x in (saved.get("parts") or range(done))}
            while done in completed:
                done += 1
        used_resume = bool(upload_id)

        def _persist() -> None:
            if resume is not None:
                resume.save({"sha256": sha256, "size": size, "file_id": file_id,
                             "upload_id": upload_id, "plan": plan, "done": done,
                             "parts": sorted(completed)})

        urls: dict[int, str] = {}
        url_lock = threading.RLock()

        def _fetch_urls(start_index: int) -> None:
            pending = plan[start_index:start_index + 100]
            if not pending:
                return
            with url_lock:
                more = self._post("/file/getUploadUrl", {
                    "fileId": file_id, "uploadId": upload_id, "partInfos": pending})
                for index, item in enumerate(more.get("partInfos") or []):
                    number = int(item.get("partNumber") or 0) or (start_index + index + 1)
                    urls[number] = str(item.get("uploadUrl") or "")

        try:
            if not upload_id:
                plan = self._plan_parts(size)
                data = self._post("/file/create", {
                    "contentHash": sha256,
                    "contentHashAlgorithm": "SHA256",
                    "contentType": "application/octet-stream",
                    "parallelUpload": False,
                    "partInfos": plan[:100],
                    "size": size,
                    "parentFileId": parent,
                    "name": path.name,
                    "type": "file",
                    "fileRenameMode": "auto_rename",
                })
                file_id = str(data.get("fileId") or "")
                upload_id = str(data.get("uploadId") or "")
                infos = data.get("partInfos") or data.get("uploadPartInfos") or []
                if not infos:
                    if resume is not None:
                        resume.clear()
                    self._index_cache.pop((remote_dir or "").strip("/"), None)
                    return {"fid": file_id, "instant": True, "md5": md5, "size": size}
                for index, item in enumerate(infos):
                    urls[int(item.get("partNumber") or index + 1)] = str(
                        item.get("uploadUrl") or "")
                _persist()

            def part_length(index: int) -> int:
                return max(0, int(plan[index]["partSize"]))

            offsets: list[int] = []
            cursor = 0
            for index in range(len(plan)):
                offsets.append(cursor)
                cursor += part_length(index)

            sent = sum(part_length(i) for i in completed if 0 <= i < len(plan))

            def report(done_bytes: int) -> None:
                if on_progress is None:
                    return
                try:
                    on_progress(min(done_bytes, size), size)
                except Exception:  # noqa: BLE001
                    pass

            report(sent)

            # 先把待传分片的上传地址取齐，避免多个线程同时补取同一批
            for start in range(0, len(plan), 100):
                block = range(start, min(start + 100, len(plan)))
                if any(i not in completed and not urls.get(int(plan[i]["partNumber"]))
                       for i in block):
                    _fetch_urls(start)

            def upload_part(offset_index: int) -> tuple[int, int]:
                item = plan[offset_index]
                number = int(item["partNumber"])
                length = part_length(offset_index)
                url = urls.get(number) or ""
                last_error = None
                for attempt in range(2):
                    if not url:
                        _fetch_urls(offset_index)
                        url = urls.get(number) or ""
                    if not url:
                        break
                    try:
                        def _send(proxies=None):
                            # 每次重建 reader：重试时旧 body 已经消费过，不能复用
                            with open(path, "rb") as handle:
                                handle.seek(offsets[offset_index])
                                body = _PartReader(handle, length)
                                extra = {"proxies": proxies} if proxies is not None else {}
                                return self.session.put(
                                    url, data=body,
                                    headers={"Content-Type": "application/octet-stream",
                                             "Content-Length": str(length)},
                                    timeout=PART_TIMEOUT, **extra)

                        try:
                            resp = _send()
                        except (requests.exceptions.ProxyError,
                                requests.exceptions.SSLError):
                            # 代理隧道异常（企业代理/本机代理对 EOS 域名回 502 或断 TLS）
                            # 与云盘协议无关，直连通常能过；只在失败后降级，不影响正常代理环境
                            resp = _send({"http": None, "https": None})
                        if resp.status_code in (200, 201, 204):
                            return offset_index, length
                        last_error = Yun139Error(resp.status_code, resp.text[:160], "uploadUrl")
                        if resp.status_code in (401, 403, 404):
                            # 上传地址过期：重新取一次再试
                            with url_lock:
                                urls.pop(number, None)
                            _fetch_urls(offset_index)
                            url = urls.get(number) or ""
                            continue
                        if resp.status_code < 500:
                            break
                    except (requests.RequestException, OSError) as exc:
                        last_error = exc
                    if attempt == 0:
                        time.sleep(1.5)
                if not url:
                    raise Yun139Error(-1, f"第 {number} 片缺少上传地址", "/file/getUploadUrl")
                hint = ("（超时：服务器出网带宽不足或连接中断，可把并发降到 2 路后重试）"
                        if isinstance(last_error, requests.exceptions.Timeout) else "")
                raise last_error if isinstance(last_error, Yun139Error) else Yun139Error(
                    -1, f"分片 {number} 上传失败{hint}：{last_error}", "uploadUrl")

            pending = [i for i in range(len(plan)) if i not in completed]
            # 移动云盘的 EOS 要求分片**严格按 partNumber 顺序**上传：乱序会回
            # `400 InvalidPartOrder: fail to get previous part hash context`，
            # 并发下还会先表现为 SSL EOF（连接被服务端掐断）。
            # 总耗时受上行带宽限制，顺序上传并不会更慢，但能消掉这整类失败。
            for index in pending:
                offset_index, length = upload_part(index)
                completed.add(offset_index)
                while done in completed:
                    done += 1
                sent += length
                _persist()
                report(sent)

            self._post("/file/complete", {
                "contentHash": sha256, "contentHashAlgorithm": "SHA256",
                "fileId": file_id, "uploadId": upload_id})
            if resume is not None:
                resume.clear()
            self._index_cache.pop((remote_dir or "").strip("/"), None)
            return {"fid": file_id, "instant": False, "md5": md5, "size": size}
        except Exception:
            if used_resume and resume is not None:
                resume.clear()
                return self.upload_file(local_path, remote_dir, on_progress=on_progress,
                                        on_stage=on_stage, hashes=(md5, sha256), resume=None)
            raise
