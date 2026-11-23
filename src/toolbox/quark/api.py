# -*- coding: utf-8 -*-
"""夸克网盘 API 客户端（Cookie 直连，纯 Python）。

**上传默认走客户端 PDS 通路**（对齐夸克网盘 PC 2.5.40），失败才退回网页五步：

PDS（首选）：
1. `POST /file/upload/pre`            `pds_version=std` + `upload_type=single_part|multi_part`
2. `POST /file/upload/getUploadUrls`  一次取最多 50 片预签名地址（省掉逐片 auth）
3. `PUT  <upload_url>`                带 `Authorization: signature_info.signature`
4. `POST /file/upload/listPart`       断点续传：拉回服务端已收到的分片
5. `POST /file/upload/finish`         交 etag 列表收尾
   `POST /file/update/hash`           并行探测秒传（命中即 finish）

网页五步（回退）：

1. `POST /file/upload/pre`      申请上传任务，拿 task_id / obj_key / bucket / auth_info / part_size
2. `POST /file/update/hash`     交 md5 + sha1，命中即「秒传」（返回 finish=true 直接完成）
3. `POST /file/upload/auth`     取 OSS 分片签名，再 `PUT` 到 {bucket}.{upload_url} 上传分片
4. `POST /file/upload/auth`     取 CompleteMultipartUpload 签名，POST XML 合并分片
5. `POST /file/upload/finish`   通知服务端收尾

为什么优先 PDS：网页版每片都要单独 `upload/auth` 再手工拼 OSS 签名，任何一次往返
抖动都会让整片重来；PDS 一次批量拿 50 片地址、签名过期才刷新，且 listPart 能续传，
这是客户端上传比网页协议稳的根本原因。

约定：
- Cookie 只在内存里用；响应里服务端刷新的 `__puus` / `__pus` 只更新内存副本，
  **绝不回写** Cookie 管理的存储（项目约定 7）。
- `pre` 不自动重试：响应丢失时任务可能已分配，重试会重复占用任务。
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import json
import mimetypes
import os
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import formatdate
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.certs import where as _requests_ca_bundle

from .config import SHARE_EXPIRED_TYPE

API_BASE = "https://drive-pc.quark.cn/1/clouddrive"
REFERER = "https://pan.quark.cn/"
# 客户端 PDS 上传参数（对齐夸克网盘 PC 2.5.40）
PDS_HEADER_VER = "std"
PDS_URL_BATCH = 50          # getUploadUrls 单次最多申请 50 片（客户端行为）
PDS_SINGLE_MAX = 4 * 1024 * 1024   # 不超过一片的文件走 single_part
PDS_MIN_PART = 64 * 1024    # 服务端要求 part_size 按 65536 对齐
# 与 alist 一致的 OSS 客户端标识（服务端会校验 auth_meta 里的这段）
OSS_USER_AGENT = "aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
DEFAULT_PART_SIZE = 4 * 1024 * 1024
TIMEOUT = 60
# 单个分片（默认 4 MB）的传输超时：太长会「假死」，太短会误杀低带宽云服务器
PART_TIMEOUT = 300

_AUTH_HINTS = ("未登录", "登录", "token", "授权", "认证")


class _QuarkSSLAdapter(HTTPAdapter):
    """夸克接口证书链含弱密钥，OpenSSL 3 默认 SECLEVEL=2 会拒绝握手。

    仅把安全级别降到 1，证书链与主机名仍完整校验；不是关闭 verify。
    """

    def init_poolmanager(self, *args, **kwargs):
        ca_bundle = (os.environ.get("REQUESTS_CA_BUNDLE")
                     or os.environ.get("CURL_CA_BUNDLE")
                     or _requests_ca_bundle())
        context = ssl.create_default_context(cafile=ca_bundle)
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = context
        super().init_poolmanager(*args, **kwargs)


class _Sha1StateReader:
    """顺序读文件，导出每个分片起点的 SHA1 中间状态（h0..h4）。

    PDS 的 `parallel_sha1_ctx` 要求第 2 片起必须带「该片之前所有字节」的 SHA1
    内部状态：缺这个字段服务端直接回 43001，填假数据 finish 时回 43008
    （实测）。hashlib 不暴露中间状态，这里借 OpenSSL 的 SHA_CTX 结构直接读，
    全程 C 速度，只多读一遍文件。
    """

    _lock = threading.Lock()
    _lib = None
    _tried = False

    class _Ctx(ctypes.Structure):
        # OpenSSL 的 SHA_CTX 布局：5 个状态字 + 长度 + 64 字节缓冲区 + 计数
        _fields_ = [
            ("h0", ctypes.c_uint), ("h1", ctypes.c_uint),
            ("h2", ctypes.c_uint), ("h3", ctypes.c_uint),
            ("h4", ctypes.c_uint),
            ("Nl", ctypes.c_uint), ("Nh", ctypes.c_uint),
            ("data", ctypes.c_uint * 16),
            ("num", ctypes.c_uint),
        ]

    @classmethod
    def _library(cls):
        with cls._lock:
            if cls._tried:
                return cls._lib
            cls._tried = True
            candidates: list[str] = []
            dll_dir = Path(sys.prefix) / "DLLs"
            for name in ("libcrypto-3-x64.dll", "libcrypto-3.dll",
                         "libcrypto-1_1-x64.dll"):
                candidates.append(str(dll_dir / name))
            found = ctypes.util.find_library("crypto")
            if found:
                candidates.append(found)
            for candidate in candidates:
                try:
                    lib = ctypes.CDLL(candidate)
                    lib.SHA1_Init.argtypes = [ctypes.POINTER(cls._Ctx)]
                    lib.SHA1_Update.argtypes = [ctypes.POINTER(cls._Ctx),
                                                ctypes.c_char_p, ctypes.c_size_t]
                except (OSError, AttributeError):
                    continue
                cls._lib = lib
                return lib
            return None

    @classmethod
    def available(cls) -> bool:
        return cls._library() is not None

    @classmethod
    def states(cls, path: Path, part_size: int, total: int,
               size: int) -> dict[int, list[int]]:
        """返回 `{分片号: [h0, h1, h2, h3, h4]}`，第 1 片不在结果里。"""
        lib = cls._library()
        if lib is None:
            raise QuarkError(-1, "缺少 libcrypto，无法计算分片 SHA1（PDS 上传必需）")
        ctx = cls._Ctx()
        lib.SHA1_Init(ctypes.byref(ctx))
        out: dict[int, list[int]] = {}
        with open(path, "rb") as handle:
            for number in range(1, total + 1):
                if number > 1:
                    out[number] = [ctx.h0, ctx.h1, ctx.h2, ctx.h3, ctx.h4]
                left = min(part_size, size - (number - 1) * part_size)
                while left > 0:
                    chunk = handle.read(min(4 * 1024 * 1024, left))
                    if not chunk:
                        break
                    lib.SHA1_Update(ctypes.byref(ctx), chunk, len(chunk))
                    left -= len(chunk)
        return out


class QuarkError(RuntimeError):
    """接口返回非 0 code（业务错误）。"""
    def __init__(self, code, message: str, path: str = ""):
        self.code = code
        self.message = message or "未知错误"
        self.path = path
        super().__init__(f"[{code}] {self.message}")

    @property
    def is_auth(self) -> bool:
        text = str(self.message).lower()
        return any(hint in text for hint in _AUTH_HINTS)


def _mime_of(path: Path) -> str:
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "application/octet-stream"


def file_hashes(path: Path) -> tuple[str, str]:
    """一次读完算 md5 + sha1（备份场景文件已落盘，顺序读最快）。"""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            md5.update(chunk)
            sha1.update(chunk)
    return md5.hexdigest(), sha1.hexdigest()


class QuarkClient:
    """一个登录态一个实例；`ensure_dir` 的结果按路径缓存。"""

    def __init__(self, cookie: str, user_agent: str = DEFAULT_UA, lanes: int = 3):
        cookie = (cookie or "").strip()
        if not cookie:
            raise QuarkError(-1, "夸克网盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
        self.lanes = max(1, min(4, int(lanes or 3)))
        self._cookie_lock = threading.Lock()
        self.session = requests.Session()
        self.session.mount("https://", _QuarkSSLAdapter())
        self._dir_cache: dict[str, str] = {"": "0", "/": "0"}
        self._name_cache: dict[str, dict[str, int]] = {}
        # (路径, 大小, mtime) → (md5, sha1)：引擎判内容与上传前算校验只读一遍盘
        self._digest_cache: dict[tuple, tuple[str, str]] = {}
        # 客户端 PDS 通路开关：接口不可用时本次实例退化为网页五步
        self._pds_enabled = True
        self._pds_last_error = ""

    def digest(self, path) -> tuple[str, str]:
        """算 (md5, sha1)，同一 (路径, 大小, mtime) 只算一次。"""
        p = Path(path)
        stat = p.stat()
        key = (str(p), stat.st_size, stat.st_mtime)
        if key not in self._digest_cache:
            self._digest_cache[key] = file_hashes(p)
        return self._digest_cache[key]

    # ==================== 基础请求 ====================

    def _headers(self) -> dict:
        return {
            "Cookie": self.cookie,
            "Accept": "application/json, text/plain, */*",
            "Referer": REFERER,
            "User-Agent": self.user_agent,
        }

    def _absorb_cookies(self, resp) -> None:
        """把服务端刷新的会话 Cookie 只更新到内存（不落盘、不回写管理器）。"""
        fresh = {}
        for raw in resp.raw.headers.getlist("Set-Cookie") if hasattr(resp.raw, "headers") else []:
            piece = raw.split(";", 1)[0]
            if "=" in piece:
                key, _, value = piece.partition("=")
                key = key.strip()
                if key in ("__puus", "__pus"):
                    fresh[key] = value.strip()
        if not fresh:
            return
        with self._cookie_lock:
            parts, seen = [], set()
            for item in self.cookie.split(";"):
                if "=" not in item:
                    continue
                key, _, value = item.partition("=")
                key = key.strip()
                seen.add(key)
                parts.append(f"{key}={fresh.pop(key, value.strip())}")
            for key, value in fresh.items():
                parts.append(f"{key}={value}")
            self.cookie = "; ".join(parts)

    def _request(self, method: str, path: str, body: dict | None = None,
                 params: dict | None = None, retries: int = 2):
        query = {"pr": "ucpro", "fr": "pc"}
        if params:
            query.update(params)
        url = f"{API_BASE}{path}"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(
                    method, url, params=query, json=body, headers=self._headers(),
                    timeout=TIMEOUT)
                self._absorb_cookies(resp)
                try:
                    payload = resp.json()
                except ValueError:
                    raise QuarkError(-1, f"接口返回非 JSON（HTTP {resp.status_code}）", path)
                code = payload.get("code")
                if resp.status_code >= 400 or code not in (0, None):
                    raise QuarkError(code if code is not None else resp.status_code,
                                     payload.get("message") or payload.get("msg") or "", path)
                return payload
            except QuarkError:
                raise
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(1.5 * (attempt + 1))
        raise QuarkError(-1, f"网络错误：{last_error}", path)

    # ==================== 目录 ====================

    def list_dir(self, fid: str = "0", page_size: int = 100) -> list[dict]:
        """列出一个目录的全部直接子项（自动翻页）。"""
        items: list[dict] = []
        page = 1
        while True:
            payload = self._request("GET", "/file/sort", params={
                "pdir_fid": fid or "0",
                "_page": page,
                "_size": page_size,
                "_fetch_total": 1,
                "fetch_all_file": 1,
                "fetch_risk_file_name": 1,
                "_sort": "file_type:asc,updated_at:desc",
            })
            data = payload.get("data") or {}
            batch = data.get("list") or []
            items.extend(batch)
            total = 0
            for holder in (payload.get("metadata") or {}, data.get("metadata") or {},
                           data if isinstance(data, dict) else {}):
                try:
                    total = int(holder.get("_total") or 0)
                except (TypeError, ValueError):
                    total = 0
                if total:
                    break
            if not batch or len(batch) < page_size or (total and len(items) >= total):
                break
            page += 1
        return items

    def list_entries(self, fid: str = "0") -> list[dict]:
        """下载页使用的统一结构，与百度 / 移动云盘字段对齐。"""
        entries: list[dict] = []
        for item in self.list_dir(fid):
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            file_id = str(item.get("fid") or "")
            entries.append({
                "file_name": str(item.get("file_name") or ""),
                "fid": file_id,
                "download_id": file_id,
                "dir": bool(item.get("dir")),
                "size": size,
                "updated_at": item.get("updated_at") or item.get("created_at") or "",
            })
        return entries

    def download_headers(self) -> dict:
        """直链下载头；夸克直链要求 Cookie + Referer。"""
        return {
            "Cookie": self.cookie,
            "Referer": REFERER,
            "User-Agent": self.user_agent or DEFAULT_UA,
            "Accept": "*/*",
        }

    def mkdir(self, parent_fid: str, name: str) -> str:
        """建目录；同名已存在时返回已有 fid。"""
        payload = self._request("POST", "/file", body={
            "pdir_fid": parent_fid or "0",
            "file_name": name,
            "dir_path": "",
            "dir_init_lock": False,
        })
        data = payload.get("data") or {}
        fid = str(data.get("fid") or "")
        if fid:
            return fid
        for item in self.list_dir(parent_fid):
            if item.get("dir") and item.get("file_name") == name:
                return str(item.get("fid") or "")
        raise QuarkError(-1, f"创建目录失败：{name}", "/file")

    def ensure_dir(self, remote_dir: str) -> str:
        """把 `备份/2026` 这类相对路径逐级建好，返回最深一级的 fid。"""
        remote_dir = str(remote_dir or "").strip("/")
        if not remote_dir:
            return "0"
        if remote_dir in self._dir_cache:
            return self._dir_cache[remote_dir]
        fid = "0"
        walked = []
        for part in [p for p in remote_dir.split("/") if p.strip()]:
            walked.append(part)
            key = "/".join(walked)
            if key in self._dir_cache:
                fid = self._dir_cache[key]
                continue
            existing = ""
            for item in self.list_dir(fid):
                if item.get("dir") and item.get("file_name") == part:
                    existing = str(item.get("fid") or "")
                    break
            fid = existing or self.mkdir(fid, part)
            self._dir_cache[key] = fid
        return fid

    def list_names(self, remote_dir: str) -> dict[str, int]:
        """云端某目录下的 `文件名 → 大小`（不含子目录），用于补记录判定。"""
        return {name: item["size"] for name, item in self.list_index(remote_dir).items()}

    def list_index(self, remote_dir: str) -> dict[str, dict]:
        """云端某目录下的 `文件名 → {fid, size}`（不含子目录）。

        覆盖旧版本时要拿到云端那一份的 fid，光有大小不够。
        """
        remote_dir = str(remote_dir or "").strip("/")
        if remote_dir in self._name_cache:
            return self._name_cache[remote_dir]
        fid = self.ensure_dir(remote_dir)
        index: dict[str, dict] = {}
        for item in self.list_dir(fid):
            if item.get("dir"):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            index[str(item.get("file_name"))] = {
                "fid": str(item.get("fid") or ""), "size": size}
        self._name_cache[remote_dir] = index
        return index

    # ==================== 上传 ====================

    def _pre(self, path: Path, parent_fid: str, md5: str, sha1: str) -> dict:
        now_ms = int(time.time() * 1000)
        body = {
            "ccp_hash_update": True,
            "ccp_sha1": base64.b64encode(bytes.fromhex(sha1)).decode(),
            "md5": md5,
            "sha1": sha1,
            "dir_name": "",
            "file_name": path.name,
            "format_type": _mime_of(path),
            "l_created_at": now_ms,
            "l_updated_at": now_ms,
            "pdir_fid": parent_fid or "0",
            "size": path.stat().st_size,
        }
        payload = self._request("POST", "/file/upload/pre", body=body, retries=0)
        data = payload.get("data") or {}
        for field in ("task_id", "obj_key", "upload_id", "bucket", "auth_info"):
            if not str(data.get(field) or ""):
                raise QuarkError(-1, f"pre 返回缺少 {field}", "/file/upload/pre")
        data["_part_size"] = int((payload.get("metadata") or {}).get("part_size")
                                 or DEFAULT_PART_SIZE)
        return data

    # ==================== 上传 · 客户端 PDS 通路 ====================
    #
    # 官方客户端（PC 2.5.40）上传走 PDS，与网页版五步不是一套：
    #
    #   1. POST /file/upload/pre            pds_version=std + upload_type=single_part|multi_part
    #   2. POST /file/upload/getUploadUrls  一次取最多 50 片预签名地址
    #   3. PUT  <upload_url>                只带 Authorization: signature_info.signature
    #   4. POST /file/upload/listPart       断点续传：拉回服务端已收到的分片
    #   5. POST /file/upload/finish         交 etag 列表收尾
    #      POST /file/update/hash           并行跑，命中即秒传（FLASH_FINISH）
    #
    # 相比网页版，每片少两次往返（auth + 手工拼 OSS 签名），且能续传；
    # 网页协议上传容易失败，主要就差在这里。

    def _pre_pds(self, path: Path, parent_fid: str, size: int,
                 md5: str, sha1: str) -> dict:
        """申请 PDS 上传任务；小文件单分片，大文件由服务端下发 part_size。"""
        now_ms = int(time.time() * 1000)
        body = {
            "pds_version": "std",
            "header_ver": PDS_HEADER_VER,
            "upload_type": ("single_part" if 0 < size <= PDS_SINGLE_MAX
                            else "multi_part"),
            "pdir_fid": parent_fid or "0",
            "dir_name": "",
            "size": size,
            "file_name": path.name,
            "format_type": _mime_of(path),
            "same_path_reuse": False,
            "ccp_hash_update": True,
            "parallel_upload": True,
            "l_shot_at": now_ms,
            "l_created_at": int(path.stat().st_ctime * 1000),
            "l_updated_at": int(path.stat().st_mtime * 1000),
            "file_local_path": str(path),
        }
        if size <= PDS_SINGLE_MAX:
            # 单分片：客户端带整文件 md5/sha1，服务端可当场判秒传
            body.update(md5=md5, sha1=sha1, part_info_list=[{
                "content_type": "application/json",
                "part_number": 1,
                "part_size": size,
            }])
        payload = self._request("POST", "/file/upload/pre", body=body, retries=0)
        data = payload.get("data") or {}
        for field in ("task_id", "upload_id"):
            if not str(data.get(field) or ""):
                raise QuarkError(-1, f"PDS pre 返回缺少 {field}", "/file/upload/pre")
        data["_pds"] = True
        data["_part_size"] = int((payload.get("metadata") or {}).get("part_size")
                                 or DEFAULT_PART_SIZE)
        data["_single"] = size <= PDS_SINGLE_MAX
        return data

    def _pds_urls(self, task_id: str, upload_id: str,
                  parts: list[dict]) -> tuple[dict, dict]:
        """取一批分片的预签名地址；返回 ({part_no: url}, 公共 headers)。"""
        payload = self._request("POST", "/file/upload/getUploadUrls", body={
            "header_ver": PDS_HEADER_VER,
            "task_id": task_id,
            "upload_id": upload_id,
            "part_info_list": parts,
        })
        data = payload.get("data") or {}
        headers = data.get("headers") or {}
        urls: dict[int, dict] = {}
        for item in data.get("upload_urls") or []:
            number = int(item.get("part_number") or 0)
            if number:
                urls[number] = {"url": item.get("upload_url"),
                                "signature": (item.get("signature_info") or {}).get("signature")}
        return urls, headers

    def _pds_put(self, url: str, signature: str, blob: bytes,
                 extra_headers: dict | None = None) -> str:
        """PUT 一个分片，返回 etag。签名过期由上层重新取地址后重试。"""
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        if signature:
            headers["Authorization"] = signature
        try:
            resp = self.session.put(url, data=blob, headers=headers,
                                    timeout=PART_TIMEOUT)
        except (requests.exceptions.ProxyError, requests.exceptions.SSLError):
            # 代理隧道异常（企业代理/本机代理对对象存储域名回 502 或断 TLS）
            # 与云盘协议无关，直连通常能过；只在失败后降级
            resp = self.session.put(url, data=blob, headers=headers,
                                    timeout=PART_TIMEOUT,
                                    proxies={"http": None, "https": None})
        if resp.status_code not in (200, 201):
            text = resp.text[:200]
            if "SignatureDoesNotMatch" in text or "RequestTimeTooSkewed" in text:
                raise QuarkError("SIGN_EXPIRED", text, "/upload_url")
            raise QuarkError(resp.status_code, f"分片上传失败：{text}", "/upload_url")
        return resp.headers.get("ETag") or resp.headers.get("Etag") or ""

    def _pds_list_parts(self, task_id: str) -> tuple[dict[int, str], int]:
        """断点续传：服务端已收到的分片 {part_no: etag} 与完成状态。"""
        done: dict[int, str] = {}
        marker = 0
        status = 0
        while True:
            payload = self._request("POST", "/file/upload/listPart", body={
                "task_id": task_id,
                "next_part_number_marker": str(marker),
            })
            data = payload.get("data") or {}
            status = int(data.get("finish_status") or status or 0)
            batch = data.get("uploaded_parts") or []
            if isinstance(batch, dict):
                batch = [batch]
            for item in batch:
                number = int(item.get("part_number") or 0)
                if number:
                    done[number] = str(item.get("etag") or "").strip('"')
            marker += len(batch)
            if len(batch) < 100:
                break
        return done, status

    def _pds_finish(self, task_id: str, etags: dict[int, str]) -> dict:
        # 服务端要求 etag 带双引号；PUT 响应本身带，listPart 回包不带，
        # 这里统一规范化，避免续传文件 finish 时报 etag 不匹配。
        parts = [{"etag": f'"{str(etags[n]).strip(chr(34))}"', "part_number": n}
                 for n in sorted(etags)]
        payload = self._request("POST", "/file/upload/finish", body={
            "task_id": task_id,
            "pds_version": "std",
            "part_info_list": parts,
        }, retries=2)
        return payload.get("data") or {}

    def _pds_hash(self, task_id: str, md5: str, sha1: str) -> tuple[bool, str]:
        """PDS 秒传探测：命中返回 (True, fid)。"""
        payload = self._request("POST", "/file/update/hash", body={
            "task_id": task_id,
            "md5": md5,
            "sha1": sha1,
            "allow_repeat_backup": False,
        })
        data = payload.get("data") or {}
        return bool(data.get("finish")), str(data.get("fid") or "")

    def _upload_pds(self, path: Path, parent_fid: str, size: int, md5: str,
                    sha1: str, remote_dir: str, on_progress, on_stage,
                    resume) -> dict:
        """PDS 主流程；内部处理续传票据与进度落库。"""
        saved = resume.load() if resume is not None else {}
        pre: dict = {}
        etags: dict[int, str] = {}
        states: dict[int, list[int]] = {}
        if (saved.get("kind") == "pds" and saved.get("md5") == md5
                and int(saved.get("size") or -1) == size
                and isinstance(saved.get("pre"), dict) and saved["pre"].get("task_id")):
            pre = dict(saved["pre"])
            etags = {int(k): v for k, v in (saved.get("etags") or {}).items()}
            # 分片校验状态一起存：重试时不必为 3 GB 文件重算一遍 SHA1
            if saved.get("states") and int(saved.get("part_size") or 0) == int(
                    pre.get("_part_size") or 0):
                states = {int(k): list(v) for k, v in saved["states"].items()}

        def _persist() -> None:
            if resume is None:
                return
            resume.save({"kind": "pds", "md5": md5, "size": size, "pre": pre,
                         "part_size": int(pre.get("_part_size") or 0),
                         "states": {str(k): list(v) for k, v in states.items()},
                         "etags": {str(k): v for k, v in etags.items()}})

        if not pre:
            pre = self._pre_pds(path, parent_fid, size, md5, sha1)
            _persist()

        task_id = str(pre["task_id"])
        part_size = max(PDS_MIN_PART, int(pre.get("_part_size") or DEFAULT_PART_SIZE))
        if part_size % PDS_MIN_PART:
            part_size = DEFAULT_PART_SIZE
        total = max(1, -(-size // part_size)) if size else 1

        if on_stage is not None:
            on_stage("upload", path.name, size)

        def part_length(number: int) -> int:
            offset = (number - 1) * part_size
            return max(0, min(part_size, size - offset))

        # 上报 md5/sha1：finish 前服务端必须收到这对值（缺了报 43009），
        # 顺带探测秒传：命中会直接返回 fid，不用传任何分片。
        try:
            finish, instant_fid = self._pds_hash(task_id, md5, sha1)
        except QuarkError:
            finish, instant_fid = False, ""
        if finish:
            if resume is not None:
                resume.clear()
            self._name_cache.pop(str(remote_dir or "").strip("/"), None)
            return {"fid": instant_fid or str(pre.get("fid") or ""), "instant": True,
                    "md5": md5, "size": size,
                    "visible": self._wait_visible(parent_fid, path.name)}

        # 续传：把服务端已收到的分片补进 etags
        if pre.get("_resume_probe", True) and size > 0:
            try:
                server_parts, status = self._pds_list_parts(task_id)
            except QuarkError:
                server_parts, status = {}, 0
            if server_parts:
                for number, etag in server_parts.items():
                    if number <= total and etag:
                        etags.setdefault(number, etag)
                _persist()
            if status == 2:
                if resume is not None:
                    resume.clear()
                self._name_cache.pop(str(remote_dir or "").strip("/"), None)
                return {"fid": str(pre.get("fid") or ""), "instant": False,
                        "md5": md5, "size": size,
                        "visible": self._wait_visible(parent_fid, path.name)}

        sent = sum(part_length(n) for n in etags if 1 <= n <= total)
        if on_progress is not None:
            try:
                on_progress(min(sent, size), size)
            except Exception:  # noqa: BLE001
                pass

        pending = [n for n in range(1, total + 1) if n not in etags]
        upload_id = str(pre.get("upload_id") or "")

        # 第 2 片起必须带真实 SHA1 中间状态（服务端 finish 时用它拼整文件 sha1
        # 与上报值比对）；状态会随断点一起落库，重试时不再重算。
        if any(number > 1 for number in pending) and not states:
            if not _Sha1StateReader.available():
                # 环境缺 libcrypto 时 PDS 走不通，按「接口不可用」降级网页通路
                raise QuarkError(404, "PDS 不可用：缺少 libcrypto，无法计算分片 SHA1")
            if on_stage is not None:
                on_stage("hash", path.name, size)
            states = _Sha1StateReader.states(path, part_size, total, size)
            _persist()

        def _part_body(number: int) -> dict:
            body = {"content_type": "application/json",
                    "part_number": number,
                    "part_size": part_length(number)}
            if number > 1:
                body["parallel_sha1_ctx"] = {
                    "h": states.get(number) or [],
                    "part_offset": (number - 1) * part_size,
                }
            return body

        def _put_one(number: int, url: str, signature: str,
                     headers: dict) -> tuple[int, int, str]:
            """PUT 一片；签名过期就重新申请这一片的地址再试一次。"""
            blob = b""
            with open(path, "rb") as handle:
                handle.seek((number - 1) * part_size)
                blob = handle.read(part_length(number))
            try:
                return number, len(blob), self._pds_put(url, signature, blob, headers)
            except QuarkError as exc:
                if str(exc.code) != "SIGN_EXPIRED":
                    raise
                urls, fresh_headers = self._pds_urls(
                    task_id, upload_id, [_part_body(number)])
                item = urls.get(number) or {}
                if not item.get("url"):
                    raise
                return number, len(blob), self._pds_put(
                    item["url"], item.get("signature") or "", blob, fresh_headers)

        def _report_done(number: int, length: int, etag: str) -> None:
            nonlocal sent
            etags[number] = etag
            sent += length
            _persist()
            if on_progress is not None:
                try:
                    on_progress(min(sent, size), size)
                except Exception:  # noqa: BLE001
                    pass

        # 按 50 片一批申请预签名地址（客户端同样批量），再并发上传这一批；
        # 逐片申请会把 getUploadUrls 打成高频请求，服务端容易限流。
        for start in range(0, len(pending), PDS_URL_BATCH):
            batch = pending[start:start + PDS_URL_BATCH]
            urls, headers = self._pds_urls(
                task_id, upload_id, [_part_body(n) for n in batch])
            jobs: list[tuple[int, str, str, dict]] = []
            for number in batch:
                item = urls.get(number) or {}
                if not item.get("url"):
                    raise QuarkError(-1, f"分片 {number} 未取到上传地址",
                                     "/file/upload/getUploadUrls")
                jobs.append((number, item["url"], item.get("signature") or "", headers))
            lanes = min(self.lanes, len(jobs))
            if lanes > 1:
                with ThreadPoolExecutor(max_workers=lanes) as pool:
                    futures = {pool.submit(_put_one, *job): job[0] for job in jobs}
                    try:
                        for future in as_completed(futures):
                            _report_done(*future.result())
                    except Exception:
                        for future in futures:
                            future.cancel()
                        raise
            else:
                for job in jobs:
                    _report_done(*_put_one(*job))

        # 服务端实测不校验分片是否齐全（只传 1 片也能 finish 成功），
        # 这里自己把关，避免漏片时静默产出损坏文件。
        missing = [n for n in range(1, total + 1) if n not in etags]
        if missing:
            raise QuarkError(-1, f"分片未传齐：缺 {len(missing)}/{total}"
                                 f"（首个缺失 {missing[0]}）", "/file/upload/finish")

        data = self._pds_finish(task_id, etags)
        if resume is not None:
            resume.clear()
        self._name_cache.pop(str(remote_dir or "").strip("/"), None)
        fid = str(data.get("fid") or pre.get("fid") or "")
        return {"fid": fid, "instant": False, "md5": md5, "size": size,
                "visible": self._wait_visible(parent_fid, path.name)}

    def _hash(self, md5: str, sha1: str, task_id: str) -> tuple[bool, str]:
        payload = self._request("POST", "/file/update/hash", body={
            "md5": md5, "sha1": sha1, "task_id": task_id})
        data = payload.get("data") or {}
        return bool(data.get("finish")), str(data.get("fid") or "")

    def _auth_key(self, auth_info: str, auth_meta: str, task_id: str) -> str:
        payload = self._request("POST", "/file/upload/auth", body={
            "auth_info": auth_info, "auth_meta": auth_meta, "task_id": task_id})
        key = str((payload.get("data") or {}).get("auth_key") or "")
        if not key:
            raise QuarkError(-1, "未取到 OSS 上传签名", "/file/upload/auth")
        return key

    def _oss_base(self, pre: dict) -> str:
        upload_url = str(pre.get("upload_url") or "")
        host = upload_url.split("://", 1)[-1]
        return f"https://{pre['bucket']}.{host}"

    def _put_part(self, pre: dict, part_number: int, blob: bytes, mime: str,
                  on_progress=None, sent_before: int = 0, total: int = 0) -> str:
        """上传一个分片。网络抖动/超时重试一次，避免 3 GB 文件因一片失败整份重来。"""
        last_error: Exception | None = None
        for attempt in range(2):
            oss_date = formatdate(time.time(), usegmt=True)
            obj_key = pre["obj_key"]
            auth_meta = (
                f"PUT\n\n{mime}\n{oss_date}\n"
                f"x-oss-date:{oss_date}\n"
                f"x-oss-user-agent:{OSS_USER_AGENT}\n"
                f"/{pre['bucket']}/{obj_key}?partNumber={part_number}&uploadId={pre['upload_id']}"
            )
            try:
                auth_key = self._auth_key(pre["auth_info"], auth_meta, pre["task_id"])
                resp = self.session.put(
                    f"{self._oss_base(pre)}/{obj_key}",
                    params={"partNumber": part_number, "uploadId": pre["upload_id"]},
                    data=blob,
                    headers={
                        "Authorization": auth_key,
                        "Content-Type": mime,
                        "Referer": REFERER,
                        "x-oss-date": oss_date,
                        "x-oss-user-agent": OSS_USER_AGENT,
                    },
                    timeout=PART_TIMEOUT,
                )
                if resp.status_code == 200:
                    if on_progress is not None:
                        try:
                            on_progress(sent_before + len(blob), total)
                        except Exception:  # noqa: BLE001 - 进度回调异常不影响上传
                            pass
                    return resp.headers.get("ETag") or resp.headers.get("Etag") or ""
                last_error = QuarkError(
                    resp.status_code, f"分片 {part_number} 上传失败：{resp.text[:200]}")
                if resp.status_code < 500:
                    raise last_error
            except QuarkError:
                raise
            except (requests.RequestException, OSError) as exc:
                last_error = exc
            if attempt == 0:
                time.sleep(1.5)
        hint = ("（超时：服务器出网带宽不足或连接中断，可把并发降到 2 路后重试）"
                if isinstance(last_error, requests.exceptions.Timeout) else "")
        raise QuarkError(-1, f"分片 {part_number} 重试后仍失败{hint}：{last_error}")

    def _commit(self, pre: dict, etags: list[str]) -> None:
        body_parts = "".join(
            f"<Part>\n<PartNumber>{index}</PartNumber>\n<ETag>{etag}</ETag>\n</Part>\n"
            for index, etag in enumerate(etags, start=1))
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<CompleteMultipartUpload>\n'
               f"{body_parts}</CompleteMultipartUpload>")
        content_md5 = base64.b64encode(hashlib.md5(xml.encode()).digest()).decode()
        callback = pre.get("callback") or {}
        callback_b64 = base64.b64encode(json.dumps(
            callback, ensure_ascii=False, separators=(",", ":")).encode()).decode()
        oss_date = formatdate(time.time(), usegmt=True)
        obj_key = pre["obj_key"]
        auth_meta = (
            f"POST\n{content_md5}\napplication/xml\n{oss_date}\n"
            f"x-oss-callback:{callback_b64}\n"
            f"x-oss-date:{oss_date}\n"
            f"x-oss-user-agent:{OSS_USER_AGENT}\n"
            f"/{pre['bucket']}/{obj_key}?uploadId={pre['upload_id']}"
        )
        auth_key = self._auth_key(pre["auth_info"], auth_meta, pre["task_id"])
        resp = self.session.post(
            f"{self._oss_base(pre)}/{obj_key}",
            params={"uploadId": pre["upload_id"]},
            data=xml.encode(),
            headers={
                "Authorization": auth_key,
                "Content-MD5": content_md5,
                "Content-Type": "application/xml",
                "Referer": REFERER,
                "x-oss-callback": callback_b64,
                "x-oss-date": oss_date,
                "x-oss-user-agent": OSS_USER_AGENT,
            },
            timeout=TIMEOUT * 5,
        )
        if resp.status_code != 200:
            raise QuarkError(resp.status_code, f"分片合并失败：{resp.text[:200]}")

    def _finish(self, pre: dict) -> None:
        self._request("POST", "/file/upload/finish", body={
            "obj_key": pre["obj_key"], "task_id": pre["task_id"]})

    # ==================== 分享 ====================

    def create_share(self, fids, title: str = "", *, expire: str = "永久",
                     passcode: str = "") -> tuple[str, str]:
        """给一个或多个文件/文件夹建分享，返回 (分享地址, 提取码)。

        `fids` 可以是单个 fid 或 fid 列表；走三步：`POST /share` 建任务 →
        `GET /task` 取 share_id → `POST /share/password` 取真实地址。
        `passcode` 留空表示公开分享；有效期见 `SHARE_EXPIRED_TYPE`。
        """
        items = [str(f) for f in ([fids] if isinstance(fids, str) else (fids or [])) if f]
        if not items:
            raise QuarkError(-1, "分享对象为空")
        body = {
            "fid_list": items,
            "title": (title or "备份")[:100],
            "url_type": 2 if passcode else 1,
            "expired_type": SHARE_EXPIRED_TYPE.get(str(expire), 1),
        }
        if passcode:
            body["passcode"] = passcode
        task = (self._request("POST", "/share", body=body).get("data") or {})
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise QuarkError(-1, "创建分享没有返回任务号", "/share")
        share_id = ""
        for attempt in range(10):
            data = self._request("GET", "/task", params={
                "task_id": task_id, "retry_index": attempt}).get("data") or {}
            share_id = str(data.get("share_id") or "")
            if share_id:
                break
            if attempt < 9:
                time.sleep(0.8)
        if not share_id:
            raise QuarkError(-1, "分享任务还没完成，请稍后在夸克网盘里查看", "/task")
        data = self._request("POST", "/share/password", body={"share_id": share_id}).get("data") or {}
        url = str(data.get("share_url") or "")
        code = str(data.get("passcode") or "")
        if not url:
            raise QuarkError(-1, "创建分享没有返回地址", "/share/password")
        return (f"{url}?pwd={code}" if code else url), code

    # ==================== 下载 ====================

    def download_links(self, fids: list[str]) -> dict[str, str]:
        """批量取下载直链：`{fid: url}`。

        直链有分钟级时效，大文件跑到一半就可能 403；调用方拿到 403 要重新
        调一次本接口，再用 `Range` 从断点续传（见 `download.Downloader`）。
        只能传文件 fid，目录会走打包任务，不在本工具范围内。

        单文件超过网页端限额时 `/file/download` 会回 `23018 download file size
        limit`（实测连 SUPER_VIP 也一样），此时改走播放链路拿**原片直链**：
        `/batch/file/play/info` 的 `video_list` 里与文件大小一致的那一项就是原始
        文件（其余是同名 m3u8 转码流），该 CDN 支持 Range 且**不能带 Cookie**。
        """
        fids = [str(f) for f in fids if f]
        if not fids:
            return {}
        links: dict[str, str] = {}
        oversize: list[str] = []
        try:
            payload = self._request("POST", "/file/download", body={"fids": fids})
            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("fid") or "")
                url = str(item.get("download_url") or "")
                if fid and url:
                    links[fid] = url
        except QuarkError as exc:
            if str(exc.code) != "23018":
                raise
            # 整批被拒：逐个重试，把真正超限的文件挑出来走播放链路
            for fid in fids:
                try:
                    payload = self._request("POST", "/file/download", body={"fids": [fid]})
                except QuarkError as one:
                    if str(one.code) == "23018":
                        oversize.append(fid)
                        continue
                    raise
                for item in payload.get("data") or []:
                    url = str((item or {}).get("download_url") or "")
                    if url:
                        links[fid] = url
        if oversize:
            links.update(self._play_links(oversize))
        if not links:
            raise QuarkError(-1, "未取到下载直链", "/file/download")
        return links

    def _play_links(self, fids: list[str]) -> dict[str, str]:
        """播放链路取原片直链（绕过网页端单文件大小限制）。"""
        links: dict[str, str] = {}
        body = {
            "fids": fids,
            "resolutions": "normal,low,high,super,2k,4k",
            "fetch_credits_setting": 1,
            "fetch_play_video_resolution_setting": 1,
            "fetch_play_audio_type_setting": 1,
            "support_resolution_free_limit_ab": 1,
            "fetch_pdir": 1,
            "support_right": "trial_1080P_zhizhen_pc",
        }
        payload = self._request("POST", "/batch/file/play/info",
                                body=body, params={"uc_param_str": "utfrpr"})
        data = payload.get("data") or {}
        for fid, info in data.items():
            if not isinstance(info, dict):
                continue
            size = int(info.get("size") or 0)
            videos = info.get("video_list") or []
            picked = ""
            # 原片的 size 与文件总大小一致；其余是同名转码流（m3u8）
            for item in videos:
                video = (item or {}).get("video_info") or {}
                url = str(video.get("url") or "")
                if not url or "m3u8" in url:
                    continue
                if size and int(video.get("size") or 0) == size:
                    picked = url
                    break
            if not picked:
                for item in videos:
                    video = (item or {}).get("video_info") or {}
                    url = str(video.get("url") or "")
                    if url and "m3u8" not in url:
                        picked = url
                        break
            if picked:
                links[str(fid)] = picked
        if not links:
            raise QuarkError(
                -1, "文件超过网页端下载限额，且不是可播放视频，无法取到原片直链",
                "/batch/file/play/info")
        return links

    def headers_for_download(self, url: str) -> dict:
        """按下载域名决定请求头：播放 CDN 带 Cookie 会 412（实测）。"""
        headers = self.download_headers()
        host = str(url or "").split("://", 1)[-1].split("/", 1)[0]
        if host.startswith("video-") or host.startswith("video."):
            headers.pop("Cookie", None)
        return headers

    def delete_files(self, fids: list[str]) -> bool:
        """把指定 fid 删到回收站（用于替换本工具上传过的旧版本）。

        只接受调用方明确给出的 fid；接口失败返回 False，由上层决定是否继续上传。
        """
        fids = [str(f) for f in fids if f]
        if not fids:
            return True
        try:
            self._request("POST", "/file/delete", body={
                "action_type": 1, "exclude_fids": [], "filelist": fids})
            return True
        except QuarkError:
            return False

    def _wait_visible(self, parent_fid: str, name: str, timeout: float = 8.0) -> bool:
        """finish 之后服务端索引是异步的：轮询直到文件出现在目录里（尽力而为）。"""
        deadline = time.time() + timeout
        while True:
            try:
                for item in self.list_dir(parent_fid):
                    if item.get("file_name") == name and not item.get("dir"):
                        return True
            except Exception:  # noqa: BLE001 - 可见性校验失败不影响上传结论
                pass
            if time.time() >= deadline:
                return False
            time.sleep(0.8)

    def upload_file(self, local_path, remote_dir: str, on_progress=None,
                    on_stage=None, hashes: tuple[str, str] | None = None,
                    resume=None, _clean_retry: bool = False) -> dict:
        """上传一个文件到 `remote_dir`（相对路径，自动建目录）。

        - `on_stage("hash" | "upload", 文件名, 字节数)`：大文件算校验、开始传输时给界面反馈
        - `hashes=(md5, sha1)`：上层已算过就直接复用，避免 3 GB 视频重复读盘
        - `resume`：`state.ResumeHandle`，分片进度落库；中断后下次从断点续传

        返回 `{"fid", "instant", "md5", "size"}`；秒传时 instant=True。
        """
        path = Path(local_path)
        stat = path.stat()
        size = stat.st_size
        if hashes:
            md5, sha1 = hashes
        else:
            if on_stage is not None:
                on_stage("hash", path.name, size)
            md5, sha1 = file_hashes(path)
        parent_fid = self.ensure_dir(remote_dir)

        saved = resume.load() if resume is not None else {}

        # 客户端 PDS 通路优先：批量预签名 + 断点续传，比网页五步少往返。
        # 旧票据（无 kind）留在旧通路续传，避免中途换协议；
        # PDS 一旦明确不可用，本实例后续文件不再尝试，避免每个文件都多一次失败。
        if self._pds_enabled and not (saved and saved.get("kind") != "pds"):
            try:
                return self._upload_pds(path, parent_fid, size, md5, sha1,
                                        remote_dir, on_progress, on_stage, resume)
            except QuarkError as exc:
                if exc.is_auth:
                    raise
                # 只有「接口不存在 / 参数不被接受」才判定 PDS 不可用并降级；
                # 网络抖动、超时、5xx 保留断点原样抛出，交给引擎重试同一条通路，
                # 降级到网页五步反而更慢更容易再失败。
                if str(exc.code) in ("404", "400"):
                    self._pds_enabled = False
                    self._pds_last_error = str(exc)
                # 票据过期 / 上下文对不上（上次失败留下的脏断点）：清掉重来一次，
                # 否则每次重试都会在同一张废票上报同样的错。
                elif str(exc.code) in ("14004", "43001", "43008", "43009") and not _clean_retry:
                    self._pds_last_error = str(exc)
                    if resume is not None:
                        resume.clear()
                    return self.upload_file(
                        local_path, remote_dir, on_progress=on_progress,
                        on_stage=on_stage, hashes=(md5, sha1), resume=resume,
                        _clean_retry=True)
                else:
                    raise

        pre: dict = {}
        etags: dict[int, str] = {}
        if (saved.get("md5") == md5 and int(saved.get("size") or -1) == size
                and isinstance(saved.get("pre"), dict) and saved["pre"].get("obj_key")):
            pre = dict(saved["pre"])
            etags = {int(k): v for k, v in (saved.get("etags") or {}).items()}
        used_resume = bool(pre)

        def _persist() -> None:
            if resume is None:
                return
            resume.save({"md5": md5, "size": size, "pre": pre,
                         "etags": {str(k): v for k, v in etags.items()}})

        try:
            if not pre:
                pre = self._pre(path, parent_fid, md5, sha1)
                finish, fid = self._hash(md5, sha1, pre["task_id"])
                if finish:
                    if resume is not None:
                        resume.clear()
                    self._name_cache.pop(str(remote_dir or "").strip("/"), None)
                    return {"fid": fid or str(pre.get("fid") or ""), "instant": True,
                            "md5": md5, "size": size,
                            "visible": self._wait_visible(parent_fid, path.name)}
                _persist()

            part_size = max(1024 * 1024, int(pre.get("_part_size") or DEFAULT_PART_SIZE))
            total_parts = max(1, -(-size // part_size))
            mime = _mime_of(path)
            if on_stage is not None:
                on_stage("upload", path.name, size)
            def part_length(number: int) -> int:
                offset = (number - 1) * part_size
                return max(0, min(part_size, size - offset))

            sent = sum(part_length(number) for number in etags)

            def report(done_bytes: int) -> None:
                if on_progress is None:
                    return
                try:
                    on_progress(min(done_bytes, size), size)
                except Exception:  # noqa: BLE001
                    pass

            report(sent)

            def upload_part(number: int) -> tuple[int, int, str]:
                with open(path, "rb") as handle:
                    handle.seek((number - 1) * part_size)
                    blob = handle.read(part_length(number))
                etag = self._put_part(pre, number, blob, mime)
                return number, len(blob), etag

            pending = [n for n in range(1, total_parts + 1) if n not in etags]
            lanes = min(self.lanes, len(pending)) if pending else 0
            if lanes > 1:
                with ThreadPoolExecutor(max_workers=lanes) as pool:
                    futures = {pool.submit(upload_part, n): n for n in pending}
                    try:
                        for future in as_completed(futures):
                            number, length, etag = future.result()
                            etags[number] = etag
                            sent += length
                            _persist()
                            report(sent)
                    except Exception:
                        for future in futures:
                            future.cancel()
                        raise
            else:
                for number in pending:
                    done_number, length, etag = upload_part(number)
                    etags[done_number] = etag
                    sent += length
                    _persist()
                    report(sent)
            if etags:
                self._commit(pre, [etags[n] for n in sorted(etags)])
            self._finish(pre)
            if resume is not None:
                resume.clear()
            self._name_cache.pop(str(remote_dir or "").strip("/"), None)
            return {"fid": str(pre.get("fid") or ""), "instant": False,
                    "md5": md5, "size": size,
                    "visible": self._wait_visible(parent_fid, path.name)}
        except Exception:
            # 续传票据过期等：丢掉断点，从头再来一次（只重试一次）
            if used_resume and resume is not None:
                resume.clear()
                return self.upload_file(local_path, remote_dir, on_progress=on_progress,
                                        on_stage=on_stage, hashes=(md5, sha1), resume=None)
            raise
