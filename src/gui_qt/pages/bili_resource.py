"""B站资源 · 聚合页：8 个功能 tab 平铺（表情包×3 + 收藏集×3 + 空间综合搜索×2）。

三个子页实例只作为逻辑宿主（控件属性与网络回调挂在其上），内层 tab
被平铺提升到本页顶部一级；子页自身的头部与内层 QTabWidget 不再展示。
导航入口位于「📊 信息获取」组。
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout

from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import BiliEmojiPage
from gui_qt.pages.bili_garb import BiliGarbPage
from gui_qt.pages.bili_info import BiliInfoPage

_HELP = (
    "<p><b>B站资源下载（8 个功能入口）。</b></p>"
    "<p>· <b>🗃 全部表情包（需登录）</b>：浏览B站全部表情包，按名字搜索、滚动到底自动加载更多；"
    "点「查看」加载详情后⬇ 全部下载（表格缩略图预览，仅显示前 500，下载不受影响）</p>"
    "<p>· <b>🆔 按ID / 装扮表情包</b>：输入表情包/装扮 ID 下载，"
    "自动识别普通表情包 → 装扮表情 → 收藏集（命中收藏集自动跳「收藏集活动」tab）</p>"
    "<p>· <b>📺 直播间表情</b>：输入房间号加载直播间专属表情 / UP主大表情 / 通用表情，"
    "附带主播头像、封面、网页+App背景图、关键帧</p>"
    "<p>· <b>🔍 装扮/收藏集搜索</b>：关键词搜装扮/收藏集（白菜、UP主名等），无需登录；"
    "点「详情」跳转对应 tab</p>"
    "<p>· <b>👕 装扮详情（按ID）</b>：输入装扮 item_id，加载套装全部资源——"
    "空间背景 / 粉丝牌 / 评论背景 / 表情包 / 加载动画 / 进度条 / 皮肤 / 点赞动画</p>"
    "<p>· <b>🎁 收藏集活动（按ID）</b>：输入活动 act_id，"
    "加载活动视觉 / 勋章 / 全部收藏集卡牌图与卡牌视频</p>"
    "<p>· <b>👤 用户空间</b>：输入 UID 查看用户公开信息——关注/粉丝/获赞/签名/直播房间，"
    "可下载头像/头像框/空间背景图/投稿封面/充电表情/收藏集封面</p>"
    "<p>· <b>🔍 综合搜索</b>：关键词搜B站用户与视频（WBI 签名），"
    "用户结果跳「用户空间」tab；视频结果自动跳「单个解析」页解析</p>"
    "<p>· 表情类接口需要 B站 登录 Cookie（侧栏「Cookie 管理」维护），其余游客接口；"
    "文件列表勾选后下载，支持多选批量</p>"
)


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

        # 子页只作为逻辑宿主；其内层 tab 按顺序平铺提升到本页顶部（3+3+2 = 8）
        self.emoji_page = BiliEmojiPage(app)
        self.garb_page = BiliGarbPage(app)
        self.info_page = BiliInfoPage(app)
        for sub in (self.emoji_page, self.garb_page, self.info_page):
            sub._tab_base = self.tabs.count()
            sub._flat_tabs = self.tabs
            while sub.tabs.count():
                w = sub.tabs.widget(0)
                label = sub.tabs.tabText(0)
                sub.tabs.removeTab(0)
                self.tabs.addTab(w, label)

    def show_garb(self, act_id: str):
        """表情包 tab 命中收藏集 ID 时切到「收藏集活动」tab 并加载活动。"""
        self.garb_page.open_act(act_id)
