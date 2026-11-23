# -*- coding: utf-8 -*-
"""115 网盘 Web API 客户端（Cookie 直连，纯 Python）。

与另外三个网盘后端共用同一组引擎接口：
`list_entries` / `list_index` / `ensure_dir` / `delete_files` / `digest` /
`upload_file` / `download_links` / `download_headers` / `session`。

能力矩阵（2026-11 核对，上传协议对齐 115 官方客户端 27.0.5.7）：

| 能力 | 状态 | 接口 |
| --- | --- | --- |
| 登录态 / 昵称 | 支持 | `GET my.115.com/?ct=ajax&ac=nav` |
| 浏览目录（分页） | 支持 | `GET webapi.115.com/files` |
| 新建目录 | 支持 | `POST webapi.115.com/files/add` |
| 下载直链 | 支持 | `GET webapi.115.com/files/download?pickcode=` |
| 上传（秒传 + OSS 直传） | 支持 | `proapi.115.com/app/uploadinfo` → `uplb.115.com/4.0/initupload.php`（EC115 加密）→ 阿里云 OSS `PUT` |
| 删除到回收站 | 支持 | `POST webapi.115.com/rb/delete`（`fid[i]` + `pid[i]` 成对） |
| 生成分享 | 未接入 | 115 分享另有一套签名，暂不做 |

上传协议要点（来自 115 官方客户端行为，2026-11 复核）：

- `POST proapi.115.com/app/uploadinfo` → `user_id` + `userkey`（签名用）；
- 初始调度 `POST uplb.115.com/4.0/initupload.php?k_ec=<token>`，请求体是
  **EC115 加密后的表单**（P-224 ECDH + AES-CBC），响应体同样加密后回；
- 表单含 `fileid`（整文件 SHA1 大写）、`target=U_1_<目录cid>`、`sig`、`token`、
  `t`（毫秒时间戳）、`topupload=true`；需要二次校验时带 `sign_key` + `sign_val`；
- `status=2` 是秒传成功；否则拿 `bucket / object / callback` 走阿里云 OSS
  `PUT`（回调里完成入库），令牌来自 `GET uplb.115.com/3.0/gettoken.php`。

已知限制：

- 下载直链与请求 UA 绑定，下载时必须用申请时同一个 UA；
- 115 的下载 CDN（`cdnfhnfile.115.com`）常见拒绝是 `no cookie value`：
  账号 Cookie 要放在会话罐里（而不是写死 `Cookie` 头），这样取链响应下发的
  新 Cookie 才能一起带上；首个文件探活确认可用组合，被拒时把 CDN 原文带进错误；
- 请求头不发 `Origin`（CDN 会按跨域请求 403）；
- 下载还依赖 Cookie 里的 `KID`（登录页会种下）；缺 KID 时错误里会点明；
- 上传走单次 OSS `PUT`，单文件上限 5 GB；更大的文件会明确报错（未做 OSS 分片）；
- 删除要 `fid + pid` 成对提交，pid 取自列目录时的缓存（没列过的 fid 退回根目录，
  115 会拒绝，返回 False 而不是静默成功）；
- 非会员限速是服务端策略，本客户端不做任何绕过。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import zlib
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

API_BASE = "https://webapi.115.com"
NAV_URL = "https://my.115.com/?ct=ajax&ac=nav"
UPLOAD_INFO_URL = "https://proapi.115.com/app/uploadinfo"
UPLOAD_INIT_URL = "https://uplb.115.com/4.0/initupload.php"
OSS_TOKEN_URL = "https://uplb.115.com/3.0/gettoken.php"
APP_VERSION_URL = "https://appversion.115.com/1/web/1.0/api/chrome"
OSS_ENDPOINT = "cn-shenzhen.oss.aliyuncs.com"
OSS_USER_AGENT = "aliyun-sdk-android/2.9.1"
UPLOAD_APP_VERSION = "37.3.3"  # 兜底值；实际优先用 115 版本接口返回的当前客户端版本
# 115 客户端里写死的两个盐
_UPLOAD_MD5_SALT = "Qclm8MGWUv59TnrR0XPg"
_EC_CRC_SALT = b"^j>WD3Kr?J2gLFjD4W2y@"
# EC115 的远端公钥（P-224，X||Y 各 28 字节）
_EC_REMOTE_KEY = bytes([
    0x57, 0xA2, 0x92, 0x57, 0xCD, 0x23, 0x20, 0xE5,
    0xD6, 0xD1, 0x43, 0x32, 0x2F, 0xA4, 0xBB, 0x8A,
    0x3C, 0xF9, 0xD3, 0xCC, 0x62, 0x3E, 0xF5, 0xED,
    0xAC, 0x62, 0xB7, 0x67, 0x8A, 0x89, 0xC9, 0x1A,
    0x83, 0xBA, 0x80, 0x0D, 0x61, 0x29, 0xF5, 0x22,
    0xD0, 0x34, 0xC8, 0x95, 0xDD, 0x24, 0x65, 0x24,
    0x3A, 0xDD, 0xC2, 0x50, 0x95, 0x3B, 0xEE, 0xBA,
])

REFERER = "https://115.com/"
ROOT_ID = "0"
PAGE_SIZE = 1150
TIMEOUT = 60
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# 注意：不要给 UA 拼 "115Browser/..." 去伪装客户端。实测这样取链会被判
# 「文件大小超出限制，请使用115电脑端下载」，下载 CDN 也会回
# "ua not match cookie"，把本来可用的 Chrome UA 组合毁掉。

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


def _md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _upload_sig(user_id: str, file_id: str, target: str, userkey: str) -> str:
    """115 上传 `sig`：内层 SHA1 取小写 hex，最终结果转大写（大小写错了会被判 sig invalid）。"""
    inner = hashlib.sha1(f"{user_id}{file_id}{target}0".encode()).hexdigest()
    return hashlib.sha1(f"{userkey}{inner}000000".encode()).hexdigest().upper()


def _upload_token(user_id: str, file_id: str, file_size, sign_key: str,
                  sign_val: str, stamp: int, app_ver: str) -> str:
    """115 上传 `token`（全部小写 hex）。"""
    user_md5 = _md5_hex(user_id.encode())
    raw = (f"{_UPLOAD_MD5_SALT}{file_id}{file_size}{sign_key}{sign_val}"
           f"{user_id}{stamp}{user_md5}{app_ver}")
    return _md5_hex(raw.encode())


def _sha1_range_file(path: Path, start: int, end: int) -> str:
    """算文件 [start, end] 区间的 SHA1（大写），用于二次校验。"""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        handle.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = handle.read(min(1024 * 1024, left))
            if not chunk:
                break
            digest.update(chunk)
            left -= len(chunk)
    return digest.hexdigest().upper()


def _lz4_decompress(src: bytes) -> bytes:
    """LZ4 block 解压（115 的响应体只用了 block 格式，没有帧头）。"""
    out = bytearray()
    index = 0
    total = len(src)
    while index < total:
        token = src[index]
        index += 1
        literals = token >> 4
        if literals == 15:
            while True:
                extra = src[index]
                index += 1
                literals += extra
                if extra != 255:
                    break
        out += src[index:index + literals]
        index += literals
        if index >= total:
            break
        offset = src[index] | (src[index + 1] << 8)
        index += 2
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                extra = src[index]
                index += 1
                match_len += extra
                if extra != 255:
                    break
        match_len += 4
        start = len(out) - offset
        if start < 0:
            raise Drive115Error(-1, "115 响应解压失败（LZ4 偏移越界）")
        for _ in range(match_len):
            out.append(out[start])
            start += 1
    return bytes(out)


class _EC115Cipher:
    """115 客户端的上传加密：P-224 ECDH 派生 AES key/iv，请求用 CBC，响应再解 LZ4。"""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec

        remote = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_EC_REMOTE_KEY[:28], "big"),
            int.from_bytes(_EC_REMOTE_KEY[28:], "big"),
            ec.SECP224R1(),
        ).public_key()
        private = ec.generate_private_key(ec.SECP224R1())
        secret = private.exchange(ec.ECDH(), remote)
        self.key = secret[:16]
        self.iv = secret[-16:]
        numbers = private.public_key().public_numbers()
        prefix = 0x03 if numbers.y & 1 else 0x02
        # 29 = 28 字节 X + 1 字节压缩点前缀
        self.pub_key = bytes([29, prefix]) + numbers.x.to_bytes(28, "big")

    def encrypt(self, plain: bytes) -> bytes:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        padder = padding.PKCS7(128).padder()
        data = padder.update(plain) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return encryptor.update(data) + encryptor.finalize()

    def decrypt(self, cipher_text: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        usable = len(cipher_text) - len(cipher_text) % 16
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        data = decryptor.update(cipher_text[:usable]) + decryptor.finalize()
        if len(data) < 2:
            raise Drive115Error(-1, "115 响应过短，无法解密")
        length = data[0] | (data[1] << 8)
        return _lz4_decompress(data[2:2 + length])

    def encode_token(self, timestamp_ms: int) -> str:
        r1 = secrets.token_bytes(1)[0]
        r2 = secrets.token_bytes(1)[0]
        stamp = int(timestamp_ms & 0xFFFFFFFF).to_bytes(4, "big")
        buf = bytearray()
        for i in range(15):
            buf.append(self.pub_key[i] ^ r1)
        buf += bytes([r1, 0x73 ^ r1])
        buf += bytes([r1, r1, r1])
        for i in range(4):
            buf.append(r1 ^ stamp[3 - i])
        for i in range(15, len(self.pub_key)):
            buf.append(self.pub_key[i] ^ r2)
        buf += bytes([r2, 0x01 ^ r2])
        buf += bytes([r2, r2, r2])
        crc = zlib.crc32(_EC_CRC_SALT + bytes(buf)) & 0xFFFFFFFF
        buf += bytes(reversed(crc.to_bytes(4, "big")))
        return base64.b64encode(bytes(buf)).decode()


def _oss_signature(secret: str, method: str, content_md5: str, content_type: str,
                   date: str, oss_headers: dict[str, str], resource: str) -> str:
    """阿里云 OSS V1 签名（PUT 单对象）。"""
    import hmac

    canonical = "".join(f"{key.lower()}:{value}\n"
                        for key, value in sorted(oss_headers.items()))
    to_sign = (f"{method}\n{content_md5}\n{content_type}\n{date}\n"
               f"{canonical}{resource}")
    digest = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


class Drive115Client:
    """一个登录态一个实例；目录 id 缓存按云端路径。

    115 用字符串 `cid` 表示目录（根为 `"0"`），文件 / 目录的 `fid` 是各自 id，
    文件另带 `pc`（pickcode）用于取直链。
    """

    # 备份引擎据此判断能否走上传；115 已接入秒传 + OSS 直传
    supports_upload = True

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
        self._app_version = ""
        self._app_versions: list[str] = []
        self._version_confirmed = False
        self._cleanup_tried: set[tuple[str, str]] = set()

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

    def iter_dir(self, cid: str = ROOT_ID, page_size: int = PAGE_SIZE):
        """逐页列出目录（115 单页上限 1150，超过要翻页）。"""
        cid = str(cid or ROOT_ID)
        offset = 0
        while True:
            payload = self._request("GET", "/files", params={
                "aid": 1, "cid": cid, "o": "user_ptime", "asc": 0,
                "offset": offset, "show_dir": 1, "limit": page_size,
                "fc_mix": 0,
            })
            batch = payload.get("data")
            batch = batch if isinstance(batch, list) else []
            if batch:
                yield batch
            total = _int(payload.get("count"))
            offset += len(batch)
            if not batch or len(batch) < page_size or (total and offset >= total):
                break

    def list_dir(self, cid: str = ROOT_ID) -> list[dict]:
        """列一页或多页目录（115 单页上限 1150，超过要翻页）。"""
        return [item for batch in self.iter_dir(cid) for item in batch]

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

    def iter_entries(self, cid: str = ROOT_ID, page_size: int = PAGE_SIZE):
        """逐页产出下载页统一结构，首分页返回后即可渲染。"""
        cid = str(cid or ROOT_ID)
        for batch in self.iter_dir(cid, page_size):
            yield [self._entry(raw, cid) for raw in batch]

    def list_entries(self, cid: str = ROOT_ID) -> list[dict]:
        """下载页使用的统一结构；目录的 `fid` 直接就是子目录 cid。"""
        return [entry for batch in self.iter_entries(cid) for entry in batch]

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

        命中哪种就按哪种下载。**不要伪装 `115Browser` UA**：实测
        `115Browser` + 账号 Cookie 会被 CDN 判 `403 ua not match cookie`，
        而重新申请直链时会直接回「文件大小超出限制，请使用115电脑端下载」，
        把本来可用的链接毁掉。全部被拒时**直接报错并停止重试**，错误里带
        每次的状态码、CDN 原文、直链主机和取链响应字段名。
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
        """上传一个文件：先试秒传，未命中再走阿里云 OSS 直传（单次 PUT）。

        协议细节见模块文档；`hashes` 是引擎算好的 (md5, sha1)，没传就自己算。
        """
        path = Path(local_path)
        size = path.stat().st_size
        if size <= 0:
            raise Drive115Error(-1, "115 不允许上传 0 字节文件")
        if size > 5 * 1024 ** 3:
            raise Drive115Error(-1, "115 单文件直传上限 5 GB（更大的文件暂未支持分片）")
        remote_dir = (remote_dir or "").strip("/")
        if on_stage is not None:
            on_stage("hash", path.name, size)
        md5, sha1 = hashes if hashes else self.digest(path)
        sha1 = str(sha1).upper()
        user_id, userkey = self._upload_credentials()
        cid = self.ensure_dir(remote_dir)
        target = f"U_1_{cid}"

        last_error: Drive115Error | None = None
        candidates = self._app_version_candidates()
        for app_ver in candidates:
            self._app_version = app_ver
            # 首档多给一次机会：同一 fileid 连续申请时 115 偶尔回 token invalid，
            # 换个新时间戳重签一次通常就过；其余档只试一次
            attempts = 2 if app_ver == candidates[0] else 1
            for _ in range(attempts):
                try:
                    payload, data, status, pick_code, bucket, obj, callback = self._init_upload(
                        path, sha1, size, target, user_id, userkey, app_ver, on_stage)
                except Drive115Error as exc:
                    text = str(exc)
                    if "升级到最新版本" in text:
                        last_error = exc
                        break            # 版本太旧：换下一档
                    if "token invalid" in text:
                        last_error = exc
                        continue         # 同版本重签再试一次
                    raise
                self._version_confirmed = True
                if status == 2:
                    if on_progress is not None:
                        try:
                            on_progress(size, size)
                        except Exception:  # noqa: BLE001
                            pass
                    return {"fid": pick_code, "instant": True, "md5": md5, "size": size}
                if not (bucket and obj):
                    raise Drive115Error(
                        -1, f"上传初始化未返回 OSS 信息（status={status}，"
                            f"字段：{'、'.join(sorted(data)) or '无'}）", "/initupload")
                if on_stage is not None:
                    on_stage("upload", path.name, size)
                self._oss_put(path, bucket, obj, callback, size, on_progress)
                self._index_cache.pop(remote_dir, None)
                return {"fid": pick_code, "instant": False, "md5": md5, "size": size}
            if (app_ver == candidates[0] and last_error is not None
                    and "token invalid" in str(last_error)):
                # 首档（已验证可用的版本）两次都 token invalid：不是版本问题，别再换档空转
                break
        if last_error is not None:
            # 所有版本都被拒：先看看目标目录里是不是已有同名（可能残缺）条目，
            # 115 会因此一直回 token invalid；清掉后重试一轮（每次映射每个文件只清一次）
            cleanup_note = ""
            marker = (cid, path.name)
            if marker not in self._cleanup_tried:
                entry = self._same_name_entry(cid, path.name)
                if entry and self.delete_files([str(entry.get("fid") or "")]):
                    self._cleanup_tried.add(marker)
                    self._index_cache.pop(remote_dir, None)
                    return self.upload_file(local_path, remote_dir,
                                            on_progress=on_progress, on_stage=on_stage,
                                            hashes=(md5, sha1), resume=resume)
                cleanup_note = ("；目标目录有同名条目但删除失败" if entry
                                else "；目标目录无同名条目")
            probe_note = self._probe_filename(path, sha1, size, target,
                                              user_id, userkey,
                                              self._app_version or UPLOAD_APP_VERSION)
            raise Drive115Error(
                last_error.code,
                f"{last_error.message}（已尝试版本：{'、'.join(self._app_versions)}"
                f"{cleanup_note}；换名探测：{probe_note}）",
                last_error.path)
        raise Drive115Error(-1, "115 上传初始化失败", "/initupload")

    def _probe_filename(self, path: Path, sha1: str, size: int, target: str,
                        user_id: int, userkey: str, app_ver: str) -> str:
        """诊断用：换一个 ASCII 临时文件名再申请一次初始化（不上传文件）。

        通过 → 说明 115 拒绝的是原文件名；仍失败 → 与文件名无关。
        """
        suffix = path.suffix or ""
        probe_name = f"qw_probe_{sha1[:8]}{suffix}"
        try:
            _payload, _data, status, _pick, _b, _o, _c = self._init_upload(
                path, sha1, size, target, user_id, userkey, app_ver, None,
                filename=probe_name)
            return f"临时名通过（status={status}）"
        except Drive115Error as exc:
            text = str(exc)
            if "被 115 拒绝" in text:
                return f"临时名仍失败：{text[:120]}"
            return f"临时名异常：{text[:120]}"

    def _same_name_entry(self, cid: str, name: str) -> dict | None:
        """目标目录里与本地同名的文件（用于清理残留下同名条目）。"""
        try:
            for entry in self.list_entries(cid):
                if not entry.get("dir") and entry.get("file_name") == name:
                    return entry
        except Drive115Error:
            return None
        return None

    def _init_upload(self, path: Path, sha1: str, size: int, target: str,
                     user_id: int, userkey: str, app_ver: str, on_stage,
                     filename: str | None = None):
        """一次上传初始化（含二次校验重试），成功返回响应拆好的字段。"""
        name = filename or path.name
        sign_key, sign_val = "", ""
        cipher = _EC115Cipher()
        payload: dict = {}
        data: dict = {}
        for attempt in range(2):
            stamp = int(time.time() * 1000)
            user_id_str = str(user_id)
            sig = _upload_sig(user_id_str, sha1, target, userkey)
            token = _upload_token(user_id_str, sha1, size, sign_key, sign_val,
                                  stamp, app_ver)
            form = {
                "appid": "0",
                "appversion": app_ver,
                "userid": user_id_str,
                "filename": name,
                "filesize": str(size),
                "fileid": sha1,
                "target": target,
                "sig": sig,
                "topupload": "true",
                "t": str(stamp),
                "token": token,
            }
            if sign_key and sign_val:
                form["sign_key"] = sign_key
                form["sign_val"] = sign_val
            body = cipher.encrypt(urlencode(form).encode())
            try:
                resp = self.session.post(
                    f"{UPLOAD_INIT_URL}?k_ec={quote(cipher.encode_token(stamp))}",
                    data=body,
                    headers={**self._upload_headers(),
                             "Content-Type": "application/x-www-form-urlencoded"},
                    timeout=TIMEOUT * 2)
            except (requests.RequestException, OSError) as exc:
                raise Drive115Error(-1, f"上传初始化请求失败：{exc}", "/initupload")
            if resp.status_code != 200:
                raise Drive115Error(resp.status_code,
                                    f"上传初始化 HTTP {resp.status_code}：{resp.text[:200]}",
                                    "/initupload")
            try:
                payload = json.loads(cipher.decrypt(resp.content).decode("utf-8"))
            except Drive115Error:
                raise
            except Exception as exc:  # noqa: BLE001 - 解密/JSON 失败都算协议异常
                raise Drive115Error(-1, f"上传初始化响应无法解析：{exc}", "/initupload")
            if payload.get("state") is False:
                raise Drive115Error(payload.get("code") or payload.get("errno") or -1,
                                    str(payload.get("message") or payload.get("error")
                                        or "上传初始化被拒绝"), "/initupload")
            nested = payload.get("data")
            data = nested if isinstance(nested, dict) else payload
            status = _int(data.get("status", payload.get("status")))
            status_code = _int(data.get("statuscode", payload.get("statuscode")))
            sign_key = str(data.get("sign_key") or payload.get("sign_key") or "")
            sign_check = str(data.get("sign_check") or payload.get("sign_check") or "")
            # 二次校验：客户端按 status 6/7/8 或 statuscode 700/701/702 触发
            need_check = status in (6, 7, 8) or status_code in (700, 701, 702)
            if need_check and attempt == 0 and sign_key and sign_check:
                if on_stage is not None:
                    on_stage("hash", path.name, size)
                sign_val = self._sha1_range(path, sign_check)
                continue
            break

        status = _int(data.get("status", payload.get("status")))
        pick_code = str(data.get("pick_code") or data.get("pickcode")
                        or payload.get("pick_code") or "")
        bucket = str(data.get("bucket") or "")
        obj = str(data.get("object") or "")
        callback = data.get("callback") if isinstance(data.get("callback"), dict) else {}
        if status != 2 and not (bucket and obj):
            status_code = _int(data.get("statuscode", payload.get("statuscode")))
            status_msg = str(data.get("statusmsg") or payload.get("statusmsg")
                             or data.get("message") or "")
            request_id = str(data.get("request") or payload.get("request") or "")
            version_echo = str(data.get("version") or payload.get("version") or "")
            raise Drive115Error(
                -1, f"上传初始化被 115 拒绝：status={status} statuscode={status_code} "
                    f"statusmsg={status_msg or '（空）'}"
                    f"{' request=' + request_id if request_id else ''}"
                    f"，target={target}，appversion={app_ver}"
                    f"{'，服务端 version=' + version_echo if version_echo else ''}，"
                    f"字段：{'、'.join(sorted(data)) or '无'}", "/initupload")
        return payload, data, status, pick_code, bucket, obj, callback

    # ==================== 上传辅助 ====================

    _upload_user: tuple[int, str] | None = None

    def _app_version_candidates(self) -> list[str]:
        """上传初始化要用的 `appversion` 候选。

        115 的 `token` 把版本号字符串一起 MD5，版本串与服务端要求不一致会回
        `token invalid`；版本过旧会回「请升级到最新版本」。所以按「接口各端版本 →
        本机 115Life 版本 → 带构建号」依次尝试，命中即缓存。
        """
        if self._app_versions:
            if self._version_confirmed and self._app_version:
                return [self._app_version]
            return list(self._app_versions)
        values: list[str] = []
        # 37.3.3 是当前能过版本门槛且 token 被接受的一档（实测 36.x 会回「请升级」）
        values.extend([UPLOAD_APP_VERSION, "37.3.3.2026110914", "37.3.3.0"])
        try:
            payload = self.session.get(APP_VERSION_URL, timeout=TIMEOUT).json()
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            # 协议归属 115浏览器（win/win64），其次是 115Life（window_115）
            for key in ("win", "win64", "window_115"):
                item = data.get(key)
                if isinstance(item, dict):
                    value = str(item.get("version_code") or item.get("version") or "").strip()
                    if value:
                        values.append(value)
        except (requests.RequestException, OSError, ValueError):
            pass
        values.extend([UPLOAD_APP_VERSION, "37.3.3.2026110914", "37.3.3.0"])
        unique: list[str] = []
        for value in values:
            if value and value not in unique:
                unique.append(value)
        self._app_versions = unique
        return list(unique)

    def _current_app_version(self) -> str:
        if self._app_version:
            return self._app_version
        candidates = self._app_version_candidates()
        return candidates[0] if candidates else UPLOAD_APP_VERSION

    def _upload_headers(self) -> dict:
        """上传接口用与客户端版本一致的 UA（webapi 的 Chrome UA 保持不动）。"""
        return {"User-Agent": f"Mozilla/5.0 115Browser/{self._current_app_version()}"}

    def _upload_credentials(self) -> tuple[int, str]:
        """`app/uploadinfo` 给 user_id + userkey（签名用），一次登录只取一次。"""
        if self._upload_user:
            return self._upload_user
        try:
            # 与官方客户端一致：POST + JSON 内容类型（无内容类型时 115 可能给降级凭证）
            resp = self.session.post(UPLOAD_INFO_URL, json={},
                                     headers=self._upload_headers(), timeout=TIMEOUT)
            payload = resp.json()
        except (requests.RequestException, OSError, ValueError) as exc:
            raise Drive115Error(-1, f"取上传凭证失败：{exc}", "/app/uploadinfo")
        if not isinstance(payload, dict):
            raise Drive115Error(-1, "上传凭证结构异常", "/app/uploadinfo")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        user_id = _int(data.get("user_id") or data.get("userid") or data.get("uid"))
        userkey = str(data.get("userkey") or data.get("user_key") or "")
        if not (user_id and userkey):
            raise Drive115Error(-1,
                                f"上传凭证缺少 user_id/userkey（字段："
                                f"{'、'.join(sorted(data)) or '无'}）", "/app/uploadinfo")
        self._upload_user = (user_id, userkey)
        return self._upload_user

    @staticmethod
    def _sha1_range(path: Path, range_spec: str) -> str:
        """`sign_check` 形如 `0-131071`：算这段字节的 SHA1（大写）。"""
        try:
            start_text, end_text = str(range_spec).split("-", 1)
            start, end = int(start_text), int(end_text)
        except (ValueError, TypeError):
            raise Drive115Error(-1, f"无法解析二次校验范围：{range_spec}", "/initupload")
        return _sha1_range_file(path, start, end)

    def _oss_token(self) -> dict:
        try:
            payload = self.session.get(OSS_TOKEN_URL,
                                       headers=self._upload_headers(),
                                       timeout=TIMEOUT).json()
        except (requests.RequestException, OSError, ValueError) as exc:
            raise Drive115Error(-1, f"取 OSS 上传令牌失败：{exc}", "/3.0/gettoken.php")
        if not isinstance(payload, dict):
            raise Drive115Error(-1, "OSS 令牌结构异常", "/3.0/gettoken.php")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        token = {
            "key_id": str(data.get("AccessKeyId") or data.get("AccessKeyID") or ""),
            "key_secret": str(data.get("AccessKeySecret") or ""),
            "security": str(data.get("SecurityToken") or ""),
        }
        if not (token["key_id"] and token["key_secret"] and token["security"]):
            raise Drive115Error(-1,
                                f"OSS 令牌缺字段（字段：{'、'.join(sorted(data)) or '无'}）",
                                "/3.0/gettoken.php")
        return token

    def _oss_put(self, path: Path, bucket: str, obj: str, callback: dict,
                 size: int, on_progress=None) -> None:
        """单次 PUT 到阿里云 OSS；回调由 115 校验，成功即入库。"""
        token = self._oss_token()
        oss_headers = {
            "x-oss-security-token": token["security"],
        }
        cb = str(callback.get("callback") or "")
        cb_var = str(callback.get("callback_var") or "")
        if cb:
            oss_headers["x-oss-callback"] = base64.b64encode(cb.encode()).decode()
        if cb_var:
            oss_headers["x-oss-callback-var"] = base64.b64encode(cb_var.encode()).decode()
        date = formatdate(usegmt=True)
        resource = f"/{bucket}/{obj}"
        signature = _oss_signature(token["key_secret"], "PUT", "", "", date,
                                   oss_headers, resource)
        headers = {
            "Date": date,
            "Authorization": f"OSS {token['key_id']}:{signature}",
            "User-Agent": OSS_USER_AGENT,
            **oss_headers,
        }
        url = f"https://{bucket}.{OSS_ENDPOINT}/{quote(obj, safe='/')}"

        sent = {"done": 0}

        class _Reader:
            """给 requests 一个带长度、可进度回调的文件流（避免 chunked PUT）。"""

            def __init__(self):
                self._handle = open(path, "rb")

            def __len__(self) -> int:
                return size

            def read(self, amount: int = -1) -> bytes:
                chunk = self._handle.read(amount if amount and amount > 0 else 1024 * 1024)
                if chunk:
                    sent["done"] += len(chunk)
                    if on_progress is not None:
                        try:
                            on_progress(min(sent["done"], size), size)
                        except Exception:  # noqa: BLE001
                            pass
                return chunk

            def close(self) -> None:
                self._handle.close()

        def _send(target, proxies=None):
            extra = {"proxies": proxies} if proxies is not None else {}
            return self.session.put(url, data=target, headers=headers,
                                    timeout=(30, 600), **extra)

        reader = _Reader()
        try:
            try:
                resp = _send(reader)
            except (requests.exceptions.ProxyError, requests.exceptions.SSLError):
                # 代理隧道异常（企业代理/本机代理对 OSS 域名回 502 或断 TLS）：
                # 关掉旧流、进度归零，改直连从头重试一次
                reader.close()
                sent["done"] = 0
                reader = _Reader()
                resp = _send(reader, {"http": None, "https": None})
        except (requests.RequestException, OSError) as exc:
            raise Drive115Error(-1, f"OSS 上传中断：{exc}", "/oss")
        finally:
            reader.close()
        if resp.status_code == 200:
            return
        body = (resp.text or "")[:300]
        raise Drive115Error(resp.status_code,
                            f"OSS 上传被拒（HTTP {resp.status_code}）：{body}", "/oss")