"""从本地微博 JSON 与图片新增腾讯智能表记录；默认只预览。

直接运行：python -B src/toolbox/tcdoc/import_local.py --help
只读调用 auth.get_cookie，不登录、不保存凭据、不改已有记录或表结构。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import secrets
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_PROJECT = Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
if str(_PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "src"))

from toolbox.utils import sanitize_filename  # noqa: E402; 纯命名函数，不调用配置初始化

API_ROOT = "https://docs.qq.com/openapi"
CRED_KEY = "tcdoc_openapi"
OFFICIAL_DOC = "DRnd3bkhkQUR1aEJr"
OFFICIAL_TABS = frozenset({"ss_h2uh2s", "ss_1xgstm"})
# 完整歌曲仅按用户明确授权的「只新增、不改不删」开放；更新/删除入口不存在。
SONG_TABS = frozenset({"ss_ktwk0t"})
# 线下活动 / 周边图鉴：用户 2026-11-21 授权「仅新增」，同样不提供修改与删除。
CANDIDATE_TABS = frozenset({"ss_e1kkm2", "ss_ekd4cz"})
# 2026-11-24 用户授权：线下活动已存在行只允许追加「宣传图」图片，不改其他字段。
IMAGE_APPEND_TABS = frozenset({"ss_e1kkm2"})
# 2026-11-26 用户授权：线下活动「活动歌单_*」三列只允许写入歌单链接，不改其他列。
PLAYLIST_LINK_TAB = "ss_e1kkm2"
PLAYLIST_LINK_COLUMNS = frozenset({"活动歌单_网易云", "活动歌单_酷狗", "活动歌单_QQ"})
TEST_TARGET = ("DRmFHc1dLcnBvbmtB", "ss_q4fwq4")
BEIJING = timezone(timedelta(hours=8))
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 脚本的内存限额，不代表服务端限额
ALIASES = {
    "作品标题": ("作品标题",), "作品内容": ("作品内容",),
    "发布账号": ("发布账号",), "封面图": ("封面图",),
    "动态类型": ("动态类型",), "发布时间": ("发布时间",),
    "发布平台": ("发布平台",), "发布位置": ("发布位置",),
    "作品链接": ("作品链接", "链接"),
}
CHOICE_COLUMNS = frozenset({"发布账号", "动态类型", "发布平台", "发布位置"})


class ImportErrorDetail(RuntimeError):
    """可安全显示的错误；不包含请求头、票据或服务端原始响应。"""


class UnknownWriteResult(ImportErrorDetail):
    """请求可能已写入，必须先按原帖链接查回，禁止盲目重复新增。"""


def validate_target(doc_key: str, tab_id: str, allow_test: bool = False) -> None:
    if doc_key == OFFICIAL_DOC and tab_id in OFFICIAL_TABS | SONG_TABS | CANDIDATE_TABS:
        return
    if allow_test and (doc_key, tab_id) == TEST_TARGET:
        return
    raise ImportErrorDetail("目标不在允许范围；测试文档须显式加 --allow-test-document，"
                            "该参数只放行本次指定的测试文档和子表")


def install_readonly_guard() -> None:
    """独立 CLI 进程不需要持久化写入；包括配置探测和凭据回写均拦截。"""
    sys.dont_write_bytecode = True

    def audit(event, args):
        if event == "open":
            mode, flags = args[1:3]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (
                    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                raise PermissionError("本地导入脚本禁止持久化文件写入")
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link",
                     "os.symlink", "os.truncate", "os.chmod", "os.chown", "os.utime",
                     "winreg.SetValue", "winreg.DeleteValue", "winreg.DeleteKey",
                     "winreg.CreateKey"}:
            raise PermissionError("本地导入脚本禁止修改本地保存状态")

    sys.addaudithook(audit)


def managed_cookie_reader(config_dir: Path | None = None) -> Callable[[], str]:
    """通过管理接口读取；仅在导入 auth 时替换路径解析，避免其写探针。

    默认使用本源码工程的已有 config。安装版若使用 AppData，须显式指定
    管理器当前目录；不猜测或回退到旧登录。只影响此 CLI 的进程内对象。
    """
    cm = importlib.import_module("toolbox.config_manager")
    directory = (config_dir or cm.get_app_root() / "config").resolve()
    if not directory.is_dir():
        raise ImportErrorDetail("管理器当前配置目录不存在；不能创建目录或迁移登录")
    # 精简 Python 运行时可能没有 rich，auth 的 Console 在此只需静默打印接口。
    try:
        importlib.import_module("rich.console")
    except ModuleNotFoundError as exc:
        if exc.name not in {"rich", "rich.console"}:
            raise ImportErrorDetail("认证依赖不可用") from None
        rich = types.ModuleType("rich")
        console = types.ModuleType("rich.console")
        console.Console = type("SilentConsole", (), {"print": lambda *a, **k: None})
        rich.console = console
        sys.modules["rich"] = rich
        sys.modules["rich.console"] = console
    original = cm.get_config_dir
    cm.get_config_dir = lambda: directory
    try:
        auth = importlib.import_module("auth")
    finally:
        cm.get_config_dir = original
    if Path(auth.COOKIE_FILE).resolve().parent != directory:
        raise ImportErrorDetail("已加载认证管理器的目录与指定目录不一致，请独立运行脚本")
    return lambda: auth.get_cookie(CRED_KEY)


@dataclass(repr=False)
class ManagedAuth:
    reader: Callable[[], str] = field(repr=False)
    _raw: str = field(init=False, repr=False)
    _credentials: dict = field(init=False, repr=False)

    def __post_init__(self):
        self._raw = self.reader()
        try:
            self._credentials = json.loads(self._raw)
        except (ValueError, TypeError):
            raise ImportErrorDetail("Cookie 管理中的 OpenAPI 凭据不可用或总开关已关闭") from None
        if not isinstance(self._credentials, dict) or not all(
            isinstance(self._credentials.get(k), str) and self._credentials[k].strip()
            for k in ("client_id", "access_token", "open_id")
        ):
            raise ImportErrorDetail("管理器缺少完整 OpenAPI 凭据")

    def headers(self) -> dict:
        if self.reader() != self._raw:
            raise ImportErrorDetail("管理器登录态已改变或关闭，本次任务停止")
        return {"Access-Token": self._credentials["access_token"].strip(),
                "Client-Id": self._credentials["client_id"].strip(),
                "Open-Id": self._credentials["open_id"].strip(),
                "Accept": "application/json"}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalImportClient:
    def __init__(self, auth: ManagedAuth, doc_key: str, tab_id: str, allow_test=False):
        validate_target(doc_key, tab_id, allow_test)
        self.auth, self.doc_key, self.tab_id = auth, doc_key, tab_id
        self.allow_test = allow_test
        self.opener = build_opener(NoRedirects())  # 不接收/回写 Cookie，不向重定向目标转发票据
        self.file_id = ""
        self.sheet_title = ""

    def _request(self, method: str, path: str, payload=None, *, image=None,
                 record_write=False) -> dict:
        if not path.startswith("/") or path.startswith("//"):
            raise ImportErrorDetail("无效的官方 API 路径")
        headers, data = self.auth.headers(), None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if image is not None:
            name, mime, content = image
            if any(c in name for c in '\r\n"'):
                raise ImportErrorDetail("图片文件名无法安全放入 multipart")
            boundary = "----FmjjLocalImage" + secrets.token_hex(16)
            data = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                    f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n').encode("utf-8")
            data += content + f"\r\n--{boundary}--\r\n".encode("ascii")
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        request = Request(API_ROOT + path, data=data, headers=headers, method=method)
        error_type = UnknownWriteResult if record_write else ImportErrorDetail
        try:
            with self.opener.open(request, timeout=45) as response:
                body = json.load(response)
        except HTTPError as exc:
            error_type = UnknownWriteResult if record_write and exc.code >= 500 else ImportErrorDetail
            raise error_type(f"官方接口 HTTP {exc.code}；未自动重试") from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
            detail = f"（系统码 {code}）" if isinstance(code, int) else ""
            raise error_type(f"官方接口连接失败{detail}；未自动重试") from None
        except ValueError:
            raise error_type("官方接口响应无法解析；未自动重试") from None
        if not isinstance(body, dict) or "ret" not in body:
            raise error_type("官方接口响应格式异常；未自动重试")
        if body["ret"] != 0:
            # 原始 msg 可能回显票据，只报告数值错误码。
            code = body["ret"] if isinstance(body["ret"], int) else "未知"
            raise ImportErrorDetail(f"官方接口 ret={code}；未自动重试")
        if not isinstance(body.get("data"), dict):
            raise error_type("官方接口缺少 data；未自动重试")
        return body["data"]

    def connect(self) -> None:
        converted = self._request("GET", "/drive/v2/util/converter?" +
                                  urlencode({"type": 2, "value": self.doc_key}))
        file_id = converted.get("fileID")
        if not isinstance(file_id, str) or not file_id:
            raise ImportErrorDetail("无法通过官方转换接口核对文档")
        sheets = self._request("GET", f"/smartbook/v2/files/{quote(file_id, safe='')}/sheets")
        matches = [s for s in sheets.get("getSheet", []) if isinstance(s, dict)
                   and (s.get("sheetID") or s.get("id")) == self.tab_id]
        if len(matches) != 1:
            raise ImportErrorDetail("官方子表清单未返回唯一指定 tab_id")
        self.file_id = file_id
        self.sheet_title = str(matches[0].get("title") or matches[0].get("name") or "")

    def _action(self, operation: str, block: dict) -> dict:
        if not self.file_id:
            raise ImportErrorDetail("尚未核对文档身份")
        if operation not in {"getFields", "getRecords", "addRecords"}:
            raise ImportErrorDetail("仅支持读取和新增明确记录")
        if operation == "addRecords":
            validate_target(self.doc_key, self.tab_id, self.allow_test)
            records = block.get("records")
            if not isinstance(records, list) or len(records) != 1 or not records[0].get("values"):
                raise ImportErrorDetail("每次只能新增一条明确记录")
        path = (f"/smartbook/v2/files/{quote(self.file_id, safe='')}"
                f"/sheets/{quote(self.tab_id, safe='')}")
        data = self._request("POST", path, {operation: block}, record_write=operation == "addRecords")
        result = data.get(operation)
        if not isinstance(result, dict):
            cls = UnknownWriteResult if operation == "addRecords" else ImportErrorDetail
            raise cls("官方接口未返回操作结果；未自动重试")
        return result

    def get_fields(self) -> list[dict]:
        fields = self._action("getFields", {}).get("fields")
        if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
            raise ImportErrorDetail("字段清单格式异常")
        return fields

    def get_records(self) -> list[dict]:
        out, seen, offset = [], set(), 0
        for _ in range(1000):
            # 服务端单页上限 1000；用 100 行翻 5000 行要 51 次请求，太慢
            block = self._action("getRecords", {"offset": offset, "limit": 1000})
            rows = block.get("records")
            if not isinstance(rows, list):
                raise ImportErrorDetail("记录清单格式异常")
            for row in rows:
                rid = row.get("recordID") if isinstance(row, dict) else None
                if not rid or rid in seen:
                    raise ImportErrorDetail("分页记录缺少身份或重复，停止以免漏判重复")
                seen.add(rid)
                out.append(row)
            if not block.get("hasMore"):
                if isinstance(block.get("total"), int) and block["total"] != len(out):
                    raise ImportErrorDetail("记录总数与分页结果不一致，请重新预览")
                return out
            if not rows:
                raise ImportErrorDetail("分页声明仍有记录但返回为空")
            next_offset = block.get("next")
            offset = next_offset if isinstance(next_offset, int) and next_offset > offset else offset + len(rows)
        raise ImportErrorDetail("记录超出脚本分页上限")

    def upload_image(self, image: LocalImage) -> str:
        validate_target(self.doc_key, self.tab_id, self.allow_test)
        result = self._request("POST", "/resources/v2/images",
                               image=(image.upload_name, image.mime, image.read_verified()))
        image_id = result.get("imageID")
        if not isinstance(image_id, str) or not image_id:
            raise ImportErrorDetail("图片上传未返回 imageID；没有新增记录")
        return image_id

    def add_record(self, values: dict) -> str:
        rows = self._action("addRecords", {"records": [{"values": values}]}).get("records")
        if not isinstance(rows, list) or len(rows) != 1 or not rows[0].get("recordID"):
            raise UnknownWriteResult("新增结果缺少唯一 recordID，请先按原帖链接查回")
        return str(rows[0]["recordID"])

    def update_record(self, record_id: str, values: dict) -> dict:
        """按 recordID 更新白名单表的指定字段（行级，不碰表结构）。"""
        validate_target(self.doc_key, self.tab_id, self.allow_test)
        rid = str(record_id or "").strip()
        if not rid:
            raise ImportErrorDetail("修改必须明确指定 recordID")
        if not isinstance(values, dict) or not values:
            raise ImportErrorDetail("修改必须明确指定要写的字段")
        if not self.file_id:
            raise ImportErrorDetail("尚未核对文档身份")
        path = (f"/smartbook/v2/files/{quote(self.file_id, safe='')}"
                f"/sheets/{quote(self.tab_id, safe='')}")
        data = self._request("POST", path, {"updateRecords": {"records": [
            {"recordID": rid, "values": values}]}}, record_write=True)
        result = data.get("updateRecords")
        if not isinstance(result, dict):
            raise UnknownWriteResult("官方接口未返回修改结果；未自动重试")
        return result

    def delete_record(self, record_id: str) -> dict:
        """按 recordID 删除白名单表的单行（行级，不碰表结构）。"""
        validate_target(self.doc_key, self.tab_id, self.allow_test)
        rid = str(record_id or "").strip()
        if not rid:
            raise ImportErrorDetail("删除必须明确指定 recordID")
        if not self.file_id:
            raise ImportErrorDetail("尚未核对文档身份")
        path = (f"/smartbook/v2/files/{quote(self.file_id, safe='')}"
                f"/sheets/{quote(self.tab_id, safe='')}")
        data = self._request("POST", path, {"deleteRecords": {
            "recordIDs": [rid]}}, record_write=True)
        result = data.get("deleteRecords")
        if not isinstance(result, dict):
            raise UnknownWriteResult("官方接口未返回删除结果；未自动重试")
        return result

    def update_image_field(self, record_id: str, field_title: str,
                           entries: list[dict]) -> None:
        """线下活动「宣传图」追加：只更新指定行的单个图片字段。"""
        if self.doc_key != OFFICIAL_DOC or self.tab_id not in IMAGE_APPEND_TABS:
            raise ImportErrorDetail("图片追加仅授权线下活动表")
        if field_title != "宣传图":
            raise ImportErrorDetail("图片追加仅授权「宣传图」列")
        if not str(record_id or "").strip():
            raise ImportErrorDetail("追加图片缺少目标行 recordID")
        if not entries or not all(
                isinstance(e, dict) and str(e.get("imageID") or "") for e in entries):
            raise ImportErrorDetail("图片追加清单格式异常")
        if not self.file_id:
            raise ImportErrorDetail("尚未核对文档身份")
        path = (f"/smartbook/v2/files/{quote(self.file_id, safe='')}"
                f"/sheets/{quote(self.tab_id, safe='')}")
        payload = {"updateRecords": {"records": [{
            "recordID": str(record_id),
            "values": {field_title: entries}}]}}
        data = self._request("POST", path, payload, record_write=True)
        result = data.get("updateRecords")
        rows = result.get("records") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != 1:
            raise UnknownWriteResult("图片追加结果缺少逐条回执，请按 recordID 读回核对")

    def update_playlist_link(self, record_id: str, field_title: str, url: str,
                             *, field_type: int = 8, text: str = "") -> None:
        """线下活动「活动歌单_*」写歌单链接：只改指定行的单个链接/文本列。"""
        if self.doc_key != OFFICIAL_DOC or self.tab_id != PLAYLIST_LINK_TAB:
            raise ImportErrorDetail("歌单链接仅授权线下活动表")
        if field_title not in PLAYLIST_LINK_COLUMNS:
            raise ImportErrorDetail(f"歌单链接仅授权「活动歌单」三列：{field_title}")
        if not str(record_id or "").strip():
            raise ImportErrorDetail("歌单回写缺少目标行 recordID")
        target = str(url or "").strip()
        if not re.match(r"^https?://", target):
            raise ImportErrorDetail("歌单链接必须是 http(s) 地址")
        if not self.file_id:
            raise ImportErrorDetail("尚未核对文档身份")
        if int(field_type) == 8:
            value = [{"type": "url", "text": str(text or "").strip() or target,
                      "link": target}]
        elif int(field_type) == 1:
            value = [{"type": "text", "text": target}]
        else:
            raise ImportErrorDetail(f"「{field_title}」不是链接/文本列，暂不支持写入")
        path = (f"/smartbook/v2/files/{quote(self.file_id, safe='')}"
                f"/sheets/{quote(self.tab_id, safe='')}")
        payload = {"updateRecords": {"records": [{
            "recordID": str(record_id),
            "values": {field_title: value}}]}}
        data = self._request("POST", path, payload, record_write=True)
        result = data.get("updateRecords")
        rows = result.get("records") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != 1:
            raise UnknownWriteResult("歌单链接写入结果缺少逐条回执，请按 recordID 读回核对")


def image_mime(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if content.startswith(b"BM"):
        return "image/bmp"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ImportErrorDetail("本地图片签名不支持（支持 JPEG、PNG、GIF、BMP、WebP）")


def convert_to_png(data: bytes) -> bytes | None:
    """非腾讯支持的图片格式（HEIC/AVIF/TIFF/SVG 等）转成 PNG 字节。

    上传时只在内存里转换，不改动本地原图；解码失败或转换后超出限额返回 None。
    """
    try:
        import io

        from PIL import Image
        try:  # HEIC/HEIF 需要先注册解码器（requirements 里已含 pillow-heif）
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:  # noqa: BLE001 - 没装也继续试其它格式
            pass
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info)
            buffer = io.BytesIO()
            img.convert("RGBA" if alpha else "RGB").save(buffer, "PNG", optimize=True)
            png = buffer.getvalue()
    except Exception:  # noqa: BLE001 - 任何解码/编码失败都按不支持处理
        return None
    if not 0 < len(png) <= MAX_IMAGE_BYTES:
        return None
    return png


@dataclass(frozen=True)
class LocalImage:
    path: Path
    size: int
    digest: str = field(repr=False)
    mime: str
    # 非支持格式转换出来的 PNG：只存在内存里，不落盘、不改原图
    data: bytes | None = field(default=None, repr=False, compare=False)
    name: str = ""

    @classmethod
    def inspect(cls, path: Path, media_root: Path):
        resolved = path.resolve()
        if not resolved.is_relative_to(media_root.resolve()) or not resolved.is_file():
            raise ImportErrorDetail(f"本地图片缺失或越出媒体目录：{path.name}")
        if any(c in path.name for c in '\r\n"'):
            raise ImportErrorDetail("图片文件名不适用于上传")
        if not 0 < resolved.stat().st_size <= MAX_IMAGE_BYTES:
            raise ImportErrorDetail(f"本地图片为空或超过脚本 20 MiB 限额：{path.name}")
        data = resolved.read_bytes()
        try:
            mime = image_mime(data)
        except ImportErrorDetail:
            # 腾讯只收 JPEG/PNG/GIF/BMP/WebP：其它格式（HEIC/AVIF/TIFF…）自动转 PNG
            png = convert_to_png(data)
            if png is None:
                raise ImportErrorDetail(
                    "本地图片格式不支持，自动转 PNG 也失败："
                    f"{path.name}（支持 JPEG、PNG、GIF、BMP、WebP）") from None
            return cls(resolved, len(png), hashlib.sha256(png).hexdigest(), "image/png",
                       data=png, name=resolved.stem[:80] + ".png")
        return cls(resolved, len(data), hashlib.sha256(data).hexdigest(), mime)

    @property
    def upload_name(self) -> str:
        return self.name or self.path.name

    def read_verified(self) -> bytes:
        if self.data is not None:
            if hashlib.sha256(self.data).hexdigest() != self.digest:
                raise ImportErrorDetail(f"转换后的 PNG 数据不一致：{self.upload_name}")
            return self.data
        try:
            if not self.path.is_file() or self.path.stat().st_size != self.size:
                raise ImportErrorDetail(f"预览后本地图片发生变化：{self.path.name}")
            data = self.path.read_bytes()
        except OSError:
            raise ImportErrorDetail(f"预览后本地图片无法读取：{self.path.name}") from None
        if hashlib.sha256(data).hexdigest() != self.digest:
            raise ImportErrorDetail(f"预览后本地图片发生变化：{self.path.name}")
        return data


def weibo_key(link: str) -> str | None:
    """去掉追踪参数，兼容移动版 mid 和桌面版 base62 链接。"""
    parsed = urlsplit(str(link).strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn"}:
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) == 2 and parts[0] in {"detail", "status"} and parts[1].isdigit():
        return "weibo:" + str(int(parts[1]))
    if parsed.hostname in {"weibo.com", "www.weibo.com"} and len(parts) == 2 and parts[0].isdigit():
        bid = parts[1]
        if bid.isdigit() and len(bid) >= 13:
            return "weibo:" + str(int(bid))
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if not bid or any(c not in alphabet for c in bid) or len(bid) > 12:
            return None
        chunks = []
        for end in range(len(bid), 0, -4):
            value = 0
            for char in bid[max(0, end - 4):end]:
                value = value * 62 + alphabet.index(char)
            chunks.append(str(value).zfill(7))
        return "weibo:" + str(int("".join(reversed(chunks))))
    return None


@dataclass(frozen=True)
class LocalPost:
    post_id: str
    title: str
    body: str
    author: str
    published: datetime
    images: tuple[LocalImage, ...]

    @property
    def key(self):
        return "weibo:" + self.post_id

    @property
    def link(self):
        return "https://m.weibo.cn/detail/" + self.post_id

    def columns(self, dynamic_type="动态", position="动态") -> dict:
        return {"作品标题": self.title, "作品内容": self.body, "发布账号": self.author,
                "动态类型": dynamic_type, "发布时间": str(int(self.published.timestamp() * 1000)),
                "发布平台": "微博", "发布位置": position, "作品链接": self.link}


def _local_images(item: dict, published: datetime, author: str, title: str,
                  post_id: str, media_root: Path) -> tuple[LocalImage, ...]:
    raw = item.get("原始数据") or {}
    if not isinstance(raw, dict):
        raise ImportErrorDetail("本地导出原始数据不是对象")
    pics = raw.get("pics") or []
    if not isinstance(pics, list):
        raise ImportErrorDetail("原帖图片清单格式异常")
    try:
        count = int(raw.get("pic_num") or len(pics))
    except (ValueError, TypeError):
        raise ImportErrorDetail("无法确定原帖图片数量") from None
    if not 1 <= count <= 50 or (pics and count != len(pics)):
        raise ImportErrorDetail(f"图片数量缺失、异常或不一致：{post_id}")
    user = sanitize_filename(author)
    directory = media_root / user
    stem = f"{published:%Y%m%d}_{user}_{sanitize_filename(title, 50)}"
    owners_path = directory / ".stem_owners.json"
    owners = {}
    if owners_path.is_file():
        if not owners_path.resolve().is_relative_to(media_root.resolve()):
            raise ImportErrorDetail("图片归属索引越出媒体目录")
        try:
            owners = json.loads(owners_path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            raise ImportErrorDetail("图片归属索引无法读取") from None
        if not isinstance(owners, dict):
            raise ImportErrorDetail("图片归属索引格式异常")
    owned = [s for s, owner in owners.items() if owner == post_id and
             isinstance(s, str) and (s == stem or s.startswith(stem + "~"))]
    if len(owned) > 1 or (not owned and owners.get(stem) not in (None, post_id)):
        raise ImportErrorDetail(f"图片文件归属有歧义：{post_id}")
    stem = owned[0] if owned else stem
    # 与 media_downloader 一致；不包含 _封面 缩略图和 _live 动图视频。
    paths = [directory / (f"{stem}.jpg" if count == 1 else f"{stem}_{i}.jpg")
             for i in range(1, count + 1)]
    return tuple(LocalImage.inspect(path, media_root) for path in paths)


def load_posts(exports: list[Path], media_root: Path, post_ids=(), max_records=20) -> list[LocalPost]:
    wanted, found, posts = set(post_ids), set(), {}
    for path in exports:
        try:
            items = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise ImportErrorDetail(f"无法读取本地 JSON 导出：{path.name}") from None
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ImportErrorDetail("需使用本项目的 JSON 列表导出")
        for item in items:
            post_id = str(item.get("视频ID") or "").strip()
            if wanted and post_id not in wanted:
                continue
            if str(item.get("平台") or "").lower() not in {"weibo", "微博"}:
                raise ImportErrorDetail("当前脚本只支持本地微博导出")
            if not post_id.isdigit():
                raise ImportErrorDetail("本地微博 ID 缺失或非数字")
            post_id = str(int(post_id))
            found.add(post_id)
            link = str(item.get("链接") or item.get("原始URL") or "")
            if weibo_key(link) != "weibo:" + post_id:
                raise ImportErrorDetail(f"原帖链接与本地 ID 不一致：{post_id}")
            body = unescape(str(item.get("正文") or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
            source_title = str(item.get("标题") or "")
            title = unescape(source_title).strip() or body.split("\n")[0][:80]
            author = str(item.get("作者") or "").strip()
            if not title or not body or not author:
                raise ImportErrorDetail(f"标题、正文或发布账号缺失：{post_id}")
            try:
                published = datetime.fromisoformat(str(item.get("发布时间") or ""))
            except ValueError:
                raise ImportErrorDetail(f"发布时间无法解析：{post_id}") from None
            published = published.replace(tzinfo=BEIJING) if published.tzinfo is None else published.astimezone(BEIJING)
            images = _local_images(item, published, author, source_title, post_id, media_root)
            post = LocalPost(post_id, title, body, author, published, images)
            if post.key in posts and posts[post.key] != post:
                raise ImportErrorDetail(f"同一原帖在本地有不同内容或图片：{post_id}")
            posts[post.key] = post
    if wanted - found:
        raise ImportErrorDetail("指定的微博 ID 未全部出现在导出中")
    if not posts or len(posts) > max_records:
        raise ImportErrorDetail(f"本次须有 1～{max_records} 条明确记录，可用 --post-id 筛选")
    return list(posts.values())


def field_options(raw: dict) -> list[dict]:
    for name, value in raw.items():
        if name.startswith("property") and isinstance(value, dict) and isinstance(value.get("options"), list):
            return [{k: opt[k] for k in ("id", "text", "style") if k in opt}
                    for opt in value["options"] if isinstance(opt, dict)]
    return []


def resolve_fields(raw_fields: list[dict], doc_key="", tab_id="") -> dict:
    from toolbox.tcdoc import field_maps

    resolved = {}
    for semantic, aliases in ALIASES.items():
        target = field_maps.target(doc_key, tab_id, semantic)
        names = (target,) if target else ()
        matches = [f for f in raw_fields if f.get("fieldTitle") in names]
        if len(matches) != 1 or not matches[0].get("fieldID"):
            if not target:
                continue  # 配置为「不写」：整列跳过
            raise ImportErrorDetail(f"缺少或无法唯一匹配字段：{semantic}")
        raw = matches[0]
        kind = raw.get("fieldType")
        allowed = {1, 9, 17} if semantic in CHOICE_COLUMNS else {
            "封面图": {5}, "发布时间": {4}, "作品链接": {8}}.get(semantic, {1})
        if kind not in allowed:
            raise ImportErrorDetail(f"字段类型不匹配：{semantic}（type={kind}）")
        resolved[semantic] = {"fieldID": raw["fieldID"], "fieldTitle": raw["fieldTitle"],
                              "fieldType": kind, "options": field_options(raw)}
    return resolved


def encode_columns(post: LocalPost, fields: dict, dynamic_type: str, position: str) -> dict:
    values = {}
    for semantic, value in post.columns(dynamic_type, position).items():
        f = fields.get(semantic)
        if f is None:
            continue  # 该字段在配置里被映射成「不写」
        kind = f["fieldType"]
        if kind in {9, 17}:
            options = [o for o in f["options"] if o.get("text") == value and o.get("id")]
            if len(options) != 1:
                names = "、".join(str(o.get("text") or "") for o in f["options"])
                raise ImportErrorDetail(f"「{semantic}」没有唯一既有选项「{value}」（已有：{names or '无'}）；"
                                        "脚本不会新增选项")
            encoded = [options[0]]
        elif kind == 1:
            encoded = [{"type": "text", "text": value}]
        elif kind == 8:
            encoded = [{"type": "url", "text": "微博原帖", "link": value}]
        else:
            encoded = value
        # 官方 addRecords 的 values 使用字段标题作为键，不使用列位置。
        values[f["fieldTitle"]] = encoded
    return values


def existing_keys(records: list[dict], fields: dict) -> dict[str, list[str]]:
    out = {}
    link = fields.get("作品链接")
    if link is None:
        raise ImportErrorDetail("作品链接列已按字段映射配置为不写，无法按链接去重")
    link_title = link["fieldTitle"]
    for row in records:
        value = (row.get("values") or {}).get(link_title) or []
        if not isinstance(value, list):
            raise ImportErrorDetail("原帖链接列返回非预期格式，无法可靠去重")
        for part in value:
            key = weibo_key(part.get("link", "")) if isinstance(part, dict) else None
            if key:
                ids = out.setdefault(key, [])
                rid = str(row["recordID"])
                if rid not in ids:
                    ids.append(rid)
    return out


def check_readback(row: dict, values: dict, post: LocalPost, fields: dict) -> dict:
    saved = row.get("values") or {}
    checks = {}
    for semantic, f in fields.items():
        title, kind = f["fieldTitle"], f["fieldType"]
        actual, expected = saved.get(title), values.get(title)
        if semantic == "封面图":
            actual = actual or []
            checks[semantic] = (isinstance(actual, list) and len(actual) == len(post.images)
                                and all(isinstance(i, dict) for i in actual)
                                and [i.get("title") for i in actual] == [i.path.name for i in post.images]
                                and len({i.get("id") for i in actual if i.get("id")}) == len(post.images))
        elif kind == 1:
            text = "".join(str(p.get("text") or "") for p in actual or [] if isinstance(p, dict))
            checks[semantic] = text == "".join(p["text"] for p in expected)
        elif kind in {9, 17}:
            checks[semantic] = (isinstance(actual, list) and all(isinstance(o, dict) for o in actual) and
                                sorted(str(o.get("id")) for o in actual) == sorted(str(o["id"]) for o in expected))
        elif kind == 8:
            checks[semantic] = isinstance(actual, list) and any(
                isinstance(o, dict) and o.get("link") == post.link for o in actual)
        else:
            checks[semantic] = str(actual) == str(expected)
    return checks


def run_import(client, posts: list[LocalPost], *, apply=False, dynamic_type="动态",
               position="动态", emit: Callable[[dict], None] = lambda event: None) -> dict:
    fields = resolve_fields(client.get_fields(), client.doc_key, client.tab_id)
    records = client.get_records()
    keys = existing_keys(records, fields)
    # 在第一张图片上传前，核对整批所有列、既有选项和本地图片。
    plans = [(post, encode_columns(post, fields, dynamic_type, position)) for post in posts]
    preview = [{"post_id": post.post_id, "title": post.title,
                "published": post.published.isoformat(), "source_link": post.link,
                "operation": "skip" if post.key in keys else "add",
                "existing_record_ids": keys.get(post.key, []),
                "columns": post.columns(dynamic_type, position),
                "images": [{"name": i.path.name, "bytes": i.size, "mime": i.mime} for i in post.images]}
               for post, _ in plans]
    emit({"phase": "preview", "apply": apply, "doc_key": client.doc_key,
          "tab_id": client.tab_id, "sheet_title": client.sheet_title,
          "rows_before": len(records), "records": preview})
    receipt = {"phase": "complete", "apply": apply, "doc_key": client.doc_key,
               "tab_id": client.tab_id, "rows_before": len(records), "rows_after": len(records),
               "planned_adds": sum(post.key not in keys for post, _ in plans),
               "added": [], "skipped": [], "uploaded_images": 0, "updated": 0, "deleted": 0}
    if not apply:
        return receipt
    for post, values in plans:
        try:
            if resolve_fields(client.get_fields(), client.doc_key, client.tab_id) != fields:
                raise ImportErrorDetail("预览后字段或选项已变化，请重新预览")
            current = client.get_records()
            current_keys = existing_keys(current, fields)
            if post.key in current_keys:
                receipt["skipped"].append({"post_id": post.post_id, "record_ids": current_keys[post.key]})
                receipt["rows_after"] = len(current)
                continue
            for image in post.images:
                image.read_verified()
            image_ids = []
            for image in post.images:
                image_ids.append(client.upload_image(image))
                receipt["uploaded_images"] += 1
                emit({"phase": "image_uploaded", "post_id": post.post_id,
                      "completed": len(image_ids), "total": len(post.images), "name": image.path.name})
            # 上传期间可能有人录入同一条；再次查回后跳过已有记录。
            latest = client.get_records()
            latest_keys = existing_keys(latest, fields)
            if post.key in latest_keys:
                receipt["skipped"].append({"post_id": post.post_id, "record_ids": latest_keys[post.key]})
                receipt["rows_after"] = len(latest)
                continue
            if resolve_fields(client.get_fields(), client.doc_key, client.tab_id) != fields:
                raise ImportErrorDetail("上传期间字段或选项已变化，未新增记录")
            values = dict(values)
            values[fields["封面图"]["fieldTitle"]] = [{"imageID": i} for i in image_ids]
            record_id = client.add_record(values)
            added = {"post_id": post.post_id, "title": post.title, "record_id": record_id,
                     "image_count": len(post.images), "verified": False}
            receipt["added"].append(added)
            emit({"phase": "record_created", **added})
            fresh = client.get_records()
            saved = [r for r in fresh if r.get("recordID") == record_id]
            if len(saved) != 1:
                raise ImportErrorDetail("已新增记录未能查回，请按已返回的 record_id 核查")
            checks = check_readback(saved[0], values, post, fields)
            added.update({"verified": all(checks.values()), "checks": checks})
            receipt["rows_after"] = len(fresh)
            emit({"phase": "readback", **added})
            if not added["verified"]:
                raise ImportErrorDetail("已新增记录读回不一致，停止后续记录")
        except ImportErrorDetail as exc:
            emit({**receipt, "phase": "stopped", "post_id": post.post_id,
                  "source_link": post.link, "unknown_record_outcome": isinstance(exc, UnknownWriteResult),
                  "error": str(exc)})
            raise
    return receipt


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc-key", required=True)
    parser.add_argument("--tab-id", required=True)
    parser.add_argument("--allow-test-document", action="store_true", help="仅放行已授权的测试表格/智能表1")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="明确执行新增；默认仅在线预览")
    modes.add_argument("--inspect", action="store_true", help="只读取目标字段及已有选项")
    modes.add_argument("--offline-preview", action="store_true", help="只检查本地素材；未验证在线字段与重复记录")
    parser.add_argument("--export", action="append", type=Path, default=[], help="本项目 JSON 列表导出，可重复指定")
    parser.add_argument("--post-id", action="append", default=[], help="从导出筛选微博 ID，可重复指定")
    parser.add_argument("--media-root", type=Path, default=_PROJECT / "download/已获取数据/微博_媒体")
    parser.add_argument("--max-records", type=int, default=20, help="整批记录上限，默认 20，最高 50")
    parser.add_argument("--dynamic-type", default="动态", help="匹配已有动态类型选项")
    parser.add_argument("--position", default="动态", help="匹配已有发布位置选项；不根据 IP 或设备猜测")
    parser.add_argument("--managed-config-dir", type=Path, help="管理器正在使用的已有目录；不创建或迁移")
    return parser


def main(argv=None) -> int:
    args = make_parser().parse_args(argv)

    def emit(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)

    try:
        validate_target(args.doc_key, args.tab_id, args.allow_test_document)
        if not 1 <= args.max_records <= 50:
            raise ImportErrorDetail("--max-records 必须为 1～50")
        if not args.inspect and not args.export:
            raise ImportErrorDetail("请提供至少一个 --export 本地 JSON 文件")
        posts = [] if args.inspect else load_posts(args.export, args.media_root, args.post_id, args.max_records)
        if args.offline_preview:
            emit({"phase": "offline_preview", "apply": False, "doc_key": args.doc_key,
                  "tab_id": args.tab_id, "online_verified": False,
                  "records": [{"post_id": p.post_id, "columns": p.columns(args.dynamic_type, args.position),
                               "images": [{"name": i.path.name, "bytes": i.size} for i in p.images]} for p in posts]})
            return 0
        auth = ManagedAuth(managed_cookie_reader(args.managed_config_dir))
        client = LocalImportClient(auth, args.doc_key, args.tab_id, args.allow_test_document)
        client.connect()
        if args.inspect:
            emit({"phase": "schema", "doc_key": args.doc_key, "tab_id": args.tab_id,
                  "sheet_title": client.sheet_title,
                  "fields": [{"id": f.get("fieldID"), "title": f.get("fieldTitle"),
                              "type": f.get("fieldType"), "options": field_options(f)} for f in client.get_fields()]})
            return 0
        receipt = run_import(client, posts, apply=args.apply, dynamic_type=args.dynamic_type,
                             position=args.position, emit=emit)
        emit(receipt)
        return 0
    except ImportErrorDetail as exc:
        emit({"phase": "error", "error": str(exc), "unknown_record_outcome": isinstance(exc, UnknownWriteResult)})
        return 3 if isinstance(exc, UnknownWriteResult) else 2
    except Exception as exc:  # 不转储 traceback、服务器响应、原始导出或认证对象
        emit({"phase": "error", "error": "本地导入异常：" + type(exc).__name__})
        return 2


if __name__ == "__main__":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    install_readonly_guard()
    sys.exit(main())
