"""B站创作中心 UPOS 分片上传与可配置投稿。网页内部接口可能随站点变更。"""
import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from contextlib import contextmanager, ExitStack
from urllib.parse import parse_qsl, urlparse

import requests

from toolbox.durable_io import atomic_write_json
from toolbox.organize_engine import VIDEO_EXTS, _locked_reader
from toolbox.utils import extract_date_from_filename

UPLOAD_PAGE = "https://member.bilibili.com/platform/upload/video/frame"
DEFAULTS = {"title": "封茗囧菌{日期}直播全程无弹幕{分P说明}～", "desc": "",
            "tags": "封茗囧菌,直播录播", "tid": 0, "copyright": 2,
            "source": "", "cover": "", "date": "", "auto_publish": True, "platform": "抖子",
            "threads": 3}
SOURCE_PLATFORMS = ("抖子", "B站")
# 预上传节点按序尝试：某个节点的域名/连接不通时换下一个，避免整场投稿卡在第 1 个分片。
UPLOAD_CDNS = ("bda2", "alia", "cos")
UPLOAD_THREADS = 3       # 并行分片线程数默认值（与 biliup 默认一致）
MAX_UPLOAD_THREADS = 8   # 并行上限：再多只会互相抢带宽，还容易触发风控
UPLOAD_ATTEMPTS = 10     # 单个分片最多尝试次数
UPLOAD_BACKOFF = 1.0     # 重试退避起点（秒），逐次翻倍、上限 8 秒


