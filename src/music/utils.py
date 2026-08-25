"""
工具函数 - HTTP 请求、加密、通用辅助
"""

from __future__ import annotations

import json
import math
import re
import random
from typing import Any, Optional

import httpx

# 常见浏览器 UA 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def random_ua() -> str:
    return random.choice(USER_AGENTS)


def make_headers(referer: str = "", extra: Optional[dict] = None) -> dict:
    """构建请求头"""
    h = {**DEFAULT_HEADERS, "User-Agent": random_ua()}
    if referer:
        h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def get_client(timeout: float = 15.0) -> httpx.Client:
    """获取 httpx Client"""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        http2=False,
        headers={"User-Agent": random_ua()},
    )


# ---------------------------------------------------------------------------
# 酷我 Web Secret 头
# www.kuwo.cn/api/v1/www/music/playUrl 需要 Secret 请求头（由首页 Hm_Iuvt cookie
# 加密生成，算法来自 kuwo 前端 497fe0a.js 的 f(token, key) 函数）。
# 签名后免费歌可拿 320k MP3 直链（M800 前缀），远优于 antiserver 的 128k WMA。

KUWO_COOKIE_KEY = "Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324"
_KUWO_MODU = 2 ** 31 - 1


def _js_parse_int(s: str) -> float:
    """JS parseInt(s, 10)：取前导数字转 double。"""
    j = 0
    n = len(s)
    while j < n and s[j] in " \t\n\r\f\v":
        j += 1
    sign = 1.0
    if j < n and s[j] in "+-":
        if s[j] == "-":
            sign = -1.0
        j += 1
    k = j
    while k < n and s[k].isdigit():
        k += 1
    if k == j:
        return float("nan")
    return sign * float(s[j:k])


def _js_num_str(x: float) -> str:
    """ECMA-262 Number::toString(x, 10)，x 为有限非负数。"""
    if x == 0:
        return "0"
    r = repr(x)
    if "e" in r:
        mant, exp_s = r.split("e")
        exp = int(exp_s)
    else:
        mant, exp = r, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    if fp and set(fp) == {"0"}:
        fp = ""
    digits = ip + fp
    lead = len(digits) - len(digits.lstrip("0"))
    digits = digits.lstrip("0")
    n = exp + len(ip) - lead
    k = len(digits)
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    es = ("+" if e >= 0 else "-") + str(abs(e))
    if k == 1:
        return f"{digits}e{es}"
    return f"{digits[0]}.{digits[1:]}e{es}"


def kuwo_secret(token: str, key: str = KUWO_COOKIE_KEY, salt: int = 0) -> str:
    """生成酷我 Web playUrl 的 Secret 请求头。

    salt 会被 JS 数字折叠的浮点精度丢失，不影响密文，任意值即可。
    """
    if not key:
        return ""
    n = "".join(str(ord(c)) for c in key)
    s_pos = math.floor(len(n) / 5)

    def char_at(i: int) -> str:
        return n[i] if 0 <= i < len(n) else ""

    mult = _js_parse_int("".join(char_at(s_pos * i) for i in range(1, 6)))
    if mult != mult or mult < 2:
        return ""
    incr = math.ceil(len(key) / 2)
    n += str(salt)
    while len(n) > 10:
        total = _js_parse_int(n[:10]) + _js_parse_int(n[10:])
        n = _js_num_str(total)
    n = float(n)
    n = (mult * n + incr) % _KUWO_MODU
    out = []
    for ch in token:
        v = ord(ch) ^ math.floor(n / _KUWO_MODU * 255)
        out.append(format(v, "02x"))
        n = (mult * n + incr) % _KUWO_MODU
    return "".join(out) + format(salt, "08x")


def kuwo_web_token(client: httpx.Client) -> str:
    """访问酷我首页，取 Hm_Iuvt cookie 值（Secret 的加密输入）。"""
    try:
        r = client.get("https://www.kuwo.cn/", headers=make_headers(referer="https://www.kuwo.cn/"))
    except Exception:
        return ""
    for h in r.headers.get_list("set-cookie"):
        kv = h.split(";")[0].strip()
        if kv.startswith("Hm_Iuvt") and "=" in kv:
            return kv.split("=", 1)[1]
    return ""


def safe_json(text: str) -> Any:
    """安全解析 JSON，处理各种格式"""
    text = text.strip()
    # 移除 BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    # 处理 JSONP callback
    cb_match = re.match(r'^[\w.]+\((.*)\)[\s;]*$', text, re.DOTALL)
    if cb_match:
        text = cb_match.group(1)
    # 处理单引号 JSON
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试替换单引号
    try:
        return json.loads(text.replace("'", '"'))
    except json.JSONDecodeError:
        pass
    return None








def parse_duration(raw) -> int:
    """将各种时长格式转为秒数"""
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw if raw < 10000 else raw // 1000  # 可能是毫秒
    if isinstance(raw, str):
        raw = raw.strip()
        # mm:ss
        m = re.match(r'^(\d+):(\d{2})$', raw)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        # 纯数字（可能是秒或毫秒）
        try:
            v = int(raw)
            return v if v < 10000 else v // 1000
        except ValueError:
            pass
    return 0
