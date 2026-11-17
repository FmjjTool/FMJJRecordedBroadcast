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
import time
from pathlib import Path

import requests

from . import sign as ysign

HOST = "https://personal-kd-njs.yun.139.com"
BASE = f"{HOST}/hcy"
TIMEOUT = 60
PART_TIMEOUT = 600
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PART_SIZE = 100 * 1024 * 1024
BIG_PART_SIZE = 512 * 1024 * 1024

# 设备指纹：服务端校验设备串格式，缺了会报「请求头不合法 00010003」
DEVICE_ID = "09dd2e466fce159c14ecc5ce24afee33"
DEVICE_INFO = f"||9|7.18.1|edge||{DEVICE_ID}||windows 10||zh-CN|||"
CLIENT_INFO = f"{DEVICE_INFO}ZWRnZQ==||"

_AUTH_HINTS = ("认证失败", "登录")


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

    def __init__(self, cookie: str, user_agent: str = UA):
        cookie = (cookie or "").strip()
        if not cookie:
            raise Yun139Error(-1, "移动云盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
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

    def list_dir(self, file_id: str = "/") -> list[dict]:
        items: list[dict] = []
        cursor = None
        while True:
            data = self._post("/file/list", {
                "pageInfo": {"pageSize": 100, "pageCursor": cursor},
                "orderBy": "updated_at", "orderDirection": "DESC",
                "parentFileId": file_id or "/",
                "imageThumbnailStyleList": ["Small", "Large"],
            })
            batch = data.get("items") or []
            items.extend(batch)
            cursor = (data.get("pageInfo") or {}).get("nextPageCursor")
            if not cursor or not batch:
                break
        return items

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
                    on_stage=None, hashes=None) -> dict:
        path = Path(local_path)
        size = path.stat().st_size
        md5, sha256 = hashes if hashes else self.digest(path)
        if on_stage is not None:
            on_stage("upload", path.name, size)
        parent = self.ensure_dir(remote_dir)
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
            # 秒传：服务端已有同样内容
            self._index_cache.pop((remote_dir or "").strip("/"), None)
            return {"fid": file_id, "instant": True, "md5": md5, "size": size}

        urls: dict[int, str] = {}
        for index, item in enumerate(infos):
            urls[int(item.get("partNumber") or index + 1)] = str(item.get("uploadUrl") or "")

        pending = [item for item in plan if int(item["partNumber"]) not in urls]
        for start in range(0, len(pending), 100):
            more = self._post("/file/getUploadUrl", {
                "fileId": file_id, "uploadId": upload_id,
                "partInfos": pending[start:start + 100],
            })
            for index, item in enumerate(more.get("partInfos") or []):
                urls[int(item.get("partNumber") or index + 1)] = str(item.get("uploadUrl") or "")

        sent = 0
        with open(path, "rb") as handle:
            for item in plan:
                number = int(item["partNumber"])
                blob = handle.read(int(item["partSize"]))
                if not blob:
                    break
                url = urls.get(number) or ""
                if not url:
                    raise Yun139Error(-1, f"第 {number} 片缺少上传地址", "/file/create")
                last_error = None
                for attempt in range(2):
                    try:
                        resp = self.session.put(
                            url, data=blob,
                            headers={"Content-Type": "application/octet-stream",
                                     "Content-Length": str(len(blob))},
                            timeout=PART_TIMEOUT)
                        if resp.status_code in (200, 201, 204):
                            last_error = None
                            break
                        last_error = Yun139Error(resp.status_code, resp.text[:160], "uploadUrl")
                        if resp.status_code < 500:
                            break
                    except (requests.RequestException, OSError) as exc:
                        last_error = exc
                    if attempt == 0:
                        time.sleep(1.5)
                if last_error is not None:
                    raise last_error if isinstance(last_error, Yun139Error) else Yun139Error(
                        -1, f"分片 {number} 上传失败：{last_error}", "uploadUrl")
                sent += len(blob)
                if on_progress is not None:
                    try:
                        on_progress(min(sent, size), size)
                    except Exception:  # noqa: BLE001
                        pass

        self._post("/file/complete", {
            "contentHash": sha256, "contentHashAlgorithm": "SHA256",
            "fileId": file_id, "uploadId": upload_id})
        self._index_cache.pop((remote_dir or "").strip("/"), None)
        return {"fid": file_id, "instant": False, "md5": md5, "size": size}
