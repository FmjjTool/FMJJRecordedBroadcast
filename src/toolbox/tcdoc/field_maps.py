"""腾讯文档写回字段映射（config 驱动，按目标表浮动）。

配置位置 config/config.json → convert.tcdoc_field_maps：

    "tcdoc_field_maps": {
      "*": {"作品时长": ""},
      "DRnd3bkhkQUR1aEJr|ss_h2uh2s": {
        "作品标题": "标题",
        "发布账号": "作者"
      }
    }

规则：
- 键写程序内部字段名（下面 INTERNAL_FIELDS 里列的名字），值写目标表里的列名；
- 值为空串 = 这个字段不写；没配置的字段完全按代码默认走；
- 优先级：doc_key|tab_id（该表） > "*"（所有表）；
- 新增表不用改代码：给它加一条 doc_key|tab_id 的映射即可，键名照旧用内部字段名。

INTERNAL_FIELDS 只是给界面/文档做提示用的清单，实际映射不校验键名
（拼错的键不会命中任何字段，不影响其它列写入）。
"""

from __future__ import annotations

from . import config as tc_cfg
from toolbox.config_manager import get_config_dir

CONFIG_KEY = "tcdoc_field_maps"
WILDCARD = "*"

# config.json 很小，但映射会在逐行匹配里被反复查询：按文件戳缓存，改配置后自动失效
_CACHE: dict = {"stamp": None, "maps": {}}

# 各同步类型可映射的内部字段（与 import_pending / bridge 里的固定列名一致）
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
    ),
    "merch": ("周边名称", "首发日期", "获取方式", "来源渠道", "备注信息", "备注"),
    "lives": ("直播账户", "直播标题", "开始时间", "结束时间", "直播平台",
              "直播链接1", "备注信息", "备注"),
}


def table_key(doc_key, tab_id) -> str:
    """配置里代表某张表的键：文档 key + 工作表 id。"""
    return f"{str(doc_key or '').strip()}|{str(tab_id or '').strip()}"


def _normalize(raw) -> dict[str, dict[str, str]]:
    """配置值 → {scope: {内部字段: 目标列名}}；非法结构直接忽略。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for scope, pairs in raw.items():
        if not isinstance(pairs, dict):
            continue
        cleaned: dict[str, str] = {}
        for name, target in pairs.items():
            name = str(name or "").strip()
            if not name:
                continue
            cleaned[name] = "" if target is None else str(target).strip()
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
    cleaned = {str(name).strip(): ("" if target is None else str(target).strip())
               for name, target in (pairs or {}).items() if str(name).strip()}
    if cleaned:
        maps[scope] = cleaned
    else:
        maps.pop(scope, None)
    save_maps(maps)


def target(doc_key, tab_id, name: str) -> str:
    """内部字段名 → 目标列名；返回空串表示「按配置不写」。"""
    name = str(name or "")
    if not name:
        return ""
    pairs = mapping(doc_key, tab_id)
    return pairs[name] if name in pairs else name


def display_columns(doc_key, tab_id, columns) -> list[str]:
    """内部列名 → 表格显示/粘贴用的目标列名（去掉配置为「不写」的列）。"""
    pairs = mapping(doc_key, tab_id)
    out = []
    for name in columns or ():
        shown = pairs.get(str(name), str(name))
        if shown:
            out.append(shown)
    return out


def source_map(doc_key, tab_id, columns) -> dict[str, str]:
    """显示列名 → 内部字段名（界面按目标列名展示，取值仍用内部字段）。"""
    pairs = mapping(doc_key, tab_id)
    out: dict[str, str] = {}
    for name in columns or ():
        name = str(name)
        shown = pairs.get(name, name)
        if shown:
            out.setdefault(shown, name)
    return out


def internal_of(doc_key, tab_id, shown: str) -> str:
    """目标列名 → 内部字段名；没有映射时原样返回。"""
    shown = str(shown or "")
    for name, target in mapping(doc_key, tab_id).items():
        if target and target == shown:
            return name
    return shown


def rename_to_internal(values, doc_key, tab_id) -> dict:
    """远端行（按目标列名取值）→ 内部字段名为键的副本，方便复用原有匹配逻辑。"""
    inverse = {target: name for name, target in mapping(doc_key, tab_id).items()
               if target}
    if not inverse:
        return dict(values or {})
    return {inverse.get(str(key), str(key)): value
            for key, value in (values or {}).items()}


def signature(doc_key="", tab_id="") -> tuple:
    """当前生效映射的指纹；缓存索引靠它判断是否需要重算。"""
    return tuple(sorted(mapping(doc_key, tab_id).items()))