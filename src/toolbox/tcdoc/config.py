"""腾讯文档监控 · 文档清单与路径管理。

配置项全部落在 config/config.json 的 convert 段（键必须在
toolbox.config_manager.DEFAULT_CONVERT_CONFIG_DICT 里登记过，否则读取时会被过滤）：
  tcdocs                 文档清单 [{key, name, url, kind}]，数量不设上限（自定义文档）
  tcdoc_collect_time     每日定时采集时间 "HH:MM"
  tcdoc_headless         采集时隐藏浏览器窗口
  tcdoc_legacy_migrated  是否已尝试导入旧版「腾讯文档数据保存」的历史数据

路径约定（全部跟随全局统一导出目录，见 config_manager.get_export_dir）：
  download/腾讯文档数据/tcdoc.db      SQLite 历史存档
  download/腾讯文档数据/报表/*.xlsx   每个文档一份 Excel
  download/腾讯文档数据/日志/         采集日志
  download/腾讯文档数据/调试/         页面改版时的诊断包
登录态含账号凭证，单独放 config/tcdoc_state.json（不进导出目录）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from toolbox.config_manager import (
    get_config_dir,
    get_export_subdir,
    load_convert_config,
    save_convert_config,
)

# 文档类型 → 界面显示名（腾讯文档链接里的路径段）
DOC_TYPES = {
    "sheet": "表格",
    "smartsheet": "智能表格",
    "aio": "AI 文档",
    "doc": "文档",
    "slide": "幻灯片",
    "form": "收集表",
    "pdf": "PDF",
}

# 首次使用（配置里还没有 tcdocs 键）预置的文档清单
DEFAULT_DOCS = [
    {"key": "DRnd3bkhkQUR1aEJr", "name": "封茗囧菌数据汇总库",
     "url": "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr", "kind": "sheet"},
    {"key": "DRndLZVRmVW1GZExW", "name": "百科用户反馈与收集",
     "url": "https://docs.qq.com/smartsheet/DRndLZVRmVW1GZExW", "kind": "smartsheet"},
    {"key": "DRkZHdnJranRIc1hD", "name": "用户协议与编辑须知",
     "url": "https://docs.qq.com/aio/DRkZHdnJranRIc1hD", "kind": "aio"},
    {"key": "DRkZIbkZtQ0taTHd4", "name": "「封茗囧菌」无料周边汇总",
     "url": "https://docs.qq.com/smartsheet/DRkZIbkZtQ0taTHd4", "kind": "smartsheet"},
]

# 旧版独立工具（腾讯文档数据保存）的候选位置：首次使用时把历史数据导进来
LEGACY_CANDIDATES = (
    Path("D:/PythonApps/腾讯文档数据保存"),
    Path.home() / "腾讯文档数据保存",
    Path("D:/腾讯文档数据保存"),
)

_URL_RE = re.compile(
    r"^(?:https?://)?(?:docs|doc)\.qq\.com/([A-Za-z]+)/([A-Za-z0-9_-]+)", re.IGNORECASE)


# ==================== 配置读写 ====================

def load_cfg() -> dict:
    """当前配置（已合并默认值，tcdocs 等键已在 config_manager 登记）。"""
    return load_convert_config()


def _raw_convert() -> dict:
    """直接读 config.json 的 convert 段（用于判断「用户是否已配置过文档清单」）。"""
    path = get_config_dir() / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    convert = data.get("convert")
    return convert if isinstance(convert, dict) else {}


def save_cfg(**kwargs) -> None:
    """写回指定配置键（保留其余配置）。"""
    cfg = load_convert_config()
    cfg.update(kwargs)
    save_convert_config(cfg)


def load_docs() -> list[dict]:
    """文档清单；从未配置过（tcdocs 未落盘 / 为 None）时返回预置的 4 个文档。

    用户主动删空清单会写入空数组，此时返回空列表（不再复活预置文档）。
    """
    raw = _raw_convert().get("tcdocs")
    if raw is None:
        raw = load_convert_config().get("tcdocs")
    if not isinstance(raw, list):
        return [dict(item) for item in DEFAULT_DOCS]
    docs = []
    for item in raw:
        doc = normalize_doc(item)
        if doc:
            docs.append(doc)
    return docs


def save_docs(docs: list[dict]) -> None:
    """写回文档清单（支持任意数量自定义文档）。"""
    cleaned = [normalize_doc(item) for item in docs]
    save_cfg(tcdocs=[item for item in cleaned if item])


# ==================== 文档链接解析 ====================

def parse_url(url: str) -> tuple[str, str, str] | None:
    """腾讯文档链接 → (文档 key, 类型, 标准链接)；无法识别返回 None。

    支持 /sheet/ /smartsheet/ /aio/ /doc/ /slide/ /form/ 等全部类型，
    后续腾讯文档新增类型也能直接加入（自定义文档不锁死类型白名单）。
    """
    match = _URL_RE.match((url or "").strip())
    if not match:
        return None
    kind = match.group(1).lower()
    key = match.group(2)
    return key, kind, f"https://docs.qq.com/{kind}/{key}"


def normalize_doc(item) -> dict | None:
    """把任意形态的清单项（字符串链接 / 字典）统一成 {key,name,url,kind}。"""
    if isinstance(item, str):
        item = {"url": item}
    if not isinstance(item, dict):
        return None
    parsed = parse_url(str(item.get("url") or ""))
    if not parsed:
        return None
    key, kind, url = parsed
    name = str(item.get("name") or "").strip() or key
    return {"key": str(item.get("key") or key), "name": name, "url": url, "kind": kind}


def kind_label(kind: str) -> str:
    return DOC_TYPES.get((kind or "").lower(), kind or "未知")


# ==================== 路径 ====================

def safe_name(name: str, fallback: str = "文档") -> str:
    """文件名安全化（报表文件用文档名）。"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", str(name or "")).strip()
    return cleaned[:80] or fallback


def data_dir() -> Path:
    """运行数据根目录：统一导出目录/腾讯文档数据。"""
    return get_export_subdir("tcdoc")


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "tcdoc.db"


def reports_dir() -> Path:
    return _ensure(data_dir() / "报表")


def logs_dir() -> Path:
    return _ensure(data_dir() / "日志")


def debug_dir() -> Path:
    return _ensure(data_dir() / "调试")


def log_path() -> Path:
    return logs_dir() / "采集日志.log"


def state_path() -> Path:
    """Playwright 登录态（含账号凭证，放 config 目录，不进导出目录）。"""
    return get_config_dir() / "tcdoc_state.json"


def report_file(doc: dict) -> Path:
    """每个文档一份独立报表，文件名为文档名。"""
    return reports_dir() / f"{safe_name(doc.get('name'), doc.get('key') or '文档')}.xlsx"


def find_legacy_dir() -> Path | None:
    """旧版独立工具目录（存在 monitor/config.json 才算有效）。"""
    for base in LEGACY_CANDIDATES:
        try:
            if (base / "config.json").is_file() or (base / "monitor" / "config.py").is_file():
                return base
        except OSError:
            continue
    return None


def legacy_db(legacy: Path) -> Path:
    return legacy / "数据" / "数据库" / "monitor.db"


def legacy_state(legacy: Path) -> Path:
    return legacy / "数据" / "登录态" / "browser_state.json"