def upload_threads(value=None):
    """并行分片线程数：1～8，缺省 3。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return UPLOAD_THREADS
    return max(1, min(MAX_UPLOAD_THREADS, number))


def today_compact():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")


def normalize_platform(value):
    value = "抖子" if value in ("抖音", "douyin") else value
    return value if value in SOURCE_PLATFORMS else "抖子"


def detect_source_platform(paths):
    """从标准录播文件名的平台字段识别一致来源；未知或混选时不猜测。"""
    aliases = {"抖音": "抖子", "抖子": "抖子", "douyin": "抖子",
               "b站": "B站", "bilibili": "B站"}
    platforms = set()
    for path in paths:
        match = re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[^_]+(?:_[^_]*)*?_(?:有弹幕|无弹幕)_([^_]+)(?:_|$)", Path(path).stem)
        platform = aliases.get(match[1].casefold()) if match else ""
        if not platform:
            return ""
        platforms.add(platform)
    return next(iter(platforms)) if len(platforms) == 1 else ""


def video_path_key(path):
    """同一文件的路径标识，统一分隔符、相对段及Windows大小写。"""
    return os.path.normcase(str(Path(path).resolve()))


def unique_video_paths(paths):
    result, seen = [], set()
    for path in paths:
        key = video_path_key(path)
        if key not in seen:
            seen.add(key)
            result.append(str(Path(path)))
    return result


def unique_video_records(records):
    """保留首条记录及顺序；扫描记录优先于旧的传入记录。"""
    result, seen = [], set()
    for record in records:
        key = video_path_key(record["path"])
        if key not in seen:
            seen.add(key)
            result.append({**record, "path": str(Path(record["path"]))})
    return result


def scan_workspace_videos(work_dir):
    """只读当前工作目录的视频，跳过链接、junction 与元数据临时目录。"""
    from toolbox.rename_engine import is_standard_recording_name
    if not work_dir or not Path(work_dir).is_dir():
        return []
    root, records = Path(work_dir).resolve(), []
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".fmjj-")
                         and not (Path(folder) / name).is_symlink()
                         and not getattr(Path(folder) / name, "is_junction", lambda: False)())
        for name in sorted(names):
            path = Path(folder) / name
            if path.suffix.lower() not in VIDEO_EXTS or path.is_symlink() or not path.is_file():
                continue
            date = extract_date_from_filename(name) or extract_date_from_filename(Path(folder).name) or ""
            records.append({"path": str(path), "date": date,
                            "renamed": is_standard_recording_name(name)})
    return sorted(records, key=lambda item: (item["date"], item["path"]))


def cookie_identity(cookie):
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest() if cookie else ""


def credential_identity():
    from auth import get_cookie
    return cookie_identity(get_cookie("bilibili"))


def validate_renamed_videos(paths):
    pending = [Path(path).name for path in paths if not is_renamed_video(path)]
    if pending:
        raise ValueError("请先在命名投稿页完成命名，再开始投稿：" + "、".join(pending[:3]))


def is_renamed_video(path):
    from toolbox.rename_engine import is_standard_recording_name
    return is_standard_recording_name(str(path))


def multipart_count(paths, date=""):
    """只把今天同一主播/平台/弹幕版本的有效录播算作一条稿件的多个 P。"""
    today = today_compact()
    if len(paths) < 2 or (date and str(date).replace("-", "") != today):
        return 0
    groups, unique = set(), set()
    for value in paths:
        path = Path(value)
        if not is_renamed_video(path) or path.suffix.lower() not in VIDEO_EXTS:
            return 0
        if extract_date_from_filename(path.name) != today:
            return 0
        # 标准交互命名中，日期/时间后是账户；直播类型和附加标题可以不同。
        match = re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_([^_]+)(?:_[^_]*)*?_(有弹幕|无弹幕)_([^_]+)(?:_|$)", path.stem)
        if not match:
            return 0
        platform = match[3]
        platform = "抖子" if platform in ("抖音", "抖子", "douyin") else platform
        groups.add((match[1], match[2], platform))
        try:
            if path.is_symlink() or not path.is_file() or not path.stat().st_size:
                return 0
            unique.add(os.path.normcase(str(path.resolve())))
        except OSError:
            return 0
    return len(paths) if len(groups) == 1 and len(unique) == len(paths) else 0


def load_settings():
    from toolbox.config_manager import get_config_dir
    try:
        result = json.loads((get_config_dir() / "organize.json").read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    from toolbox.config_manager import get_config_dir
    atomic_write_json(get_config_dir() / "organize.json", settings)


def build_metadata(paths, settings):
    config = {**DEFAULTS, **settings}
    if not paths:
        raise ValueError("请先选择视频")
    dates = {extract_date_from_filename(Path(path).name) for path in paths}
    date = config.get("date") or (next(iter(dates)) if len(dates) == 1 else "")
    if not date:
        raise ValueError("无法识别同一场日期，请填写日期或按日期分组投稿")
    from datetime import datetime
    compact = str(date).replace("-", "")
    datetime.strptime(compact, "%Y%m%d")
    count = len(paths)
    parts = multipart_count(paths, compact)
    if count > 1 and not parts:
        raise ValueError("只有今天同一主播、平台、弹幕版本的多个有效录播才可组成一条分P稿件；请按条件分组或单独选择视频")
    values = {"日期": compact, "文件名": Path(paths[0]).stem, "P数": count,
              "平台": normalize_platform(config.get("platform")),
              "分P说明": (f"（分{'两' if parts == 2 else parts} p）" if parts else "")}
    try:
        title = config["title"].format(**values).strip()
        desc = config["desc"].format(**values)
    except (KeyError, ValueError, IndexError) as error:
        raise ValueError("文案模板仅支持 {日期}、{平台}、{文件名}、{P数}、{分P说明}") from error
    if not title or len(title) > 80:
        raise ValueError("标题须为 1～80 个字符")
    if len(desc) > 2000:
        raise ValueError("简介不能超过 2000 字符")
    tid = int(config.get("tid") or 0)
    if tid <= 0:
        raise ValueError("请填写投稿分区 ID（图片未提供分区）")
    tags = [tag.strip() for tag in str(config["tags"]).replace("，", ",").split(",") if tag.strip()]
    if not 1 <= len(tags) <= 12:
        raise ValueError("请填写 1～12 个标签，以逗号分隔")
    copyright = int(config["copyright"])
    if copyright not in (1, 2):
        raise ValueError("投稿类型须为自制或转载")
    if copyright == 2 and not str(config["source"]).strip():
        raise ValueError("转载投稿请填写原直播来源链接")
    return {"title": title, "desc": desc, "tag": ",".join(tags), "tid": tid,
            "copyright": copyright, "source": config["source"] if copyright == 2 else "",
            "dynamic": "", "cover": "", "desc_format_id": 0, "interactive": 0,
            "no_reprint": 0, "open_elec": 0, "origin_state": 0,
            "subtitles": {"lan": "", "open": 0}, "up_close_danmaku": False,
            "up_close_reply": False, "up_selection_reply": False}


class UploadError(RuntimeError):
    pass


class UploadCancelled(UploadError):
    pass


class UploadNodeError(UploadError):
    """单个 CDN 节点不可用（域名不通、连不上、初始化被拒）；还没传任何分片，可换节点重试。"""


def _failure_reason(error):
    """把 requests 异常翻成一句能直接定位的失败原因。"""
    text = str(error)
    if isinstance(error, requests.exceptions.ConnectTimeout):
        return "连接超时"
    if isinstance(error, requests.exceptions.ReadTimeout):
        return "传输超时（120 秒无响应）"
    if isinstance(error, requests.exceptions.SSLError):
        return "TLS 握手失败"
    if "NameResolutionError" in text or "Name or service not known" in text \
            or "getaddrinfo failed" in text or "No address associated with hostname" in text:
        return "域名解析失败"
    if isinstance(error, requests.exceptions.Timeout):
        return "请求超时"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "连接被重置或中断"
    return type(error).__name__


class UploadHistory:
    """已投稿与响应不明的任务不能再次自动提交，避免重复发布。"""
    def __init__(self, path=None):
        if path is None:
            from toolbox.config_manager import get_config_dir
            path = get_config_dir() / "bili_upload.db"
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT, data TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def claim(self, key, data):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state, data FROM jobs WHERE id=?", (key,)).fetchone()
            if row and row[0] in ("uploading", "submitting", "uncertain", "published"):
                raise UploadError(f"任务状态为 {row[0]}，请先在投稿管理核对；已拦截重复投稿")
            previous = json.loads(row[1]) if row else {}
            data["videos"] = previous.get("videos", [])
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)",
                       (key, "uploading", json.dumps(data, ensure_ascii=False)))
        return data

    def save(self, key, state, data):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?, data=? WHERE id=?",
                       (state, json.dumps(data, ensure_ascii=False), key))

    def recent(self):
        with self.connect() as db:
            return [(key, state, json.loads(data)) for key, state, data in
                    db.execute("SELECT id,state,data FROM jobs ORDER BY rowid DESC LIMIT 30")]


class BiliUploader:
    def __init__(self, cookie=None, on_progress=None, cancel=None, history=None):
        self.managed_auth = cookie is None
        if cookie is None:
            from auth import get_cookie
            cookie = get_cookie("bilibili")
        from http.cookies import SimpleCookie
        parsed = SimpleCookie()
        parsed.load(cookie or "")
        if "SESSDATA" not in parsed or "bili_jct" not in parsed:
            raise UploadError("请先在 Cookie 管理登录 B站并启用 Cookie，需要 SESSDATA 与 bili_jct")
        self.csrf = parsed["bili_jct"].value
        self.auth_identity = cookie_identity(cookie)
        self.member = requests.Session()
        self.member.headers.update({"Cookie": cookie, "User-Agent": "Mozilla/5.0",
                                    "Referer": UPLOAD_PAGE, "Origin": "https://member.bilibili.com"})
        # UPOS 客户端只携带一次性上传授权，绝不转发账号 Cookie。
        self.upos = requests.Session()
        # 上传节点一律直连：环境代理（HTTP_PROXY/HTTPS_PROXY）常让分片 PUT 连不上，
        # 且失败时只剩一句“网络错误”，很难判断是代理还是节点的问题。
        self.upos.trust_env = False
        self._lines = None
        self.threads = UPLOAD_THREADS
        self.cancel = cancel or threading.Event()
        self.progress = on_progress or (lambda done, total, text: None)
        self.history = history or UploadHistory()

    def close(self):
        self.member.close()
        self.upos.close()

    def _check_cancel(self):
        if self.cancel.is_set():
            raise UploadCancelled("已取消；本地视频保留")
        if self.managed_auth and credential_identity() != self.auth_identity:
            raise UploadCancelled("Cookie 管理的登录状态已改变，本任务已停止；请重新进入投稿窗口")

    def _json(self, session, method, url, **kwargs):
        # node=True：请求的是上传节点，失败要带上原因并允许换节点重试。
        node = kwargs.pop("node", False)
        self._check_cancel()
        try:
            response = session.request(method, url, timeout=(15, 120), allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            reason = _failure_reason(error)
            if node:
                raise UploadNodeError(f"节点请求失败（{reason}）") from error
            raise UploadError(f"网络请求失败或超时（{reason}），请重试/检查任务状态") from error
        if node and response.status_code >= 400:
            raise UploadNodeError(f"节点返回 HTTP {response.status_code}")
        if response.status_code == 412:
            raise UploadError("B站返回 412 风控；请在官方投稿页面检查登录和验证后重试")
        if response.status_code >= 400 or 300 <= response.status_code < 400:
            raise UploadError(f"接口返回 HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            if node:
                raise UploadNodeError("节点未返回 JSON") from error
            raise UploadError("接口未返回 JSON，可能需要网页登录验证") from error
        if not isinstance(data, dict):
            raise UploadError("接口响应结构发生变化")
        return data

    @staticmethod
    def _require_ok(data):
        if data.get("code") != 0:
            raise UploadError(f"B站接口错误 {data.get('code')}：{data.get('message') or '请求被拒绝'}")
        return data.get("data") or {}

    def check_account(self):
        data = self._require_ok(self._json(self.member, "GET", "https://api.bilibili.com/x/web-interface/nav"))
        if not data.get("isLogin"):
            raise UploadError("B站登录已失效，请重新登录")
        return {"mid": data["mid"], "name": data.get("uname") or str(data["mid"])}

    def creator_info(self):
        return self._require_ok(self._json(self.member, "GET",
                                           "https://member.bilibili.com/x/vupre/web/archive/pre"))

    def _upload_video(self, path, complete_before, total_size):
        """按线路依次尝试（参考 biliup）：一条线路整段失败才换下一条，避免重复上传。"""
        path = Path(path)
        size = path.stat().st_size
        before = (size, path.stat().st_mtime_ns)
        lines = self._upload_lines()
        failures = []
        for position, line in enumerate(lines):
            name = str(line.get("upcdn") or "upos")
            try:
                return self._upload_via_line(path, size, before, line, complete_before, total_size)
            except UploadNodeError as error:
                failures.append(f"{name}：{error}")
                if position + 1 < len(lines):
                    following = str(lines[position + 1].get("upcdn") or "下一条")
                    self.progress(complete_before, total_size,
                                  f"{name} 线路不可用（{error}），改用 {following} 重试 {path.name}")
        raise UploadError("上传线路都没能完成（" + "；".join(failures) + "）；本地视频保留")

    def _upload_lines(self):
        """预上传线路：优先用 B 站 ?r=probe 的实测结果，失败再退回固定清单。"""
        if self._lines is None:
            self._lines = self._probe_lines()
        return self._lines or [{"upcdn": cdn, "probe_version": "20211012"} for cdn in UPLOAD_CDNS]

    def _probe_lines(self):
        """问 B 站要可选上传线路，并行测延迟后按快慢排序；任何异常都退回空表。"""
        try:
            data = self._json(self.member, "GET", "https://member.bilibili.com/preupload",
                              params={"r": "probe"})
        except UploadError:
            return []
        lines = [line for line in (data.get("lines") or [])
                 if isinstance(line, dict) and str(line.get("os") or "") == "upos"
                 and line.get("query") and line.get("probe_url")]
        if not lines:
            return []
        costs = {}

        def cost(line):
            start = time.monotonic()
            try:
                self.member.get("https:" + str(line["probe_url"]), timeout=(2, 3))
            except requests.RequestException:
                return None
            return time.monotonic() - start

        pool = ThreadPoolExecutor(max_workers=min(8, len(lines)))
        try:
            jobs = {pool.submit(cost, line): line for line in lines}
            for job in as_completed(list(jobs), timeout=6):
                try:
                    value = job.result()
                except Exception:
                    continue
                if value is not None:
                    costs[str(jobs[job]["query"])] = value
        except Exception:
            pass
        finally:
            pool.shutdown(wait=False)
        ordered = sorted(lines, key=lambda line: costs.get(str(line["query"]), float("inf")))
        return [dict(parse_qsl(str(line["query"]))) for line in ordered]

    def _upload_via_line(self, path, size, before, line, complete_before, total_size):
        pre = self._json(self.member, "GET", "https://member.bilibili.com/preupload", params={
            "profile": "ugcfx/bup", "name": path.name, "size": size, "r": "upos", "ssl": 0,
            "version": "2.14.0", "build": "2100400", "probe_version": "20211012", **line})
        if pre.get("OK") != 1:
            raise UploadError("预上传被拒绝，请在官方投稿页面检查账号")
        endpoint = str(pre.get("endpoint") or "")
        endpoint = "https:" + endpoint if endpoint.startswith("//") else endpoint
        host = urlparse(endpoint)
        if host.scheme != "https" or not (host.hostname or "").endswith(".bilivideo.com") or host.username:
            raise UploadNodeError("返回了无法识别的上传地址")
        uri = str(pre.get("upos_uri") or "")
        if not uri.startswith("upos://") or any(part in ("", ".", "..") for part in uri[7:].split("/")):
            raise UploadNodeError("返回的文件标识不合法")
        url = endpoint.rstrip("/") + "/" + uri[7:]
        headers = {"x-upos-auth": pre["auth"]}
        chunk_size = int(pre["chunk_size"])
        if not 1 <= chunk_size <= 64 * 1024 * 1024:
            raise UploadNodeError("返回的分片大小不合法")
        init = self._json(self.upos, "POST", url, headers=headers, node=True, params={
            "uploads": "", "output": "json", "profile": "ugcfx/bup", "filesize": size,
            "partsize": chunk_size, "biz_id": pre["biz_id"]})
        if init.get("OK") != 1 or not init.get("upload_id"):
            raise UploadNodeError("上传任务初始化失败")
        chunks = math.ceil(size / chunk_size)
        parts = self._upload_chunks(url, headers, path, chunk_size, chunks, size,
                                    init["upload_id"], complete_before, total_size)
        if (path.stat().st_size, path.stat().st_mtime_ns) != before:
            raise UploadError("上传期间视频变化，本次不投稿")
        # 合并失败只影响这一条线路的重传成本，多试几次（biliup 也是重试合并）。
        result = {}
        for attempt in range(5):
            try:
                result = self._json(self.upos, "POST", url, headers=headers, json={"parts": parts}, params={
                    "output": "json", "name": path.name, "profile": "ugcfx/bup",
                    "uploadId": init["upload_id"], "biz_id": pre["biz_id"]})
            except UploadError as error:
                if attempt == 4 or "412" in str(error):
                    raise
                self.cancel.wait(5 + attempt * 5)
                continue
            if result.get("OK") == 1:
                break
            self.cancel.wait(5 + attempt * 5)
        if result.get("OK") != 1:
            raise UploadError("分片合并失败（" + str(result.get("message") or f"code {result.get('code')}") + "），本次不投稿")
        if not result.get("key"):
            raise UploadError("分片合并未返回文件标识，本次不投稿")
        return {"filename": os.path.splitext(str(result["key"]).lstrip("/"))[0],
                "title": path.stem[:80], "desc": "", "cid": pre["biz_id"]}

    def _upload_chunks(self, url, headers, path, chunk_size, chunks, size, upload_id,
                       complete_before, total_size):
        """多线程并行传分片：单连接被限速、网关空闲超时时更快，也不容易整段卡住。"""
        parts = [None] * chunks
        errors, lock = [], threading.Lock()
        cursor, finished = [0], [0]

        def worker():
            while not self.cancel.is_set():
                with lock:
                    number = cursor[0]
                    if number >= chunks:
                        return
                    cursor[0] = number + 1
                start = number * chunk_size
                try:
                    with open(path, "rb") as stream:
                        stream.seek(start)
                        chunk = stream.read(chunk_size)
                except OSError as error:
                    with lock:
                        errors.append((number, f"读取文件失败（{error}）", 0))
                    return
                if not chunk:
                    with lock:
                        errors.append((number, "上传期间文件被截断", 0))
                    return
                try:
                    response, reason = self._put_chunk(url, headers, chunk, upload_id,
                                                       number, chunks, start, size)
                except UploadCancelled:
                    return
                if reason:
                    with lock:
                        if not errors:
                            errors.append((number, reason,
                                           response.status_code if response is not None else 0))
                    return
                parts[number] = {"partNumber": number + 1,
                                 "eTag": response.headers.get("ETag", "etag").strip('"')}
                with lock:
                    finished[0] += 1
                    done = finished[0]
                self.progress(min(total_size, complete_before + done * chunk_size), total_size,
                              f"上传 {path.name} · {done}/{chunks}")

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(max(1, min(self.threads, chunks)))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self._check_cancel()
        if errors:
            number, reason, status = sorted(errors)[0]
            detail = f"第 {number + 1}/{chunks} 个分片上传失败（{reason}）"
            # 4xx 多半是鉴权/参数问题，换线路没用；5xx、超时、断连、504 网关超时都算线路故障。
            if 400 <= status < 500 and status not in (408, 429):
                raise UploadError(detail + "；本地视频保留")
            raise UploadNodeError(detail)
        return parts

    def _put_chunk(self, url, headers, chunk, upload_id, number, chunks, start, size):
        """分片 PUT：最多 4 次，退避等待，重试换新连接；返回（响应, 失败原因），成功时原因为空串。"""
        reason, response = "", None
        for attempt in range(UPLOAD_ATTEMPTS):
            self._check_cancel()
            # 重试时强制换新连接：网关/代理上的旧 keep-alive 连接常直接回 504。
            request_headers = headers if not attempt else {**headers, "Connection": "close"}
            try:
                response = self.upos.put(url, data=chunk, headers=request_headers, timeout=(15, 120),
                    allow_redirects=False, params={"partNumber": number + 1, "uploadId": upload_id,
                        "chunk": number, "chunks": chunks, "size": len(chunk), "start": start,
                        "end": start + len(chunk), "total": size})
            except requests.RequestException as error:
                reason, response = _failure_reason(error), None
            else:
                text = (response.text or "").strip()
                if response.status_code < 300 and text in ("", "MULTIPART_PUT_SUCCESS"):
                    return response, ""
                reason = f"HTTP {response.status_code}" + (f"，响应 {text[:80]}" if text else "")
            if attempt + 1 < UPLOAD_ATTEMPTS and self.cancel.wait(min(UPLOAD_BACKOFF * 2 ** attempt, 8)):
                self._check_cancel()
        return response, reason

    def _upload_cover(self, video, cover):
        with tempfile.TemporaryDirectory(prefix="fmjj-cover-") as folder:
            if cover:
                from PIL import Image
                image = Image.open(cover).convert("RGB")
                image.thumbnail((1920, 1080))
                path = Path(folder) / "cover.jpg"
                image.save(path, quality=90)
            else:
                from toolbox.live_record import find_ffmpeg
                exe = find_ffmpeg()
                if not exe:
                    raise UploadError("自动封面需要 FFmpeg，也可自行选择封面图片")
                path = Path(folder) / "cover.jpg"
                result = subprocess.run([exe, "-v", "error", "-ss", "0", "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=1280:-2", "-y", str(path)],
                    capture_output=True, timeout=60)
                if result.returncode or not path.exists():
                    raise UploadError("无法提取视频封面，请选择封面图片")
            data_uri = "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            data = self._require_ok(self._json(self.member, "POST", "https://member.bilibili.com/x/vu/web/cover/up",
                data={"cover": data_uri, "csrf": self.csrf}))
            if not data.get("url"):
                raise UploadError("封面上传未返回 URL")
            return data["url"]

    def run(self, paths, settings):
        validate_renamed_videos(paths)
        self.threads = upload_threads(settings.get("threads"))
        # 在封面/分片/提交期间阻止 Windows 写入，防止暂停写盘的录制视频被当成整场。
        with ExitStack() as readers:
            for path in paths:
                self._check_cancel()
                readers.enter_context(_locked_reader(path))
            return self._run_locked(paths, settings)

    def _run_locked(self, paths, settings):
        paths = [Path(path).resolve() for path in paths]
        metadata = build_metadata(paths, settings)
        snapshots = []
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS or path.is_symlink():
                raise UploadError("只支持普通视频文件")
            stat = path.stat()
            if not stat.st_size:
                raise UploadError("不能上传空视频")
            if time.time_ns() - stat.st_mtime_ns < 30 * 10**9:
                raise UploadError("视频最近仍有写入，请等待录制结束至少30秒后上传")
            snapshots.append((str(path), stat.st_size, stat.st_mtime_ns))
        account = self.check_account()
        identity = json.dumps([account["mid"], snapshots], ensure_ascii=False).encode("utf-8")
        key = hashlib.sha256(identity).hexdigest()
        task = self.history.claim(key, {"title": metadata["title"], "account": account,
                                       "paths": [str(path) for path in paths], "videos": []})
        status = "failed"
        try:
            metadata["cover"] = self._upload_cover(paths[0], settings.get("cover") or "")
            total = sum(item[1] for item in snapshots)
            completed = 0
            for index, path in enumerate(paths):
                if index >= len(task["videos"]):
                    task["videos"].append(self._upload_video(path, completed, total))
                    self.history.save(key, "uploading", task)
                completed += snapshots[index][1]
            for path, size, mtime in snapshots:
                current = Path(path).stat()
                if (current.st_size, current.st_mtime_ns) != (size, mtime):
                    raise UploadError("视频已变化，本次不投稿")
            metadata["videos"] = task["videos"]
            if not settings.get("auto_publish", True):
                status = "uploaded"
                return {"state": status, "task": key, "title": metadata["title"]}
            self._check_cancel()
            metadata["csrf"] = self.csrf
            # 从此处开始，超时/取消均不能证明服务器未收稿，禁止自动重发。
            status = "uncertain"
            self.history.save(key, "submitting", task)
            response = self._json(self.member, "POST", "https://member.bilibili.com/x/vu/web/add/v3",
                                   params={"csrf": self.csrf}, json=metadata)
            if response.get("code") != 0:
                status = "failed"
                self._require_ok(response)
            result = response.get("data") or {}
            if not result.get("bvid") and not result.get("aid"):
                raise UploadError("投稿响应没有稿件 ID，请在官方投稿管理核对")
            task["result"] = result
            status = "published"
            return {"state": status, **result, "task": key, "title": metadata["title"]}
        finally:
            self.history.save(key, status, task)
