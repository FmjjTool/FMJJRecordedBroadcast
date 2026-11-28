# -*- coding: utf-8 -*-
"""7-Zip 引擎封装（内置官方 7z.exe + 7z.dll，完整格式支持）。

为什么内置而不是依赖 Python 库：
- `py7zr` 只认 7z，`rarfile` 还得外挂 unrar.exe，等于仍然离不开外部 exe；
- 官方 7z.exe + 7z.dll 共约 2.4 MB，一个组合通吃 rar / rar5 / 7z / zip / tar /
  gz / bz2 / xz / cab / iso / 分卷，还能压 7z（同体积比 zip 小）；
- 许可为 LGPL（见同目录 `7-Zip-License.txt` 与 `THIRD_PARTY_NOTICES.md`）。

定位顺序：内置二进制 → 系统 PATH → 常见安装目录。内置版永远优先，因为它是
已知完整的 x64 26.06，不会被系统里残缺的 `7za.exe`（不支持 rar）顶替。

命令行要点：
- 列表：`7z l -slt -ba <包>`，`-slt` 输出 `键 = 值` 段落；
- 解压：`7z x -bsp1 -bso0 -y -o<目录>`，`-bsp1` 把百分比进度送到 stdout；
- 压缩：`7z a -bsp1 -bso0 -t7z|zip -mx=N`。
"""

from __future__ import annotations

import codecs
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# 界面过滤用：7z 能打开的常见后缀
ARCHIVE_SUFFIXES = (
    ".7z", ".rar", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz",
    ".cab", ".iso", ".zst", ".lzma", ".zipx", ".001", ".jar", ".apk", ".epub",
    ".docx", ".xlsx", ".pptx",
)
# 分卷后续卷：.002/.003 或 .r00/.z01，双击这些也能定位到首卷
_VOLUME_TAIL = re.compile(r"\.(0\d\d|r\d\d|z\d\d)$", re.IGNORECASE)
_PERCENT = re.compile(r"(\d{1,3})%")

# 打包后（--windowed）不能让子进程弹黑框
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_EXE_NAME = "7z.exe" if sys.platform == "win32" else "7z"

# 这些退出码代表「结束但不能算完全成功」
_WARNING_CODES = {1: "部分文件被跳过（可能有占用或权限问题）"}
_FATAL_CODES = {
    2: "发生致命错误",
    7: "命令行参数错误",
    8: "内存不足",
    255: "操作被中断",
}


class SevenZipError(RuntimeError):
    """7z 找不到 / 执行失败 / 用户中止。"""

    def __init__(self, message: str, code: int | None = None):
        self.code = code
        super().__init__(message)


@dataclass
class Entry:
    """压缩包内的一条记录。"""

    path: str
    size: int = 0
    packed: int = 0
    is_dir: bool = False
    encrypted: bool = False
    modified: str = ""

    @property
    def name(self) -> str:
        return self.path.rstrip("\\/").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]


# ==================== 可执行文件定位 ====================

_found: dict[str, str] = {}


