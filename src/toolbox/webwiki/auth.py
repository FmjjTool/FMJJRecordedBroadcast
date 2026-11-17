"""本地资料站 · GitHub 登录。

支持两种方式：
1. 识别本机已保存的 GitHub 账户（Git Credential Manager / ~/.git-credentials）；
2. 手动填「账户 + 密码 / Personal Access Token」。

登录态由 credentials 适配层统一保存；旧 webwiki_auth.json 只读兼容。
GitHub 自 2021 年起已停用账户密码做 Git / API 认证，密码框实际需要填
Personal Access Token（ghp_… / github_pat_…）；如果填的是账户登录密码，
GitHub 会返回 401，界面会给出明确提示。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import httpx

from . import config, credentials

USER_URL = "https://api.github.com/user"
TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")


class AuthError(RuntimeError):
    """GitHub 登录失败（网络 / Token 无效 / 凭据不可用）。"""


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


def _clean_token(value: str) -> str:
    token = (value or "").strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def fetch_user(token_value: str) -> dict:
    """验证 Token 并返回 GitHub 用户信息。"""
    token_value = _clean_token(token_value)
    if not token_value:
        raise AuthError("请填写密码 / Personal Access Token")
    try:
        with _client() as client:
            resp = client.get(USER_URL, headers={
                "Authorization": f"Bearer {token_value}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
            if resp.status_code == 401:
                raise AuthError(
                    "GitHub 拒绝了这组凭据。\n\n"
                    "GitHub 已停用账户密码登录，密码处必须填写 "
                    "Personal Access Token（ghp_… / github_pat_…）。\n"
                    "也可以直接使用本机 Git 凭据中的账户。")
            resp.raise_for_status()
            data = resp.json()
    except AuthError:
        raise
    except httpx.HTTPError as exc:
        raise AuthError(f"获取 GitHub 用户失败：{exc}") from exc
    if not isinstance(data, dict) or not data.get("login"):
        raise AuthError("GitHub 用户信息格式异常")
    return data


def login_with_token(token_value: str) -> dict:
    """用 Token 登录并保存到本机。"""
    token_value = _clean_token(token_value)
    user = fetch_user(token_value)
    credentials.save(token_value, user)
    return user


def login_with_account(account: str, password: str) -> dict:
    """账户 + 密码 / Token 登录。

    account 只用于界面提示和本地核对；真正认证的是 password 里的 Token。
    如果 Token 实际属于另一个账户，以 GitHub 返回的账户为准。
    """
    token_value = _clean_token(password)
    if not token_value:
        alt = _clean_token(account)
        if _looks_like_token(alt):
            token_value = alt
        else:
            raise AuthError("请填写密码 / Personal Access Token")
    if not _looks_like_token(token_value):
        # 不把疑似账户密码发送到任何接口，直接给出明确说明
        raise AuthError(
            "GitHub 已停用账户密码登录，密码处必须填写 "
            "Personal Access Token（ghp_… / github_pat_…）。\n"
            "如果本机已保存 GitHub 凭据，可直接使用识别到的本机账户。")
    user = fetch_user(token_value)
    credentials.save(token_value, user)
    return user


def _looks_like_token(value: str) -> bool:
    value = _clean_token(value)
    if not value:
        return False
    if value.startswith(TOKEN_PREFIXES):
        return True
    # 经典 Personal Access Token 是 40 位十六进制
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", value))


# ==================== 识别本机账户 ====================

def _credential_from_git() -> tuple[str, str, str]:
    """从 Git 凭据管理器读取 github.com 的 (token, username, source)。"""
    try:
        git = config.find_git()
    except RuntimeError:
        return "", "", ""
    env = config.child_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        proc = subprocess.run(
            [git, "-c", "credential.interactive=false", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            text=True, capture_output=True, env=env, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "", "", ""
    if proc.returncode:
        return "", "", ""
    username = ""
    token = ""
    for line in proc.stdout.splitlines():
        if line.startswith("username="):
            username = line.split("=", 1)[1].strip()
        elif line.startswith("password="):
            token = line.split("=", 1)[1].strip()
    if token:
        return token, username, "本机 Git 凭据（Git Credential Manager）"
    return "", "", ""


def _credential_from_file() -> tuple[str, str, str]:
    """读取 ~/.git-credentials（credential.helper=store 时使用）。"""
    path = Path.home() / ".git-credentials"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", "", ""
    for line in text.splitlines():
        line = line.strip()
        if "github.com" not in line:
            continue
        match = re.search(r"https?://([^:/@]+):([^@]+)@github\.com", line)
        if match:
            return match.group(2), match.group(1), "~/.git-credentials"
    return "", "", ""


def detect_local_account() -> dict:
    """识别本机 GitHub 账户；没有可用凭据时返回空字典。

    返回 {token, login, name, avatar, source}；token 只放在内存，不写日志。
    """
    token, username, source = _credential_from_git()
    if not token:
        token, username, source = _credential_from_file()
    if not token:
        return {}
    try:
        user = fetch_user(token)
    except AuthError:
        return {
            "token": "",
            "login": username,
            "name": "",
            "avatar": "",
            "source": source,
            "error": "本机凭据已失效，请手动填写 Token",
        }
    return {
        "token": token,
        "login": str(user.get("login") or username),
        "name": str(user.get("name") or ""),
        "avatar": str(user.get("avatar_url") or ""),
        "source": source,
    }


def logout() -> None:
    credentials.clear()
