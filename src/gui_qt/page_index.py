"""页面注册表：页面名、分组、页面类、搜索别名、首页卡片、容器页签的唯一注册处。

新增一个页面只改这里：

1. 在 `PAGE_INDEX` 增加一条 `PageMeta(...)`（标题、分组、module/cls）
2. 需要侧栏入口就把 key 加进 `NAV_LAYOUT`；需要首页卡片就加进 `HOME_COMMON / HOME_INFO`
3. 旧入口改名就在 `ALIASES` 留一条映射

侧栏、面包屑、搜索面板、首页卡片、启动预载全部从这里读取，
`main_window.PAGE_CLASSES` 也由它派生；`tests/test_page_index.py` 会校验一一对应。

注意：模块路径写相对 `src` 的导入路径（如 `gui_qt.pages.asset`），
main_window 在切页时才 importlib 懒加载，不在启动时全量导入。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PageMeta:
    title: str                     # 侧栏 / 面包屑 / 搜索显示名
    group: str                     # 面包屑分组（侧栏分组）
    module: str = ""               # 页面类所在模块
    cls: str = ""                  # 页面类名
    aliases: str = ""              # 搜索命中词（旧叫法、俗称，空格分隔，不显示）
    card_title: str = ""           # 首页卡片短名，留空则用 title
    card_note: str = ""            # 首页卡片说明
    footer_label: str = ""         # 侧栏底部按钮短名（任务 / 设置 / 性能）
    preload: bool = False          # 低频页面首次访问时创建；核心保留既有预载
    tab_of: str = ""               # 属于哪个容器页（容器子页填容器 key）


PAGE_INDEX = {
    # ---- 首页 ----
    "home": PageMeta(
        "工作台", "总览", module="gui_qt.pages.home", cls="HomePage"),

    # ---- 内容采集 ----
    "info_accounts": PageMeta(
        "采集任务", "内容采集",
        module="gui_qt.pages.info_accounts", cls="InfoAccountsPage",
        aliases="信息获取 账号 解析", card_note="批量链接解析与账号采集"),
    "info_results": PageMeta(
        "采集结果", "内容采集",
        module="gui_qt.pages.info_results", cls="InfoResultsPage",
        aliases="已获取 结果", card_note="结果库、下载与导出"),
    "info_tools": PageMeta(
        "采集工具", "内容采集",
        module="gui_qt.pages.containers", cls="InfoToolsPage",
        aliases="信息工具"),
    "video_comments": PageMeta(
        "视频评论", "信息工具",
        module="gui_qt.pages.video_comments", cls="VideoCommentsPage",
        aliases="视频评论区",
        preload=False, tab_of="info_tools", card_note="三平台评论抓取与长图"),
    "douyin_index": PageMeta(
        "抖音指数", "账户追踪",
        module="gui_qt.pages.douyin_index", cls="DouyinIndexPage",
        card_note="达人指标、作品与粉丝画像"),
    "bili_resource": PageMeta(
        "B站资源", "信息工具",
        module="gui_qt.pages.bili_resource", cls="BiliResourcePage",
        preload=False, tab_of="info_tools", card_note="表情包、收藏集、空间数据"),
    "wechat_article": PageMeta(
        "公众号文章", "信息工具",
        module="gui_qt.pages.wechat_article", cls="WechatArticlePage",
        aliases="公众号解析",
        preload=False, tab_of="info_tools", card_note="文章导出 HTML / PDF / Word"),
    "douyin_emoji": PageMeta(
        "抖音表情", "信息工具",
        module="gui_qt.pages.douyin_emoji_page", cls="DouyinEmojiPage",
        aliases="抖音表情包 表情收集 会员表情",
        preload=False, tab_of="info_tools", card_note="会员表情、直播收集与导出"),

    # ---- 资产管理 ----
    "asset": PageMeta(
        "资产库", "资产管理", module="gui_qt.pages.asset", cls="AssetPage",
        aliases="资产管理", card_note="资源归档与本地文件"),
    "netdisk": PageMeta(
        "网络库", "资产管理", module="gui_qt.pages.netdisk", cls="NetdiskPage",
        aliases="网盘"),

    # ---- 录播工具箱 ----
    "toolbox_organize": PageMeta(
        "自动整理", "录播工具箱", module="gui_qt.pages.organize", cls="OrganizePage", preload=True),
    "toolbox_editor": PageMeta(
        "视频操作", "录播工具箱", module="gui_qt.pages.editor", cls="EditorPage",
        card_note="转换 / 合并 / 切割 / 压缩", preload=True),
    "toolbox_convert": PageMeta(
        "视频操作", "录播工具箱", module="gui_qt.pages.editor", cls="EditorPage",
        preload=False),
    "toolbox_rename": PageMeta(
        "命名投稿", "录播工具箱", module="gui_qt.pages.rename", cls="RenamePage",
        aliases="交互重命名 重命名 投稿", preload=True),
    "toolbox_audio": PageMeta(
        "音频提取", "录播工具箱", module="gui_qt.pages.audio", cls="AudioPage", preload=True),
    "toolbox_subtitle": PageMeta(
        "字幕转换", "录播工具箱", module="gui_qt.pages.subtitle", cls="SubtitlePage",
        card_note="字幕与歌词格式转换", preload=True),
    "toolbox_launcher": PageMeta(
        "继续操作", "录播工具箱", module="gui_qt.pages.launcher", cls="LauncherPage", preload=True),
    "toolbox_calendar": PageMeta(
        "日历浏览", "录播工具箱", module="gui_qt.pages.calendar", cls="CalendarPage", preload=True),
    "toolbox_migrate": PageMeta(
        "文件迁移", "录播工具箱", module="gui_qt.pages.migrate", cls="MigratePage", preload=True),

    # ---- 账户追踪 ----
    "music_tracker": PageMeta(
        "音乐账号", "账户追踪", module="gui_qt.pages.music_tracker", cls="MusicTrackerPage",
        aliases="音乐账号追踪"),
    "tracker": PageMeta(
        "网媒账号", "账户追踪", module="gui_qt.pages.tracker", cls="TrackerPage",
        aliases="网媒账号追踪 账号追踪"),
    "tcdoc": PageMeta(
        "腾讯文档", "账户追踪", module="gui_qt.pages.tcdoc", cls="TcDocPage",
        aliases="腾讯文档监控 监控", card_note="浏览量 / 成员数每日记录"),
    "weibo_chaohua": PageMeta(
        "微博超话", "账户追踪", module="gui_qt.pages.weibo_chaohua", cls="WeiboChaohuaPage"),
    "fan_groups": PageMeta(
        "粉丝群", "账户追踪", module="gui_qt.pages.fan_groups_page", cls="FanGroupsPage",
        aliases="粉丝群查询 QQ群 QQ群订阅"),
    "account_registry": PageMeta(
        "账号名单", "账户追踪",
        module="gui_qt.pages.account_registry", cls="AccountRegistryPage"),

    # ---- 票务购物 ----
    "tickets": PageMeta(
        "票务解析", "票务购物", module="gui_qt.pages.tickets_page", cls="TicketsPage"),
    "shopping": PageMeta(
        "购物解析", "票务购物", module="gui_qt.pages.shopping_hub", cls="ShoppingHubPage"),

    # ---- 音乐工具 ----
    "music_search_download": PageMeta(
        "歌曲下载", "音乐工具",
        module="gui_qt.pages.containers", cls="MusicSearchDownloadPage",
        aliases="音乐搜索下载 搜索 下载", card_title="歌曲下载", card_note="五平台信息与歌曲下载"),
    "music_playlist": PageMeta(
        "歌单制作", "音乐工具",
        module="gui_qt.pages.music_playlist", cls="MusicPlaylistPage",
        aliases="歌曲合集 创建歌单 合集 playlist", card_note="跨平台创建歌曲合集"),
    "music_file_tools": PageMeta(
        "音乐标签", "音乐工具", module="gui_qt.pages.music_tag", cls="MusicTagPage",
        aliases="标签 音乐标签"),

    # ---- 直播录制 ----
    "live_record": PageMeta(
        "直播录制", "直播录制", module="gui_qt.pages.live_record", cls="LiveRecordPage"),
    "bili_danmaku": PageMeta(
        "B站弹幕姬", "直播录制", module="gui_qt.pages.bili_danmaku", cls="BiliDanmakuPage"),
    "douyin_danmaku": PageMeta(
        "抖音弹幕姬", "直播录制",
        module="gui_qt.pages.douyin_danmaku", cls="DouyinDanmakuPage"),

    # ---- 本地工具 ----
    "localwiki": PageMeta(
        "资料站", "本地工具", module="gui_qt.pages.localwiki", cls="LocalWikiPage",
        aliases="本地资料站 资料 wiki", card_note="本地 Wiki + 腾讯文档预览"),
    "livestream_summary": PageMeta(
        "直播总结", "本地工具",
        module="gui_qt.pages.livestream_summary", cls="LivestreamSummaryPage"),
    "image_crop": PageMeta(
        "图片裁切", "本地工具", module="gui_qt.pages.image_crop", cls="ImageCropPage"),
    "cover_maker": PageMeta(
        "封面制作", "本地工具", module="gui_qt.pages.cover_maker", cls="CoverMakerPage",
        aliases="套版封面制作 封面"),
    "certificate_maker": PageMeta(
        "证书制作", "本地工具",
        module="gui_qt.pages.certificate_maker", cls="CertificateMakerPage",
        aliases="证书生成器 证书"),
    "image_sorter": PageMeta(
        "媒体筛选", "本地工具",
        module="gui_qt.pages.image_sorter", cls="ImageSorterPage",
        aliases="媒体筛选分类 筛选 分类"),
    "rename_batch": PageMeta(
        "批量改名", "本地工具", module="gui_qt.pages.rename_batch", cls="RenameBatchPage",
        aliases="批量重命名 重命名"),

    # ---- 全局 ----
    "tasks": PageMeta(
        "定时任务", "全局", module="gui_qt.pages.tasks", cls="TasksPage",
        footer_label="任务"),
    "global_settings": PageMeta(
        "全局设置", "全局", module="gui_qt.pages.settings", cls="GlobalSettingsPage",
        footer_label="设置"),
    "proxy": PageMeta(
        "网络代理", "网络", module="gui_qt.pages.proxy", cls="ProxyPage",
        aliases="代理 机场 订阅 mihomo 节点 内核"),
    "performance": PageMeta(
        "性能诊断", "全局", module="gui_qt.pages.performance", cls="PerformancePage",
        footer_label="性能"),
}


# 页面类路径（由 PAGE_INDEX 派生）：main_window 用它懒导入，不再单独维护一份。
PAGE_CLASSES = {
    key: (meta.module, meta.cls) for key, meta in PAGE_INDEX.items() if meta.module
}


# 侧栏分组与顺序（标题取 group 名，条目标题查 PAGE_INDEX）
NAV_LAYOUT = [
    ("", ["tcdoc"]),
    ("内容采集", ["info_accounts", "info_results", "info_tools"]),
    ("资产管理", ["asset", "netdisk"]),
    ("录播工具箱", [
        "toolbox_organize", "toolbox_editor", "toolbox_rename", "toolbox_audio",
        "toolbox_subtitle", "toolbox_launcher", "toolbox_calendar", "toolbox_migrate",
    ]),
    ("账户追踪", [
        "music_tracker", "tracker", "douyin_index", "weibo_chaohua", "fan_groups",
    ]),
    ("票务购物", ["tickets", "shopping"]),
    ("音乐工具", ["music_search_download", "music_playlist", "music_file_tools"]),
    ("直播录制", ["live_record"]),
    ("本地工具", [
        "localwiki", "livestream_summary", "image_crop", "cover_maker",
        "certificate_maker", "image_sorter", "rename_batch",
    ]),
    ("网络", ["proxy"]),
    ("全局", ["tasks", "global_settings", "performance"]),
]

# 首页「常用工具」「更多信息工具」的顺序
HOME_COMMON = [
    "info_accounts", "info_results", "asset",
    "toolbox_editor", "toolbox_subtitle", "music_search_download",
]
HOME_INFO = [
    "douyin_index", "wechat_article", "bili_resource",
    "video_comments", "tcdoc", "localwiki",
]

# 旧入口 / 外部链接兼容：旧 key → 现有 key
ALIASES = {
    "test_page": "douyin_danmaku",
    "quark_backup": "netdisk",
    "quark_download": "netdisk",
    "backup_center": "global_settings",
    "douyin_shop": "shopping",
    "bili_shop": "shopping",
}


def page_meta(key) -> PageMeta:
    """取页面元信息；未知 key 返回一个兜底项，调用方不必到处判空。"""
    return PAGE_INDEX.get(str(key or ""), PageMeta(str(key or ""), "工具"))


def page_title(key) -> str:
    """页面显示名。跳转按钮 / 提示文案里需要页面名时统一用它，不要再写字面量。"""
    return page_meta(key).title


def nav_groups():
    """[(分组名, [(key, 标题), ...]), ...]，与旧 NAV_GROUPS 结构保持一致。"""
    return [
        (group, [(key, PAGE_INDEX[key].title) for key in keys])
        for group, keys in NAV_LAYOUT
    ]


def nav_keys():
    """侧栏实际出现的 key 集合（以 NAV_LAYOUT 为准，不在元信息里重复标记）。"""
    return {key for _group, keys in NAV_LAYOUT for key in keys}


def search_aliases():
    """{key: 搜索命中词}，只参与匹配、不显示。"""
    return {key: meta.aliases for key, meta in PAGE_INDEX.items() if meta.aliases}


def container_tabs(container_key):
    """某个容器页的页签 key 列表（按 PAGE_INDEX 顺序）。"""
    return [key for key, meta in PAGE_INDEX.items() if meta.tab_of == container_key]


def preload_keys(page_classes):
    """启动预载队列：只挑 PAGE_INDEX 标记可预载、且确实注册了页面类的 key。"""
    return [key for key in page_classes if page_meta(key).preload]
