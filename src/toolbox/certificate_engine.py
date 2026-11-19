"""内部认证证书生成引擎（L4 领域引擎，纯标准库 + Pillow，不依赖 PySide6）。

用途：把「封茗囧菌数据汇总库·美工作品授权与二级分发协议」录入进来，
自动匹配协议正文 / 附件一 / 附件三里的字段与复选框，人工复核后输出
横版 A4 300dpi 的内部认证证书（固定两页：证书页 + 图片页）。

设计要点：
- 解析：txt / md / docx / pdf → 纯文本 → 正则字段 + 复选框匹配（□ ☑ ■ √）；
  另支持 xlsx 填写表：按「字段名称 / 填写内容」三张表直接映射字段与勾选；
- 复选框：文档里的勾选状态自动带出来，未匹配到的项交给页面复核勾选；
- 派生字段：作品类型勾选后自动生成用途摘要与证书副标题；
  证书载明项留空时自动跟随正文 / 附件一；
- 水印：按勾选内容把署名 / 认证号以淡纹稀疏平铺到作品图，右下角再压一个小标签
  （不遮挡主体），导出时另存到证书文件夹的「水印件」子目录（原图不修改）；
- 渲染：Pillow 直接绘制 A4 横版 3508x2480，证书页 + 图片页；
- 导出：每份证书一个「认证号_作品名_内部认证证书」文件夹（两页 PNG 300dpi +
  合并 PDF + 水印件），并在同级打一个同名 zip 包。

历史数据只存字段、不含图片，见 `toolbox/certificate_history.py`。
版式规范见 `src/skills/image-output-spec/SKILL.md` 的「内部认证证书」一节。
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

from toolbox import certificate_agreement
from toolbox import image_render

# ==================== 版式常量 ====================

A4_LANDSCAPE = (3508, 2480)      # A4 横版 300dpi
PAGE_W, PAGE_H = A4_LANDSCAPE
DPI = 300
PAGE_MARGIN = 150
FRAME_GAP = 46                   # 外框与内框间距
MAX_IMAGES = 6                   # 图片页最多 6 张，超出部分提示另存

DEFAULT_LICENSEE = "封茗囧菌数据汇总库管理组"
APP_NAME = image_render.APP_NAME

# 证书配色（不参与 UI 主题，导出图固定纸感配色）
INK = "#1f3350"
INK_SOFT = "#4a5d78"
GOLD = "#a9853f"
GOLD_LIGHT = "#d9c28a"
PAPER = "#fcfaf4"
PAPER_EDGE = "#f4efe2"
LINE = "#d8cdb4"
SEAL = "#a83a34"
STATUS_COLORS = {
    "有效": "#2f6b4f",
    "已续签": "#8a6a1f",
    "已终止": "#8c3b36",
    "已作废": "#6b6b6b",
}

# ==================== 复选框与字段定义 ====================

CHECK_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "art_type",
        "label": "作品类型",
        "single": True,
        "items": ("曲绘/插画", "封面设计", "海报", "其他"),
    },
    {
        "key": "sample_format",
        "label": "水印样本格式",
        "single": True,
        "items": ("PNG", "JPG", "PDF", "其他"),
    },
    {
        "key": "watermark_items",
        "label": "水印内容核对",
        "single": False,
        "items": (
            "授权方署名/圈名",
            "主要设计人署名",
            "协作设计人署名",
            "作品标题/项目名称",
            "内部认证号",
            "其他约定标识",
        ),
    },
    {
        "key": "usage_places",
        "label": "水印计划使用位置",
        "single": False,
        "items": ("数据库条目展示", "网络宣发", "非盈利无料", "其他"),
    },
    {
        "key": "cert_status",
        "label": "证书状态",
        "single": True,
        "items": ("有效", "已终止", "已作废", "已续签"),
    },
    {
        "key": "image_format",
        "label": "证书图片格式",
        "single": True,
        "items": ("PNG", "JPG", "PDF", "其他"),
    },
)

CHECK_GROUP_BY_KEY = {group["key"]: group for group in CHECK_GROUPS}

CHECKED_MARKS = "☑☒■√✓✔"
UNCHECKED_MARKS = "□☐"
_ALL_MARKS = CHECKED_MARKS + UNCHECKED_MARKS

# 填写表（Excel / Word 表格）里的「字段名称」→ 数据字段。
# 名称先做规范化（去空格、全角括号转半角）再查表，兼容“授权方姓名 / 圈名”这类写法。
LABEL_FIELDS: dict[str, str] = {
    "内部认证号": "archive_no",
    "授权方姓名/圈名": "author_name",
    "授权方联系方式(常用邮箱)": "author_contact",
    "被授权方管理组代表/负责人": "manager_rep",
    "被授权方对接人": "contact_person",
    "作品标题": "title",
    "作品类型其他说明": "art_type_other",
    "作品对应歌曲/项目名称": "project_name",
    "授权期限起始日": "period_start",
    "授权期限届满日": "period_end",
    "授权方确认签名/圈名": "author_sign",
    "授权方确认日期": "author_sign_date",
    "被授权方代表签名": "licensee_rep_sign",
    "被授权方确认日期": "licensee_sign_date",
    "被授权方确认内部认证号": "confirm_archive_no",
    "已加水印作品图片样本电子附件文件名": "watermark_file",
    "样本格式其他说明": "sample_other",
    "授权方署名/圈名": "wm_author",
    "主要设计人署名": "wm_lead",
    "协作设计人署名(如有)": "wm_co",
    "作品标题/项目名称": "wm_title",
    "内部认证号(图片中)": "wm_archive_no",
    "其他约定标识": "wm_other",
    "主要设计人": "lead_designer",
    "协作设计人(如有)": "co_designers",
    "署名写法说明": "credit_note",
    "图片格式其他说明": "image_other",
    "证书载明作品标题": "cert_title",
    "证书载明作品类型": "cert_type",
    "证书载明授权方署名/圈名": "cert_author",
    "证书载明主要设计人署名": "cert_lead",
    "证书载明协作设计人署名(如有)": "cert_co",
    "证书载明授权期限起始日": "cert_period_start",
    "证书载明授权期限届满日": "cert_period_end",
    "证书载明已加水印作品图片样本电子文件名": "cert_sample_file",
    "续签至": "renew_until",
    "签发日期": "issue_date",
    "签发方代表签名/电子章": "signer",
}

# 单选勾选项：字段名称 → (CHECK_GROUPS key, 数据字段)
LABEL_CHOICES: dict[str, tuple[str, str]] = {
    "作品类型": ("art_type", "art_type"),
    "样本格式": ("sample_format", "sample_format"),
    "图片格式": ("image_format", "image_format"),
    "证书状态": ("cert_status", "status"),
}

# 使用位置：字段名称 → 勾选项
LABEL_USAGE = {
    "已加水印图片计划使用位置-数据库条目展示": "数据库条目展示",
    "已加水印图片计划使用位置-网络宣发": "网络宣发",
    "已加水印图片计划使用位置-非盈利无料": "非盈利无料",
}

# 图片中署名：字段名称 → 水印勾选项
LABEL_WATERMARK = {
    "授权方署名/圈名": "授权方署名/圈名",
    "主要设计人署名": "主要设计人署名",
    "协作设计人署名(如有)": "协作设计人署名",
    "作品标题/项目名称": "作品标题/项目名称",
    "内部认证号(图片中)": "内部认证号",
    "其他约定标识": "其他约定标识",
}

# 作品类型 → 用途摘要 / 证书副标题。勾选类型后自动匹配，页面可再手动改。
ART_TYPE_USAGE: dict[str, str] = {
    "曲绘/插画": "内部存档展示、网络宣发、非盈利无料；用于曲绘/插画作品在数据库条目中的展示与署名呈现。",
    "封面设计": "内部存档展示、网络宣发、非盈利无料；用于封面设计作品在数据库条目、宣发物料中的排版展示。",
    "海报": "内部存档展示、网络宣发、非盈利无料；用于海报类作品在活动宣发与条目展示中的呈现。",
}
DEFAULT_USAGE = (
    "内部存档展示、网络宣发、非盈利无料；不含商业使用、改编、二次创作、"
    "AI训练或生成、对外再授权及本协议未列明用途。"
)
ART_TYPE_SUBTITLE = {
    "曲绘/插画": "曲绘 / 插画作品 · 二级分发授权",
    "封面设计": "封面设计作品 · 二级分发授权",
    "海报": "海报作品 · 二级分发授权",
}
DEFAULT_SUBTITLE = "美工作品 · 二级分发授权"

# 认证号 / 协议编号 / 证书编号格式
# 内部认证号：FMJJ-DB-ART-<年份>-<6 位序号>-<8 位校验哈希>
ARCHIVE_PREFIX = "FMJJ-DB-ART"
ARCHIVE_PREFIX_PARTS = ("FMJJ", "DB", "ART")   # 前三段默认值（页面可分别改）
ARCHIVE_SERIAL_DIGITS = 6
ARCHIVE_HASH_LEN = 8
YEAR_MIN, YEAR_MAX = 1900, 2199      # 编号年份可选范围
_RE_NUMBER = re.compile(
    rf"^([A-Z]{{1,8}})-([A-Z0-9]{{1,8}})-([A-Z0-9]{{1,8}})-(\d{{4}})-(\d{{3,}})"
    rf"(?:-([0-9A-F]{{{ARCHIVE_HASH_LEN}}}))?$")
# 从文件夹名等长文本里捞编号（不要求整串就是编号）
_RE_NUMBER_SCAN = re.compile(
    rf"([A-Z]{{1,8}})-([A-Z0-9]{{1,8}})-([A-Z0-9]{{1,8}})-(\d{{4}})-(\d{{3,}})"
    rf"(?:-([0-9A-F]{{{ARCHIVE_HASH_LEN}}}))?")
# 协议文档里识别内部认证号（标签写法之外的正则兜底）
_RE_ARCHIVE = re.compile(
    rf"FMJJ-DB-ART-\d{{4}}-\d{{3,}}(?:-[0-9A-F]{{{ARCHIVE_HASH_LEN}}})?")
_RE_LICENSE = re.compile(r"ART-LIC-\d{4}-\d{3,}")
_RE_CERT = re.compile(r"FMJJ-CERT-\d{4}-\d{3,}")


_PLACEHOLDER_RE = re.compile(r"〔[^〕]*〕")
_SYMBOL_ONLY_RE = re.compile(r"^[\s□☐☑☒■√✓✔_＿\-—·．.、，,：:；;（）()\[\]【】]+$")
_MEANINGFUL_RE = re.compile(r"[\w\u4e00-\u9fff]")


def _clean(value: str | None) -> str:
    """清洗录入值：去占位符、下划线、复选框符号与首尾空白。"""
    if not value:
        return ""
    text = str(value).strip()
    text = _PLACEHOLDER_RE.sub("", text)
    text = re.sub(r"[_＿]{2,}", "", text)
    text = text.strip("　 ").strip("：: 　")
    # 只去掉成对包裹的括号，不误删作品名里的「（终章）」这类右括号
    if len(text) > 1 and text[0] in "（(" and text[-1] in "）)":
        text = text[1:-1]
    return text.strip()


def _meaningful(value: str) -> bool:
    """值里至少有一个文字/数字，排除只剩符号、复选框与括号的占位。"""
    return bool(value) and not _SYMBOL_ONLY_RE.match(value) and bool(_MEANINGFUL_RE.search(value))


def _line_value(text: str, label: str, *, allow_checked: bool = True) -> str:
    """取「标签：值」里的值；同标签出现多次时返回第一个有实质内容的。"""
    pattern = re.compile(
        r"^[^\n]*?" + re.escape(label) + r"[^\n：:]*[：:][ \t　]*(.+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        raw = match.group(1)
        if "〔" in raw:
            continue
        # 同一行常接第二个字段（「________    日期：________」），按连续空白截断
        raw = re.split(r"[ \t　]{2,}", raw.strip())[0]
        value = _clean(raw)
        if not allow_checked:
            value = re.sub("[" + _ALL_MARKS + "]", "", value).strip()
        if _meaningful(value):
            return value
    return ""


def _any_code(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    return match.group(0) if match else ""


def _norm_label(text: str | None) -> str:
    """字段名称规范化：去空格、全角括号转半角，便于对照填写表。"""
    label = re.sub(r"\s+", "", str(text or ""))
    return label.replace("（", "(").replace("）", ")")


def _cell_text(value: Any) -> str:
    """Excel 单元格转文本：日期按 YYYY-MM-DD，整数去掉 .0。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _clean(str(value))


