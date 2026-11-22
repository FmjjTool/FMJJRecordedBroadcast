"""mihomo 内核进程管理与本地控制接口。

生命周期：页面/启动引导调用 start() → 生成 runtime.yaml → 启动内核子进程
（纳入 Windows Job Object，主程序退出即整树结束）→ 轮询 /version 就绪。
停止时优先终止 Job Object，再兜底终止进程。

内核只监听 127.0.0.1 的混合端口与控制器端口，本程序通过进程环境变量使用它，
不改系统代理、不启用 TUN。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from toolbox import process_tree

from . import environ, provider, settings


class ProxyError(RuntimeError):
    """代理相关失败：message 可直接显示给用户。"""


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            return probe.connect_ex((host, int(port))) != 0
    except OSError:
        return False


def _tail(path: Path, limit: int = 1200) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = data[-limit:].decode("utf-8", errors="replace").strip()
    return text


class MihomoCore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._job = None
        self._log = None
        self._version = ""
        self._group = ""

    # ---------- 内核文件 ----------

    def candidates(self) -> list[Path]:
        cfg = settings.load()
        items: list[Path] = []
        if cfg.get("core_path"):
            items.append(Path(str(cfg["core_path"])))
        items.append(settings.bin_dir() / "mihomo.exe")
        root = Path(__file__).resolve().parents[3]
        items.append(root / "src" / "bin" / "mihomo.exe")
        items.append(root / "bin" / "mihomo.exe")
        local = os.environ.get("LOCALAPPDATA") or ""
        if local:
            base = Path(local)
            items += [
                base / "Programs" / "Clash Verge" / "verge-mihomo.exe",
                base / "Programs" / "Clash Verge" / "mihomo.exe",
                base / "io.github.clash-verge-rev.clash-verge-rev" / "verge-mihomo.exe",
                base / "Programs" / "clash-verge" / "verge-mihomo.exe",
            ]
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            folder = os.environ.get(env_key)
            if folder:
                items.append(Path(folder) / "Clash Verge" / "verge-mihomo.exe")
        for name in ("mihomo", "verge-mihomo", "clash-meta"):
            found = _which(name)
            if found:
                items.append(found)
        return items

    def resolve_core(self) -> Path | None:
        for item in self.candidates():
            try:
                if item.is_file():
                    return item
            except OSError:
                continue
        return None

    # ---------- 进程状态 ----------

    def is_running(self) -> bool:
        proc = self._proc
        return bool(proc is not None and proc.poll() is None)

    def status(self) -> dict:
        core = self.resolve_core()
        return {
            "running": self.is_running(),
            "pid": self._proc.pid if self.is_running() else 0,
            "version": self._version,
            "core_path": str(core) if core else "",
            "group": self._group,
            "proxy_url": environ.url(),
        }

    # ---------- 本地控制接口 ----------

    def _api(self, method: str, path: str, body: dict | None = None,
             params: dict | None = None, timeout: float = 4.0):
        cfg = settings.load()
        url = f"http://127.0.0.1:{int(cfg['api_port'])}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Authorization", f"Bearer {cfg['secret']}")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:120]
            except Exception:
                pass
            raise ProxyError(f"内核接口返回 HTTP {exc.code}{('：' + detail) if detail else ''}") from exc
        except Exception as exc:
            raise ProxyError(f"内核接口不可用：{exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ProxyError("内核接口返回了无法解析的内容") from exc

    def version(self) -> str:
        info = self._api("GET", "/version", timeout=2.0)
        self._version = str(info.get("version") or "")
        return self._version

    def proxies(self) -> dict:
        data = self._api("GET", "/proxies")
        proxies = data.get("proxies") or {}
        return proxies if isinstance(proxies, dict) else {}

    def group_name(self) -> str:
        if self._group:
            return self._group
        cfg = settings.load()
        proxies = self.proxies()
        group = str(cfg.get("group") or "")
        if group and group in proxies:
            self._group = group
            return group
        for name, item in proxies.items():
            if isinstance(item, dict) and str(item.get("type", "")).lower() in (
                    "selector", "select"):
                self._group = name
                return name
        return ""

    def group_info(self) -> dict:
        group = self.group_name()
        if not group:
            return {"name": "", "now": "", "all": []}
        item = self.proxies().get(group) or {}
        nodes = [str(name) for name in (item.get("all") or [])]
        return {"name": group, "now": str(item.get("now") or ""), "all": nodes}

    def select(self, node: str) -> None:
        group = self.group_name()
        if not group:
            raise ProxyError("没有可切换节点的策略组")
        self._api("PUT", "/proxies/" + urllib.parse.quote(group, safe=""),
                  body={"name": str(node)})

    def delay_all(self, timeout_ms: int = 5000) -> dict:
        group = self.group_name()
        if not group:
            raise ProxyError("没有可测速的策略组")
        data = self._api(
            "GET",
            "/group/" + urllib.parse.quote(group, safe="") + "/delay",
            params={"url": settings.DEFAULT_TEST_URL, "timeout": int(timeout_ms)},
            timeout=max(6.0, timeout_ms / 1000 + 3),
        )
        return {str(k): int(v) for k, v in (data or {}).items()
                if isinstance(v, (int, float))}

    def set_mode(self, mode: str) -> None:
        if mode not in settings.MODES:
            raise ProxyError("未知的代理模式")
        self._api("PATCH", "/configs", body={"mode": mode})

    def reload(self) -> None:
        path, group = provider.build_runtime()
        self._group = group
        self._api("PUT", "/configs", body={"force": True, "path": str(path)},
                  params={"force": "true"})

    def check_exit(self, timeout: float = 10.0) -> str:
        """通过本程序代理访问出口 IP，验证链路真的通了。"""
        proxy_url = environ.url()
        if not proxy_url:
            raise ProxyError("程序代理未启用，请先打开开关")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        request = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "FengMingToolbox/proxy-check"})
        try:
            with opener.open(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            ip = str(data.get("ip") or "")
        except Exception as exc:
            raise ProxyError(f"经代理访问失败：{exc}") from exc
        if not ip:
            raise ProxyError("代理已连通，但没有拿到出口地址")
        return ip

    # ---------- 启停 ----------

    def start(self) -> dict:
        with self._lock:
            if self.is_running():
                try:
                    self.version()
                except ProxyError:
                    pass
                return self.status()
            cfg = settings.load()
            if not port_free(cfg["mixed_port"]) or not port_free(cfg["api_port"]):
                raise ProxyError(
                    f"端口被占用：混合端口 {cfg['mixed_port']}、控制端口 {cfg['api_port']}，"
                    "请在页面里换一组端口后重试")
            core = self.resolve_core()
            if core is None:
                raise ProxyError("未找到代理内核：请到「全局设置 → 检测依赖」下载，"
                                 "或在页面里选择已有的内核文件")
            runtime, group = provider.build_runtime(cfg)
            log_path = settings.log_path()
            try:
                self._log = open(log_path, "ab")
            except OSError as exc:
                raise ProxyError(f"打开内核日志失败：{exc.strerror or '磁盘不可写'}") from exc
            child_env = os.environ.copy()
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                        "http_proxy", "https_proxy", "all_proxy"):
                child_env.pop(key, None)
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                proc = subprocess.Popen(
                    [str(core), "-d", str(settings.proxy_dir()), "-f", str(runtime)],
                    stdout=self._log, stderr=self._log, stdin=subprocess.DEVNULL,
                    creationflags=flags, env=child_env,
                    cwd=str(settings.proxy_dir()),
                )
            except OSError as exc:
                self._close_log()
                raise ProxyError(f"启动内核失败：{exc}") from exc
            self._proc = proc
            self._group = group
            self._job = process_tree.create_kill_job()
            if self._job:
                process_tree.assign_job(self._job, proc)
            deadline = time.time() + 15
            while time.time() < deadline:
                if proc.poll() is not None:
                    detail = _tail(log_path) or "进程已退出"
                    self._cleanup_process()
                    raise ProxyError(f"内核启动失败：{detail}")
                try:
                    self.version()
                    return self.status()
                except ProxyError:
                    time.sleep(0.3)
            self._cleanup_process()
            detail = _tail(log_path)
            raise ProxyError("内核启动超时" + (f"：{detail}" if detail else "，请检查端口与提供商配置"))

    def stop(self) -> None:
        with self._lock:
            self._cleanup_process()

    def _cleanup_process(self) -> None:
        proc, job = self._proc, self._job
        self._proc = None
        self._job = None
        self._version = ""
        if job:
            process_tree.terminate_job(job)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._close_log()

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None


def _which(name: str) -> Path | None:
    import shutil

    found = shutil.which(name)
    return Path(found) if found else None


core = MihomoCore()