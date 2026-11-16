"""B站创作中心 UPOS 分片上传与可配置投稿。网页内部接口可能随站点变更。"""
import base64
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from contextlib import contextmanager, ExitStack
from urllib.parse import urlparse

import requests

from toolbox.durable_io import atomic_write_json
from toolbox.organize_engine import VIDEO_EXTS, _locked_reader
from toolbox.utils import extract_date_from_filename

UPLOAD_PAGE = "https://member.bilibili.com/platform/upload/video/frame"
DEFAULTS = {"title": "封茗囧菌{日期}直播全程无弹幕{分P说明}～", "desc": "",
            "tags": "封茗囧菌,直播录播", "tid": 0, "copyright": 2,
            "source": "", "cover": "", "date": "", "auto_publish": True}


def load_settings():
    from toolbox.config_manager import get_config_dir
    try:
        return json.loads((get_config_dir() / "organize.json").read_text(encoding="utf-8"))
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
    values = {"日期": compact, "文件名": Path(paths[0]).stem, "P数": count,
              "分P说明": (f"（分{'两' if count == 2 else count} p）" if count > 1 else "")}
    try:
        title = config["title"].format(**values).strip()
        desc = config["desc"].format(**values)
    except (KeyError, ValueError, IndexError) as error:
        raise ValueError("文案模板仅支持 {日期}、{文件名}、{P数}、{分P说明}") from error
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
        if cookie is None:
            from auth import get_cookie
            cookie = get_cookie("bilibili")
        from http.cookies import SimpleCookie
        parsed = SimpleCookie()
        parsed.load(cookie or "")
        if "SESSDATA" not in parsed or "bili_jct" not in parsed:
            raise UploadError("请先在 Cookie 管理登录 B站并启用 Cookie，需要 SESSDATA 与 bili_jct")
        self.csrf = parsed["bili_jct"].value
        self.member = requests.Session()
        self.member.headers.update({"Cookie": cookie, "User-Agent": "Mozilla/5.0",
                                    "Referer": UPLOAD_PAGE, "Origin": "https://member.bilibili.com"})
        # UPOS 客户端只携带一次性上传授权，绝不转发账号 Cookie。
        self.upos = requests.Session()
        self.cancel = cancel or threading.Event()
        self.progress = on_progress or (lambda done, total, text: None)
        self.history = history or UploadHistory()

    def close(self):
        self.member.close()
        self.upos.close()

    def _check_cancel(self):
        if self.cancel.is_set():
            raise UploadCancelled("已取消；本地视频保留")

    def _json(self, session, method, url, **kwargs):
        self._check_cancel()
        try:
            response = session.request(method, url, timeout=(15, 120), allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            raise UploadError("网络请求失败或超时，请重试/检查任务状态") from error
        if response.status_code == 412:
            raise UploadError("B站返回 412 风控；请在官方投稿页面检查登录和验证后重试")
        if response.status_code >= 400 or 300 <= response.status_code < 400:
            raise UploadError(f"接口返回 HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
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
        path = Path(path)
        size = path.stat().st_size
        before = (size, path.stat().st_mtime_ns)
        pre = self._json(self.member, "GET", "https://member.bilibili.com/preupload", params={
            "profile": "ugcfx/bup", "name": path.name, "size": size, "r": "upos", "ssl": 0,
            "version": "2.14.0", "build": "2100400", "upcdn": "bda2", "probe_version": "20211012"})
        if pre.get("OK") != 1:
            raise UploadError("预上传被拒绝，请在官方投稿页面检查账号")
        endpoint = str(pre.get("endpoint") or "")
        endpoint = "https:" + endpoint if endpoint.startswith("//") else endpoint
        host = urlparse(endpoint)
        if host.scheme != "https" or not (host.hostname or "").endswith(".bilivideo.com") or host.username:
            raise UploadError("预上传返回了无法识别的上传地址")
        uri = str(pre.get("upos_uri") or "")
        if not uri.startswith("upos://") or any(part in ("", ".", "..") for part in uri[7:].split("/")):
            raise UploadError("预上传文件标识不合法")
        url = endpoint.rstrip("/") + "/" + uri[7:]
        headers = {"x-upos-auth": pre["auth"]}
        chunk_size = int(pre["chunk_size"])
        if not 1 <= chunk_size <= 64 * 1024 * 1024:
            raise UploadError("上传分片大小不合法")
        init = self._json(self.upos, "POST", url, headers=headers, params={
            "uploads": "", "output": "json", "profile": "ugcfx/bup", "filesize": size,
            "partsize": chunk_size, "biz_id": pre["biz_id"]})
        if init.get("OK") != 1 or not init.get("upload_id"):
            raise UploadError("上传任务初始化失败")
        chunks, parts = math.ceil(size / chunk_size), []
        with open(path, "rb") as stream:
            for number in range(chunks):
                self._check_cancel()
                chunk = stream.read(chunk_size)
                start = number * chunk_size
                if not chunk:
                    raise UploadError("上传期间文件被截断，请重新选择")
                for attempt in range(3):
                    try:
                        response = self.upos.put(url, data=chunk, headers=headers, timeout=(15, 120),
                            allow_redirects=False, params={"partNumber": number + 1, "uploadId": init["upload_id"],
                                "chunk": number, "chunks": chunks, "size": len(chunk), "start": start,
                                "end": start + len(chunk), "total": size})
                        if response.status_code < 300 and response.text.strip() in ("", "MULTIPART_PUT_SUCCESS"):
                            break
                    except requests.RequestException:
                        pass
                    if attempt == 2:
                        raise UploadError(f"第 {number + 1} 个分片上传失败；本地视频保留")
                    if self.cancel.wait(1):
                        self._check_cancel()
                parts.append({"partNumber": number + 1, "eTag": response.headers.get("ETag", "etag").strip('"')})
                self.progress(complete_before + start + len(chunk), total_size, f"上传 {path.name} · {number + 1}/{chunks}")
        if (path.stat().st_size, path.stat().st_mtime_ns) != before:
            raise UploadError("上传期间视频变化，本次不投稿")
        result = self._json(self.upos, "POST", url, headers=headers, json={"parts": parts}, params={
            "output": "json", "name": path.name, "profile": "ugcfx/bup",
            "uploadId": init["upload_id"], "biz_id": pre["biz_id"]})
        if result.get("OK") != 1 or not result.get("key"):
            raise UploadError("分片合并失败，本次不投稿")
        return {"filename": os.path.splitext(str(result["key"]).lstrip("/"))[0],
                "title": path.stem[:80], "desc": "", "cid": pre["biz_id"]}

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
