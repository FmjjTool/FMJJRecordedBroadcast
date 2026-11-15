"""本地资料站 · GitHub OAuth 设备码登录。

GitHub 没有「扫一下就自动登录」的公开接口，设备码登录是官方最接近的流程：
应用申请设备码 → 页面显示二维码（内容为 https://github.com/login/device）+
8 位设备码 → 手机扫码打开授权页并输入设备码 → 应用轮询拿到 Token。
Token 只写本机 config/webwiki_auth.json，用于 clone / pull 私有仓库。
"""

from __future__ import annotations

import httpx

from . import config

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
SCOPE = "repo"


class AuthError(RuntimeError):
    """GitHub 登录失败（网络 / 设备码过期 / 授权被拒 / Token 无效）。"""


def _client(timeout: float = 30) -> httpx.Client:
    proxy = config.system_proxy()
    kwargs = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": {
            "Accept": "application/json",
            "User-Agent": "FengMingToolbox-LocalWiki/1.0",
        },
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def start_device_flow() -> dict:
    """申请设备码。返回 {device_code,user_code,verification_uri,expires_in,interval}。"""
    try:
        with _client() as client:
            resp = client.post(DEVICE_CODE_URL, data={
                "client_id": config.github_client_id(),
                "scope": SCOPE,
            })
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise AuthError(f"无法连接 GitHub：{exc}") from exc
    if not data.get("device_code") or not data.get("user_code"):
        raise AuthError(data.get("error_description") or "GitHub 未返回设备码")
    return data


def poll_token(device_code: str) -> dict:
    """轮询一次。返回 {token} / {error: authorization_pending|slow_down|...}。"""
    try:
        with _client() as client:
            resp = client.post(TOKEN_URL, data={
                "client_id": config.github_client_id(),
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            })
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": "network", "message": str(exc)}
    token = str(data.get("access_token") or "")
    if token:
        return {"token": token}
    error = str(data.get("error") or "unknown")
    return {"error": error, "message": str(data.get("error_description") or error)}


def fetch_user(token_value: str) -> dict:
    """验证 Token 并返回 GitHub 用户信息。"""
    try:
        with _client() as client:
            resp = client.get(USER_URL, headers={
                "Authorization": f"Bearer {token_value}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
            if resp.status_code == 401:
                raise AuthError("Token 无效或已过期")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise AuthError(f"获取 GitHub 用户失败：{exc}") from exc
    if not isinstance(data, dict) or not data.get("login"):
        raise AuthError("GitHub 用户信息格式异常")
    return data


def login_with_token(token_value: str) -> dict:
    """用 Personal Access Token 登录并保存。"""
    token_value = (token_value or "").strip()
    if not token_value:
        raise AuthError("请填写 GitHub Token")
    user = fetch_user(token_value)
    config.save_auth(token_value, user)
    return user


def finish_device_login(token_value: str) -> dict:
    """设备码授权成功后收尾：取用户名并保存 Token。"""
    try:
        user = fetch_user(token_value)
    except AuthError:
        user = {}
    config.save_auth(token_value, user)
    return user


def logout() -> None:
    config.clear_auth()
