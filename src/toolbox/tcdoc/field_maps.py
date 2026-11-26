"""腾讯文档写回字段映射（config 驱动，按目标表浮动）。

配的是「腾讯表这一列，用本地哪个字段的值」，腾讯列固定不动：

    "tcdoc_field_maps": {
      "DRnd3bkhkQUR1aEJr|ss_h2uh2s": {
        "标题": "作品标题",
        "作者": "发布账号",
        "备注": "@description",
        "作品时长": ""
      }
    }

- 键 = 腾讯表里的列名（不存在的列不会写）；
- 值 = 本地来源字段：
  * 程序字段名（见 INTERNAL_FIELDS，如 作品标题 / 发布账号）；
  * "@键名" = 本地原始 JSON 字段（如 @title、@cover_url，支持 a.b 取嵌套）；
  * 空串 = 这一列不写。
- 没配置的列按默认：列名与程序字段同名时取该字段，否则不写；
- 优先级：`doc_key|tab_id`（该表） > `*`（所有表）。

同一个本地字段只写一处（配置里多个列指向同一字段时取第一个），避免重复写。
"""

from __future__ import annotations

from . import config as tc_cfg
from toolbox.config_manager import get_config_dir

CONFIG_KEY = "tcdoc_field_maps"
WILDCARD = "*"

# config.json 很小，但映射会在逐行匹配里被反复查询：按文件戳缓存，改配置后自动失效
_CACHE: dict = {"stamp": None, "maps": {}}

# 各同步类型可用于映射的程序字段（与 import_pending / bridge 的固定列名一致）
INTERNAL_FIELDS = {
    "posts": (
        "作品标题", "发布距今", "作品内容", "发布账号", "封面图", "动态类型",
        "发布时间", "发布平台", "发布位置", "作品时长", "作品链接",
        "动态链接(b站)", "备注信息", "备注", "评论区图片",
    ),
    "songs": (
        "封面图", "歌曲名称", "演唱者", "歌曲简介", "完整歌词", "歌曲时长",
        "作词人", "作曲人", "出品方", "所属专辑", "语言", "合唱形式", "首发日期",
        "QQ音乐", "网易云音乐", "酷狗音乐", "酷我音乐", "5sing", "汽水音乐",
        "B站链接", "备注信息", "备注",
    ),
    "events": (
        "活动名称", "活动日期", "年份", "城市", "场馆名称", "场馆地址",
        "场馆地址（文本）", "活动状态", "活动类型", "舞台环节", "签售环节",
        "票档价格", "购票链接 1", "宣传图", "备注信息", "备注",
        # 歌单页按活动曲目建完歌单后回填的链接列（2026-11-26 用户建列）
        "活动歌单_网易云", "活动歌单_酷狗", "活动歌单_QQ",
    ),
    "merch": ("周边名称", "首发日期", "获取方式", "来源渠道", "备注信息", "备注"),
    "lives": ("直播账户", "直播标题", "开始时间", "结束时间", "直播平台",
              "直播链接1", "备注信息", "备注"),
}


def table_key(doc_key, tab_id) -> str:
    """配置里代表某张表的键：文档 key + 工作表 id。"""
    return f"{str(doc_key or '').strip()}|{str(tab_id or '').strip()}"


