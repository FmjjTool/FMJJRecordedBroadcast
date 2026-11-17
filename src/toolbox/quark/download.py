# -*- coding: utf-8 -*-
"""网盘下载引擎（夸克 / 百度 / 移动云盘）：目录递归 + Range 断点续传。

各后端的客户端只需提供同一组接口：
`list_entries(fid)` / `download_links(ids)` / `download_headers()` / `session`。

大文件的三个真实难点，以及各自对策：

1. **直链有时效** —— 大文件跑到一半链接就废了。
   对策：401/403 时自动重新申请直链，再用 `Range: bytes=N-` 从断点续传。
2. **长连接必断** —— 单连接跑几十 GB 几乎不可能一次成功。
   对策：`.part` 文件本身就是断点（文件系统记已下载字节，不用额外数据库），
   中断后下次启动自动从 `.part` 长度接着要。
3. **非会员限速是服务端策略** —— 不做任何绕过。
   本引擎只保证「单线程、不重下、断了能接」，把有限带宽用满。

落盘约定：先写 `<文件名>.part`，字节数等于远端大小才重命名为正式文件。
所以中途出现 `.part` 属于正常未完成状态，不会污染目标目录。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1024 * 1024          # 1 MB 一块，进度与写入的平衡点
MAX_RETRY = 6                # 单个文件最多换链重试次数
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 180           # 单块读取超时：慢速网络下也不能无限等
PART_SUFFIX = ".part"

# Windows 文件名非法字符；云端文件名可能带 : * ? 之类
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
             "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
             "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}


class DownloadError(RuntimeError):
    """下载失败（网络、权限、直链失效等）。"""


class _LinkExpired(DownloadError):
    """直链 401/403：夸克直链有分钟级时效，需要重新申请后再续传。"""


def safe_name(name: str) -> str:
    """把云端文件名洗成 Windows 能落盘的名字。"""
    cleaned = _BAD_CHARS.sub("_", str(name or "")).strip().rstrip(". ")
    if not cleaned:
        cleaned = "未命名"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def human_size(num: float) -> str:
    """1234567 → `1.2 MB`（界面与日志共用）。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def human_speed(bps: float) -> str:
    return f"{human_size(bps)}/s" if bps > 0 else "-"


@dataclass
class DownloadItem:
    """一个待下载的远端文件。

    `fid` 用于下载页内部；`download_id` 是取直链用的标识（百度必须用 fs_id，
    夸克 / 移动云盘与 fid 相同）。
    """

    fid: str
    name: str
    size: int = 0
    rel_dir: str = ""          # 云端相对目录（不含文件名），用于在本地复刻层级
    updated: str = ""
    download_id: str = ""

    @property
    def link_id(self) -> str:
        return self.download_id or self.fid

    @property
    def rel_path(self) -> str:
        return f"{self.rel_dir}/{self.name}" if self.rel_dir else self.name


@dataclass
class DownloadResult:
    ok: bool
    name: str
    local_path: str = ""
    size: int = 0
    message: str = ""
    skipped: bool = False
    seconds: float = 0.0

    def summary(self) -> str:
        if self.skipped:
            return f"{self.name}（已存在，跳过）"
        if self.ok:
            return f"{self.name} · {human_size(self.size)} · {self.seconds:.1f}s"
        return f"{self.name} 失败：{self.message}"


