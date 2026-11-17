"""夸克网盘备份：Cookie 直连 Web API 的增量备份。

分层：
- `api.py`      drive-pc.quark.cn 接口封装（列表 / 建目录 / 秒传 / 分块上传）
- `config.py`   多组「本地目录 → 云端文件夹」映射与计划设置（存 convert 配置）
- `state.py`    增量判定用的本地 SQLite（已上传文件的路径 / 大小 / mtime / md5）
- `backup.py`   扫描 → 比对 → 上传 → 记录，GUI 与定时任务共用同一条链路

凭据：只通过 `auth.get_cookie("quark")` 读取（Cookie 管理统一入口），
本包不写、不缓存、不落盘任何 Cookie。
"""

__all__ = ["api", "backup", "config", "state"]