def _normalize(raw) -> dict[str, dict[str, str]]:
    """配置值 → {scope: {腾讯列名: 来源字段}}；非法结构直接忽略。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for scope, pairs in raw.items():
        if not isinstance(pairs, dict):
            continue
        cleaned: dict[str, str] = {}
        for column, source in pairs.items():
            column = str(column or "").strip()
            if not column:
                continue
            cleaned[column] = "" if source is None else str(source).strip()
        if cleaned:
            out[str(scope).strip()] = cleaned
    return out


def _config_stamp():
    try:
        path = get_config_dir() / "config.json"
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def load_maps() -> dict[str, dict[str, str]]:
    """当前配置里的全部映射（按 scope 分组，按文件戳缓存）。"""
    stamp = _config_stamp()
    if _CACHE["stamp"] == stamp:
        return _CACHE["maps"]
    maps = _normalize(tc_cfg.load_cfg().get(CONFIG_KEY))
    _CACHE["stamp"], _CACHE["maps"] = stamp, maps
    return maps


def save_maps(maps) -> None:
    """整体写回映射配置（保留其它配置）；改完立即失效缓存。"""
    tc_cfg.save_cfg(**{CONFIG_KEY: _normalize(maps)})
    _CACHE["stamp"] = None


def mapping(doc_key, tab_id) -> dict[str, str]:
    """该目标表生效的映射：先 "*"，再用该表的键覆盖。"""
    maps = load_maps()
    merged: dict[str, str] = dict(maps.get(WILDCARD) or {})
    merged.update(maps.get(table_key(doc_key, tab_id)) or {})
    return merged


def save_scope(doc_key, tab_id, pairs) -> None:
    """替换某张表的映射（其它表与 "*" 保持不变）；清空即删除该表条目。"""
    maps = dict(load_maps())
    scope = table_key(doc_key, tab_id)
    cleaned = {str(column).strip(): ("" if source is None else str(source).strip())
               for column, source in (pairs or {}).items() if str(column).strip()}
    if cleaned:
        maps[scope] = cleaned
    else:
        maps.pop(scope, None)
    save_maps(maps)


def source_of(doc_key, tab_id, column) -> str:
    """腾讯列 → 本地来源字段；未配置时返回同名程序字段（列名本身）。"""
    column = str(column or "")
    if not column:
        return ""
    pairs = mapping(doc_key, tab_id)
    return pairs[column] if column in pairs else column


def target(doc_key, tab_id, internal) -> str:
    """程序字段 → 实际写入的腾讯列；空串表示不写。

    优先找显式指向该字段的列；该字段的同名列被改成别的来源时返回空串。
    """
    internal = str(internal or "")
    if not internal:
        return ""
    pairs = mapping(doc_key, tab_id)
    for column, source in pairs.items():
        if source == internal:
            return column
    if internal in pairs:
        return ""
    return internal


def raw_targets(doc_key, tab_id) -> list[tuple[str, str]]:
    """配置里来自本地原始 JSON 的列：[(腾讯列, "@键名"), …]。"""
    return [(column, source) for column, source in mapping(doc_key, tab_id).items()
            if source.startswith("@")]


def raw_value(raw, source: str):
    """从原始记录取 "@a.b" 的值；取不到返回空串。"""
    key = str(source or "").lstrip("@")
    value = raw or {}
    for part in key.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part)
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def display_columns(doc_key, tab_id, columns) -> list[str]:
    """程序字段列 → 表格显示/粘贴用的腾讯列名（去掉「不写」的列）。"""
    out = []
    for name in columns or ():
        shown = target(doc_key, tab_id, name)
        if shown:
            out.append(shown)
    return out


def source_map(doc_key, tab_id, columns) -> dict[str, str]:
    """腾讯列名 → 程序字段名（界面按腾讯列展示，取值仍用程序字段）。"""
    out: dict[str, str] = {}
    for name in columns or ():
        name = str(name)
        shown = target(doc_key, tab_id, name)
        if shown:
            out.setdefault(shown, name)
    return out


def internal_of(doc_key, tab_id, column) -> str:
    """腾讯列 → 该列实际取值的程序字段名；来源是 @JSON 或「不写」时返回空串。"""
    source = source_of(doc_key, tab_id, column)
    if not source or source.startswith("@"):
        return ""
    return source


def rename_to_internal(values, doc_key, tab_id) -> dict:
    """远端行（按腾讯列名取值）→ 程序字段名为键的副本，便于复用原有匹配逻辑。"""
    pairs = mapping(doc_key, tab_id)
    if not pairs:
        return dict(values or {})
    out = {}
    for key, value in (values or {}).items():
        key = str(key)
        source = pairs.get(key) if key in pairs else key
        if not source or source.startswith("@"):
            continue
        out.setdefault(source, value)
    return out


def signature(doc_key="", tab_id="") -> tuple:
    """当前生效映射的指纹；缓存索引靠它判断是否需要重算。"""
    return tuple(sorted(mapping(doc_key, tab_id).items()))