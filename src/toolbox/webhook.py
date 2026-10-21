# -*- coding: utf-8 -*-
"""WebHook 推送（对应江湖录播的 UserC_PushMsgSet / dy/pusher.py）。

支持：企业微信机器人 / 钉钉机器人 / 飞书机器人 / 自定义 Webhook。
事件：开播 / 关播 / 开始录制 / 停止录制 / 在线人数。
纯标准库 urllib，不引第三方包。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

PLATFORMS = ["企业微信机器人", "钉钉机器人", "飞书机器人", "自定义 Webhook"]

DEFAULT_EVENTS = {"开播": True, "关播": True, "开始录制": True,
                  "停止录制": True, "在线人数": False}

DEFAULT_PUSH = {"enabled": False, "platform": "企业微信机器人", "url": "",
                "prefix": "", "events": dict(DEFAULT_EVENTS)}


def build_body(platform, text):
    """按平台组装 JSON 消息体。"""
    if platform == "企业微信机器人":
        return {"msgtype": "text", "text": {"content": text}}
    if platform == "钉钉机器人":
        return {"msgtype": "text", "text": {"content": text}}
    if platform == "飞书机器人":
        return {"msg_type": "text", "content": {"text": text}}
    return {"text": text, "content": text, "message": text,
            "timestamp": int(time.time())}


def validate_url(url):
    if not url:
        return "未填写"
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return "地址格式错误"
    if u.scheme not in ("http", "https"):
        return "必须以 http:// 或 https:// 开头"
    if not u.netloc:
        return "缺少域名"
    return ""


def send(url, platform, text, timeout=10):
    """同步发送，返回 (成功, 返回内容/错误)。"""
    err = validate_url(url)
    if err:
        return False, err
    payload = build_body(platform, text)
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


class Pusher:
    """按配置异步推送（不阻塞录制/监控线程）。"""

    def __init__(self, conf=None):
        self.conf = {**DEFAULT_PUSH, **(conf or {})}
        self.conf["events"] = {**DEFAULT_EVENTS, **(self.conf.get("events") or {})}

    def enabled(self):
        return bool(self.conf.get("enabled") and self.conf.get("url"))

    def want(self, event):
        return bool((self.conf.get("events") or {}).get(event, False))

    def push(self, event, text, force=False):
        if not self.enabled():
            return False
        if not force and not self.want(event):
            return False
        threading.Thread(target=self._send, args=(event, text), daemon=True).start()
        return True

    def _send(self, event, text):
        prefix = self.conf.get("prefix") or ""
        body = "%s【%s】%s" % (prefix, event, text) if prefix else "【%s】%s" % (event, text)
        send(self.conf.get("url"), self.conf.get("platform"), body)

    def test(self, text="【测试】直播录制 · WebHook 连通性测试"):
        return send(self.conf.get("url"), self.conf.get("platform"), text)
