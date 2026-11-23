"""WebUI 无窗口启动入口：python main.py --webui [--webui-port 8765]。

只导入 Qt-free 的看板服务；启动后把带令牌的访问地址写入
config/webui_url.txt，便于打包后的无控制台环境取用。
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path


def _write_url(url: str) -> Path | None:
    try:
        from toolbox.config_manager import get_config_dir
        path = get_config_dir() / "webui_url.txt"
        path.write_text(url + "\n", encoding="utf-8")
        return path
    except Exception:
        return None


def _safe_print(text: str) -> None:
    try:
        if sys.stdout is not None:
            print(text, flush=True)
    except Exception:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="main.py --webui")
    parser.add_argument("--webui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--webui-port", type=int, default=8765)
    parser.add_argument("--webui-host", default="127.0.0.1")
    parser.add_argument("--webui-token", default="")
    args, _unknown = parser.parse_known_args(argv[1:] if argv else sys.argv[1:])

    token = (args.webui_token or os.environ.get("FMJJ_WEBUI_TOKEN")
             or secrets.token_urlsafe(24))
    from toolbox.webui.app import build_app

    app = build_app(token, host=args.webui_host, port=args.webui_port)
    try:
        app.start()
    except OSError as exc:
        _safe_print(f"WebUI 启动失败：端口 {args.webui_port} 无法监听（{exc}）")
        return 1
    url = app.url
    path = _write_url(url)
    _safe_print(f"WebUI 已启动：{url}")
    if path is not None:
        _safe_print(f"访问地址已写入：{path}")
    _safe_print("按 Ctrl+C 停止服务。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
