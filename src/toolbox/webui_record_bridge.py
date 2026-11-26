"""WebUI 侧录制控制桥客户端（L3）：读取桌面桥信息并调用本机通道。

桥信息由桌面程序写在 `logs/webui_desktop_bridge.json`（随机端口 + 令牌，
仅 127.0.0.1）。使用 `http.client` 直连，避免 urllib 代理/环境差异；
浏览器不直接接触桥令牌。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

from toolbox.config_manager import get_app_root

BRIDGE_FILE = "webui_desktop_bridge.json"
ACTIONS = ("start_record", "stop_record", "start_monitor", "stop_monitor",
           "add_anchor", "import_anchors")


def bridge_info() -> dict | None:
    path = get_app_root() / "logs" / BRIDGE_FILE
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        port = int(data.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    token = str(data.get("token") or "")
    if not port or not token:
        return None
    return {"port": port, "token": token}


def _request(port: int, method: str, path: str, token: str,
             body: dict | None = None, timeout: float = 6.0) -> dict:
    headers = {"X-Bridge-Token": token}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", port,
                                            timeout=timeout)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            try:
                message = json.loads(raw.decode("utf-8")).get("error")
            except Exception:
                message = ""
            raise ValueError(message or f"桌面控制通道返回 {response.status}")
        return json.loads(raw.decode("utf-8"))
    finally:
        connection.close()


def status() -> dict:
    info = bridge_info()
    if info is None:
        return {"connected": False, "platforms": [],
                "error": "桌面程序未运行或录制控制桥未启动"}
    try:
        data = _request(info["port"], "GET", "/status", info["token"])
        data["connected"] = True
        return data
    except Exception as exc:  # noqa: BLE001 - 回传可读错误
        return {"connected": False, "platforms": [],
                "error": f"桌面控制通道不可用：{str(exc)[:200]}"}


def action(platform: str, room: str, name: str,
           extra: dict | None = None) -> dict:
    if name not in ACTIONS:
        raise ValueError("不支持的操作")
    info = bridge_info()
    if info is None:
        raise ValueError("桌面程序未运行，无法控制录制")
    try:
        return _request(info["port"], "POST", "/action", info["token"],
                        body={"platform": platform, "room": room,
                              "action": name, "extra": extra or {}},
                        timeout=25)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"桌面控制通道不可用：{str(exc)[:200]}") from exc