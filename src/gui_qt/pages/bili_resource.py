"""B站资源 · 聚合页：8 个功能 tab 平铺（表情包×3 + 收藏集×3 + 空间综合搜索×2）。

三个子页实例只作为逻辑宿主（控件属性与网络回调挂在其上），内层 tab
被平铺提升到本页顶部一级（顺序交错：表情包搜索/装扮搜索/装扮表情包/
直播表情/装扮ID详情/收藏集/空间/综合搜索）；子页自身的头部与内层
QTabWidget 不再展示。导航入口位于「📊 信息获取」组。
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout

from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import BiliEmojiPage
from gui_qt.pages.bili_garb import BiliGarbPage
from gui_qt.pages.bili_info import BiliInfoPage

_HELP = (
    "<p><b>B站资源下载（8 个功能入口）。</b></p>"
    "<p>· <b>🗃 表情包搜索</b>：浏览B站全部表情包（需登录），按名字搜索、滚动到底自动加载更多；"
    "点「查看」加载详情后⬇ 全部下载（表格缩略图预览，仅显示前 500，下载不受影响）</p>"
    "<p>· <b>🔍 装扮搜索</b>：关键词搜装扮/收藏集（白菜、UP主名等），无需登录；"
    "点「详情」跳转对应 tab</p>"
    "<p>· <b>🆔 装扮表情包</b>：输入表情包/装扮 ID 下载，"
    "自动识别普通表情包 → 装扮表情 → 收藏集（命中收藏集自动跳「收藏集」tab）</p>"
    "<p>· <b>📺 直播表情</b>：输入房间号加载直播间专属表情 / UP主大表情 / 通用表情，"
    "附带主播头像、封面、网页+App背景图、关键帧</p>"
    "<p>· <b>👕 装扮ID详情</b>：输入装扮 item_id，加载套装全部资源——"
    "空间背景 / 粉丝牌 / 评论背景 / 表情包 / 加载动画 / 进度条 / 皮肤 / 点赞动画</p>"
    "<p>· <b>🎁 收藏集</b>：输入活动 act_id，"
    "加载活动视觉 / 勋章 / 全部收藏集卡牌图与卡牌视频</p>"
    "<p>· <b>👤 空间</b>：输入 UID 查看用户公开信息——关注/粉丝/获赞/签名/直播房间，"
    "可下载头像/头像框/空间背景图/投稿封面/充电表情/收藏集封面</p>"
    "<p>· <b>🔍 综合搜索</b>：关键词搜B站用户与视频（WBI 签名），"
    "用户结果跳「空间」tab；视频结果自动跳「单个解析」页解析</p>"
    "<p>· 表情类接口需要 B站 登录 Cookie（侧栏「Cookie 管理」维护），其余游客接口；"
    "文件列表勾选后下载，支持多选批量</p>"
)

# 平铺顺序与命名：(子页键, 内层 tab 下标, 顶部标签)
_FLAT = [
    ("emoji", 0, "🗃 表情包搜索"),
    ("garb", 0, "🔍 装扮搜索"),
    ("emoji", 1, "🆔 装扮表情包"),
    ("emoji", 2, "📺 直播表情"),
    ("garb", 1, "👕 装扮ID详情"),
    ("garb", 2, "🎁 收藏集"),
    ("info", 0, "👤 空间"),
    ("info", 1, "🔍 综合搜索"),
]


class BiliResourcePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        head.addWidget(QLabel("🎁 B站资源", styleSheet="font-size: 16px; font-weight: bold;"))
        head.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        head.addWidget(attach_help_overlay(self, "❓ B站资源 · 功能介绍", _HELP))
        layout.addLayout(head)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # 子页只作为逻辑宿主；先取出各自内层 tab 控件（按本地顺序）
        self.emoji_page = BiliEmojiPage(app)
        self.garb_page = BiliGarbPage(app)
        self.info_page = BiliInfoPage(app)
        subs = {"emoji": self.emoji_page, "garb": self.garb_page, "info": self.info_page}
        widgets = {}
        for key, sub in subs.items():
            ws = []
            while sub.tabs.count():
                w = sub.tabs.widget(0)
                sub.tabs.removeTab(0)
                ws.append(w)
            widgets[key] = ws

        # 按 _FLAT 顺序平铺提升到本页顶部（两个子页的 tab 交错），并记录映射表
        tab_map = {"emoji": [], "garb": [], "info": []}
        for key, local, label in _FLAT:
            flat_idx = self.tabs.addTab(widgets[key][local], label)
            tab_map[key].append(flat_idx)
        for key, sub in subs.items():
            sub._flat_tabs = self.tabs
            sub._tab_map = tab_map[key]

    def show_garb(self, act_id: str):
        """表情包 tab 命中收藏集 ID 时切到「收藏集」tab 并加载活动。"""
        self.garb_page.open_act(act_id)
