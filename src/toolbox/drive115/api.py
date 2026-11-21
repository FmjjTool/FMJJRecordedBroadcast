# -*- coding: utf-8 -*-
"""115 网盘 Web API 客户端（Cookie 直连，纯 Python）。

与另外三个网盘后端共用同一组引擎接口：
`list_entries` / `list_index` / `ensure_dir` / `delete_files` / `digest` /
`download_links` / `download_headers` / `session`。

能力矩阵（2026-11 核对）：

| 能力 | 状态 | 接口 |
| --- | --- | --- |
| 登录态 / 昵称 | 支持 | `GET my.115.com/?ct=ajax&ac=nav` |
| 浏览目录（分页） | 支持 | `GET webapi.115.com/files` |
| 新建目录 | 支持 | `POST webapi.115.com/files/add` |
| 下载直链 | 支持 | `GET webapi.115.com/files/download?pickcode=` |
| 删除到回收站 | 支持 | `POST webapi.115.com/rb/delete`（`fid[i]` + `pid[i]` 成对） |
| 上传（备份） | **未接入** | 115 网页上传要 `uplb.115.com` 初始调度 + 阿里云 OSS 直传 + 回调三步 |
| 生成分享 | 未接入 | 115 分享另有一套签名，暂不做 |

已知限制：

- 下载直链与请求 UA 绑定，`download_headers()` 必须与申请直链时用同一个 UA；
- 115 的下载 CDN 对 `Origin` / UA 挑剔：请求头里不发 `Origin`，首次取链后用
  「Range: bytes=0-0」探活；被 401/403 就换 `115Browser` UA 重新申请一次，
  仍然失败时把 CDN 返回的状态码与响应片段带进错误（不再只报「直链失效」）；
- 下载还需要 Cookie 里的 `KID`（登录页会种下）；缺 KID 时探活失败会在错误里点明；
- 删除要 `fid + pid` 成对提交，pid 取自列目录时的缓存（没列过的 fid 退回根目录，
  115 会拒绝，返回 False 而不是静默成功）；
- 非会员限速是服务端策略，本客户端不做任何绕过；
- 空文件（0 字节）在 115 侧可能被合并/忽略，备份引擎按普通文件处理。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests

API_BASE = "https://webapi.115.com"
NAV_URL = "https://my.115.com/?ct=ajax&ac=nav"
REFERER = "https://115.com/"
ROOT_ID = "0"
PAGE_SIZE = 1150
TIMEOUT = 60
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# 115 的下载 CDN 对 UA 有偏好：普通 Chrome UA 被 403 时，用带 115Browser 的 UA
# 重新申请一次直链（UA 与直链绑定，换 UA 必须连申请一起换）
FALLBACK_UA = DEFAULT_UA + " 115Browser/27.0.3.7"

_AUTH_HINTS = ("登录", "登入", "授权", "认证", "token", "sign")


class Drive115Error(RuntimeError):
    """接口返回 state=false，或网络 / 鉴权失败。"""

    def __init__(self, code, message: str = "", path: str = ""):
        self.code = code
        self.message = message or "未知错误"
        self.path = path
        super().__init__(f"[{code}] {self.message}")

    @property
    def is_auth(self) -> bool:
        text = str(self.message).lower()
        return any(hint in text for hint in _AUTH_HINTS)


def file_hashes(path) -> tuple[str, str]:
    """一次读完算 md5 + sha1（引擎判内容用 md5，115 上传口径是 sha1）。"""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            md5.update(chunk)
            sha1.update(chunk)
    return md5.hexdigest(), sha1.hexdigest()


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Drive115Client:
    """一个登录态一个实例；目录 id 缓存按云端路径。

    115 用字符串 `cid` 表示目录（根为 `"0"`），文件 / 目录的 `fid` 是各自 id，
    文件另带 `pc`（pickcode）用于取直链。
    """

    # 备份引擎据此判断能否走上传；115 上传还没接入
    supports_upload = False

    def __init__(self, cookie: str, user_agent: str = DEFAULT_UA):
        cookie = (cookie or "").strip()
        if not cookie:
            raise Drive115Error(-1, "115 网盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({
            "Cookie": self.cookie,
            "User-Agent": self.user_agent,
            "Referer": REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            # 故意不设 Origin：115 的下载 CDN 对带 Origin 的 GET 会按跨域请求处理并 403
        })
        self._dir_cache: dict[str, str] = {"": ROOT_ID, "/": ROOT_ID}
        self._pid_of: dict[str, str] = {}
        self._index_cache: dict[str, dict] = {}
        self._digest_cache: dict[tuple, tuple[str, str]] = {}
        self._user_id = ""
        # 直链探活只做一次：记录最终可用的 UA / 是否带 Cookie
        self._download_checked = False
        self._download_mode = "cookie"
        # 探活四种组合全被拒后缓存原因：同一批后续文件直接失败，不再各等 6 次重试
        self._download_blocked = ""
        self.has_kid = "KID=" in self.cookie

    # ==================== 基础 ====================

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = kwargs.pop("url", None) or f"{API_BASE}{path}"
        try:
            resp = self.session.request(method, url, timeout=TIMEOUT, **kwargs)
        except (requests.RequestException, OSError) as exc:
            raise Drive115Error(-1, f"网络错误：{exc}", path)
        if resp.status_code in (401, 403):
            raise Drive115Error(resp.status_code, "登录已失效，请重新登录 115", path)
        try:
            payload = resp.json()
        except ValueError:
            raise Drive115Error(-1, f"接口返回非 JSON（HTTP {resp.status_code}）", path)
        if not isinstance(payload, dict):
            raise Drive115Error(-1, "接口返回结构异常", path)
        if payload.get("state") is False:
            code = payload.get("errno")
            if code in (None, 0, "0"):
                code = payload.get("code") or -1
            message = (payload.get("error") or payload.get("msg")
                       or payload.get("message") or "接口返回失败")
            raise Drive115Error(code, str(message), path)
        return payload

    def user_info(self) -> dict:
        """当前登录账号（`uname` / `uid`）；未登录由调用方按异常处理。"""
        payload = self._request("GET", "", url=NAV_URL)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    @property
    def user_id(self) -> str:
        if self._user_id:
            return self._user_id
        data = self.user_info()
        self._user_id = str(data.get("uid") or data.get("user_id") or "")
        return self._user_id

    # ==================== 目录 ====================

    def list_dir(self, cid: str = ROOT_ID) -> list[dict]:
        """列一页或多页目录（115 单页上限 1150，超过要翻页）。"""
        cid = str(cid or ROOT_ID)
        items: list[dict] = []
        offset = 0
        while True:
            payload = self._request("GET", "/files", params={
                "aid": 1, "cid": cid, "o": "user_ptime", "asc": 0,
                "offset": offset, "show_dir": 1, "limit": PAGE_SIZE,
                "fc_mix": 0,
            })
            batch = payload.get("data")
            batch = batch if isinstance(batch, list) else []
            items.extend(batch)
            total = _int(payload.get("count"))
            offset += len(batch)
            if not batch or len(batch) < PAGE_SIZE or (total and offset >= total):
                break
        return items

    @staticmethod
    def _is_dir(raw: dict) -> bool:
        """`fc == "0"` 是 115 的目录标记，`ico == "folder"` 是旧版口径。"""
        return str(raw.get("fc")) == "0" or str(raw.get("ico") or "") == "folder"

    def _entry(self, raw: dict, parent: str) -> dict:
        is_dir = self._is_dir(raw)
        fid = str(raw.get("fid") or raw.get("cid") or "")
        if fid:
            self._pid_of[fid] = str(parent or ROOT_ID)
        size = 0 if is_dir else _int(raw.get("s"))
        return {
            "file_name": str(raw.get("n") or raw.get("fn") or ""),
            "fid": fid,
            # 直链按 pickcode 申请（下载页的 download_id 语义）
            "download_id": str(raw.get("pc") or ""),
            "dir": is_dir,
            "size": size,
            "updated_at": raw.get("t") or raw.get("u") or "",
        }

    def list_entries(self, cid: str = ROOT_ID) -> list[dict]:
        """下载页使用的统一结构；目录的 `fid` 直接就是子目录 cid。"""
        cid = str(cid or ROOT_ID)
        return [self._entry(raw, cid) for raw in self.list_dir(cid)]

    def ensure_dir(self, remote_dir: str) -> str:
        """逐级建目录，返回最终 cid；已存在则复用同名目录。"""
        remote_dir = (remote_dir or "").strip("/")
        if not remote_dir:
            return ROOT_ID
        if remote_dir in self._dir_cache:
            return self._dir_cache[remote_dir]
        cid = ROOT_ID
        walked = ""
        for part in [p for p in remote_dir.split("/") if p.strip()]:
            walked = f"{walked}/{part}" if walked else part
            if walked in self._dir_cache:
                cid = self._dir_cache[walked]
                continue
            existing = ""
            for entry in self.list_entries(cid):
                if entry["dir"] and entry["file_name"] == part:
                    existing = entry["fid"]
                    break
            if existing:
                cid = existing
            else:
                payload = self._request("POST", "/files/add",
                                        data={"pid": cid, "cname": part})
                data = payload.get("data")
                nested = data if isinstance(data, dict) else {}
                new_id = str(payload.get("cid") or nested.get("cid")
                             or nested.get("file_id") or "")
                if not new_id:
                    raise Drive115Error(-1, f"创建目录失败：{part}", "/files/add")
                cid = new_id
            self._dir_cache[walked] = cid
        return cid

    def list_index(self, remote_dir: str) -> dict[str, dict]:
        """`文件名 → {fid, size}`；目录不存在时按空目录处理。"""
        remote_dir = (remote_dir or "").strip("/")
        if remote_dir in self._index_cache:
            return self._index_cache[remote_dir]
        index: dict[str, dict] = {}
        try:
            cid = self.ensure_dir(remote_dir)
            for entry in self.list_entries(cid):
                if entry["dir"]:
                    continue
                index[entry["file_name"]] = {
                    "fid": entry["fid"], "size": entry["size"]}
        except Drive115Error:
            index = {}
        self._index_cache[remote_dir] = index
        return index

    def delete_files(self, fids: list[str]) -> bool:
        """按 `fid + pid` 删到回收站；没有 pid 的 fid 退回根目录（多半会失败）。"""
        ids = [str(f) for f in fids if f]
        if not ids:
            return True
        data: dict[str, str] = {}
        for i, fid in enumerate(ids):
            data[f"fid[{i}]"] = fid
            data[f"pid[{i}]"] = self._pid_of.get(fid, ROOT_ID)
        try:
            self._request("POST", "/rb/delete", data=data)
            return True
        except Drive115Error:
            return False

    # ==================== 下载 ====================

    def _fetch_link(self, pick: str) -> str:
        """按 pickcode 申请一次直链。"""
        payload = self._request("GET", "/files/download", params={
            "pickcode": pick, "_": int(time.time() * 1000)})
        url = str(payload.get("file_url") or "")
        if not url:
            data = payload.get("data")
            nested = data if isinstance(data, dict) else {}
            url = str(nested.get("file_url") or nested.get("url") or "")
        if not url:
            raise Drive115Error(-1, "未取到下载直链", "/files/download")
        return url

    def _headers_for(self, mode: str, ua: str) -> dict:
        """下载头；`mode` 决定带不带登录 Cookie（部分 CDN 签名链接只认裸请求）。"""
        headers = {
            "User-Agent": ua,
            "Referer": REFERER,
            "Accept": "*/*",
        }
        if mode == "cookie":
            headers["Cookie"] = self.cookie
        return headers

    def _probe_link(self, url: str, mode: str, ua: str) -> tuple[int, str]:
        """按给定时序/头试下载同一个直链，返回 (状态码, 响应片段)。

        与下载引擎第一次请求完全一致（普通 GET、不带 Range），所以探活通过
        就说明引擎马上要发的那个请求也能过。状态码 0 表示网络异常。
        """
        headers = self._headers_for(mode, ua)
        try:
            resp = self.session.get(url, headers=headers, stream=True,
                                    allow_redirects=True, timeout=(15, 30))
        except (requests.RequestException, OSError) as exc:
            return 0, f"探活未完成（{exc}）"
        try:
            if resp.status_code in (200, 206):
                return resp.status_code, ""
            try:
                body = resp.raw.read(200, decode_content=True) or b""
                text = body.decode("utf-8", "replace").strip()
            except Exception:  # noqa: BLE001 - 读取错误体失败不影响判定
                text = ""
            return resp.status_code, text[:180]
        finally:
            resp.close()

    def download_links(self, pickcodes: list[str]) -> dict[str, str]:
        """按 pickcode 取直链；返回 `{pickcode: url}`。

        115 的 CDN 会按 UA / Cookie / Referer 组合拒绝（401/403）。首个文件把
        「普通 UA / 115Browser UA」×「带 Cookie / 不带 Cookie」四种组合各试一次，
        命中哪种就按哪种下载；四种都不过时**直接报错并停止重试**，错误里带
        CDN 的状态码与原文，避免日志只剩「直链失效」看不出原因。
        """
        from toolbox.quark.download import DownloadError

        if self._download_blocked:
            raise DownloadError(self._download_blocked)

        links: dict[str, str] = {}
        for pick in [str(p) for p in pickcodes if p]:
            links[pick] = self._fetch_link(pick)
        if not links:
            raise Drive115Error(-1, "未取到下载直链", "/files/download")

        if self._download_checked:
            return links

        self._download_checked = True
        first = next(iter(links))
        saved_ua = self.user_agent
        attempts: list[str] = []
        for ua in (saved_ua, FALLBACK_UA):
            label = "115Browser UA" if ua == FALLBACK_UA else "Chrome UA"
            if ua != saved_ua:
                # 直链与 UA 绑定：换 UA 必须重新申请，不能只换下载头
                self.user_agent = ua
                self.session.headers["User-Agent"] = ua
                links[first] = self._fetch_link(first)
            for mode in ("cookie", "nocookie"):
                how = "带Cookie" if mode == "cookie" else "不带Cookie"
                code, detail = self._probe_link(links[first], mode, ua)
                if code == 0:
                    # 网络异常不当作 CDN 拒绝，交给下载引擎重试
                    return links
                if code in (200, 206):
                    self._download_mode = mode
                    return links
                attempts.append(f"{label}/{how}：HTTP {code} {detail}".strip())

        self.user_agent = saved_ua
        self.session.headers["User-Agent"] = saved_ua
        hint = ("" if self.has_kid else
                "\n当前登录里没有 KID，可在 115 页面重新登录一次再试")
        host = ""
        try:
            from urllib.parse import urlsplit
            host = urlsplit(links[first]).netloc
        except ValueError:
            pass
        message = ("115 拒绝下载直链（四种组合都被拒，已停止重试）：\n  "
                   + "\n  ".join(attempts)
                   + (f"\n直链主机：{host}" if host else "")
                   + hint)
        self._download_blocked = message
        raise DownloadError(message)

    # ==================== 上传（待接入） ====================

    def download_headers(self) -> dict:
        """直链下载头；UA 必须与申请直链时一致，Cookie 按探活结论带或不带。"""
        return self._headers_for(self._download_mode, self.user_agent)

    def digest(self, path) -> tuple[str, str]:
        p = Path(path)
        stat = p.stat()
        key = (str(p), stat.st_size, stat.st_mtime)
        if key not in self._digest_cache:
            self._digest_cache[key] = file_hashes(p)
        return self._digest_cache[key]

    def upload_file(self, local_path, remote_dir: str, on_progress=None,
                    on_stage=None, hashes=None, resume=None) -> dict:
        """115 上传未接入：明确报错，不返回假成功。"""
        raise Drive115Error(
            -1, "115 网盘的上传还没接入（当前只支持浏览与下载）", "/upload")