def bin_dir() -> Path:
    """内置二进制目录：开发态 src/toolbox/bin，打包后 _MEIPASS/toolbox/bin。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "toolbox" / "bin"
    return Path(__file__).resolve().parent / "bin"


def _is_usable(exe: Path) -> bool:
    """exe 存在且同目录有 7z.dll（内置版）或本身就是独立版。"""
    if not exe.is_file():
        return False
    dll = exe.with_name("7z.dll")
    if dll.is_file():
        return True
    # 系统安装的 7z.exe 一定和 dll 同目录；7za.exe 独立但缺 rar，仅兜底可用
    return exe.stem.lower() in ("7za", "7zr")


def find_7z() -> str:
    """返回可执行的 7z 路径；内置优先，其次系统。"""
    if "exe" in _found:
        return _found["exe"]

    candidates: list[Path] = [bin_dir() / _EXE_NAME]
    which = shutil.which("7z")
    if which:
        candidates.append(Path(which))
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidates.append(Path(root) / "7-Zip" / _EXE_NAME)
    for cand in candidates:
        if _is_usable(cand):
            _found["exe"] = str(cand)
            return _found["exe"]
    raise SevenZipError("未找到 7z 可执行文件（内置二进制缺失且系统未安装 7-Zip）")


def invalidate_cache() -> None:
    _found.clear()


def available() -> bool:
    """界面在渲染前用它决定是否可用，不抛异常。"""
    try:
        find_7z()
        return True
    except SevenZipError:
        return False


def version() -> str:
    """形如 `7-Zip 26.06 (x64)`；探测失败返回空串。"""
    if "version" in _found:
        return _found["version"]
    try:
        out = subprocess.run([find_7z()], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, encoding="utf-8",
                             errors="replace", timeout=15,
                             creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if line.strip().lower().startswith("7-zip"):
            _found["version"] = line.strip()
            return _found["version"]
    return ""


def is_archive(path) -> bool:
    """按后缀粗判（用于界面把文件标成可解压）。"""
    return Path(path).suffix.lower() in ARCHIVE_SUFFIXES


def volume_group(path) -> list[Path]:
    """分卷包给一组同根卷（如 a.7z.001 → a.7z.001/.002…），非分卷返回自身。"""
    p = Path(path)
    if not _VOLUME_TAIL.search(p.name):
        return [p]
    stem = _VOLUME_TAIL.sub("", str(p))
    first = Path(stem)
    if first.is_file():
        return [first]
    # .r00 形态的首卷通常是 .rar
    rar = Path(str(stem) + ".rar")
    return [rar] if rar.is_file() else [p]


# ==================== 命令执行（带进度） ====================

def _pump(stream, sink) -> None:
    """读 7z 的原始字节流并切成行。

    这里故意不用 `text=True`：文本模式的 `read(n)` 要凑满 n 个字符才返回，
    7z 每刷新一次进度只有十几个字节，用文本模式会让进度条半天不动。
    改为二进制 read + 自己做增量 UTF-8 解码（配合命令行的 `-sccUTF-8`）。
    7z 的进度用 \\r 原地刷新，所以按 \\r / \\n 都要切。
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buf = ""
    while True:
        raw = stream.read(1024)
        if not raw:
            break
        buf += decoder.decode(raw)
        while True:
            cuts = [i for i in (buf.find("\r"), buf.find("\n")) if i >= 0]
            if not cuts:
                break
            cut = min(cuts)
            piece, buf = buf[:cut], buf[cut + 1:]
            if piece.strip():
                sink(piece)
    buf += decoder.decode(b"", final=True)
    if buf.strip():
        sink(buf)


def _run(args: list[str], on_progress=None, stop_event=None) -> int:
    """执行 7z，边跑边回报百分比；stop_event 置位时终止子进程。"""
    exe = find_7z()
    # -sccUTF-8：强制 7z 用 UTF-8 输出，否则中文文件名按 GBK 吐出来会成乱码
    cmd = [exe, "-sccUTF-8", *args]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW, bufsize=0,
        )
    except OSError as exc:
        raise SevenZipError(f"无法启动 7z：{exc}") from exc

    last = [0]

    def sink(piece: str) -> None:
        if on_progress is None:
            return
        match = _PERCENT.search(piece)
        if match:
            pct = max(0, min(100, int(match.group(1))))
            if pct != last[0]:
                last[0] = pct
                try:
                    on_progress(pct, piece.strip()[:120])
                except Exception:  # noqa: BLE001 - 回调异常不能打断解压
                    pass
        else:
            try:
                on_progress(last[0], piece.strip()[:120])
            except Exception:  # noqa: BLE001
                pass

    reader = threading.Thread(target=_pump, args=(proc.stdout, sink), daemon=True)
    reader.start()
    while proc.poll() is None:
        if stop_event is not None and stop_event.is_set():
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise SevenZipError("已停止")
        time.sleep(0.12)
    reader.join(2)
    # 7z 收尾时不一定会再吐一行 100%，这里补上，否则界面进度条会卡在 9x%
    if on_progress is not None and last[0] < 100:
        try:
            on_progress(100, "完成")
        except Exception:  # noqa: BLE001
            pass
    return proc.returncode


def _check(code: int) -> None:
    if code == 0:
        return
    if code in _WARNING_CODES:
        # 警告不算失败：文件已解出，只是个别项被跳过
        return
    raise SevenZipError(_FATAL_CODES.get(code, f"7z 退出码 {code}"), code)


# ==================== 列出内容 ====================

