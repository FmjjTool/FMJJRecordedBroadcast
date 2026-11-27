"""Qt-free exports of exact, bounded music-search snapshots.

Search snapshots are derived, process-local JSON bytes (schema 1), not another
music database, credential store or persistent configuration. This module owns
only snapshot expiry and export workspaces; the shared music exporter owns the
raw/translated format rules. Outputs are private JobManager attachments and
never use the user's music-information directory. No export fetches metadata.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import zipfile

from music.models import SearchResult, SongInfo
from toolbox import webui_music

MAX_RESULTS = 3
MAX_ROWS = 300
MAX_MEMORY = 24 * 1024 * 1024
TTL_SECONDS = 30 * 60
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_OUTPUT_BYTES + 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 16
MAX_STRING_BYTES = 2 * 1024 * 1024
_PUBLIC_QUERY = {"id", "songid", "songmid", "albumid", "albummid", "singerid",
                 "singermid", "artistid", "mid", "platform", "type"}
_SECRET_KEYS = {"cookie", "cookies", "setcookie", "authorization", "proxyauthorization",
                "password", "passwd", "pwd", "secret", "clientsecret", "apikey",
                "accesskey", "accesstoken", "refreshtoken", "idtoken", "token",
                "session", "sessionid", "sessionkey", "storagestate", "csrf",
                "csrftoken", "auth", "authkey", "authentication", "privatekey",
                "signingkey", "credentials", "credential", "localstorage", "sessionstorage"}
_SECRET_PARTS = {"cookie", "cookies", "authorization", "password", "passwd", "secret",
                 "csrf", "token"}
_CONTENT_FIELDS = {"song_name", "title", "subtitle", "alias", "singer", "album_name", "lyrics"}
_URL = re.compile(r"(?:https?:)?//[^\s<>\"'\[\]()]+", re.IGNORECASE)
_AUTH_TEXT = re.compile(
    r"\b(?:cookie|authorization|access[_ -]?token|refresh[_ -]?token|password|passwd|"
    r"client[_ -]?secret)[\"']?\s*[:=]", re.IGNORECASE)


class _ExportError(ValueError):
    """Deliberately public, path-free diagnostic text."""


def _secret_key(key):
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    parts = set(re.findall(r"[a-z0-9]+", separated.casefold()))
    normalized = "".join(re.findall(r"[a-z0-9]+", key.casefold()))
    return normalized in _SECRET_KEYS or bool(parts & _SECRET_PARTS)


def _public_url(value):
    try:
        source = urlsplit(value)
        if source.scheme.lower() not in ("", "http", "https") or not source.hostname:
            return "[已移除链接]"
        host = source.hostname
        if ":" in host:
            host = "[" + host + "]"
        if source.port is not None:
            host += ":" + str(source.port)
        query = [(key, val) for key, val in parse_qsl(source.query, keep_blank_values=True,
                                                    max_num_fields=1000)
                 if key.casefold() in _PUBLIC_QUERY and len(val) <= 200]
        fragment = ""
        if (source.hostname.lower() in ("www.kugou.com", "kugou.com")
                and source.path.rstrip("/") == "/song"):
            # Kugou song pages use a public song hash as their fragment route.
            # Keep only that identity, never other fragment parameters.
            hashes = [val for key, val in parse_qsl(source.fragment, max_num_fields=1000)
                      if key == "hash"]
            if len(hashes) == 1 and re.fullmatch(r"[a-fA-F0-9]{32}", hashes[0]):
                fragment = "hash=" + hashes[0]
        return urlunsplit((source.scheme, host, source.path, urlencode(query), fragment))
    except (ValueError, UnicodeError):
        return "[已移除链接]"


class _Cleaner:
    def __init__(self):
        self.nodes = 0
        self.bytes = 0
        self.redactions = 0

    def walk(self, value, *, depth=0, content=False):
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise _ExportError("搜索信息结构过大，请缩小搜索范围后重试导出")
        if value is None or type(value) in (bool, int):
            if type(value) is int and value.bit_length() > 4096:
                raise _ExportError("搜索信息含不支持的数据，请重新搜索后重试")
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise _ExportError("搜索信息含不支持的数据，请重新搜索后重试")
            return value
        if type(value) is str:
            size = len(value.encode("utf-8"))
            self.bytes += size
            if size > MAX_STRING_BYTES or self.bytes > MAX_MEMORY:
                raise _ExportError("搜索信息过大，请缩小搜索范围后重试导出")
            if not content and value.lstrip().startswith(("{", "[")):
                try:
                    embedded = json.loads(value)
                except RecursionError:
                    raise _ExportError("搜索信息结构过大，请缩小搜索范围后重试导出") from None
                except ValueError:
                    embedded = None
                if type(embedded) in (dict, list):
                    # Some adapters retain the raw response as JSON text.
                    # Preserve that field's string type while cleaning its tree.
                    clean = self.walk(embedded, depth=depth + 1)
                    return json.dumps(clean, ensure_ascii=False, allow_nan=False)
            if not content and _AUTH_TEXT.search(value):
                self.redactions += 1
                return "[已移除敏感信息]"
            cleaned = _URL.sub(lambda match: _public_url(match.group()), value)
            if cleaned != value:
                self.redactions += 1
            return cleaned
        if type(value) is list:
            return [self.walk(item, depth=depth + 1, content=content) for item in value]
        if type(value) is dict:
            result = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise _ExportError("搜索信息含不支持的数据，请重新搜索后重试")
                if _secret_key(key):
                    self.redactions += 1
                    continue
                self.bytes += len(key.encode("utf-8"))
                if self.bytes > MAX_MEMORY:
                    raise _ExportError("搜索信息过大，请缩小搜索范围后重试导出")
                result[key] = self.walk(item, depth=depth + 1,
                                        content=depth == 0 and key in _CONTENT_FIELDS)
            return result
        raise _ExportError("搜索信息含不支持的数据，请重新搜索后重试")


def _check_cancel(callback):
    if callback():
        raise InterruptedError("已取消音乐信息导出")


def _progress(callback, message, fraction):
    callback(message, fraction)


def _regular(path):
    value = path.lstat()
    if (stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
        raise _ExportError("导出产物位置不安全，请重新导出")
    return value


class MusicSearchExports:
    """Search normally, retain safe full models, then export selected IDs only."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._export_lock = threading.Lock()
        self._results = OrderedDict()

    def _expire(self):
        now = self._clock()
        for key in list(self._results):
            if now - self._results[key]["created"] >= TTL_SECONDS:
                del self._results[key]

    def search(self, payload, on_log=None, is_cancelled=None):
        captured = []
        outcome = webui_music.search(payload, on_log=on_log, is_cancelled=is_cancelled,
                                     on_songs=captured.extend)
        outcome.update(export_result_id="", export_available=False, expires_in=TTL_SECONDS,
                       export_message="没有可导出的歌曲结果")
        if not captured:
            return outcome
        try:
            if len(captured) > MAX_ROWS or len(captured) != len(outcome["items"]):
                raise _ExportError("搜索结果过多，请缩小范围后重试导出")
            cleaner = _Cleaner()
            keyword = cleaner.walk(outcome["keyword"], content=True)
            songs = []
            for song in captured:
                if not isinstance(song, SongInfo):
                    raise _ExportError("搜索信息格式不受支持，请重新搜索后重试")
                clean = cleaner.walk(song.model_dump(mode="python"))
                # Validation also verifies nested file, MV and payment fields.
                validated = SongInfo.model_validate(clean)
                songs.append(json.dumps(validated.model_dump(mode="python"), ensure_ascii=False,
                                        allow_nan=False, separators=(",", ":")).encode("utf-8"))
            size = sum(map(len, songs)) + len(keyword.encode("utf-8"))
            if size > MAX_MEMORY:
                raise _ExportError("搜索信息过大，请缩小搜索范围后重试导出")
            key = secrets.token_urlsafe(24)
            with self._lock:
                self._expire()
                while self._results and (len(self._results) >= MAX_RESULTS
                        or sum(item["size"] for item in self._results.values()) + size > MAX_MEMORY):
                    self._results.popitem(last=False)
                self._results[key] = {"created": self._clock(), "keyword": keyword,
                                      "songs": tuple(songs), "size": size,
                                      "redactions": cleaner.redactions}
            for index, row in enumerate(outcome["items"]):
                row["export_id"] = str(index)
            outcome.update(export_result_id=key, export_available=True, export_message="")
        except _ExportError as error:
            outcome["export_message"] = str(error)
        except Exception:
            outcome["export_message"] = "搜索信息暂不能导出，请重新搜索后重试"
        return outcome

    def export_selected(self, payload, output_dir, *, on_progress=lambda message, value: None,
                        is_cancelled=lambda: False):
        if (not isinstance(payload, dict) or set(payload) != {"result_id", "ids"}
                or not isinstance(payload.get("result_id"), str)
                or not 1 <= len(payload["result_id"]) <= 128):
            raise _ExportError("导出选择无效，请重新搜索并勾选歌曲")
        ids = payload["ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ROWS
                or any(type(item) is not str for item in ids) or len(ids) != len(set(ids))):
            raise _ExportError("请选择有效且不重复的歌曲后导出")
        with self._lock:
            self._expire()
            snapshot = self._results.get(payload["result_id"])
            if snapshot is None:
                raise _ExportError("搜索结果已失效，请重新搜索后导出")
            known = {str(index) for index in range(len(snapshot["songs"]))}
            if any(item not in known for item in ids):
                raise _ExportError("勾选歌曲不属于当前搜索结果，请重新选择")
            chosen = [song for index, song in enumerate(snapshot["songs"]) if str(index) in ids]
            keyword, redactions = snapshot["keyword"], snapshot["redactions"]
        _check_cancel(is_cancelled)
        if not self._export_lock.acquire(blocking=False):
            raise _ExportError("已有音乐信息导出任务，请稍后重试")
        staging = None
        try:
            workspace = Path(output_dir)
            if not stat.S_ISDIR(_regular(workspace).st_mode):
                raise _ExportError("导出任务目录不可用，请重新导出")
            workspace = workspace.resolve(strict=True)
            staging = Path(tempfile.mkdtemp(prefix="music-export-", dir=workspace))
            songs = [SongInfo.model_validate(json.loads(song)) for song in chosen]
            labels = list(dict.fromkeys(song.platform_name for song in songs))
            result = SearchResult(keyword=keyword, songs=songs, total_songs=len(songs),
                                  platforms_available=labels)
            _progress(on_progress, "正在生成音乐信息文件", 0.1)
            _check_cancel(is_cancelled)
            from music.exporter import export_all_formats
            outputs = export_all_formats(result, str(staging), literal_strings=True)
            required = ("原始JSON", "汇总JSON", "完整JSON", "Excel", "Markdown")
            if not isinstance(outputs, dict) or any(key not in outputs for key in required):
                raise _ExportError("音乐信息文件未完整生成，请重新导出")
            for key in required:
                path = Path(outputs[key])
                if (not path.resolve(strict=True).is_relative_to(staging)
                        or not stat.S_ISREG(_regular(path).st_mode)):
                    raise _ExportError("音乐信息文件未完整生成，请重新导出")
            _check_cancel(is_cancelled)
            manifest = {"schema_version": 1, "keyword": keyword, "songs": len(songs),
                        "platforms": labels, "sanitized": bool(redactions),
                        "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
            files = []
            total = 0
            for path in sorted(staging.rglob("*")):
                state = _regular(path)
                if stat.S_ISDIR(state.st_mode):
                    continue
                if not stat.S_ISREG(state.st_mode) or path.suffix.lower() not in (".json", ".md", ".xlsx"):
                    raise _ExportError("导出产物类型不受支持，请重新导出")
                if not path.resolve(strict=True).is_relative_to(staging):
                    raise _ExportError("导出产物位置不安全，请重新导出")
                total += state.st_size
                if state.st_size > MAX_FILE_BYTES or total > MAX_OUTPUT_BYTES:
                    raise _ExportError("导出内容过大，请减少勾选歌曲后重试")
                files.append(path)
            if not files or len(files) > MAX_ROWS + 10:
                raise _ExportError("导出文件数量无效，请减少勾选歌曲后重试")
            archive = staging / "音乐搜索结果.zip"
            # Stored entries keep the decompressed and attachment budgets equal;
            # highly repetitive public metadata cannot form a compressed bomb.
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as package:
                for index, path in enumerate(files, 1):
                    _check_cancel(is_cancelled)
                    package.write(path, path.relative_to(staging).as_posix())
                    _progress(on_progress, f"已打包 {index}/{len(files)} 个文件",
                              0.55 + 0.4 * index / len(files))
                    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
                        raise _ExportError("导出包过大，请减少勾选歌曲后重试")
            _check_cancel(is_cancelled)
            public = {"schema_version": 1, "count": len(songs), "formats": ["JSON", "Excel", "Markdown"],
                      "file_count": len(files), "sanitized": bool(redactions),
                      "summary": f"已导出 {len(songs)} 首歌曲的信息，可下载完整导出包"}
            _progress(on_progress, "音乐信息导出包已生成", 1.0)
            return public, archive
        except InterruptedError:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise
        except _ExportError:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise _ExportError("音乐信息导出失败，请减少勾选歌曲并重试") from None
        finally:
            self._export_lock.release()
