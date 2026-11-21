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

- 下载直链与请求 UA 绑定，下载时必须用申请时同一个 UA；
- 115 的下载 CDN（`cdnfhnfile.115.com`）常见拒绝是 `no cookie value`：
  账号 Cookie 要放在会话罐里（而不是写死 `Cookie` 头），这样取链响应下发的
  新 Cookie 才能一起带上；首个文件探活确认可用组合，被拒时把 CDN 原文带进错误；
- 请求头不发 `Origin`（CDN 会按跨域请求 403）；
- 下载还依赖 Cookie 里的 `KID`（登录页会种下）；缺 KID 时错误里会点明；
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

    @staticmethod
    def _parse_cookie(cookie: str) -> dict[str, str]:
        jar: dict[str, str] = {}
        for part in (cookie or "").split(";"):
            if "=" in part:
                key, _, value = part.partition("=")
                key = key.strip()
                if key:
                    jar[key] = value.strip()
        return jar

    def __init__(self, cookie: str, user_agent: str = DEFAULT_UA):
        cookie = (cookie or "").strip()
        if not cookie:
            raise Drive115Error(-1, "115 网盘未登录")
        self.cookie = cookie
        self.user_agent = user_agent
        self.session = requests.Session()
        # 账号 Cookie 放进会话罐，而不是写死 Cookie 头：
        # ① 取链接口若下发新 Cookie，罐里能自动带上；
        # ② 显式 Cookie 头会覆盖罐内容，115 的 cdnfhnfile 节点正是靠这条判定
        #    「no cookie value」→ 403。
        self._account_cookies = self._parse_cookie(cookie)
        self.session.cookies.update(self._account_cookies)
        self.session.headers.update({
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
        # 直链探活只做一次：记录最终可用的 UA / Cookie 方式
        self._download_checked = False
        self._download_ua = ""
        self._download_mode = "jar"      # jar / meta
        self._download_cookie = ""
        # 探活全部被拒后缓存原因：同一批后续文件直接失败，不再各等 6 次重试
        self._download_blocked = ""
        self.has_kid = "KID" in self._account_cookies

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

    def _fetch_download(self, pick: str) -> tuple[str, dict]:
        """按 pickcode 申请直链，连同响应里可能下发的下载头字段一起返回。

        115 的 `/files/download` 除 `file_url` 外还可能给 `user_agent` /
        `cookie`（CDN 认的是这一份，不是账号 Cookie），先原样收着备用。
        """
        payload = self._request("GET", "/files/download", params={
            "pickcode": pick, "_": int(time.time() * 1000)})
        data = payload.get("data")
        nested = data if isinstance(data, dict) else {}
        url = str(payload.get("file_url") or nested.get("file_url")
                  or nested.get("url") or "")
        if not url:
            raise Drive115Error(-1, "未取到下载直链", "/files/download")
        meta: dict = {"keys": sorted(set(payload) | set(nested))}
        for source in (payload, nested):
            for key in ("user_agent", "cookie", "downcookie"):
                value = source.get(key)
                if isinstance(value, str) and value and key not in meta:
                    meta[key] = value
        return url, meta

    def _probe_link(self, url: str, ua: str, cookie: str | None) -> tuple[int, str]:
        """试下载同一个直链，返回 (状态码, 响应片段)。

        `cookie=None` 表示不写 Cookie 头、交给会话罐（账号 Cookie + 取链时
        服务端新下的 Cookie）；传字符串则显式用这份 Cookie。
        与下载引擎第一次请求完全一致（普通 GET、不带 Range），探活通过就说明
        引擎马上要发的那个请求也能过。状态码 0 表示网络异常。
        """
        headers = {"User-Agent": ua, "Referer": REFERER, "Accept": "*/*"}
        if cookie is not None:
            headers["Cookie"] = cookie
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

        115 的 CDN 对下载请求的 Cookie / UA 很挑（典型拒绝是
        `{"message":"no cookie value"}`）。首个文件按顺序探活：

        1. 会话罐（账号 Cookie，自动并入取链响应下发的 Cookie）
        2. 响应里显式下发的 cookie（若有该字段）
        3. 换 `115Browser` UA 重新申请后再试 1 / 2

        命中哪种就按哪种下载；全部被拒时**直接报错并停止重试**，
        错误里带每次的状态码、CDN 原文、直链主机和取链响应字段名。
        """
        from toolbox.quark.download import DownloadError

        if self._download_blocked:
            raise DownloadError(self._download_blocked)

        links: dict[str, str] = {}
        metas: dict[str, dict] = {}
        for pick in [str(p) for p in pickcodes if p]:
            links[pick], metas[pick] = self._fetch_download(pick)
        if not links:
            raise Drive115Error(-1, "未取到下载直链", "/files/download")

        if self._download_checked:
            return links

        self._download_checked = True
        first = next(iter(links))
        meta = metas[first]
        # 直链可能绑定响应里下发的 UA，优先用它
        ua = str(meta.get("user_agent") or self.user_agent)
        meta_cookie = str(meta.get("cookie") or meta.get("downcookie") or "")
        attempts: list[str] = []

        plans: list[tuple[str, str, str | None]] = [("会话罐", ua, None)]
        if meta_cookie:
            plans.append(("响应下发 cookie", ua, meta_cookie))
        for label, probe_ua, cookie in plans:
            code, detail = self._probe_link(links[first], probe_ua, cookie)
            if code == 0:
                # 网络异常不当作 CDN 拒绝，交给下载引擎重试
                return links
            if code in (200, 206):
                self._download_ua = probe_ua
                self._download_mode = "meta" if cookie is not None else "jar"
                self._download_cookie = cookie or ""
                return links
            attempts.append(f"{label}：HTTP {code} {detail}".strip())

        saved_ua = self.user_agent
        self.user_agent = FALLBACK_UA
        self.session.headers["User-Agent"] = FALLBACK_UA
        links[first], meta = self._fetch_download(first)
        meta_cookie = str(meta.get("cookie") or meta.get("downcookie") or "")
        retry_plans: list[tuple[str, str, str | None]] = [
            ("115Browser UA + 会话罐", FALLBACK_UA, None)]
        if meta_cookie:
            retry_plans.append(("115Browser UA + 响应 cookie", FALLBACK_UA, meta_cookie))
        for label, probe_ua, cookie in retry_plans:
            code, detail = self._probe_link(links[first], probe_ua, cookie)
            if code == 0:
                return links
            if code in (200, 206):
                self._download_ua = probe_ua
                self._download_mode = "meta" if cookie is not None else "jar"
                self._download_cookie = cookie or ""
                return links
            attempts.append(f"{label}：HTTP {code} {detail}".strip())

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
        keys = "、".join(meta.get("keys") or []) or "（无）"
        try:
            extra = len(self.session.cookies) - len(self._account_cookies)
        except Exception:  # noqa: BLE001 - 诊断信息拿不到不影响报错
            extra = 0
        message = ("115 拒绝下载直链（已停止重试）：\n  "
                   + "\n  ".join(attempts)
                   + (f"\n直链主机：{host}" if host else "")
                   + f"\n取链响应字段：{keys}"
                   + (f"\n会话罐新增 Cookie：{extra} 个" if extra > 0 else "")
                   + hint)
        self._download_blocked = message
        raise DownloadError(message)

    # ==================== 上传（待接入） ====================

    def download_headers(self) -> dict:
        """直链下载头；UA 必须与申请直链时一致。

        会话罐模式不写 Cookie 头，让 requests 用罐里的 Cookie（含取链时服务端
        新下的那份）；只有响应明确下发了 cookie 才显式带上。
        """
        headers = {"User-Agent": self._download_ua or self.user_agent,
                   "Referer": REFERER, "Accept": "*/*"}
        if self._download_mode == "meta" and self._download_cookie:
            headers["Cookie"] = self._download_cookie
        return headers

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