def _norm_date(text: str) -> str:
    match = re.search(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", text or "")
    if not match:
        return text
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _match_option(text: str, options: Sequence[str]) -> str:
    value = (text or "").strip()
    for option in options:
        if option in value:
            return option
    return ""


def _is_yes(text: str) -> bool:
    value = (text or "").strip()
    return value in ("是", "√", "✓", "☑", "■", "Y", "y", "yes", "YES", "TRUE", "1") \
        or value.startswith("是")


def _is_no(text: str) -> bool:
    value = (text or "").strip()
    return value in ("否", "×", "✗", "☐", "N", "n", "no", "NO", "FALSE", "0") \
        or value.startswith("否")


def _parse_period(text: str) -> tuple[str, str]:
    """解析授权期限「自 X 年 X 月 X 日至 X 年 X 月 X 日」，返回 (起, 止)。"""
    pattern = re.compile(
        r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"\s*至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    )
    match = pattern.search(text)
    if not match:
        return "", ""
    y1, m1, d1, y2, m2, d2 = match.groups()
    return f"{y1}-{int(m1):02d}-{int(d1):02d}", f"{y2}-{int(m2):02d}-{int(d2):02d}"


def _match_checks(text: str, group: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    """匹配一组复选框：返回 (勾选项, 每项冒号后已填的值)。"""
    selected: list[str] = []
    values: dict[str, str] = {}
    for item in group["items"]:
        # 「其他」要带冒号才匹配，避免吃掉「其他约定标识」这类同前缀项
        tail = r"\s*[：:]" if item == "其他" else r"[^\n：:]*[：:]?"
        pattern = re.compile(
            r"[" + _ALL_MARKS + r"]\s*" + re.escape(item) + tail + r"[ \t　]*([^\n]*)"
        )
        for match in pattern.finditer(text):
            mark_match = re.search("[" + _ALL_MARKS + r"]", match.group(0))
            if mark_match and mark_match.group(0) in CHECKED_MARKS and item not in selected:
                selected.append(item)
            raw = match.group(1)
            if raw[:1] in tuple(_ALL_MARKS):
                continue
            value = _clean(raw)
            if _meaningful(value):
                values.setdefault(item, value)
    return selected, values


# ==================== 数据模型 ====================


@dataclass
class CertificateData:
    """一张证书需要的全部字段（页面表单与渲染共用）。"""

    archive_no: str = ""
    title: str = ""
    art_type: str = ""
    art_type_other: str = ""
    author_name: str = ""
    author_contact: str = ""
    lead_designer: str = ""
    co_designers: str = ""
    licensee: str = DEFAULT_LICENSEE
    period_start: str = ""
    period_end: str = ""
    usage_note: str = ""
    watermark_file: str = ""
    status: str = "有效"
    renew_until: str = ""
    issue_date: str = ""
    signer: str = ""
    sample_format: str = ""
    image_format: str = ""
    watermark_items: list[str] = field(default_factory=list)
    usage_places: list[str] = field(default_factory=list)
    check_values: dict[str, str] = field(default_factory=dict)

    # ---------- 协议正文补充（对应填写表「协议正文」）----------
    manager_rep: str = ""          # 被授权方管理组代表 / 负责人
    contact_person: str = ""       # 被授权方对接人
    project_name: str = ""         # 作品对应歌曲 / 项目名称
    author_sign: str = ""          # 授权方确认签名 / 圈名
    author_sign_date: str = ""     # 授权方确认日期
    licensee_rep_sign: str = ""    # 被授权方代表签名
    licensee_sign_date: str = ""   # 被授权方确认日期
    confirm_archive_no: str = ""   # 被授权方确认内部认证号

    # ---------- 附件一（水印样本与署名确认）----------
    sample_other: str = ""         # 样本格式「其他」说明
    image_other: str = ""          # 证书图片格式「其他」说明
    wm_author: str = ""            # 图片中的授权方署名 / 圈名
    wm_lead: str = ""              # 图片中的主要设计人署名
    wm_co: str = ""                # 图片中的协作设计人署名
    wm_title: str = ""             # 图片中的作品标题 / 项目名称
    wm_archive_no: str = ""        # 图片中的内部认证号
    wm_other: str = ""             # 其他约定标识
    credit_note: str = ""          # 署名写法说明（圈名、ID、格式）
    usage_other: str = ""          # 使用位置「其他」具体位置

    # ---------- 证书载明（留空自动取正文 / 附件一）----------
    cert_title: str = ""
    cert_type: str = ""
    cert_author: str = ""
    cert_lead: str = ""
    cert_co: str = ""
    cert_period_start: str = ""
    cert_period_end: str = ""
    cert_sample_file: str = ""

    # ---------- 自动水印 ----------
    auto_watermark: bool = True    # 导出时把水印直接合成到作品图上

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: getattr(self, item.name)
            for item in dataclasses.fields(self)
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "CertificateData":
        if not data:
            return cls()
        kwargs: dict[str, Any] = {}
        for item in dataclasses.fields(cls):
            if item.name in ("watermark_items", "usage_places", "check_values"):
                continue
            default = item.default if item.default is not dataclasses.MISSING else ""
            value = data.get(item.name, default)
            kwargs[item.name] = bool(value) if isinstance(default, bool) else str(value or "")
        kwargs["licensee"] = kwargs.get("licensee") or DEFAULT_LICENSEE
        kwargs["status"] = kwargs.get("status") or "有效"
        return cls(
            **kwargs,
            watermark_items=[str(item) for item in data.get("watermark_items") or []],
            usage_places=[str(item) for item in data.get("usage_places") or []],
            check_values={
                str(key): str(value)
                for key, value in (data.get("check_values") or {}).items()
            },
        )

    # ---------- 派生字段 ----------

    def effective_type(self) -> str:
        if self.art_type == "其他":
            return _clean(self.art_type_other) or "其他"
        return self.art_type

    def effective_usage(self) -> str:
        if _clean(self.usage_note):
            return _clean(self.usage_note)
        return ART_TYPE_USAGE.get(self.art_type, DEFAULT_USAGE)

    def subtitle(self) -> str:
        return ART_TYPE_SUBTITLE.get(self.art_type, DEFAULT_SUBTITLE)

    def period_text(self) -> str:
        start, end = self.cert_period_start_text(), self.cert_period_end_text()
        if start and end:
            return f"{start}  至  {end}"
        return "自双方确认之日起，按协议第四条执行"

    def status_color(self) -> str:
        return STATUS_COLORS.get(self.status, STATUS_COLORS["有效"])

    # ---------- 证书载明（留空自动跟随正文 / 附件一）----------

    def cert_title_text(self) -> str:
        return _clean(self.cert_title) or _clean(self.title)

    def cert_type_text(self) -> str:
        return _clean(self.cert_type) or self.effective_type()

    def cert_author_text(self) -> str:
        return _clean(self.cert_author) or _clean(self.author_name)

    def cert_lead_text(self) -> str:
        return _clean(self.cert_lead) or _clean(self.lead_designer)

    def cert_co_text(self) -> str:
        return _clean(self.cert_co) or _clean(self.co_designers)

    def cert_period_start_text(self) -> str:
        return _clean(self.cert_period_start) or _clean(self.period_start)

    def cert_period_end_text(self) -> str:
        return _clean(self.cert_period_end) or _clean(self.period_end)

    def cert_sample_text(self) -> str:
        return _clean(self.cert_sample_file) or _clean(self.watermark_file)

    def confirm_no(self) -> str:
        return _clean(self.confirm_archive_no) or _clean(self.archive_no)

    # ---------- 图片内署名（水印）----------

    def wm_author_text(self) -> str:
        return _clean(self.wm_author) or _clean(self.author_name)

    def wm_lead_text(self) -> str:
        return _clean(self.wm_lead) or _clean(self.lead_designer)

    def wm_co_text(self) -> str:
        return _clean(self.wm_co) or _clean(self.co_designers)

    def wm_title_text(self) -> str:
        return _clean(self.wm_title) or _clean(self.title)

    def wm_archive_text(self) -> str:
        return _clean(self.wm_archive_no) or _clean(self.archive_no)

    def watermark_parts(self) -> list[str]:
        """按「水印内容核对」勾选项拼出水印文字；未勾选时用署名 + 认证号兜底。"""
        mapping = (
            ("授权方署名/圈名", self.wm_author_text()),
            ("主要设计人署名", self.wm_lead_text()),
            ("协作设计人署名", self.wm_co_text()),
            ("作品标题/项目名称", self.wm_title_text()),
            ("内部认证号", self.wm_archive_text()),
            ("其他约定标识", _clean(self.wm_other)),
        )
        checked = set(self.watermark_items)
        parts: list[str] = []
        for item, value in mapping:
            if (checked and item not in checked) or not value or value in parts:
                continue
            parts.append(value)
        if not parts:
            parts = [part for part in (self.wm_author_text(), self.wm_archive_text()) if part]
        return parts

    def watermark_text(self) -> str:
        return " · ".join(self.watermark_parts())


# ---------- 内部认证号：年份 + 6 位序号 + 8 位校验哈希 ----------

def default_year() -> int:
    """默认年份：取本机当前年份（页面可手动改）。"""
    return date.today().year


def archive_parts(code: str) -> tuple[int | None, int | None]:
    """拆分内部认证号，返回 (年份, 序号)；也可从证书文件夹名里识别。"""
    match = _RE_NUMBER_SCAN.search(str(code or "").strip().upper())
    if not match:
        return None, None
    return int(match.group(4)), int(match.group(5))


def split_archive_segments(code: str) -> tuple[str, str, str, int, int] | None:
    """把编号拆成五段：(前缀1, 前缀2, 前缀3, 年份, 序号)；不合法返回 None。"""
    match = _RE_NUMBER.match(str(code or "").strip().upper())
    if not match:
        return None
    return (match.group(1), match.group(2), match.group(3),
            int(match.group(4)), int(match.group(5)))


def compose_archive_no(prefix1: str, prefix2: str, prefix3: str,
                       year: int, serial: int) -> str:
    """按五段拼出编号主干（不含校验哈希）。"""
    parts = [str(prefix1 or "").strip().upper(), str(prefix2 or "").strip().upper(),
             str(prefix3 or "").strip().upper()]
    if not all(parts):
        return ""
    return (f"{'-'.join(parts)}-{int(year)}-"
            f"{int(serial):0{ARCHIVE_SERIAL_DIGITS}d}")


def archive_base(year: int, serial: int) -> str:
    """用默认前缀拼编号主干：FMJJ-DB-ART-年份-6 位序号（不含哈希）。"""
    return compose_archive_no(*ARCHIVE_PREFIX_PARTS, int(year), int(serial))


def license_no(code: str) -> str:
    """按内部认证号派生协议编号：ART-LIC-年份-序号（序号位数跟随原编号）。"""
    match = _RE_NUMBER.match(str(code or "").strip().upper())
    if not match:
        return ""
    width = len(match.group(5))
    return f"ART-LIC-{int(match.group(4))}-{int(match.group(5)):0{width}d}"


def _archive_hash_of(code: str) -> str:
    """取编号末尾的校验哈希；没有则返回空串。"""
    match = _RE_NUMBER.match(str(code or "").strip().upper())
    return match.group(6) if match else ""


def archive_fingerprint(base: str, data: "CertificateData") -> str:
    """校验哈希：编号主干 + 作品标题 + 授权方 + 授权期限 + 签发日期的 SHA-256 前 8 位。"""
    payload = "\n".join((
        str(base or "").strip(),
        _clean(data.cert_title_text()),
        _clean(data.cert_author_text()),
        _clean(data.cert_period_start_text()),
        _clean(data.cert_period_end_text()),
        _clean(data.issue_date),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:ARCHIVE_HASH_LEN].upper()


def with_fingerprint(code: str, data: "CertificateData") -> str:
    """给编号补上（或重算）末尾校验哈希，前三段前缀原样保留。

    只处理 6 位序号的编号；历史遗留的 3 位编号保持原样，避免改动已签发的旧证书号。
    """
    if not is_managed_archive_no(code):
        return str(code or "").strip().upper()
    segments = split_archive_segments(code)
    base = compose_archive_no(*segments[:3], segments[3], segments[4])
    return f"{base}-{archive_fingerprint(base, data)}"


def is_managed_archive_no(code: str) -> bool:
    """是否为当前规则下的编号（五段、6 位序号，可带校验哈希）。"""
    match = _RE_NUMBER.match(str(code or "").strip().upper())
    return bool(match) and len(match.group(5)) == ARCHIVE_SERIAL_DIGITS


@dataclass
class ParseResult:
    """文档解析结果：字段 + 复选框匹配 + 解析提示。"""

    fields: CertificateData = field(default_factory=CertificateData)
    checks: dict[str, list[str]] = field(default_factory=dict)
    check_values: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    source: str = ""

    def matched_summary(self) -> str:
        """如「复选框 4/6 组匹配」。"""
        groups = [group for group in CHECK_GROUPS if self.checks.get(group["key"])]
        return f"复选框 {len(groups)}/{len(CHECK_GROUPS)} 组匹配"


def load_document_text(path: str | Path) -> str:
    """读取 txt / md / docx / pdf 的纯文本；docx 含表格单元格。"""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return file_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return file_path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        try:
            import docx  # 惰性导入：只有处理 Word 时才需要
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
            raise ValueError(
                "缺少 Word 解析组件 python-docx，请改用 txt/md 或安装依赖后重试"
            ) from exc

        document = docx.Document(str(file_path))
        blocks = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                blocks.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(blocks)
    if suffix == ".pdf":
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
                raise ValueError(
                    "缺少 PDF 解析组件 PyMuPDF，请改用 docx/txt 或安装依赖后重试"
                ) from exc

        chunks: list[str] = []
        with pymupdf.open(str(file_path)) as document:
            for page in document:
                chunks.append(page.get_text("text"))
        return "\n".join(chunks)
    raise ValueError(f"暂不支持的文件类型：{suffix or '无扩展名'}（支持 txt / md / docx / pdf，填写表用 xlsx 导入）")


def parse_document(text: str, source: str = "粘贴文本") -> ParseResult:
    """从协议文本抽取证书字段与复选框状态。"""
    body = text or ""
    fields = CertificateData()
    notes: list[str] = []

    fields.archive_no = _any_code(body, _RE_ARCHIVE) or _line_value(body, "内部认证号")

    fields.title = _line_value(body, "作品标题")
    fields.author_name = _line_value(body, "姓名 / 圈名") or _line_value(body, "姓名/圈名")
    fields.author_contact = _line_value(body, "联系方式（常用邮箱）") or _line_value(body, "联系方式")
    fields.lead_designer = _line_value(body, "主要设计人")
    fields.co_designers = _line_value(body, "协作设计人")
    fields.renew_until = _line_value(body, "续签至")
    fields.signer = _line_value(body, "代表签名")
    fields.issue_date = _line_value(body, "签发日期")
    fields.watermark_file = _line_value(body, "电子附件文件名（如有）")
    fields.watermark_file = fields.watermark_file or _line_value(
        body, "已加水印作品图片样本电子附件文件名")

    # 协议正文补充项
    fields.manager_rep = _line_value(body, "管理组代表") or _line_value(body, "负责人")
    fields.contact_person = _line_value(body, "被授权方对接人") or _line_value(body, "对接人")
    fields.project_name = _line_value(body, "作品对应歌曲") or _line_value(body, "项目名称")
    fields.author_sign = _line_value(body, "授权方确认签名")
    fields.author_sign_date = _line_value(body, "授权方确认日期")
    fields.licensee_rep_sign = _line_value(body, "被授权方代表签名")
    fields.licensee_sign_date = _line_value(body, "被授权方确认日期")
    fields.confirm_archive_no = _line_value(body, "被授权方确认内部认证号")

    # 附件一：图片内署名与说明
    fields.sample_other = _line_value(body, "样本格式其他说明")
    fields.image_other = _line_value(body, "图片格式其他说明")
    fields.wm_author = _line_value(body, "授权方署名/圈名")
    fields.wm_lead = _line_value(body, "主要设计人署名")
    fields.wm_co = _line_value(body, "协作设计人署名")
    fields.wm_title = _line_value(body, "作品标题/项目名称")
    fields.wm_archive_no = _line_value(body, "内部认证号（图片中）")
    fields.wm_other = _line_value(body, "其他约定标识")
    fields.credit_note = _line_value(body, "署名写法说明")

    # 证书载明项（留空时渲染自动回落到正文 / 附件一）
    fields.cert_title = _line_value(body, "证书载明作品标题")
    fields.cert_type = _line_value(body, "证书载明作品类型")
    fields.cert_author = _line_value(body, "证书载明授权方署名")
    fields.cert_lead = _line_value(body, "证书载明主要设计人署名")
    fields.cert_co = _line_value(body, "证书载明协作设计人署名")
    fields.cert_period_start = _line_value(body, "证书载明授权期限起始日")
    fields.cert_period_end = _line_value(body, "证书载明授权期限届满日")
    fields.cert_sample_file = _line_value(body, "证书载明已加水印作品图片样本电子文件名")

    period_start, period_end = _parse_period(body)
    fields.period_start = period_start
    fields.period_end = period_end

    checks: dict[str, list[str]] = {}
    check_values: dict[str, str] = {}
    for group in CHECK_GROUPS:
        selected, values = _match_checks(body, group)
        checks[group["key"]] = selected
        # 键带组名前缀：各组的「其他」不会互相串值
        check_values.update({f"{group['key']}.{item}": value for item, value in values.items()})
        if group["single"] and len(selected) > 1:
            notes.append(f"{group['label']}匹配到多项，已取第一项，请复核")

    fields.watermark_items = list(checks.get("watermark_items") or [])
    fields.usage_places = list(checks.get("usage_places") or [])
    fields.art_type = (checks.get("art_type") or [""])[0]
    fields.sample_format = (checks.get("sample_format") or [""])[0]
    fields.image_format = (checks.get("image_format") or [""])[0]
    fields.status = (checks.get("cert_status") or ["有效"])[0]
    fields.check_values = dict(check_values)

    # 勾选项冒号后的填写值回填到对应字段
    fields.sample_other = fields.sample_other or check_values.get("sample_format.其他", "")
    fields.image_other = fields.image_other or check_values.get("image_format.其他", "")
    fields.usage_other = check_values.get("usage_places.其他", "")
    fields.wm_author = fields.wm_author or check_values.get("watermark_items.授权方署名/圈名", "")
    fields.wm_lead = fields.wm_lead or check_values.get("watermark_items.主要设计人署名", "")
    fields.wm_co = fields.wm_co or check_values.get("watermark_items.协作设计人署名", "")
    fields.wm_title = fields.wm_title or check_values.get("watermark_items.作品标题/项目名称", "")
    fields.wm_archive_no = fields.wm_archive_no or check_values.get(
        "watermark_items.内部认证号", "")
    fields.wm_other = fields.wm_other or check_values.get("watermark_items.其他约定标识", "")

    # 作品类型有值但没有匹配到勾选时，按「作品类型：xxx」的明确写法兜底；
    # 空白模板里所有候选词都会出现，不能按关键词猜，否则会误判成第一项。
    if not fields.art_type:
        type_line = ""
        match = re.search(r"作品类型[^\n]*", body)
        if match:
            type_line = match.group(0)
        for candidate in ART_TYPE_USAGE:
            if re.search(r"作品类型[^\n：:]*[：:][ \t　]*" + re.escape(candidate), type_line):
                fields.art_type = candidate
                break
    if not fields.art_type:
        notes.append("作品类型未勾选，请在复核区手动选择")

    # 文本框里的其他类型
    if fields.art_type == "其他":
        other = check_values.get("art_type.其他") or ""
        if not other:
            type_line = ""
            match = re.search(r"作品类型[^\n]*", body)
            if match:
                type_line = match.group(0)
            match = re.search(r"其他[：:][ \t　]*([^\n□☑☒■]+)", type_line)
            if match:
                other = _clean(match.group(1))
        if _meaningful(other):
            fields.art_type_other = other

    if not fields.title:
        notes.append("未匹配到作品标题")
    if not fields.author_name:
        notes.append("未匹配到授权方圈名")
    if not (fields.period_start and fields.period_end):
        notes.append("未匹配到完整授权期限")

    for label, column in (
        ("内部认证号", "archive_no"),
    ):
        if not getattr(fields, column):
            notes.append(f"未匹配到{label}")

    # 用途摘要不解析：协议里的固定写法只作模板，勾选作品类型后由
    # effective_usage() 自动匹配，页面上的手动修改优先。

    return ParseResult(fields=fields, checks=checks, check_values=check_values,
                       notes=notes, source=source)


def parse_file(path: str | Path) -> ParseResult:
    file_path = Path(path)
    if file_path.suffix.lower() in (".xlsx", ".xlsm"):
        return parse_spreadsheet(file_path)
    text = load_document_text(file_path)
    return parse_document(text, source=file_path.name)


# ==================== 填写表（Excel）导入 ====================


def load_spreadsheet_rows(path: str | Path) -> list[tuple[str, str]]:
    """读取填写表：逐表找「字段名称 / 填写内容」表头，返回 (名称, 内容文本)。"""
    try:
        import openpyxl  # 惰性导入：只有导入 xlsx 时才需要
    except ImportError as exc:  # pragma: no cover
        raise ValueError("缺少 Excel 解析组件 openpyxl，请安装依赖或改用 docx/txt") from exc

    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    rows: list[tuple[str, str]] = []
    try:
        for sheet in workbook.worksheets:
            name_index = value_index = -1
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if cell is None else str(cell).strip() for cell in row]
                if "字段名称" in cells and "填写内容" in cells:
                    name_index = cells.index("字段名称")
                    value_index = cells.index("填写内容")
                    continue
                if name_index < 0 or max(name_index, value_index) >= len(row):
                    continue
                name = _norm_label(row[name_index])
                if not name or name == "序号":
                    continue
                rows.append((name, _cell_text(row[value_index])))
    finally:
        workbook.close()
    return rows


def parse_spreadsheet(path: str | Path) -> ParseResult:
    """按填写表的三张表（协议正文 / 附件一 / 内部认证证书）匹配字段与勾选。"""
    fields = CertificateData()
    notes: list[str] = []
    checks: dict[str, list[str]] = {group["key"]: [] for group in CHECK_GROUPS}
    check_values: dict[str, str] = {}
    usage_places: list[str] = []
    watermark_items: list[str] = []

    for name, text in load_spreadsheet_rows(path):
        if name in LABEL_CHOICES:
            group_key, attribute = LABEL_CHOICES[name]
            options = CHECK_GROUP_BY_KEY[group_key]["items"]
            value = _match_option(text, options)
            if value:
                setattr(fields, attribute, value)
                checks[group_key] = [value]
                if value == "其他":
                    detail = re.sub(r"^其他[：:\s]*", "", text).strip()
                    if detail and detail != "其他":
                        check_values[f"{group_key}.其他"] = detail
            continue

        if name in LABEL_USAGE:
            if _is_yes(text):
                usage_places.append(LABEL_USAGE[name])
            continue

        if name.startswith("已加水印图片计划使用位置-其他"):
            if _meaningful(text) and not _is_yes(text) and not _is_no(text):
                usage_places.append("其他")
                fields.usage_other = text
                check_values["usage_places.其他"] = text
            continue

        attribute = LABEL_FIELDS.get(name)
        if not attribute:
            continue
        if name in ("授权期限起始日", "授权期限届满日", "授权方确认日期",
                    "被授权方确认日期", "续签至", "签发日期",
                    "证书载明授权期限起始日", "证书载明授权期限届满日"):
            text = _norm_date(text)
        setattr(fields, attribute, text)
        if name in LABEL_WATERMARK and text:
            watermark_items.append(LABEL_WATERMARK[name])
        if name in ("样本格式其他说明",) and text:
            check_values["sample_format.其他"] = text
        if name in ("图片格式其他说明",) and text:
            check_values["image_format.其他"] = text

    fields.usage_places = usage_places
    fields.watermark_items = watermark_items
    fields.check_values = check_values
    checks["usage_places"] = usage_places

    if not fields.title:
        notes.append("填写表里没有作品标题")
    if not fields.author_name:
        notes.append("填写表里没有授权方姓名 / 圈名")
    if not fields.art_type:
        notes.append("作品类型未填写，请在复核区手动选择")
    return ParseResult(fields=fields, checks=checks, check_values=check_values,
                       notes=notes, source=Path(path).name)


# ==================== 编号建议 ====================


def next_serial(year: int | None = None, existing: Iterable[str] = ()) -> int:
    """该年份下一个可用序号：已有最大值 + 1（作废序号由调用方一并传入）。"""
    year = int(year or default_year())
    serial = 1
    for code in existing:
        found_year, found_serial = archive_parts(str(code))
        if found_year == year and found_serial:
            serial = max(serial, found_serial + 1)
    return serial


def suggest_numbers(year: int | None = None, existing: Iterable[str] = (),
                    data: CertificateData | None = None,
                    prefixes: Sequence[str] = ARCHIVE_PREFIX_PARTS) -> dict[str, str]:
    """按年份给出下一个内部认证号（五段 + 校验哈希）。

    `existing` 可以是历史认证号、证书文件夹名或任何含编号的字符串；
    序号按「该年份已有最大值 + 1」顺延。传入 `data` 时追加校验哈希。
    """
    year = int(year or default_year())
    base = compose_archive_no(*prefixes, year, next_serial(year, existing))
    code = f"{base}-{archive_fingerprint(base, data)}" if data is not None else base
    return {"archive_no": code}


def missing_fields(data: CertificateData) -> list[str]:
    """生成证书前必须补齐的字段（返回中文名）。"""
    required = (
        ("archive_no", "内部认证号"),
        ("title", "作品标题"),
        ("author_name", "授权方圈名"),
        ("period_start", "授权起始日期"),
        ("period_end", "授权截止日期"),
    )
    missing = [label for key, label in required if not _clean(getattr(data, key))]
    if not data.art_type:
        missing.append("作品类型")
    if data.status == "已续签" and not _clean(data.renew_until):
        missing.append("续签至日期")
    return missing


# 填写表里标「是」的必填项（页面直接可填的字段）。
# 带自动回落的字段（证书载明、图片内署名、确认签名等）不在这里，
# 由 cert_*/wm_*/author_sign 的回落逻辑保证有值，页面只标星、不判红。
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("archive_no", "内部认证号"),
    ("title", "作品标题"),
    ("author_name", "授权方圈名"),
    ("author_contact", "授权方联系方式"),
    ("manager_rep", "管理组代表 / 负责人"),
    ("contact_person", "被授权方对接人"),
    ("art_type", "作品类型"),
    ("lead_designer", "主要设计人"),
    ("period_start", "授权期限起始日"),
    ("period_end", "授权期限届满日"),
    ("author_sign_date", "授权方确认日期"),
    ("licensee_rep_sign", "被授权方代表签名"),
    ("licensee_sign_date", "被授权方确认日期"),
    ("sample_format", "样本格式"),
    ("image_format", "证书图片格式"),
    ("credit_note", "署名写法说明"),
)


def required_missing(data: CertificateData) -> list[tuple[str, str]]:
    """按填写表的必填清单找出未填项，返回 [(字段名, 中文名)]，供页面标红。"""
    missing = [
        (key, label) for key, label in REQUIRED_FIELDS
        if not _clean(str(getattr(data, key, "") or ""))
    ]
    if data.status == "已续签" and not _clean(data.renew_until):
        missing.append(("renew_until", "续签至"))
    return missing


# ==================== 字体与绘制辅助 ====================

_SERIF_CANDIDATES = (
    "C:/Windows/Fonts/STZHONGS.TTF",
    "C:/Windows/Fonts/simsunb.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
_SERIF_LATIN_CANDIDATES = (
    "C:/Windows/Fonts/timesbd.ttf",
    "C:/Windows/Fonts/times.ttf",
)


@lru_cache(maxsize=128)
def _font(size: int, bold: bool = False, serif: bool = False):
    """字体缓存：中文标题走宋体（华文中宋），正文走微软雅黑。"""
    if serif:
        candidates = _SERIF_CANDIDATES
    else:
        candidates = (
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


@lru_cache(maxsize=32)
def _latin_font(size: int, bold: bool = True):
    for path in _SERIF_LATIN_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return _font(size, bold=bold)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _tracked_text(draw: ImageDraw.ImageDraw, center_x: float, top: float, text: str,
                  font, fill: str, tracking: float = 0.0) -> float:
    """按字距居中绘制一行文字，返回总宽度。"""
    if not text:
        return 0.0
    widths = [_text_width(draw, ch, font) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    x = center_x - total / 2
    for ch, width in zip(text, widths):
        draw.text((x, top), ch, font=font, fill=fill, anchor="la")
        x += width + tracking
    return total


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float,
               max_lines: int = 2) -> list[str]:
    """按像素宽度逐字换行；超出 max_lines 时末行加省略号。"""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + ch
        if _text_width(draw, candidate, font) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    tail = "…"
    while text and _text_width(draw, text + tail, font) > max_width:
        text = text[:-1]
    return text + tail


def _fit_size(draw: ImageDraw.ImageDraw, text: str, max_width: float, start: int,
              *, serif: bool = True, bold: bool = True, min_size: int = 36,
              step: int = 4) -> int:
    """从 start 逐步缩字号，直到文字宽度放得下。"""
    size = start
    while size > min_size:
        font = _font(size, bold=bold, serif=serif)
        if _text_width(draw, text, font) <= max_width:
            return size
        size -= step
    return min_size


def _rgb(value: str) -> tuple[int, int, int]:
    from PIL import ImageColor

    return ImageColor.getrgb(value)


def _new_page() -> Image.Image:
    """纸感底色 + 上下边缘极淡压暗，返回 RGB 画布。"""
    page = Image.new("RGB", (PAGE_W, PAGE_H), PAPER)
    draw = ImageDraw.Draw(page)
    top = _rgb(PAPER)
    bottom = _rgb(PAPER_EDGE)
    band = 420
    for y in range(band):
        ratio = (1 - y / band) ** 2 * 0.9
        color = tuple(int(top[index] + (bottom[index] - top[index]) * ratio) for index in range(3))
        draw.line([(0, y), (PAGE_W, y)], fill=color)
        draw.line([(0, PAGE_H - 1 - y), (PAGE_W, PAGE_H - 1 - y)], fill=color)
    return page


def _draw_frame(draw: ImageDraw.ImageDraw) -> None:
    """双线外框 + 四角装饰。"""
    outer = (PAGE_MARGIN, PAGE_MARGIN, PAGE_W - PAGE_MARGIN, PAGE_H - PAGE_MARGIN)
    draw.rectangle(outer, outline=GOLD, width=6)
    inner = (PAGE_MARGIN + FRAME_GAP // 2, PAGE_MARGIN + FRAME_GAP // 2,
             PAGE_W - PAGE_MARGIN - FRAME_GAP // 2, PAGE_H - PAGE_MARGIN - FRAME_GAP // 2)
    draw.rectangle(inner, outline=GOLD_LIGHT, width=2)

    # 四角 L 形加粗角线
    arm = 90
    for corner_x, corner_y, dx, dy in (
        (PAGE_MARGIN, PAGE_MARGIN, 1, 1),
        (PAGE_W - PAGE_MARGIN, PAGE_MARGIN, -1, 1),
        (PAGE_MARGIN, PAGE_H - PAGE_MARGIN, 1, -1),
        (PAGE_W - PAGE_MARGIN, PAGE_H - PAGE_MARGIN, -1, -1),
    ):
        draw.line([(corner_x, corner_y), (corner_x + dx * arm, corner_y)], fill=GOLD, width=10)
        draw.line([(corner_x, corner_y), (corner_x, corner_y + dy * arm)], fill=GOLD, width=10)


def _tracked_width(draw: ImageDraw.ImageDraw, text: str, font, tracking: float = 0.0) -> float:
    if not text:
        return 0.0
    return sum(_text_width(draw, ch, font) for ch in text) + tracking * max(0, len(text) - 1)


def _draw_page_head(draw: ImageDraw.ImageDraw, right_label: str) -> None:
    """页头品牌行 + 细分隔线。"""
    brand_font = _font(30, bold=True)
    draw.text((260, 196), APP_NAME, font=brand_font, fill=INK, anchor="la")
    small = _font(21)
    draw.text((260 + _text_width(draw, APP_NAME, brand_font) + 24, 208),
              "· 内部认证体系", font=small, fill=INK_SOFT, anchor="la")
    label_font = _font(21)
    label_w = _tracked_width(draw, right_label, label_font, 3)
    _tracked_text(draw, PAGE_W - 260 - label_w / 2, 206, right_label, label_font, GOLD, tracking=3)
    draw.line([(260, 262), (PAGE_W - 260, 262)], fill=LINE, width=2)


def _draw_ornament(draw: ImageDraw.ImageDraw, y: float, half_width: float = 640) -> None:
    """标题下方的金色双线 + 中央菱形。"""
    cx = PAGE_W / 2
    left_end = cx - 46
    draw.line([(cx - half_width, y), (left_end, y)], fill=GOLD_LIGHT, width=3)
    draw.line([(cx + 46, y), (cx + half_width, y)], fill=GOLD_LIGHT, width=3)
    draw.polygon([(cx, y - 13), (cx + 16, y), (cx, y + 13), (cx - 16, y)], fill=GOLD)
    draw.polygon([(cx, y - 6), (cx + 7, y), (cx, y + 6), (cx - 7, y)], fill=PAPER)


def _draw_seal(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float,
               data: CertificateData) -> None:
    """右下角圆形电子章：双环 + 五角星 + 环绕文字。"""
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 outline=SEAL, width=8)
    draw.ellipse((cx - radius + 18, cy - radius + 18, cx + radius - 18, cy + radius - 18),
                 outline=SEAL, width=2)
    # 五角星
    import math

    points = []
    for index in range(10):
        angle = math.radians(-90 + index * 36)
        r = radius * (0.30 if index % 2 == 0 else 0.12)
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=SEAL)
    label_font = _font(int(radius * 0.19), bold=True)
    draw.text((cx, cy + radius * 0.52), "内部认证", font=label_font, fill=SEAL, anchor="mm")
    _year, serial = archive_parts(data.archive_no)
    code = f"{serial:0{ARCHIVE_SERIAL_DIGITS}d}" if serial else ""
    if code:
        code_font = _latin_font(int(radius * 0.16))
        draw.text((cx, cy + radius * 0.78), code, font=code_font, fill=SEAL, anchor="mm")


def _rotated_text_layer(text: str, font, color: str, angle: float) -> Image.Image:
    """把一行字渲染成旋转后的透明图层，用于斜向状态水印。"""
    probe = Image.new("RGBA", (10, 10))
    box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font, anchor="lt")
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    layer = Image.new("RGBA", (width + 80, height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(((layer.width) / 2, (layer.height) / 2), text,
                               font=font, fill=color, anchor="mm")
    return layer.rotate(angle, expand=True, resample=Image.BICUBIC)


def _draw_status_band(page: Image.Image, data: CertificateData) -> None:
    """非「有效」状态：右上角胶囊标签 + 页面中央极淡斜向水印。"""
    if data.status in ("", "有效"):
        return
    color = data.status_color()
    base = page.convert("RGBA")

    # 胶囊标签：贴右侧内框内沿，不越界
    label_font = _font(30, bold=True)
    label_w = _text_width(ImageDraw.Draw(base), data.status, label_font) + 76
    right = PAGE_W - PAGE_MARGIN - FRAME_GAP // 2 - 34
    top = 300
    box = (right - label_w, top, right, top + 74)
    ImageDraw.Draw(base).rounded_rectangle(box, radius=37, fill=color + "e6")
    ImageDraw.Draw(base).text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), data.status,
                              font=label_font, fill="#ffffff", anchor="mm")

    # 斜向水印：低透明度，居中压在中部，不影响关键信息阅读
    mark = _rotated_text_layer(data.status, _font(150, bold=True, serif=True),
                               color + "1a", -16)
    base.alpha_composite(mark, (int((PAGE_W - mark.width) / 2),
                                int((PAGE_H - mark.height) / 2)))
    page.paste(base.convert("RGB"))


def _draw_footer(draw: ImageDraw.ImageDraw, page_no: int, total: int, data: CertificateData) -> None:
    font = _font(20)
    text = (
        f"{data.archive_no or '未编号'} · 内部认证证书 · 本证书为内部认证凭证，"
        f"不是著作权登记证书 · 第 {page_no}/{total} 页"
    )
    draw.text((PAGE_W / 2, PAGE_H - 196), text, font=font, fill=INK_SOFT, anchor="mm")


# ==================== 证书页 ====================


def render_certificate_page(data: CertificateData) -> Image.Image:
    """横版 A4 证书页：标题、作品、信息网格、分发层级、用途、期限、印章。"""
    page = _new_page()
    draw = ImageDraw.Draw(page)
    _draw_frame(draw)
    _draw_page_head(draw, "INTERNAL CERTIFICATION")

    cx = PAGE_W / 2
    # ---- 主标题 ----
    title_font = _font(150, bold=True, serif=True)
    _tracked_text(draw, cx, 300, "内部认证证书", title_font, INK, tracking=18)
    latin_font = _latin_font(28)
    _tracked_text(draw, cx, 500, "CERTIFICATE OF INTERNAL AUTHORIZATION",
                  latin_font, GOLD, tracking=9)
    _draw_ornament(draw, 566)

    no_font = _font(23)
    code_line = []
    if data.archive_no:
        code_line.append(f"内部认证号 {data.archive_no}")
    _tracked_text(draw, cx, 596, "　·　".join(code_line), no_font, INK_SOFT, tracking=1)

    # ---- 作品标题 ----
    label_font = _font(20)
    _tracked_text(draw, cx, 672, "授 权 作 品", label_font, GOLD, tracking=6)
    title_text = _clean(data.cert_title_text()) or "（未填写作品标题）"
    size = _fit_size(draw, title_text, PAGE_W - 900, 96, serif=True, bold=True, min_size=48)
    work_font = _font(size, bold=True, serif=True)
    draw.text((cx, 792), _ellipsize(draw, title_text, work_font, PAGE_W - 900),
              font=work_font, fill=INK, anchor="mm")

    # ---- 类型胶囊 ----
    chip_text = data.subtitle()
    if data.cert_type_text() == "其他" and _clean(data.art_type_other):
        chip_text = f"{data.art_type_other} · 二级分发授权"
    chip_font = _font(25)
    chip_w = _text_width(draw, chip_text, chip_font) + 76
    chip_box = (cx - chip_w / 2, 858, cx + chip_w / 2, 922)
    draw.rounded_rectangle(chip_box, radius=32, fill=PAPER_EDGE, outline=GOLD_LIGHT, width=2)
    draw.text((cx, 890), chip_text, font=chip_font, fill=INK_SOFT, anchor="mm")

    # ---- 信息网格（2 x 2）----
    grid_top, grid_bottom = 980, 1370
    grid_left, grid_right = 300, PAGE_W - 300
    mid_x = (grid_left + grid_right) / 2
    mid_y = (grid_top + grid_bottom) / 2
    draw.line([(mid_x, grid_top + 18), (mid_x, grid_bottom - 18)], fill=LINE, width=2)
    draw.line([(grid_left + 10, mid_y), (grid_right - 10, mid_y)], fill=LINE, width=2)

    cells = (
        ("授权方（美工老师）", data.cert_author_text() or "—"),
        ("主要设计人", data.cert_lead_text() or "—"),
        ("协作设计人", data.cert_co_text() or "未填写"),
        ("被授权方", data.licensee or DEFAULT_LICENSEE),
    )
    cell_w = (grid_right - grid_left) / 2
    cell_h = (grid_bottom - grid_top) / 2
    for index, (label, value) in enumerate(cells):
        col, row = index % 2, index // 2
        cell_cx = grid_left + cell_w * col + cell_w / 2
        cell_top = grid_top + cell_h * row
        _tracked_text(draw, cell_cx, cell_top + 42, label, _font(21), GOLD, tracking=2)
        value_font = _font(38, bold=True)
        draw.text((cell_cx, cell_top + 128),
                  _ellipsize(draw, value, value_font, cell_w - 80),
                  font=value_font, fill=INK, anchor="mm")

    # ---- 分发层级 ----
    tier_label_y = 1420
    _tracked_text(draw, cx, tier_label_y, "分 发 层 级", _font(20), GOLD, tracking=5)
    tiers = (
        ("一级分发方", "授权方（美工老师）", "著作权人 / 有权授权人"),
        ("二级分发方", "数据汇总库管理组", "本协议项下经授权"),
        ("三级分发方", "其他第三方", "须经授权方书面同意"),
    )
    card_gap = 56
    card_w = (PAGE_W - 600 - card_gap * 2) / 3
    card_top, card_h = 1478, 196
    for index, (tier, who, note) in enumerate(tiers):
        left = 300 + index * (card_w + card_gap)
        box = (left, card_top, left + card_w, card_top + card_h)
        draw.rounded_rectangle(box, radius=14, fill=PAPER_EDGE, outline=LINE, width=2)
        draw.text((left + 28, card_top + 24), tier, font=_font(22), fill=GOLD, anchor="la")
        who_font = _font(30, bold=True)
        draw.text((left + 28, card_top + 82),
                  _ellipsize(draw, who, who_font, card_w - 56), font=who_font, fill=INK, anchor="la")
        note_font = _font(20)
        draw.text((left + 28, card_top + 140),
                  _ellipsize(draw, note, note_font, card_w - 56), font=note_font,
                  fill=INK_SOFT, anchor="la")
        if index < len(tiers) - 1:
            arrow_x = left + card_w + card_gap / 2
            arrow_y = card_top + card_h / 2
            draw.line([(arrow_x - 12, arrow_y), (arrow_x + 12, arrow_y)], fill=GOLD, width=4)
            draw.polygon([(arrow_x + 16, arrow_y), (arrow_x + 2, arrow_y - 8),
                          (arrow_x + 2, arrow_y + 8)], fill=GOLD)

    # ---- 用途摘要 ----
    usage_top = 1736
    _tracked_text(draw, cx, usage_top, "授 权 用 途 摘 要", _font(20), GOLD, tracking=4)
    usage_font = _font(26)
    usage_lines = _wrap_text(draw, data.effective_usage(), usage_font, PAGE_W - 780, max_lines=2)
    for line_no, line in enumerate(usage_lines):
        draw.text((cx, usage_top + 58 + line_no * 44), line, font=usage_font,
                  fill=INK_SOFT, anchor="ma")

    # ---- 授权期限 ----
    period_top = 1888
    draw.line([(300, period_top), (PAGE_W - 300, period_top)], fill=LINE, width=2)
    _tracked_text(draw, 300 + 120, period_top + 36, "授权期限", _font(21), GOLD, tracking=2)
    period_font = _font(34, bold=True)
    draw.text((300 + 300, period_top + 74), data.period_text(), font=period_font,
              fill=INK, anchor="lm")
    if data.status == "已续签" and _clean(data.renew_until):
        draw.text((PAGE_W - 300, period_top + 74), f"续签至 {data.renew_until}",
                  font=_font(24), fill=STATUS_COLORS["已续签"], anchor="rm")

    # ---- 三方确认与印章 ----
    sign_top = 2040
    # 横线在印章左侧收尾，避免从印章中间穿过
    draw.line([(300, sign_top - 34), (PAGE_W - 760, sign_top - 34)], fill=LINE, width=2)

    def _confirm_block(x: float, label: str, name: str, date_text: str) -> None:
        draw.text((x, sign_top), label, font=_font(20), fill=GOLD, anchor="la")
        value = _ellipsize(draw, _clean(name) or "—", _font(26, bold=True, serif=True), 470)
        draw.text((x, sign_top + 40), value, font=_font(26, bold=True, serif=True),
                  fill=INK, anchor="la")
        draw.line([(x, sign_top + 92), (x + 520, sign_top + 92)], fill=INK_SOFT, width=2)
        draw.text((x, sign_top + 106), f"日期：{_clean(date_text) or '—'}",
                  font=_font(20), fill=INK_SOFT, anchor="la")

    _confirm_block(300, "授权方确认", data.author_sign or data.cert_author_text(),
                   data.author_sign_date)
    _confirm_block(1120, "被授权方确认", data.licensee_rep_sign or data.manager_rep,
                   data.licensee_sign_date)
    _confirm_block(1940, "签发方代表", data.signer,
                   data.issue_date or date.today().strftime("%Y-%m-%d"))

    _draw_seal(draw, PAGE_W - 470, sign_top + 60, 120, data)

    # ---- 底部说明 ----
    draw.text((cx, 2222),
              "本证书用于数据汇总库内部归档、检索与认证状态展示，不单独作为权利归属证明。",
              font=_font(21), fill=INK_SOFT, anchor="mm")
    _draw_footer(draw, 1, 2, data)
    _draw_status_band(page, data)
    return page


# ==================== 水印合成 ====================


def _text_layer(text: str, font, fill: tuple[int, int, int, int], angle: float) -> Image.Image:
    """把一行文字画在透明层上再旋转，避免直接旋转画布边缘裁字。"""
    probe = Image.new("RGBA", (10, 10))
    box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    layer = Image.new("RGBA", (width + 48, height + 48), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((24 - box[0], 24 - box[1]), text, font=font, fill=fill)
    return layer.rotate(angle, expand=True, resample=Image.BICUBIC)


def apply_watermark(image: Image.Image, text: str, *, angle: float = -28.0) -> Image.Image:
    """把署名 / 认证号以淡纹稀疏平铺到作品图上，右下角再压一个小标签。

    水印直接合成进图片（对应协议附件二：水印已合成在作品图片上，
    无需独立水印文件）；透明度与间距都收得比较轻，避免遮挡画面主体，
    同时保留「清晰可辨」的右下角标签。"""
    if not _clean(text):
        return image.convert("RGB")
    base = image.convert("RGBA")
    width, height = base.size
    short = max(1, min(width, height))
    ink = _rgb(INK)

    font_size = max(16, int(short * 0.034))
    while font_size > 15:
        font = _font(font_size, bold=True)
        probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        if probe.textlength(text, font=font) <= width * 0.6:
            break
        font_size -= 2
    font = _font(font_size, bold=True)
    tile = _text_layer(text, font, (*ink, 30), angle)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    step_x = int(tile.width * 1.45)
    step_y = int(tile.height * 3.4)
    row = 0
    y = -tile.height
    while y < height + tile.height:
        x = -tile.width + (step_x // 2 if row % 2 else 0)
        while x < width + tile.width:
            overlay.alpha_composite(tile, (int(x), int(y)))
            x += step_x
        y += step_y
        row += 1
    base.alpha_composite(overlay)

    # 右下角标签：半透明小标签，保证「图中清晰可辨」但不压住主体
    label_size = max(14, int(short * 0.019))
    label_font = _font(label_size, bold=True)
    label_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    label = _ellipsize(label_draw, text, label_font, width * 0.5)
    pad_x, pad_y = int(label_size * 0.7), int(label_size * 0.42)
    text_w = int(label_draw.textlength(label, font=label_font))
    box_w = text_w + pad_x * 2
    box_h = label_size + pad_y * 2
    margin = max(12, int(short * 0.018))
    plaque = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    plaque_draw = ImageDraw.Draw(plaque)
    plaque_draw.rounded_rectangle(
        (0, 0, box_w - 1, box_h - 1), radius=min(14, box_h // 3),
        fill=(255, 255, 255, 132), outline=(*_rgb(GOLD), 96), width=1,
    )
    plaque_draw.text((box_w / 2, box_h / 2), label, font=label_font,
                     fill=(*ink, 190), anchor="mm")
    base.alpha_composite(plaque, (width - box_w - margin, height - box_h - margin))
    return base.convert("RGB")


def export_watermarked_copies(images: Sequence[str | Path], data: CertificateData,
                              out_dir: str | Path) -> list[str]:
    """把水印合成到原图并另存「水印件」文件夹，返回文件路径。"""
    if not data.auto_watermark or not images:
        return []
    text = data.watermark_text()
    if not text:
        return []
    folder = Path(out_dir) / "水印件"
    folder.mkdir(parents=True, exist_ok=True)
    stem = "_".join(
        part for part in (
            image_render.safe_filename_part(data.archive_no, 40),
            image_render.safe_filename_part(data.title, 30),
        ) if part
    ) or "证书作品"
    results: list[str] = []
    for index, item in enumerate(images, start=1):
        source = _load_image(item)
        if source is None:
            continue
        marked = apply_watermark(source, text)
        target = folder / f"{stem}_已加水印_{index:02d}.png"
        marked.save(target, "PNG", dpi=(DPI, DPI), optimize=True)
        results.append(str(target))
    return results


# ==================== 图片页 ====================


def _grid_shape(count: int) -> tuple[int, int]:
    """按图片数量给网格：1 张整幅、2 张左右、3 张三列、4 张两列两行、5~6 张三列。"""
    if count <= 1:
        return 1, 1
    if count == 2:
        return 2, 1
    if count == 3:
        return 3, 1
    if count == 4:
        return 2, 2
    return 3, 2


def _load_image(path: str | Path) -> Image.Image | None:
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, "#ffffff")
                background.paste(image, mask=image.split()[-1])
                return background
            return image.convert("RGB")
    except (OSError, ValueError):
        return None


def _draw_empty_slot(draw: ImageDraw.ImageDraw, box: Sequence[float]) -> None:
    """虚线占位框。"""
    x1, y1, x2, y2 = box
    dash, gap = 18, 12
    for x in range(int(x1), int(x2), dash + gap):
        draw.line([(x, y1), (min(x + dash, x2), y1)], fill=LINE, width=2)
        draw.line([(x, y2), (min(x + dash, x2), y2)], fill=LINE, width=2)
    for y in range(int(y1), int(y2), dash + gap):
        draw.line([(x1, y), (x1, min(y + dash, y2))], fill=LINE, width=2)
        draw.line([(x2, y), (x2, min(y + dash, y2))], fill=LINE, width=2)
    draw.text(((x1 + x2) / 2, (y1 + y2) / 2), "未添加图片",
              font=_font(26), fill=INK_SOFT, anchor="mm")


def render_image_page(data: CertificateData, images: Sequence[str | Path]) -> Image.Image:
    """图片页：作品图 / 水印样本网格，保留完整画面不裁切。"""
    page = _new_page()
    draw = ImageDraw.Draw(page)
    _draw_frame(draw)
    _draw_page_head(draw, "WORK SAMPLES · WATERMARK")

    cx = PAGE_W / 2
    title_font = _font(72, bold=True, serif=True)
    _tracked_text(draw, cx, 330, "授权作品与水印样本", title_font, INK, tracking=8)
    _draw_ornament(draw, 460, half_width=520)

    sub_parts = [part for part in (data.cert_title_text(), data.cert_type_text(),
                                  data.archive_no) if part]
    draw.text((cx, 508), "　·　".join(sub_parts), font=_font(24), fill=INK_SOFT, anchor="mm")
    if _clean(data.credit_note):
        draw.text((cx, 552), f"署名写法：{data.credit_note}", font=_font(21),
                  fill=GOLD, anchor="mm")
    if data.auto_watermark and data.watermark_text():
        draw.text((PAGE_W - 300, 578), "图片水印由系统自动合成", font=_font(19),
                  fill=INK_SOFT, anchor="rm")

    area = (300, 620, PAGE_W - 300, 2080)
    paths = [Path(item) for item in images][:MAX_IMAGES]
    if not paths:
        slot_w, slot_h = 1500, 720
        slot = ((PAGE_W - slot_w) / 2, 780, (PAGE_W + slot_w) / 2, 780 + slot_h)
        _draw_empty_slot(draw, slot)
        draw.text((PAGE_W / 2, slot[3] + 56),
                  "在左侧图片列表加入作品图或水印样本后，这里会自动按数量排版",
                  font=_font(26), fill=INK_SOFT, anchor="ma")
    else:
        cols, rows = _grid_shape(len(paths))
        gap = 34
        area_w = area[2] - area[0]
        area_h = area[3] - area[1]
        cell_w = (area_w - gap * (cols - 1)) / cols
        cell_h_max = (area_h - gap * (rows - 1)) / rows
        caption_h = 64
        # 单元格高度按图片常见比例收敛，避免宽图被撑出大片白底
        ratios: list[float] = []
        for path in paths:
            try:
                with Image.open(path) as probe:
                    ratios.append(probe.width / max(1, probe.height))
            except (OSError, ValueError):
                continue
        median_ratio = sorted(ratios)[len(ratios) // 2] if ratios else 1.4
        median_ratio = max(0.4, min(3.0, median_ratio))
        cell_h = min(cell_h_max, cell_w / median_ratio + caption_h + 28)
        grid_h = cell_h * rows + gap * (rows - 1)
        grid_top = area[1] + (area_h - grid_h) / 2
        for index, path in enumerate(paths):
            col, row = index % cols, index // cols
            left = area[0] + col * (cell_w + gap)
            top = grid_top + row * (cell_h + gap)
            box = (left, top, left + cell_w, top + cell_h)
            draw.rounded_rectangle(box, radius=12, fill="#ffffff", outline=LINE, width=2)
            inner = (box[0] + 14, box[1] + 14, box[2] - 14, box[3] - caption_h - 6)
            image = _load_image(path)
            if image is None:
                _draw_empty_slot(draw, inner)
            else:
                fitted = ImageOps.contain(
                    image, (int(inner[2] - inner[0]), int(inner[3] - inner[1])),
                    Image.LANCZOS,
                )
                if data.auto_watermark:
                    fitted = apply_watermark(fitted, data.watermark_text())
                offset_x = int(inner[0] + (inner[2] - inner[0] - fitted.width) / 2)
                offset_y = int(inner[1] + (inner[3] - inner[1] - fitted.height) / 2)
                page.paste(fitted, (offset_x, offset_y))
                draw.rectangle((inner[0] - 1, inner[1] - 1, inner[2] + 1, inner[3] + 1),
                               outline="#ece5d6", width=1)
            caption = f"{index + 1:02d}　{_ellipsize(draw, path.name, _font(20), cell_w - 60)}"
            draw.text((box[0] + 22, box[3] - caption_h / 2 - 8), caption,
                      font=_font(20), fill=INK_SOFT, anchor="lm")
        if len(images) > MAX_IMAGES:
            draw.text((PAGE_W - 300, 2140),
                      f"另有 {len(images) - MAX_IMAGES} 张未排入本页，请另存",
                      font=_font(20), fill=STATUS_COLORS["已终止"], anchor="rm")

    _draw_footer(draw, 2, 2, data)
    _draw_status_band(page, data)
    return page


def render_pages(data: CertificateData, images: Sequence[str | Path] = ()) -> list[Image.Image]:
    """固定两页：证书页 + 图片页。"""
    return [render_certificate_page(data), render_image_page(data, images)]


# ==================== 导出 ====================


@dataclass
class ExportResult:
    png_paths: list[str] = field(default_factory=list)
    pdf_path: str = ""
    page_count: int = 0
    watermarked_paths: list[str] = field(default_factory=list)
    folder_path: str = ""     # 本次证书的专属文件夹
    zip_path: str = ""        # 该文件夹的附属 zip 包


# ==================== Word（docx）标准文档 ====================

def _docx_escape(text: str) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _docx_para(text: str, *, size: int = 22, bold: bool = False,
               align: str = "left", space_before: int = 0,
               space_after: int = 60, color: str | None = None) -> str:
    """单个段落（size 为半磅，22 ≈ 11pt）。"""
    props = (f'<w:pPr><w:spacing w:before="{space_before}" w:after="{space_after}"'
             f' w:line="360" w:lineRule="auto"/>'
             f'<w:jc w:val="{align}"/></w:pPr>')
    run_props = (f'<w:rPr><w:rFonts w:ascii="Microsoft YaHei" w:eastAsia="Microsoft YaHei"/>'
                 f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
                 + ('<w:b/>' if bold else "")
                 + (f'<w:color w:val="{color}"/>' if color else "")
                 + "</w:rPr>")
    pieces = []
    for index, line in enumerate(str(text or "").split("\n")):
        if index:
            pieces.append("<w:r><w:br/></w:r>")
        pieces.append(f'<w:r>{run_props}<w:t xml:space="preserve">'
                      f'{_docx_escape(line)}</w:t></w:r>')
    return f"<w:p>{props}{''.join(pieces)}</w:p>"


def _docx_certificate_block(data: CertificateData) -> list[str]:
    """证书页文字块（与 PNG 证书同字段，便于 Word 里再编辑）。"""
    out = [
        _docx_para("内部认证证书", size=56, bold=True, align="center", space_after=120),
        _docx_para("CERTIFICATE OF INTERNAL AUTHORIZATION", size=20,
                   align="center", space_after=200, color="8A6D3B"),
    ]
    if data.archive_no:
        out.append(_docx_para(f"内部认证号 {data.archive_no}", size=22,
                              align="center", space_after=60))
    fingerprint = _archive_hash_of(data.archive_no)
    if fingerprint:
        out.append(_docx_para(f"校验哈希 {fingerprint}（SHA-256 前 {ARCHIVE_HASH_LEN} 位）",
                              size=18, align="center", space_after=220, color="6B7280"))
    pairs = (
        ("授权作品", data.cert_title_text()),
        ("作品类型", data.cert_type_text()),
        ("作品对应歌曲/项目", _clean(data.project_name)),
        ("授权方", data.cert_author_text()),
        ("联系方式", _clean(data.author_contact)),
        ("主要设计人", data.cert_lead_text()),
        ("协作设计人", data.cert_co_text()),
        ("授权期限", f"{data.cert_period_start_text()} 至 {data.cert_period_end_text()}"),
        ("证书状态", _clean(data.status)),
        ("签发方", data.licensee),
        ("签发日期", _clean(data.issue_date) or date.today().isoformat()),
        ("协议编号", license_no(data.archive_no)),
    )
    for label, value in pairs:
        if not str(value or "").strip():
            continue
        out.append(_docx_para(f"{label}：{value}", size=24, space_after=100))
    out.append(_docx_para(
        "本证书为内部认证凭证，不是著作权登记证书。", size=18,
        align="center", space_before=240, color="6B7280"))
    return out


def _docx_body_paragraphs(text: str) -> list[str]:
    """把协议文本按行转成 Word 段落，识别标题 / 条款 / 附件小标题。"""
    out: list[str] = []
    for index, raw in enumerate(str(text or "").splitlines()):
        line = raw.strip()
        if not line:
            continue
        if index == 0:
            out.append(_docx_para(line, size=36, bold=True, align="center", space_after=80))
        elif line.startswith("（单作品"):
            out.append(_docx_para(line, size=20, align="center", space_after=260,
                                  color="8A6D3B"))
        elif line.startswith("附件一") or line.startswith("附件二"):
            out.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            out.append(_docx_para(line, size=28, bold=True, align="center",
                                  space_before=60, space_after=160))
        elif re.match(r"^第[一二三四五六七八九十]+条", line):
            out.append(_docx_para(line, size=25, bold=True, space_before=200, space_after=90))
        elif re.match(r"^[一二三四五六七八九十]+、", line):
            out.append(_docx_para(line, size=23, bold=True, space_before=140, space_after=70))
        elif line in ("授权方确认", "被授权方确认"):
            out.append(_docx_para(line, size=24, bold=True, space_before=220, space_after=80))
        else:
            out.append(_docx_para(line, size=21, space_after=60))
    return out


def _agreement_values(data: CertificateData) -> dict[str, str]:
    """协议全文占位符 → 软件当前字段值（冲突一律以软件为准）。"""
    def mark(flag: bool) -> str:
        return "☑" if flag else "□"

    art_type = data.art_type
    sample_format = data.sample_format
    usage_places = set(data.usage_places)
    watermark_items = set(data.watermark_items)
    return {
        "archive_no": _clean(data.archive_no),
        "license_no": license_no(data.archive_no),
        "author_name": _clean(data.author_name),
        "author_contact": _clean(data.author_contact),
        "manager_rep": _clean(data.manager_rep),
        "contact_person": _clean(data.contact_person),
        "title": data.cert_title_text(),
        "project_name": _clean(data.project_name),
        "art_type_other": _clean(data.art_type_other) if art_type == "其他" else "",
        "chk_art_illust": mark(art_type == "曲绘/插画"),
        "chk_art_cover": mark(art_type == "封面设计"),
        "chk_art_poster": mark(art_type == "海报"),
        "chk_art_other": mark(art_type == "其他"),
        "period_start": data.cert_period_start_text(),
        "period_end": data.cert_period_end_text(),
        "author_sign": _clean(data.author_sign) or data.cert_author_text(),
        "author_sign_date": _clean(data.author_sign_date),
        "licensee_rep_sign": _clean(data.licensee_rep_sign),
        "licensee_sign_date": _clean(data.licensee_sign_date),
        "confirm_no": data.confirm_no(),
        "watermark_file": data.cert_sample_text(),
        "sample_other": _clean(data.sample_other) if sample_format == "其他" else "",
        "chk_fmt_png": mark(sample_format == "PNG"),
        "chk_fmt_jpg": mark(sample_format == "JPG"),
        "chk_fmt_pdf": mark(sample_format == "PDF"),
        "chk_fmt_other": mark(sample_format == "其他"),
        "wm_author": data.wm_author_text(),
        "wm_lead": data.wm_lead_text(),
        "wm_co": data.wm_co_text(),
        "wm_title": data.wm_title_text(),
        "wm_archive_no": data.wm_archive_text(),
        "wm_other": _clean(data.wm_other),
        "chk_wm_author": mark("授权方署名/圈名" in watermark_items),
        "chk_wm_lead": mark("主要设计人署名" in watermark_items),
        "chk_wm_co": mark("协作设计人署名" in watermark_items),
        "chk_wm_title": mark("作品标题/项目名称" in watermark_items),
        "chk_wm_archive": mark("内部认证号" in watermark_items),
        "chk_wm_other": mark("其他约定标识" in watermark_items),
        "chk_use_db": mark("数据库条目展示" in usage_places),
        "chk_use_pub": mark("网络宣发" in usage_places),
        "chk_use_free": mark("非盈利无料" in usage_places),
        "chk_use_other": mark("其他" in usage_places),
        "usage_other": _clean(data.usage_other),
        "lead_designer": data.cert_lead_text(),
        "co_designers": data.cert_co_text(),
        "credit_note": _clean(data.credit_note),
    }


def export_docx(data: CertificateData, out_dir: str | Path) -> str:
    """导出 Word：证书页 + 完整协议（正文 + 附件一 + 附件二），可再编辑。

    正文用内置全文按软件字段填充：导入文档里的旧值不会覆盖当前录入，
    出现冲突时以软件生成为准。
    """
    import zipfile

    missing = missing_fields(data)
    if missing:
        raise ValueError("缺少必填字段：" + "、".join(missing))
    folder, stem = certificate_package(data, out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stem}.docx"

    body: list[str] = list(_docx_certificate_block(data))
    body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    body.append(_docx_para("协议正文", size=32, bold=True, align="center",
                           space_after=200))
    body.extend(_docx_body_paragraphs(
        certificate_agreement.agreement_text(_agreement_values(data))))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1247" w:bottom="1440" w:left="1247"/>'
        '</w:sectPr></w:body></w:document>'
    )
    temp = target.with_name(f".{target.stem}.tmp.docx")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    "</Types>")
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                    "</Relationships>")
        zf.writestr("word/document.xml", document)
    temp.replace(target)
    archive = folder.parent / f"{stem}.zip"
    if archive.is_file():
        _zip_package(folder, archive)
    return str(target)


def _save_png(image: Image.Image, path: Path) -> str:
    """300dpi PNG，先写临时文件再原子替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}.tmp.png")
    image.convert("RGB").save(temp, "PNG", dpi=(DPI, DPI), optimize=True)
    temp.replace(path)
    return str(path)


def certificate_package(data: CertificateData, out_dir: str | Path) -> tuple[Path, str]:
    """返回「认证号_作品名_内部认证证书」文件夹与其名称。"""
    out = Path(out_dir)
    stem = "_".join(
        part for part in (
            image_render.safe_filename_part(data.archive_no, 40),
            image_render.safe_filename_part(data.title, 40),
            "内部认证证书",
        ) if part
    )
    return out / stem, stem


def _zip_package(folder: Path, target: Path) -> str:
    """把证书文件夹整体打包（解压后仍是同名文件夹），先写临时文件再替换。"""
    import zipfile

    temp = target.with_name(f".{target.stem}.tmp.zip")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                arcname = f"{folder.name}/{path.relative_to(folder).as_posix()}"
                archive.write(path, arcname)
    temp.replace(target)
    return str(target)


def export_certificate(data: CertificateData, images: Sequence[str | Path],
                       out_dir: str | Path) -> ExportResult:
    """导出两页 PNG + PDF 到专属文件夹，并额外打一个同名 zip 包。"""
    missing = missing_fields(data)
    if missing:
        raise ValueError("缺少必填字段：" + "、".join(missing))
    pages = render_pages(data, images)
    folder, stem = certificate_package(data, out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    result = ExportResult(page_count=len(pages), folder_path=str(folder))
    result.watermarked_paths = export_watermarked_copies(images, data, folder)
    if result.watermarked_paths:
        data.watermark_file = data.watermark_file or Path(result.watermarked_paths[0]).name
        data.cert_sample_file = data.cert_sample_file or data.watermark_file
    for index, page in enumerate(pages, start=1):
        result.png_paths.append(_save_png(page, folder / f"{stem}_{index:02d}.png"))
    pdf_path = folder / f"{stem}.pdf"
    temp_pdf = pdf_path.with_name(f".{pdf_path.stem}.tmp.pdf")
    pages[0].convert("RGB").save(
        temp_pdf, "PDF", resolution=DPI, save_all=True,
        append_images=[page.convert("RGB") for page in pages[1:]],
    )
    temp_pdf.replace(pdf_path)
    result.pdf_path = str(pdf_path)
    result.zip_path = _zip_package(folder, folder.parent / f"{stem}.zip")
    return result