class Downloader:
    """一个任务一个实例；`stop_event` 置位后当前块结束即退出。"""

    def __init__(self, client: QuarkClient, local_root, on_progress=None,
                 on_log=None, stop_event=None):
        self.client = client
        self.local_root = Path(local_root)
        self.on_progress = on_progress
        self.on_log = on_log
        self.stop_event = stop_event
        self._link_cache: dict[str, str] = {}

    # ==================== 回调工具 ====================

    def _log(self, message: str) -> None:
        if self.on_log is not None:
            try:
                self.on_log(message)
            except Exception:  # noqa: BLE001 - 日志异常不能打断下载
                pass

    def _progress(self, name: str, done: int, total: int, speed: float = 0.0) -> None:
        if self.on_progress is not None:
            try:
                self.on_progress(name, done, total, speed)
            except Exception:  # noqa: BLE001
                pass

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    # ==================== 远端遍历 ====================

    def walk(self, fid: str = "0", rel_dir: str = "", depth: int = 0) -> list[DownloadItem]:
        """把云端目录递归展开成文件清单（保持相对目录层级）。"""
        if depth > 20:
            self._log(f"目录层级过深，跳过：{rel_dir or '/'}")
            return []
        items: list[DownloadItem] = []
        try:
            listing = self.client.list_entries(fid)
        except Exception as exc:  # noqa: BLE001 - 三个后端的异常类型不同
            self._log(f"列目录失败 {rel_dir or '/'}：{exc}")
            return []
        for entry in listing:
            if self._stopped():
                break
            name = str(entry.get("file_name") or "")
            sub_fid = str(entry.get("fid") or "")
            if entry.get("dir"):
                child = f"{rel_dir}/{name}" if rel_dir else name
                items.extend(self.walk(sub_fid, child, depth + 1))
                continue
            try:
                size = int(entry.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            items.append(DownloadItem(
                fid=sub_fid, name=name, size=size, rel_dir=rel_dir,
                updated=str(entry.get("updated_at") or ""),
                download_id=str(entry.get("download_id") or sub_fid),
            ))
        return items

    # ==================== 直链 ====================

    def _link(self, item: DownloadItem, refresh: bool = False) -> str:
        key = item.link_id
        if not refresh and key in self._link_cache:
            return self._link_cache[key]
        links = self.client.download_links([key])
        url = links.get(key, "") or next(iter(links.values()), "")
        if not url:
            raise DownloadError("未取到下载直链（可能需要在 Cookie 管理里重新登录）")
        self._link_cache[key] = url
        return url

    # ==================== 单个文件 ====================

    def _target(self, item: DownloadItem) -> Path:
        folder = self.local_root
        if item.rel_dir:
            for part in item.rel_dir.split("/"):
                folder = folder / safe_name(part)
        return folder / safe_name(item.name)

    def download_one(self, item: DownloadItem, overwrite: bool = False) -> DownloadResult:
        target = self._target(item)
        part = target.with_name(target.name + PART_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)

        # 已下完且大小对得上 → 跳过（增量下载的核心）
        if target.exists() and not overwrite:
            if item.size <= 0 or target.stat().st_size == item.size:
                return DownloadResult(True, item.name, str(target),
                                      target.stat().st_size, "已存在", skipped=True)

        started = time.time()
        done = part.stat().st_size if part.exists() else 0
        if item.size and done > item.size:
            # .part 比远端还大：说明远端文件变小了，旧断点作废
            part.unlink(missing_ok=True)
            done = 0
        if done:
            self._log(f"从断点续传：{item.name}（已有 {human_size(done)}）")

        attempt = 0
        speed = 0.0
        last_error = ""
        while attempt <= MAX_RETRY:
            if self._stopped():
                raise DownloadError("已停止")
            try:
                url = self._link(item, refresh=attempt > 0)
                done, speed = self._stream(url, part, item, done)
                break
            except _LinkExpired as exc:
                last_error = str(exc)
                attempt += 1
                self._link_cache.pop(item.link_id, None)
                if attempt > MAX_RETRY:
                    raise DownloadError(f"直链多次失效，重试 {MAX_RETRY} 次仍失败")
                self._log(f"直链失效，重新申请（第 {attempt} 次）：{item.name}")
                time.sleep(min(1.5 * attempt, 6))
            except DownloadError:
                raise
            except Exception as exc:  # noqa: BLE001 - 网络层异常统一兜底
                last_error = str(exc)
                attempt += 1
                if attempt > MAX_RETRY:
                    raise DownloadError(f"网络中断，重试 {MAX_RETRY} 次仍失败：{last_error}")
                self._log(f"连接中断，重试（第 {attempt} 次）：{item.name}")
                time.sleep(min(1.5 * attempt, 6))

        final_size = part.stat().st_size if part.exists() else 0
        if item.size and final_size != item.size:
            raise DownloadError(f"大小不符（期望 {human_size(item.size)}，"
                                f"实得 {human_size(final_size)}）")
        if overwrite and target.exists():
            target.unlink()
        part.replace(target)
        self._progress(item.name, final_size, item.size or final_size, speed)
        return DownloadResult(True, item.name, str(target), final_size, "",
                              seconds=time.time() - started)

    def _stream(self, url: str, part: Path, item: DownloadItem,
                done: int) -> tuple[int, float]:
        """把直链内容写进 .part，返回 (总字节, 末速)。"""
        headers = dict(self.client.download_headers())
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            resp = self.client.session.get(
                url, headers=headers, stream=True, allow_redirects=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        except Exception as exc:  # noqa: BLE001 - requests 的异常族太杂，统一转网络错误
            raise DownloadError(f"连接失败：{exc}") from exc

        with resp:
            if resp.status_code == 416:
                # 服务器认为已经下完：只要大小对得上就当作完成
                return done, 0.0
            if resp.status_code in (401, 403):
                raise _LinkExpired(f"HTTP {resp.status_code}")
            if resp.status_code not in (200, 206):
                raise DownloadError(f"HTTP {resp.status_code}")

            mode = "ab"
            if resp.status_code == 200 and done:
                # 服务端不认 Range，只能从头下（会重下已下部分）
                self._log(f"服务端不支持续传，从头下载：{item.name}")
                done = 0
                mode = "wb"
            elif resp.status_code == 206:
                mode = "ab"
            else:
                mode = "wb"

            started = time.time()
            base = done
            total = item.size or 0
            if not total:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit():
                    total = base + int(length)
            speed = 0.0
            with open(part, mode) as handle:
                for chunk in resp.iter_content(CHUNK):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    elapsed = time.time() - started
                    speed = (done - base) / elapsed if elapsed > 0.5 else 0.0
                    self._progress(item.name, done, total or done, speed)
                    if self._stopped():
                        raise DownloadError("已停止")
            return done, speed

    # ==================== 批量 ====================

    def run(self, items: list[DownloadItem], overwrite: bool = False) -> list[DownloadResult]:
        results: list[DownloadResult] = []
        total = len(items)
        for index, item in enumerate(items, start=1):
            if self._stopped():
                self._log(f"已停止（完成 {index - 1}/{total}）")
                break
            self._log(f"[{index}/{total}] 开始：{item.rel_path}"
                      f"{' · ' + human_size(item.size) if item.size else ''}")
            try:
                result = self.download_one(item, overwrite=overwrite)
            except DownloadError as exc:
                result = DownloadResult(False, item.name, message=str(exc))
            except Exception as exc:  # noqa: BLE001 - 三个后端的业务异常类型不同
                result = DownloadResult(False, item.name, message=str(exc))
            results.append(result)
            self._log(result.summary())
        return results
