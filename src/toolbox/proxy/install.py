"""代理内核安装：多源加速 + 断点续传 + 本地导入。

GitHub 直连在国内常常只有十几 KB/s（21MB 的包要半小时），所以先对镜像做一次
短测速，挑当前最快的源下载；中途卡住换源时从已下载的字节接着下（HTTP Range），
已下好的部分不会白费。也支持「本地导入」：用浏览器/下载器下好 zip 再装。

下载入口只挂在「全局设置 → 检测依赖」，页面本身不提供安装按钮（约定 11）。
安装位置：config/proxy/bin/mihomo.exe（config/ 已在 .gitignore，不进仓库）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import settings

CORE_VERSION = "v1.19.34"
CORE_ASSET = f"mihomo-windows-amd64-compatible-{CORE_VERSION}.zip"
CORE_URL = (f"https://github.com/MetaCubeX/mihomo/releases/download/"
            f"{CORE_VERSION}/{CORE_ASSET}")
DOWNLOAD_PAGE = "https://github.com/MetaCubeX/mihomo/releases"

EXE_MIN_BYTES = 8 * 1024 * 1024      # 内核 exe 本体下限，明显更小视为异常
ZIP_MIN_BYTES = 12 * 1024 * 1024     # 官方包约 21MB；低于此值基本是没下完
PART_MAX_BYTES = 40 * 1024 * 1024    # 续传文件异常膨胀就重下

PROBE_BYTES = 256 * 1024
PROBE_TIMEOUT = 6
READ_TIMEOUT = 25
CHUNK_BYTES = 256 * 1024
UA = "FengMingToolbox/proxy-installer"

# 国内常见的 GitHub 加速前缀；{url} 处填原始 release 地址。
MIRRORS = (
    ("ghproxy.net", "https://ghproxy.net/{url}"),
    ("ghfast.top", "https://ghfast.top/{url}"),
    ("gh-proxy.com", "https://gh-proxy.com/{url}"),
    ("ghproxy.cc", "https://ghproxy.cc/{url}"),
    ("gh.llkk.cc", "https://gh.llkk.cc/{url}"),
    ("github.moeyy.xyz", "https://github.moeyy.xyz/{url}"),
)


class InstallError(RuntimeError):
    pass


def _open(request, timeout):
    """urlopen 的唯一出口：测试里替换它就能离线验证下载逻辑。"""
    return urllib.request.urlopen(request, timeout=timeout)


def part_path() -> Path:
    """断点续传文件：下完并解压成功后才删除。"""
    return settings.bin_dir() / "mihomo.zip.part"


def sources() -> list[tuple[str, str]]:
    """候选源，镜像在前、直连兜底；settings.json 的 core_mirror 可自定义。"""
    items: list[tuple[str, str]] = []
    try:
        custom = str(settings.load().get("core_mirror") or "").strip()
    except Exception:
        custom = ""
    if custom:
        items.append(("自定义镜像", custom.replace("{url}", CORE_URL)))
    items.extend((label, template.format(url=CORE_URL))
                 for label, template in MIRRORS)
    items.append(("GitHub 直连", CORE_URL))
    return items


def installed() -> Path | None:
    target = settings.bin_dir() / "mihomo.exe"
    try:
        if target.is_file() and target.stat().st_size >= EXE_MIN_BYTES:
            return target
    except OSError:
        return None
    return None


def uninstall_core() -> tuple[bool, int, str]:
    """删除程序下载的内置代理内核；提供商配置与外部内核不受影响。"""
    target = installed()
    if target is None:
        return False, 0, "未安装内置代理内核"
    from .core import core as proxy_core
    try:
        if proxy_core.is_running():
            return False, 0, "代理内核正在运行，请先在网络代理页停止"
    except Exception:
        pass
    try:
        if target.parent.resolve() != settings.bin_dir().resolve() or target.is_symlink():
            return False, 0, "内核路径异常，未删除"
        size = target.stat().st_size
        target.unlink()
    except PermissionError:
        return False, 0, "内核文件正在使用，请先停止代理"
    except OSError as exc:
        return False, 0, f"删除失败：{exc}"
    return True, size, "已卸载代理内核，可重新下载"


def _probe(url: str) -> float:
    """取前 256KB 测速，返回字节/秒；0 表示这个源当前不可用。"""
    request = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Range": f"bytes=0-{PROBE_BYTES - 1}",
    })
    started = time.monotonic()
    try:
        response = _open(request, PROBE_TIMEOUT)
        with response:
            if int(getattr(response, "status", 200) or 200) >= 400:
                return 0.0
            got = 0
            while got < PROBE_BYTES:
                if time.monotonic() - started > PROBE_TIMEOUT:
                    break
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                got += len(chunk)
    except Exception:
        return 0.0
    elapsed = max(time.monotonic() - started, 0.05)
    if got < 64 * 1024:
        return 0.0
    return got / elapsed


def rank_sources(status=None) -> list[tuple[str, str]]:
    """并行测速后按速度排序；全部探测失败则退回镜像顺序依次尝试。"""
    items = sources()
    if status:
        status("正在测试加速源（约 5 秒）…")
    speeds: list[tuple[float, str, str]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
        futures = {pool.submit(_probe, url): (label, url)
                   for label, url in items}
        for future in as_completed(futures):
            label, url = futures[future]
            try:
                speed = future.result()
            except Exception:
                speed = 0.0
            if speed > 0:
                speeds.append((speed, label, url))
    if not speeds:
        return items
    speeds.sort(reverse=True)
    return [(label, url) for _speed, label, url in speeds]


def _total_bytes(response, offset: int) -> int:
    """从 Content-Range / Content-Length 推断完整包大小，未知时返回 0。"""
    content_range = response.headers.get("Content-Range") or ""
    if "/" in content_range:
        try:
            return int(content_range.rsplit("/", 1)[1])
        except ValueError:
            pass
    try:
        length = int(response.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    return length + offset if length else 0


def _fetch(url: str, part: Path, progress=None) -> int:
    """把 url 续写进 part，返回当前 part 大小；网络异常原样抛出。"""
    if part.is_file() and part.stat().st_size > PART_MAX_BYTES:
        part.unlink()
    offset = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": UA}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with _open(request, READ_TIMEOUT) as response:
        if int(getattr(response, "status", 200) or 200) >= 400:
            raise InstallError(f"响应异常：HTTP {response.status}")
        if offset and int(getattr(response, "status", 200) or 200) != 206:
            offset = 0  # 该源不支持续传，只能从头写
        total = _total_bytes(response, offset)
        if progress:
            progress(offset, total)
        with open(part, "ab" if offset else "wb") as stream:
            done = offset
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                stream.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return part.stat().st_size if part.is_file() else 0


def _pick_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    """挑出要安装的可执行文件。

    官方包里的文件名不固定：v1.19.x 的 zip 内是
    `mihomo-windows-amd64-compatible.exe`，而不是 `mihomo.exe`。按
    「精确 mihomo.exe → 名称以 mihomo 开头的 exe → 包内唯一的 exe」依次匹配，
    避免因为改名就整包安装失败。
    """
    exes = [info for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".exe")]
    for info in exes:
        if Path(info.filename).name.lower() == "mihomo.exe":
            return info
    for info in exes:
        if Path(info.filename).name.lower().startswith("mihomo"):
            return info
    if len(exes) == 1:
        return exes[0]
    return None


def _extract(zip_path: Path, target: Path) -> None:
    temporary = None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            member = _pick_member(archive)
            if member is None:
                names = "、".join(Path(i.filename).name
                                  for i in archive.infolist() if not i.is_dir())[:120]
                raise InstallError(f"内核包内未找到 mihomo 可执行文件（包内：{names or '空'}）")
            if member.file_size > 80 * 1024 * 1024:
                raise InstallError("内核包内文件大小异常，已停止解压")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, tempfile.NamedTemporaryFile(
                    delete=False, dir=target.parent, suffix=".exe.tmp") as dst:
                shutil.copyfileobj(src, dst)
                temporary = dst.name
    except zipfile.BadZipFile as exc:
        raise InstallError("内核包未下载完整或已损坏") from exc
    if temporary is None:
        raise InstallError("内核包解压失败")
    if Path(temporary).stat().st_size < EXE_MIN_BYTES:
        os.unlink(temporary)
        raise InstallError("内核文件大小异常，请重试")
    os.replace(temporary, target)


def _zip_ready(zip_path: Path) -> bool:
    """能读出目录且体积达标才算下完；不整包校验，避免多读 21MB。"""
    try:
        if zip_path.stat().st_size < ZIP_MIN_BYTES:
            return False
        with zipfile.ZipFile(zip_path) as archive:
            return bool(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def install_from_zip(zip_path: str | os.PathLike, progress=None) -> Path:
    """安装用户自己下载好的内核 zip（浏览器/下载器下完再导入）。"""
    source = Path(zip_path)
    if not source.is_file():
        raise InstallError("找不到这个文件，请重新选择")
    if progress:
        progress(0, source.stat().st_size)
    target = settings.bin_dir() / "mihomo.exe"
    _extract(source, target)
    if progress:
        progress(source.stat().st_size, source.stat().st_size)
    if not target.is_file() or target.stat().st_size < EXE_MIN_BYTES:
        raise InstallError("内核安装后校验失败，请重试")
    return target


def download_core(progress=None, status=None) -> Path:
    """多源下载并安装内核；progress(done, total) 报进度，status(text) 报当前动作。"""
    target = settings.bin_dir() / "mihomo.exe"
    part = part_path()
    ranked = rank_sources(status)
    resumed = part.stat().st_size if part.is_file() else 0
    failures: list[str] = []

    for round_no in range(2):
        if round_no:
            if status:
                status("第一轮全部失败，重试剩余部分…")
        for label, url in ranked:
            have = part.stat().st_size if part.is_file() else 0
            if status:
                note = f"，续传 {have / 1024 ** 2:.1f}MB" if have else ""
                status(f"从 {label} 下载{note}")
            try:
                _fetch(url, part, progress=progress)
                if not _zip_ready(part):
                    failures.append(f"{label}：包不完整")
                    continue
                _extract(part, target)
                part.unlink(missing_ok=True)
                if not target.is_file() or target.stat().st_size < EXE_MIN_BYTES:
                    raise InstallError("内核安装后校验失败")
                return target
            except InstallError as exc:
                failures.append(f"{label}：{exc}")
            except Exception as exc:
                failures.append(f"{label}：{type(exc).__name__} {exc}")
            # 包本身完整（例如换了源但文件已下全），即使这次 fetch 报错也试着装一次
            if part.is_file() and part.stat().st_size >= ZIP_MIN_BYTES and _zip_ready(part):
                try:
                    _extract(part, target)
                except InstallError as exc:
                    failures.append(f"{label}：{exc}")
                    continue
                part.unlink(missing_ok=True)
                return target

    hint = "；".join(failures[:6]) or "没有可用下载源"
    more = "" if len(failures) <= 6 else f"（共 {len(failures)} 次尝试）"
    partial = ""
    if part.is_file() and part.stat().st_size:
        partial = f"；已保留 {part.stat().st_size / 1024 ** 2:.1f}MB 续传文件，下次从这里继续"
    raise InstallError(
        f"加速源均下载失败{hint}{more}{partial}。"
        "可在「全局设置 → 检测依赖 → 代理内核」用「本地导入」安装自己下好的 zip。"
        f"下载页：{DOWNLOAD_PAGE}")