# -*- coding: utf-8 -*-
"""夸克网盘 Web API 客户端（Cookie 直连，纯 Python）。

接口顺序与 alist 的 `drivers/quark_uc` 对齐，共五步：

1. `POST /file/upload/pre`      申请上传任务，拿 task_id / obj_key / bucket / auth_info / part_size
2. `POST /file/update/hash`     交 md5 + sha1，命中即「秒传」（返回 finish=true 直接完成）
3. `POST /file/upload/auth`     取 OSS 分片签名，再 `PUT` 到 {bucket}.{upload_url} 上传分片
4. `POST /file/upload/auth`     取 CompleteMultipartUpload 签名，POST XML 合并分片
5. `POST /file/upload/finish`   通知服务端收尾

约定：
- Cookie 只在内存里用；响应里服务端刷新的 `__puus` / `__pus` 只更新内存副本，
  **绝不回写** Cookie 管理的存储（项目约定 7）。
- `pre` 不自动重试：响应丢失时任务可能已分配，重试会重复占用任务。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
from email.utils import formatdate
from pathlib import Path

import requests

API_BASE = "https://drive-pc.quark.cn/1/clouddrive"
REFERER = "https://pan.quark.cn/"
# 与 alist 一致的 OSS 客户端标识（服务端会校验 auth_meta 里的这段）
OSS_USER_AGENT = "aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
DEFAULT_PART_SIZE = 4 * 1024 * 1024
TIMEOUT = 60
# 单个分片（默认 4 MB）的传输超时：太长会「假死」几分钟没反馈，太短会误杀慢速网络
PART_TIMEOUT = 150

_AUTH_HINTS = ("未登录", "登录", "token", "授权", "认证")


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

    def __init__(self, cookie: str, user_agent: str = DEFAULT_UA):
        cookie = (cookie or "").strip()
        if not cookie:
            raise QuarkError(-1, "夸克网盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
        self.session = requests.Session()
        self._dir_cache: dict[str, str] = {"": "0", "/": "0"}
        self._name_cache: dict[str, dict[str, int]] = {}
        # (路径, 大小, mtime) → (md5, sha1)：引擎判内容与上传前算校验只读一遍盘
        self._digest_cache: dict[tuple, tuple[str, str]] = {}

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
        raise QuarkError(-1, f"分片 {part_number} 重试后仍失败：{last_error}")

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

    # ==================== 下载 ====================

    def download_links(self, fids: list[str]) -> dict[str, str]:
        """批量取下载直链：`{fid: url}`。

        直链有分钟级时效，大文件跑到一半就可能 403；调用方拿到 403 要重新
        调一次本接口，再用 `Range` 从断点续传（见 `download.Downloader`）。
        只能传文件 fid，目录会走打包任务，不在本工具范围内。
        """
        fids = [str(f) for f in fids if f]
        if not fids:
            return {}
        payload = self._request("POST", "/file/download", body={"fids": fids})
        links: dict[str, str] = {}
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("fid") or "")
            url = str(item.get("download_url") or "")
            if fid and url:
                links[fid] = url
        if not links:
            raise QuarkError(-1, "未取到下载直链", "/file/download")
        return links

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
                    resume=None) -> dict:
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
            sent = 0
            with open(path, "rb") as handle:
                for number in range(1, total_parts + 1):
                    offset = (number - 1) * part_size
                    handle.seek(offset)
                    blob = handle.read(min(part_size, size - offset))
                    if number in etags:
                        sent += len(blob)
                        if on_progress is not None:
                            try:
                                on_progress(min(sent, size), size)
                            except Exception:  # noqa: BLE001
                                pass
                        continue
                    etag = self._put_part(pre, number, blob, mime,
                                          on_progress=on_progress,
                                          sent_before=sent, total=size)
                    etags[number] = etag
                    sent += len(blob)
                    _persist()
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
