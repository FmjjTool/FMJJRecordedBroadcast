"""本地资料站 · 本地服务（FastAPI 后端 + 静态站反代）。

浏览器只访问 127.0.0.1:<站点端口>，页面里的 /api、/uploads 由本模块反代到
127.0.0.1:<后端端口>，不需要 nginx。服务只绑定本机回环地址，不对外网开放。
"""

from __future__ import annotations

import http.client
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from . import config, imgcache, localimages

UI_HINT = "本地资料站"


class ServerError(RuntimeError):
    """本地服务启动失败（端口占用 / 依赖缺失 / 前端未构建）。"""


def _health(port: int, timeout: float = 1.2) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            resp = client.get(f"http://127.0.0.1:{port}/api/health")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


class _QuietServer(ThreadingHTTPServer):
    """浏览器关闭连接时不要往日志里刷 ConnectionResetError 堆栈。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class _StaticProxy(BaseHTTPRequestHandler):
    """静态文件 + /api、/uploads 反代；history 路由回退到 index.html。"""

    server_version = "LocalWiki/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *args):  # noqa: A003 - 静默访问日志
        pass

    def do_GET(self):  # noqa: N802
        self._handle(head=False)

    def do_HEAD(self):  # noqa: N802
        self._handle(head=True)

    def _handle(self, head: bool):
        parts = urlsplit(self.path)
        if parts.path == "/api/img":
            self._image(parts, head)
            return
        if parts.path.startswith("/api/") or parts.path.startswith("/uploads/"):
            self._proxy(parts, head)
            return
        dist = Path(self.server.dist_dir)  # type: ignore[attr-defined]
        rel = unquote(parts.path).lstrip("/")
        try:
            target = (dist / rel).resolve()
            target.relative_to(dist.resolve())
        except (OSError, ValueError):
            self._send(403, b"Forbidden", "text/plain; charset=utf-8", head=head)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            target = dist / "index.html"
        if not target.is_file():
            self._send(404, b"dist/index.html not found", "text/plain; charset=utf-8",
                       head=head)
            return
        try:
            body = target.read_bytes()
        except OSError as exc:
            self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8", head=head)
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        cache = ("no-cache" if target.name == "index.html"
                 else "public, max-age=2592000")
        self._send(200, body, ctype,
                   headers=[("Cache-Control", cache)], head=head)

    def _image(self, parts, head: bool):
        """首屏封面优先走本地缓存，未命中再让后端去图床取。"""
        query = parse_qs(parts.query)
        raw_u = (query.get("u") or [""])[0]
        try:
            size = int((query.get("size") or ["0"])[0] or 0)
        except (TypeError, ValueError):
            size = 0
        first = str(raw_u).split(" ")[0]
        root = getattr(self.server, "root_dir", "")
        if first and root:
            # 1) 腾讯文档监控已下载到本地的原图（最高优先级，完全不走网络）
            local = localimages.serve(root, first, size)
            if local:
                body, ctype = local
                self._send(200, body, ctype, headers=[
                    ("Cache-Control", "public, max-age=2592000"),
                    ("X-Local-Image", "1"),
                ], head=head)
                return
            # 2) 之前预热 / 回源留下的缩略图缓存
            cached = imgcache.load(root, first, size)
            if cached:
                body, ctype = cached
                self._send(200, body, ctype, headers=[
                    ("Cache-Control", "public, max-age=2592000"),
                    ("X-Local-Cache", "1"),
                ], head=head)
                return
        self._proxy(parts, head, cache_hint=(root, first, size))

    def _proxy(self, parts, head: bool, cache_hint=None):
        api_port = int(self.server.api_port)  # type: ignore[attr-defined]
        path = parts.path + (("?" + parts.query) if parts.query else "")
        headers = {}
        for key, value in self.headers.items():
            if key.lower() in ("connection", "keep-alive", "host",
                               "proxy-authenticate", "proxy-authorization",
                               "te", "trailers", "transfer-encoding", "upgrade"):
                continue
            headers[key] = value
        headers["Host"] = f"127.0.0.1:{api_port}"
        conn = http.client.HTTPConnection("127.0.0.1", api_port, timeout=60)
        try:
            conn.request("HEAD" if head else "GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            ctype = resp.getheader("Content-Type") or "application/octet-stream"
            if (cache_hint and resp.status == 200 and body
                    and ctype.startswith("image/")):
                root, original, size = cache_hint
                if root and original:
                    try:
                        imgcache.save(root, original, size, body)
                    except OSError:
                        pass
            out = [(key, value) for key, value in resp.getheaders()
                   if key.lower() not in ("connection", "transfer-encoding",
                                          "content-length", "content-type")]
            self._send(resp.status, body, ctype, headers=out, head=head)
        except Exception as exc:  # noqa: BLE001 - 后端未起时给前端一个可读错误
            payload = json.dumps(
                {"detail": f"本地 API 未启动或不可用：{exc}"},
                ensure_ascii=False).encode("utf-8")
            self._send(502, payload, "application/json; charset=utf-8", head=head)
        finally:
            conn.close()

    def _send(self, status: int, body: bytes, ctype: str,
              headers=(), head: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for key, value in headers:
            if key.lower() not in ("content-type", "content-length"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass


class SiteService:
    """管理后端 uvicorn 与静态反代服务，只在本机回环地址监听。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.root_dir())
        self.api_port, self.site_port = config.ports()
        self._proc: subprocess.Popen | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._job = None
        self._external_api = False

    # ---------- 状态 ----------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.site_port}/"

    @property
    def running(self) -> bool:
        return bool(self._httpd is not None and self._thread and self._thread.is_alive())

    @property
    def api_running(self) -> bool:
        return _health(self.api_port)

    def status(self) -> dict:
        return {
            "running": self.running,
            "api_running": self.api_running,
            "url": self.url,
            "api_port": self.api_port,
            "site_port": self.site_port,
            "root": str(self.root),
        }

    def set_root(self, root: Path):
        if self.running:
            raise ServerError("服务运行中，请先停止服务再更换目录")
        self.root = Path(root)
        self.api_port, self.site_port = config.ports()

    # ---------- 启动 / 停止 ----------

    def start(self, log=print) -> str:
        if self.running:
            return self.url
        dist = config.dist_dir(self.root)
        if not (dist / "index.html").is_file():
            raise ServerError("前端尚未构建：请先点「构建前端」或「一键部署」")
        python = config.venv_python(self.root)
        if not python.is_file():
            raise ServerError("Python 依赖尚未安装：请先点「安装依赖」或「一键部署」")

        started_api = False
        try:
            if not self.api_running:
                self._start_backend(python, log)
                started_api = True
            else:
                self._external_api = True
                log(f"检测到 {self.api_port} 端口已有 API 服务，直接复用")
            self._start_static(log)
        except Exception:
            if started_api:
                self._stop_backend(log)
            raise
        return self.url

    def _start_backend(self, python: Path, log):
        api = config.api_dir(self.root)
        if not (api / "app" / "main.py").is_file():
            raise ServerError("webwiki-api/app/main.py 不存在，请先拉取代码")
        cmd = [str(python), "-m", "uvicorn", "app.main:app",
               "--host", "127.0.0.1", "--port", str(self.api_port)]
        log(f"启动本地 API：127.0.0.1:{self.api_port}")
        env = config.child_env()
        # 本机站点图片量很小（缩略图 10~30KB），默认 6 并发在首屏会排队；
        # 提高到 12，配合 NO_PROXY 直连图床，首屏缩略图从 20s+ 降到几秒。
        env.setdefault("WEBWIKI_IMG_CONCURRENCY", "12")
        kwargs = {
            "cwd": str(api),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd, **kwargs)
        self._proc = proc
        try:
            from gui_qt.process_tree import assign_job, create_kill_job
            self._job = create_kill_job()
            if self._job:
                assign_job(self._job, proc)
        except Exception:  # noqa: BLE001 - Job Object 只是退出兜底
            self._job = None
        threading.Thread(target=self._pump, args=(proc, log), daemon=True).start()

        deadline = time.time() + 45
        while time.time() < deadline:
            if proc.poll() is not None:
                raise ServerError(
                    f"本地 API 启动失败（退出码 {proc.returncode}），"
                    f"请检查 {self.api_port} 端口是否被占用或依赖是否完整")
            if _health(self.api_port):
                log("本地 API 已就绪")
                return
            time.sleep(0.4)
        raise ServerError("本地 API 启动超时（45 秒）")

    def _start_static(self, log):
        try:
            httpd = _QuietServer(("127.0.0.1", self.site_port), _StaticProxy)
        except OSError as exc:
            raise ServerError(
                f"站点端口 {self.site_port} 无法监听：{exc}\n"
                "请在页面底部换一个站点端口后重试") from exc
        httpd.dist_dir = str(config.dist_dir(self.root))  # type: ignore[attr-defined]
        httpd.api_port = self.api_port  # type: ignore[attr-defined]
        httpd.root_dir = str(self.root)  # type: ignore[attr-defined]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self._httpd = httpd
        self._thread = thread
        log(f"本地站点已启动：{self.url}")

    def _pump(self, proc: subprocess.Popen, log):
        try:
            for raw in proc.stdout or []:
                line = raw.rstrip()
                if line:
                    log(f"[API] {line}")
        except Exception:  # noqa: BLE001
            pass

    def stop(self, log=print):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None
            self._thread = None
            log("本地站点已停止")
        self._stop_backend(log)

    def _stop_backend(self, log):
        proc, job = self._proc, self._job
        self._proc = None
        self._job = None
        if proc is None:
            return
        if job is not None:
            try:
                from gui_qt.process_tree import terminate_job
                terminate_job(job)
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if not self._external_api:
            log("本地 API 已停止")
        self._external_api = False


def open_site(url: str) -> bool:
    """用系统默认浏览器打开本地站点。"""
    try:
        return webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001
        return False


def open_external(url: str) -> bool:
    """用系统默认浏览器打开任意链接（腾讯文档预览等）。"""
    return open_site(url)
