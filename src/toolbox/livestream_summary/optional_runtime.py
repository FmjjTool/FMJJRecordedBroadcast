"""可选本地总结运行模块（Qt-free L3）。

本模块是 optional_components/local_llm 的唯一写入方（manifest schema 1）。
CPU/Vulkan 程序、GGUF 模型分别保存；均不属于 FULL 内置资源。下载资产来自
固定官方版本并强校验 SHA-256；导入模型只复制，不修改、移动或删除用户源文件。
模型以首个 GGUF 文件名去重，manifest 为受控副本的索引；不读写 config/。
只有显式安装、导入、删除、推理才会创建目录；catalog/status 均无写入副作用。
桌面/Web 调用相同服务；跨进程文件锁保护下载、卸载及推理，忙碌时拒绝新操作。
所有公开 API 的 root 仅用于显式隔离与测试，不接受 Web 端传入的存储根目录。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import requests

from .client import CancelledError, ChatClient, ChatError, _content

_OWNER = "fmjj.optional-local-llm"
_SCHEMA = 1
_TAG = "b12758"
_RELEASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{_TAG}"
_RUNTIMES = {
    "cpu": {
        "id": "cpu", "label": "CPU 模块", "size": 25273544,
        "filename": f"llama-{_TAG}-bin-win-cpu-x64.zip",
        "sha256": "cc705feb715dd11e2710007710d878e19e16a5a6f6a8d003bf9dda6ded4fe6c8",
    },
    "vulkan": {
        "id": "vulkan", "label": "GPU 模块（Vulkan）", "size": 39145625,
        "filename": f"llama-{_TAG}-bin-win-vulkan-x64.zip",
        "sha256": "45902f11e019fa8fc1acc9a815cc230a1a590fd2f64109124550bbb969585123",
    },
}
_MODELS = (
    {
        "id": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "label": "通义千问 2.5 3B · Q4_K_M", "size": 2104932768,
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "revision": "7dabda4d13d513e3e842b20f0d435c732f172cbe",
        "files": [
            {"filename": "qwen2.5-3b-instruct-q4_k_m.gguf", "size": 2104932768,
             "sha256": "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"},
        ],
    },
    {
        "id": "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
        "label": "通义千问 2.5 7B · Q4_K_M（2 个分片）", "size": 4683073632,
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "revision": "bb5d59e06d9551d752d08b292a50eb208b07ab1f",
        "files": [
            {"filename": "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
             "size": 3993201344,
             "sha256": "dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db"},
            {"filename": "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
             "size": 689872288,
             "sha256": "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a"},
        ],
    },
)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SPLIT = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", re.I)
_CHUNK = 256 * 1024
_MAX_MODEL = 64 * 1024 ** 3
_MAX_ZIP_FILES = 1024
_MAX_ZIP_TOTAL = 2 * 1024 ** 3
_MAX_ZIP_FILE = 512 * 1024 ** 2
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RuntimeErrorBase(RuntimeError):
    """可直接显示的本地推理组件错误，不包含用户绝对路径或凭据。"""


def _check_cancel(cancel) -> None:
    if cancel is not None and bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise CancelledError("已停止")


def _report(progress, percent, message):
    if progress:
        progress(max(0, min(100, int(percent))), str(message))


def _supported():
    return sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64")


def _app_root():
    return (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[3])


def _default_root():
    app = _app_root()
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    fallback = local / "FengMingToolbox" / "optional_components" / "local_llm"
    candidate = app / "optional_components" / "local_llm"
    # 不用 mkdir/写探针判断路径，保证 status() 真正只读。
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        folder = os.environ.get(key)
        if folder:
            try:
                app.relative_to(Path(folder).resolve())
                return fallback
            except ValueError:
                pass
    if not candidate.exists() and fallback.exists():
        return fallback
    return candidate if os.access(app, os.W_OK) else fallback


def _is_link(path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _plain_path(path):
    path = Path(os.path.abspath(path))
    for current in (path, *path.parents):
        if _is_link(current):
            raise RuntimeErrorBase("组件目录包含链接或重解析点，已停止操作")
    return path


def _root(root=None):
    return _plain_path(Path(root) if root is not None else _default_root())


def _safe_name(value, *, gguf=False):
    text = str(value or "")
    if (not text or len(text) > 180 or text != text.strip() or text.endswith((".", " "))
            or not re.fullmatch(r"[\w.-]+", text, flags=re.UNICODE)
            or text in (".", "..") or text.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
            or (gguf and not text.lower().endswith(".gguf"))):
        raise RuntimeErrorBase("模型或组件标识无效")
    return text


def _within(root, *parts):
    base = _root(root)
    path = _plain_path(base.joinpath(*parts))
    try:
        path.relative_to(base)
    except ValueError:
        raise RuntimeErrorBase("组件路径超出受控目录") from None
    return path


class _Guard:
    """OS 级非阻塞互斥：桌面/Web 共用，异常退出后由 OS 自动释放锁。"""

    def __init__(self, root):
        self.root = root
        self.handle = None

    def __enter__(self):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = _within(self.root, ".store.lock")
            self.handle = path.open("a+b")
            if path.stat().st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle:
                self.handle.close()
                self.handle = None
            raise RuntimeErrorBase("本地推理组件正在使用或目录不可写，请稍后重试") from exc
        return self

    def __exit__(self, *_):
        if self.handle:
            handle, self.handle = self.handle, None
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def catalog():
    """固定官方资产目录；size 为下载字节数，不含模型加载内存。"""
    runtimes = [{**item, "version": _TAG, "url": f"{_RELEASE}/{item['filename']}",
                 "optional": True, "bundled": False} for item in _RUNTIMES.values()]
    models = [{**item, "filename": item["id"], "files": [dict(f) for f in item["files"]],
               "optional": True, "bundled": False} for item in _MODELS]
    return {"runtimes": runtimes, "models": models}


def _read_manifest(directory, kind, identifier):
    try:
        path = _plain_path(directory / "manifest.json")
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("owner") != _OWNER
                or value.get("schema") != _SCHEMA or value.get("kind") != kind
                or value.get("id") != identifier):
            return None
        files = value.get("files")
        if not isinstance(files, list) or not files or len(files) > _MAX_ZIP_FILES:
            return None
        names = set()
        for item in files:
            if not isinstance(item, dict) or not _HASH.fullmatch(str(item.get("sha256") or "")):
                return None
            name = _relative_name(item.get("filename"))
            if kind == "model":
                _safe_name(name, gguf=True)
            if name.casefold() in names or not 0 < int(item.get("size", 0)) <= _MAX_MODEL:
                return None
            names.add(name.casefold())
        return value
    except (OSError, ValueError, TypeError, RuntimeErrorBase):
        return None


def _relative_name(name):
    text = str(name or "")
    if "\\" in text or ":" in text or text.startswith("/") or "\x00" in text:
        raise RuntimeErrorBase("压缩包包含不安全路径")
    rel = PurePosixPath(text)
    if not rel.parts or str(rel) != text or any(p in (".", "..") for p in rel.parts):
        raise RuntimeErrorBase("压缩包包含不安全路径")
    for part in rel.parts:
        _safe_name(part)
    return text


def _entry_ready(root, kind, identifier, *, verify=False, cancel=None):
    folder = _within(root, "runtimes" if kind == "runtime" else "models", identifier)
    value = _read_manifest(folder, kind, identifier)
    if not value:
        return None
    try:
        for item in value["files"]:
            path = _within(folder, item["filename"])
            if not path.is_file() or path.stat().st_size != int(item["size"]):
                return None
            if verify and (kind == "runtime" or path.stat().st_mtime_ns != item.get("mtime_ns")):
                if _sha_file(path, cancel) != item["sha256"]:
                    return None
        if kind == "runtime":
            if value.get("version") != _TAG or value.get("archive_sha256") != _RUNTIMES[identifier]["sha256"]:
                return None
            server = _relative_name(value.get("server"))
            if Path(server).name.lower() != "llama-server.exe" or not any(
                    i["filename"] == server for i in value["files"]):
                return None
        elif not any(i["filename"] == identifier for i in value["files"]):
            return None
        return value
    except (OSError, KeyError, ValueError, TypeError, RuntimeErrorBase):
        return None


def status(*, root=None):
    """只读本地安装快照；不扫描用户模型目录，不初始化目录，不输出绝对路径。"""
    base = _root(root)
    runtimes = []
    for key, item in _RUNTIMES.items():
        value = _entry_ready(base, "runtime", key)
        runtimes.append({"id": key, "label": item["label"], "installed": bool(value),
                         "size": sum(i["size"] for i in value["files"]) if value else 0,
                         "download_size": item["size"], "version": _TAG,
                         "optional": True, "bundled": False})
    models = []
    folder = _within(base, "models")
    if folder.is_dir():
        for child in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            try:
                identifier = _safe_name(child.name, gguf=True)
                value = _entry_ready(base, "model", identifier)
            except RuntimeErrorBase:
                continue
            if value:
                models.append({"id": identifier, "name": str(value.get("label") or identifier),
                               "size": sum(i["size"] for i in value["files"]),
                               "parts": len(value["files"])})
    return {"supported": _supported(), "runtimes": runtimes, "models": models,
            "model_catalog": catalog()["models"],
            "note": "CPU/GPU 模块与模型均为可选下载，不随标准版或 FULL 版内置；"
                    "GPU 模块需要显卡驱动支持 Vulkan。"}


def _sha_file(path, cancel=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            _check_cancel(cancel)
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _url_allowed(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and any(host == domain or host.endswith("." + domain)
                    for domain in ("github.com", "githubusercontent.com", "huggingface.co", "hf.co")))


def _proxy_candidates():
    values = [os.environ.get("HTTPS_PROXY"), os.environ.get("ALL_PROXY")]
    values += [f"http://127.0.0.1:{port}" for port in (7890, 10809, 12450)]
    try:
        result = subprocess.run(["git", "-C", str(_app_root()), "config", "--get", "http.proxy"],
                                capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW)
        if result.returncode == 0:
            values.append(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    seen = set()
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https", "socks5", "socks5h") or not parsed.hostname:
                continue
            port = parsed.port or (443 if parsed.scheme == "https" else 1080 if parsed.scheme.startswith("socks") else 80)
            with socket.create_connection((parsed.hostname, port), timeout=0.5):
                pass
        except (OSError, ValueError):
            continue
        yield value


def _get_response(session, url, cancel):
    for _ in range(6):
        _check_cancel(cancel)
        if not _url_allowed(url):
            raise RuntimeErrorBase("下载重定向不在官方域名范围内")
        response = session.get(url, stream=True, allow_redirects=False, timeout=(5, 5))
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("Location", "")
        response.close()
        if not location:
            raise RuntimeErrorBase("下载重定向缺少目标地址")
        url = urljoin(url, location)
    raise RuntimeErrorBase("下载重定向次数过多")


def _interrupt_response(response):
    # shutdown 精确中断本次下载 socket；不影响系统代理、其它请求或用户进程。
    for get_socket in (lambda: response.raw._connection.sock,
                       lambda: response.raw._fp.fp.raw._sock):
        try:
            sock = get_socket()
            if sock:
                sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
    with contextlib.suppress(Exception):
        response.close()


def _download(url, target, size, expected_sha, *, progress=None, cancel=None):
    if not _url_allowed(url) or not _HASH.fullmatch(str(expected_sha)) or not 0 < int(size) <= _MAX_MODEL:
        raise RuntimeErrorBase("官方资产缺少可信大小或 SHA-256，已拒绝下载")
    proxies = iter(_proxy_candidates())
    proxy = None
    attempt = 0
    errors = []
    try:
        while True:
            _check_cancel(cancel)
            attempt += 1
            _report(progress, 0, "通过直连下载官方资产…" if proxy is None else "直连失败，尝试可用代理…")
            try:
                with requests.Session() as session:
                    session.trust_env = False  # 直连必须显式忽略环境残留代理。
                    session.headers.update({"User-Agent": "FengMingToolbox/optional-llm"})
                    if proxy:
                        session.proxies.update({"http": proxy, "https": proxy})
                    response = _get_response(session, url, cancel)
                    stopped = threading.Event()

                    def watch():
                        while not stopped.wait(0.1):
                            try:
                                _check_cancel(cancel)
                            except CancelledError:
                                _interrupt_response(response)
                                return

                    watcher = threading.Thread(target=watch, daemon=True)
                    watcher.start()
                    try:
                        if response.status_code != 200:
                            raise RuntimeErrorBase(f"官方资产下载失败（HTTP {response.status_code}）")
                        if "text/html" in response.headers.get("Content-Type", "").lower():
                            raise RuntimeErrorBase("下载源返回网页，未安装任何文件")
                        length = response.headers.get("Content-Length")
                        if length and int(length) != size:
                            raise RuntimeErrorBase("官方资产响应大小与固定清单不符")
                        digest, done = hashlib.sha256(), 0
                        with target.open("wb") as stream:
                            for chunk in response.iter_content(_CHUNK):
                                _check_cancel(cancel)
                                if not chunk:
                                    continue
                                done += len(chunk)
                                if done > size:
                                    raise RuntimeErrorBase("下载数据超过固定清单大小")
                                stream.write(chunk)
                                digest.update(chunk)
                                _report(progress, done * 100 // size, f"已下载 {done / 1048576:.1f} / {size / 1048576:.1f} MB")
                        _check_cancel(cancel)
                        if done != size or digest.hexdigest() != expected_sha:
                            raise RuntimeErrorBase("资产大小或 SHA-256 校验失败，未安装任何文件")
                        return
                    finally:
                        stopped.set()
                        _interrupt_response(response)
            except requests.RequestException as exc:
                _check_cancel(cancel)
                errors.append(type(exc).__name__)
                target.unlink(missing_ok=True)
                try:
                    proxy = next(proxies)
                except StopIteration:
                    raise RuntimeErrorBase("官方源直连与可用代理均下载失败，请检查网络后重试"
                                           f"（{', '.join(errors[-3:])}）") from exc
            except (ValueError, OSError) as exc:
                _check_cancel(cancel)
                raise RuntimeErrorBase("下载文件无法安全保存或响应格式异常") from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _remove_tree(path, root):
    path = _within(root, path.relative_to(root))
    if path == root:
        raise RuntimeErrorBase("拒绝删除组件根目录")
    if path.exists():
        for current, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                if _is_link(Path(current) / name):
                    raise RuntimeErrorBase("受控目录包含链接，未执行删除")
        shutil.rmtree(path)


def _write_manifest(folder, kind, identifier, files, **extra):
    value = {"owner": _OWNER, "schema": _SCHEMA, "kind": kind, "id": identifier,
             "files": files, **extra}
    (folder / "manifest.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_record(path, relative, cancel=None):
    st = path.stat()
    return {"filename": relative, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": _sha_file(path, cancel)}


def _publish(root, staged, kind, identifier):
    target = _within(root, "runtimes" if kind == "runtime" else "models", identifier)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(".previous-" + uuid.uuid4().hex)
    previous = False
    if target.exists():
        if not _read_manifest(target, kind, identifier):
            raise RuntimeErrorBase("目标目录已有未知内容，未覆盖；请人工核对")
        target.rename(backup)
        previous = True
    try:
        staged.rename(target)
    except BaseException:
        if previous:
            backup.rename(target)
        raise
    if previous:
        _remove_tree(backup, root)


def _extract_runtime(archive_path, staged, cancel):
    records, servers = [], []
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > _MAX_ZIP_FILES:
            raise RuntimeErrorBase("运行模块压缩包条目数量异常")
        total, names, selected = 0, set(), []
        for info in entries:
            _check_cancel(cancel)
            name = info.filename.rstrip("/")
            _relative_name(name)
            if name.casefold() in names:
                raise RuntimeErrorBase("运行模块压缩包含重复或大小写冲突路径")
            names.add(name.casefold())
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if (info.flag_bits & 1 or file_type not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or (info.is_dir() and file_type == stat.S_IFREG)
                    or (not info.is_dir() and file_type == stat.S_IFDIR)):
                raise RuntimeErrorBase("运行模块压缩包含链接、特殊文件或加密条目")
            if info.is_dir():
                continue
            total += info.file_size
            if (info.file_size <= 0 or info.file_size > _MAX_ZIP_FILE or total > _MAX_ZIP_TOTAL
                    or info.file_size > max(1, info.compress_size) * 500
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise RuntimeErrorBase("运行模块压缩包大小、压缩比或类型异常")
            suffix = PurePosixPath(name).suffix.lower()
            basename = PurePosixPath(name).name.lower()
            # 只安装服务及依赖 DLL、许可文件；其它 CLI、PDB、开发文件不执行也不落盘。
            if basename == "llama-server.exe" or suffix == ".dll" or basename.startswith(("license", "copying", "notice")):
                if basename == "llama-server.exe":
                    servers.append(name)
                selected.append((info, name))
        if len(servers) != 1:
            raise RuntimeErrorBase("运行模块中未找到唯一的 llama-server.exe")
        for info, name in selected:
            _check_cancel(cancel)
            target = _within(staged, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as dest:
                while True:
                    _check_cancel(cancel)
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    dest.write(chunk)
            if target.stat().st_size != info.file_size:
                raise RuntimeErrorBase("运行模块解压后大小不符")
            if target.suffix.lower() in (".exe", ".dll"):
                with target.open("rb") as file:
                    if file.read(2) != b"MZ":
                        raise RuntimeErrorBase("运行模块包含无效的 Windows 可执行文件")
            records.append(_file_record(target, name, cancel))
    return records, servers[0]


def install_runtime(backend, *, progress=None, cancel=None, root=None):
    backend = str(backend or "")
    if backend not in _RUNTIMES:
        raise RuntimeErrorBase("请选择 CPU 或 Vulkan GPU 模块")
    if not _supported():
        raise RuntimeErrorBase("可选推理模块目前仅支持 Windows x64")
    base = _root(root)
    with _Guard(base):
        _check_cancel(cancel)
        if _entry_ready(base, "runtime", backend, verify=True, cancel=cancel):
            return next(i for i in status(root=base)["runtimes"] if i["id"] == backend)
        temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=base))
        try:
            item = _RUNTIMES[backend]
            partial = temporary / "runtime.zip.part"
            _download(f"{_RELEASE}/{item['filename']}", partial, item["size"], item["sha256"],
                      progress=lambda p, m: _report(progress, p * 8 // 10, m), cancel=cancel)
            staged = temporary / "unpacked"
            staged.mkdir()
            _report(progress, 85, "正在校验并安装运行模块…")
            records, server = _extract_runtime(partial, staged, cancel)
            _write_manifest(staged, "runtime", backend, records, version=_TAG,
                            archive_sha256=item["sha256"], server=server)
            _check_cancel(cancel)
            _publish(base, staged, "runtime", backend)
            _report(progress, 100, "运行模块已安装")
        except (OSError, zipfile.BadZipFile) as exc:
            raise RuntimeErrorBase("运行模块无法安装，请检查文件、可用空间与目录权限") from exc
        finally:
            _remove_tree(temporary, base)
    return next(i for i in status(root=base)["runtimes"] if i["id"] == backend)


def _gguf_header(path):
    with path.open("rb") as file:
        header = file.read(24)
    if len(header) != 24 or header[:4] != b"GGUF" or int.from_bytes(header[4:8], "little") not in (2, 3):
        raise RuntimeErrorBase("文件不是受支持的 GGUF v2/v3 模型")


def _model_result(identifier, value):
    return {"id": identifier, "name": str(value.get("label") or identifier),
            "size": sum(i["size"] for i in value["files"])}


def download_model(model_id, *, progress=None, cancel=None, root=None):
    identifier = _safe_name(model_id, gguf=True)
    item = next((m for m in _MODELS if m["id"] == identifier), None)
    if item is None:
        raise RuntimeErrorBase("模型不在固定官方目录内，可使用本地 GGUF 导入")
    base = _root(root)
    with _Guard(base):
        _check_cancel(cancel)
        ready = _entry_ready(base, "model", identifier, verify=True, cancel=cancel)
        if ready:
            return _model_result(identifier, ready)
        temporary = Path(tempfile.mkdtemp(prefix=".model-", dir=base))
        try:
            staged = temporary / "model"
            staged.mkdir()
            records, complete = [], 0
            for entry in item["files"]:
                filename = entry["filename"]
                partial = staged / (filename + ".part")
                url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{filename}"

                def update(percent, message, previous=complete, current=entry["size"]):
                    _report(progress, (previous + current * percent // 100) * 100 // item["size"], message)

                _download(url, partial, entry["size"], entry["sha256"], progress=update, cancel=cancel)
                _gguf_header(partial)
                target = staged / filename
                partial.rename(target)
                records.append({**entry, "mtime_ns": target.stat().st_mtime_ns})
                complete += entry["size"]
            _write_manifest(staged, "model", identifier, records, label=item["label"],
                            source="official", repo=item["repo"], revision=item["revision"])
            _check_cancel(cancel)
            _publish(base, staged, "model", identifier)
            _report(progress, 100, "模型已下载并校验")
            return _model_result(identifier, {"label": item["label"], "files": records})
        except OSError as exc:
            raise RuntimeErrorBase("模型无法保存，请检查可用空间与目录权限") from exc
        finally:
            _remove_tree(temporary, base)


def _import_sources(path):
    first = Path(path)
    identifier = _safe_name(first.name, gguf=True)
    match = _SPLIT.fullmatch(identifier)
    if not match:
        sources = [first]
    else:
        if int(match[2]) != 1 or not 1 <= int(match[3]) <= 64:
            raise RuntimeErrorBase("分片模型请选择第一个分片（00001），并把所有分片放在同一目录")
        sources = [first.with_name(f"{match[1]}-{i:05d}-of-{int(match[3]):05d}.gguf")
                   for i in range(1, int(match[3]) + 1)]
    total = 0
    for source in sources:
        if not source.is_file() or not 24 <= source.stat().st_size <= _MAX_MODEL:
            raise RuntimeErrorBase("GGUF 模型或分片缺失、大小异常")
        _gguf_header(source)
        total += source.stat().st_size
    if total > _MAX_MODEL:
        raise RuntimeErrorBase("模型分片总大小超过支持范围")
    return identifier, sources, total


def import_model(path, *, progress=None, cancel=None, root=None):
    identifier, sources, total = _import_sources(path)
    base = _root(root)
    with _Guard(base):
        _check_cancel(cancel)
        if _within(base, "models", identifier).exists():
            raise RuntimeErrorBase("已存在同名受控模型，请改名后导入或先删除受控副本")
        temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=base))
        try:
            staged = temporary / "model"
            staged.mkdir()
            records, done = [], 0
            for source in sources:
                before = source.stat()
                target = staged / source.name
                digest = hashlib.sha256()
                with source.open("rb") as reader, target.open("wb") as writer:
                    while True:
                        _check_cancel(cancel)
                        chunk = reader.read(_CHUNK)
                        if not chunk:
                            break
                        writer.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if done > total:
                            raise RuntimeErrorBase("源模型导入期间发生变化，已停止")
                        _report(progress, done * 100 // total, "正在复制并校验模型…")
                after = source.stat()
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or target.stat().st_size != before.st_size:
                    raise RuntimeErrorBase("源模型导入期间发生变化，已停止")
                records.append({"filename": source.name, "size": before.st_size,
                                "sha256": digest.hexdigest(), "mtime_ns": target.stat().st_mtime_ns})
            _write_manifest(staged, "model", identifier, records, label=identifier, source="import")
            _check_cancel(cancel)
            _publish(base, staged, "model", identifier)
            _report(progress, 100, "模型副本已导入，原文件保持不变")
            return _model_result(identifier, {"files": records})
        except OSError as exc:
            raise RuntimeErrorBase("模型导入失败，请检查源文件、可用空间与目录权限") from exc
        finally:
            _remove_tree(temporary, base)


def _remove(kind, identifier, root):
    base = _root(root)
    if not base.is_dir():
        return False
    with _Guard(base):
        folder = _within(base, "runtimes" if kind == "runtime" else "models", identifier)
        if not folder.exists():
            return False
        if not _read_manifest(folder, kind, identifier):
            raise RuntimeErrorBase("目录内容不属于受控组件，未执行删除")
        _remove_tree(folder, base)
        return True


def remove_runtime(backend, *, root=None):
    if backend not in _RUNTIMES:
        raise RuntimeErrorBase("未知的运行模块")
    return _remove("runtime", backend, root)


def remove_model(model_id, *, root=None):
    return _remove("model", _safe_name(model_id, gguf=True), root)


def _options(options):
    value = dict(options or {})
    backend = str(value.get("backend") or "cpu")
    if backend not in _RUNTIMES:
        raise RuntimeErrorBase("请选择 CPU 或 Vulkan GPU 模块")
    result = {"backend": backend, "model": _safe_name(value.get("model"), gguf=True)}
    for key, default, lo, hi in (("context", 16384, 1024, 131072), ("threads", 0, 0, 256),
                                 ("gpu_layers", 99, 0, 999), ("batch", 256, 1, 2048),
                                 ("max_tokens", 2048, 1, 32768)):
        try:
            number = int(value.get(key, default))
        except (ValueError, TypeError):
            raise RuntimeErrorBase(f"{key} 必须为整数") from None
        if not lo <= number <= hi:
            raise RuntimeErrorBase(f"{key} 超出支持范围")
        result[key] = number
    try:
        temperature = float(value.get("temperature", 0.3))
    except (ValueError, TypeError):
        raise RuntimeErrorBase("temperature 必须为数字") from None
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise RuntimeErrorBase("temperature 超出支持范围")
    if result["max_tokens"] >= result["context"] or result["batch"] > result["context"]:
        raise RuntimeErrorBase("输出长度与批量大小必须小于上下文容量")
    result["temperature"] = temperature
    return result


class _LocalClient(ChatClient):
    """复用现有客户端接口/响应解析；回环请求明确不走代理，取消关闭自有进程。"""

    def __init__(self, owner, base_url, token):
        super().__init__(base_url, token, "fmjj-local", allow_empty_key=True)
        self.owner = owner
        self.http = requests.Session()
        self.http.trust_env = False

    def chat(self, system, user, *, temperature=0.3, max_tokens=8192, cancel=None, on_wait=None):
        flag = cancel or self.owner.cancel
        _check_cancel(flag)
        if self.owner._closed:
            raise ChatError("本地推理会话已结束")
        options = self.owner.options
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": options["temperature"],
                "max_tokens": min(max(1, int(max_tokens)), options["max_tokens"]), "stream": False}
        self.requests_made += 1
        try:
            response = self.http.post(self.base_url + "/chat/completions", json=body,
                                      headers={"Authorization": "Bearer " + self.api_key}, timeout=(3, 600))
            with response:
                _check_cancel(flag)
                if response.status_code >= 400:
                    raise ChatError(f"本地模型请求失败（HTTP {response.status_code}），请检查上下文、模型与内存设置")
                return _content(response.json())
        except (requests.RequestException, ValueError) as exc:
            _check_cancel(flag)
            raise ChatError("本地推理连接中断，请检查模型、显卡驱动与可用内存") from exc

    def close(self):
        self.http.close()


class LocalSession:
    """每个任务独立的 native 子进程；with 结束必释放，不连接已有用户服务。"""

    def __init__(self, options, cancel=None, progress=None, *, root=None):
        self.options = _options(options)
        self.cancel = cancel
        self.progress = progress
        self.root = _root(root)
        self.process = self.client = self.guard = None
        self._closed = True
        self._process_lock = threading.Lock()
        self._stop_monitor = threading.Event()
        self._job = None

    def __enter__(self):
        if not _supported():
            raise RuntimeErrorBase("可选推理模块目前仅支持 Windows x64")
        _check_cancel(self.cancel)
        self.guard = _Guard(self.root)
        self.guard.__enter__()
        self._closed = False
        try:
            opts = self.options
            _report(self.progress, 0, "正在校验本地推理组件…")
            runtime = _entry_ready(self.root, "runtime", opts["backend"], verify=True, cancel=self.cancel)
            model = _entry_ready(self.root, "model", opts["model"], verify=True, cancel=self.cancel)
            if not runtime:
                raise RuntimeErrorBase("所选运行模块未安装或校验失败，请在全局设置中安装")
            if not model:
                raise RuntimeErrorBase("所选模型未安装、分片不全或校验失败，请重新下载或导入")
            executable = _within(self.root, "runtimes", opts["backend"], runtime["server"])
            model_path = _within(self.root, "models", opts["model"], opts["model"])
            _gguf_header(model_path)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            token = secrets.token_urlsafe(32)
            command = [str(executable), "--model", str(model_path), "--host", "127.0.0.1", "--port", str(port),
                       "--alias", "fmjj-local", "--no-webui", "--parallel", "1", "--ctx-size", str(opts["context"]),
                       "--batch-size", str(opts["batch"]), "--n-gpu-layers",
                       str(opts["gpu_layers"] if opts["backend"] == "vulkan" else 0)]
            if opts["threads"]:
                command += ["--threads", str(opts["threads"])]
            env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
            env["LLAMA_API_KEY"] = token  # 临时会话令牌不放入命令行、配置或日志。
            self.process = subprocess.Popen(command, cwd=executable.parent, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=_NO_WINDOW)
            try:
                from toolbox.process_tree import create_kill_job, assign_job
                self._job = create_kill_job()
                if self._job:
                    assign_job(self._job, self.process)
            except Exception:
                self._job = None
            threading.Thread(target=self._monitor_cancel, daemon=True).start()
            self.client = _LocalClient(self, f"http://127.0.0.1:{port}/v1", token)
            _report(self.progress, 5, "正在加载本地模型，首次加载可能需要一些时间…")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                _check_cancel(self.cancel)
                code = self.process.poll()
                if code is not None:
                    raise RuntimeErrorBase(f"本地推理进程启动失败（退出码 {code}），请检查模型、显卡驱动与可用内存")
                try:
                    with self.client.http.get(self.client.base_url + "/models",
                                              headers={"Authorization": "Bearer " + token}, timeout=(0.5, 0.5)) as response:
                        if response.status_code == 200:
                            data = response.json().get("data", [])
                            if any(isinstance(item, dict) and item.get("id") == "fmjj-local" for item in data):
                                _check_cancel(self.cancel)
                                _report(self.progress, 100, "本地模型已就绪")
                                return self.client
                except (requests.RequestException, ValueError, AttributeError):
                    pass
                self._stop_monitor.wait(0.1)
            raise RuntimeErrorBase("本地模型加载超时，请减小上下文或使用更小模型")
        except BaseException:
            self.close()
            raise

    def _monitor_cancel(self):
        while not self._stop_monitor.wait(0.1):
            try:
                _check_cancel(self.cancel)
            except CancelledError:
                self._stop_child()
                return

    def _stop_child(self):
        with self._process_lock:
            if self.process is not None and self.process.poll() is None:
                with contextlib.suppress(OSError):
                    self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        self.process.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                        self.process.wait(timeout=2)
            if self._job:
                from toolbox.process_tree import terminate_job
                terminate_job(self._job)
                self._job = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop_monitor.set()
        try:
            self._stop_child()
            if self.client:
                self.client.close()
        finally:
            if self.guard:
                self.guard.__exit__(None, None, None)
                self.guard = None

    def __exit__(self, *_):
        self.close()
