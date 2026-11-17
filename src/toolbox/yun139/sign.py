# -*- coding: utf-8 -*-
"""移动云盘（139）请求签名：从网页前端 `getNewSign` 还原。

前端原始实现（`app.*.js`）：

```js
getNewSign: (params, data, ts, rand) => {
    let s = "";
    if (data) {
        s = encodeURIComponent(JSON.stringify(data));
        s = s.split("").sort().join("");        // 字符级排序
    }
    const r = md5(base64(s));
    const l = md5(ts + ":" + rand);
    return md5(r + l).toUpperCase();
}
// 头部：mcloud-sign = `${ts},${rand},${sign}`
```

实测口径（用真实登录态验证过「签名是否被服务端接受」）：
- `ts` 必须是 `YYYY-MM-DD HH:MM:SS`；
- `rand` 是 16 位大小写字母 + 数字；
- body 必须是**实际发送的那串字节**（`json.dumps(..., separators=(",", ":"))`
  且不转义中文），先 `quote(safe="")`（等价 JS 的 encodeURIComponent），再按字符排序。

该算法验证结果：错误码由 `1010010014 签名校验失败` 变为 `1010010002 鉴权失败`，
说明签名层已通过，剩下的是会话票据（token/skey）问题。
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import string
import time
from urllib.parse import quote

_ALPHABET = string.ascii_letters + string.digits


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def random_string(length: int = 16) -> str:
    return "".join(random.choice(_ALPHABET) for _ in range(length))


def json_body(data) -> str:
    """与前端 JSON.stringify 对齐的紧凑 JSON（不转义中文、无空格）。"""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def new_sign(data_str: str, ts: str, rand: str) -> str:
    """等价前端的 getNewSign(params, data, ts, rand)。"""
    encoded = quote(data_str or "", safe="")
    sorted_encoded = "".join(sorted(encoded))
    step1 = _md5(base64.b64encode(sorted_encoded.encode("utf-8")).decode())
    step2 = _md5(f"{ts}:{rand}")
    return _md5(step1 + step2).upper()


def make_sign(data_str: str, ts: str | None = None,
              rand: str | None = None) -> str:
    """生成 `mcloud-sign` 头的完整值：`ts,rand,sign`。"""
    stamp = ts or time.strftime("%Y-%m-%d %H:%M:%S")
    nonce = rand or random_string(16)
    return f"{stamp},{nonce},{new_sign(data_str, stamp, nonce)}"


def sign_headers(data_str: str) -> dict:
    """给请求用的最小签名头（其余固定头由客户端统一补）。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    nonce = random_string(16)
    return {
        "mcloud-sign": f"{stamp},{nonce},{new_sign(data_str, stamp, nonce)}",
    }
