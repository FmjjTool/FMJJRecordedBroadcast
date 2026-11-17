"""移动云盘（139）备份后端。

- `sign.py`：请求签名（从前端 `getNewSign` 还原，已验证签名层通过）
- 待补：`api.py`（getDisk / createCatalog / pcUploadFileRequest / complete）与 provider 注册

凭据：网页登录后的 Cookie（`token` / `skey` / `auth_token`），由「Cookie 管理 → 移动云盘」写入。
"""

__all__ = ["sign"]