def list_archive(archive, password: str | None = None) -> list[Entry]:
    """列出压缩包条目。加密包未给密码时 7z 仍能列出文件名（内容除外）。"""
    args = ["l", "-slt", "-ba", "-sccUTF-8", "--", str(archive)]
    if password:
        args.insert(1, f"-p{password}")
    try:
        out = subprocess.run([find_7z(), *args], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, encoding="utf-8",
                             errors="replace", timeout=120,
                             creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise SevenZipError(f"读取压缩包失败：{exc}") from exc

    entries: list[Entry] = []
    block: dict[str, str] = {}

    def flush() -> None:
        if not block.get("Path"):
            return
        attrs = block.get("Attributes", "")
        try:
            size = int(block.get("Size") or 0)
        except ValueError:
            size = 0
        try:
            packed = int(block.get("Packed Size") or 0)
        except ValueError:
            packed = 0
        entries.append(Entry(
            path=block["Path"],
            size=size,
            packed=packed,
            is_dir="D" in attrs,
            encrypted=block.get("Encrypted", "").strip() == "+",
            modified=block.get("Modified", ""),
        ))

    for line in out.splitlines():
        if not line.strip():
            flush()
            block = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep:
            block[key.strip()] = value.strip()
    flush()
    return entries


def list_archive_limited(archive, *, max_entries: int = 10000,
                         max_output_bytes: int = 4 * 1024 * 1024,
                         max_line_bytes: int = 8192,
                         max_total_size: int | None = None,
                         timeout: float = 120, stop_event=None) -> list[Entry]:
    """有界地读取压缩包目录，供自动解压前的安全检查使用。"""
    if max_entries <= 0 or max_output_bytes <= 0 or max_line_bytes <= 0:
        raise ValueError("压缩包目录限制必须为正数")
    if timeout <= 0:
        raise ValueError("读取超时必须为正数")

    path = Path(archive).resolve()
    command = [find_7z(), "-sccUTF-8", "l", "-slt", "-ba",
               "--", str(path)]
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            cwd=str(path.parent), creationflags=_NO_WINDOW, bufsize=1)
    except OSError as exc:
        raise SevenZipError("无法启动 7-Zip 读取压缩包") from exc

    lines = queue.Queue()
    finished = object()

    def pump_output() -> None:
        output_bytes = 0
        error = ""
        try:
            while True:
                line = proc.stdout.readline(max_line_bytes + 1)
                if not line:
                    break
                line_bytes = len(line.encode("utf-8", errors="replace"))
                output_bytes += line_bytes
                if line_bytes > max_line_bytes:
                    error = "压缩包目录单行内容超过限制"
                    break
                if output_bytes > max_output_bytes:
                    error = "压缩包目录内容超过限制"
                    break
                lines.put((line, ""))
        except (OSError, UnicodeError):
            error = "读取压缩包目录失败"
        finally:
            lines.put((finished, error))

    reader = threading.Thread(target=pump_output, daemon=True)
    reader.start()
    entries: list[Entry] = []
    block: dict[str, str] = {}
    total_size = 0
    deadline = time.monotonic() + timeout

    def flush() -> None:
        nonlocal total_size
        raw_path = block.get("Path", "")
        if not raw_path:
            return
        normalized = raw_path.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if ("\x00" in raw_path or relative.is_absolute()
                or normalized.startswith("//")
                or re.match(r"^[A-Za-z]:", normalized)
                or ".." in relative.parts):
            raise SevenZipError("压缩包包含不安全的路径")
        if block.get("Symbolic Link") or block.get("Hard Link"):
            raise SevenZipError("压缩包包含不支持的链接")
        try:
            size = int(block.get("Size") or 0)
            packed = int(block.get("Packed Size") or 0)
        except ValueError as exc:
            raise SevenZipError("压缩包目录包含无效大小") from exc
        if size < 0 or packed < 0:
            raise SevenZipError("压缩包目录包含无效大小")
        is_dir = "D" in block.get("Attributes", "")
        if not is_dir:
            total_size += size
            if max_total_size is not None and total_size > max_total_size:
                raise SevenZipError("压缩包解压体积超过安全上限")
        if len(entries) >= max_entries:
            raise SevenZipError("压缩包条目数超过安全上限")
        entries.append(Entry(
            path=raw_path, size=size, packed=packed, is_dir=is_dir,
            encrypted=block.get("Encrypted", "").strip() == "+",
            modified=block.get("Modified", "")))

    def stop_process() -> None:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    pass

    try:
        done = False
        while not done:
            if stop_event is not None and stop_event.is_set():
                raise SevenZipError("已停止")
            if time.monotonic() > deadline:
                raise SevenZipError("读取压缩包目录超时")
            try:
                line, error = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is finished:
                if error:
                    raise SevenZipError(error)
                done = True
                continue
            if not line.strip():
                flush()
                block = {}
                continue
            key, sep, value = line.partition(" = ")
            if sep:
                block[key.strip()] = value.strip()
        flush()
        if stop_event is not None and stop_event.is_set():
            raise SevenZipError("已停止")
        remaining = max(0.1, deadline - time.monotonic())
        try:
            return_code = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise SevenZipError("读取压缩包目录超时") from exc
        if return_code != 0:
            raise SevenZipError("压缩包无法读取，请检查文件完整性")
        return entries
    finally:
        if proc.poll() is None:
            stop_process()


def total_size(archive, password: str | None = None) -> int:
    """包内所有文件解压后的总字节数（进度条按比例显示用）。"""
    return sum(e.size for e in list_archive(archive, password) if not e.is_dir)


# ==================== 解压 ====================

def extract(archive, out_dir, password: str | None = None, on_progress=None,
            stop_event=None, overwrite: bool = True, keep_paths: bool = True) -> None:
    """解压到 out_dir。

    - `keep_paths=False` 时把包内所有文件平铺到 out_dir 根（对付套了一层文件夹的包）
    - `overwrite=False` 时遇到同名文件自动跳过（`-aos`）
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    args = ["x" if keep_paths else "e", "-bsp1", "-bso0", "-bse1"]
    args.append("-y" if overwrite else "-aos")
    if password:
        args.append(f"-p{password}")
    args += [f"-o{out}", "--", str(archive)]
    _check(_run(args, on_progress=on_progress, stop_event=stop_event))


def test_archive(archive, password: str | None = None) -> tuple[bool, str]:
    """完整性校验。返回 (是否通过, 说明)；加密包密码错会返回 False。"""
    args = ["t", "-bso0", "-bse1", "-sccUTF-8"]
    if password:
        args.append(f"-p{password}")
    args += ["--", str(archive)]
    try:
        proc = subprocess.run([find_7z(), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, encoding="utf-8",
                              errors="replace", timeout=1800,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"校验失败：{exc}"
    if proc.returncode == 0:
        return True, "完整"
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, detail[-1][:200] if detail else f"退出码 {proc.returncode}"


# ==================== 压缩 ====================

def _level_args(fmt: str, level: int) -> list[str]:
    """压缩级别：0=仅存储，1/3/5/7/9 对应 -mx。"""
    if level <= 0:
        return ["-mx0"]
    return [f"-mx{max(1, min(9, level))}"]


def compress(paths, out_file, fmt: str = "7z", level: int = 5,
             password: str | None = None, on_progress=None, stop_event=None,
             volume_mb: int = 0) -> None:
    """把若干文件/目录压成一个包。

    - `fmt`：`7z` 或 `zip`（zip 兼容性最好，7z 体积最小）
    - `password`：仅 7z/zip 支持；非空时启用 AES-256（`-mhe=on` 连文件名一起加密）
    - `volume_mb`：>0 时切成指定 MB 的分卷（`-v`），7z/zip 均支持
    - 命令行里用 `-spf` 保留完整路径会让不同盘符的条目错乱，所以统一靠 cwd 相对路径
    """
    out = Path(out_file).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    items = [Path(p).resolve() for p in paths if Path(p).exists()]
    if not items:
        raise SevenZipError("没有可压缩的文件")
    # 压缩包落在「被压缩的目录」内部时，7z 会一边写一边把自己读进去，产出半截包。
    # 单文件压缩到同目录属于正常用法，不受此限。
    for item in items:
        if item.is_dir() and (out == item or item in out.parents):
            raise SevenZipError(f"压缩包不能放在待压缩的目录内部：{out.name}")

    # 所有条目必须共享一个根，否则 7z 会把绝对路径塞进包里。
    # 单个文件夹要保留「文件夹本身」这一层，所以根取它的父目录，包内路径即文件夹名。
    if len(items) == 1:
        root = items[0].parent
    else:
        root = Path(os.path.commonpath(
            [str(p if p.is_dir() else p.parent) for p in items]))
    names = [os.path.relpath(str(p), str(root)) for p in items]

    args = ["a", "-bsp1", "-bso0", "-bse1", "-sccUTF-8", f"-t{fmt}"]
    args += _level_args(fmt, level)
    if password:
        args.append(f"-p{password}")
        if fmt == "7z":
            args.append("-mhe=on")
    if volume_mb > 0:
        args.append(f"-v{int(volume_mb)}m")
    args.append(str(out))
    args += names

    exe = find_7z()
    try:
        proc = subprocess.Popen(
            [exe, *args], cwd=str(root), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW, bufsize=0,
        )
    except OSError as exc:
        raise SevenZipError(f"无法启动 7z：{exc}") from exc

    last = [0]

    def sink(piece: str) -> None:
        if on_progress is None:
            return
        match = _PERCENT.search(piece)
        pct = max(0, min(100, int(match.group(1)))) if match else last[0]
        if pct != last[0] or not match:
            last[0] = pct
            try:
                on_progress(pct, piece.strip()[:120])
            except Exception:  # noqa: BLE001
                pass

    reader = threading.Thread(target=_pump, args=(proc.stdout, sink), daemon=True)
    reader.start()
    while proc.poll() is None:
        if stop_event is not None and stop_event.is_set():
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise SevenZipError("已停止")
        time.sleep(0.12)
    reader.join(2)
    if on_progress is not None and last[0] < 100:
        try:
            on_progress(100, "完成")
        except Exception:  # noqa: BLE001
            pass
    _check(proc.returncode)


def default_extract_dir(archive) -> Path:
    """双击压缩包时默认解开到「同名文件夹」。"""
    p = Path(archive)
    base = p
    while base.suffix.lower() in ARCHIVE_SUFFIXES and base.suffix:
        base = base.with_suffix("")
    return base if base != p else p.with_name(p.stem + "_解压")
