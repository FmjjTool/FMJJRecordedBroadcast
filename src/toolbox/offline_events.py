"""线下活动现场视频检索（L3）。

数据源：
- 活动清单：腾讯文档《封茗囧菌数据汇总库》本地快照 `tables.db` 的「线下活动」表
- B站：`platforms.video_search.search_bilibili`（游客优先、登录态兜底，只读）
- 抖音：项目已采集的 `download/已获取数据/抖音_文件` 归档（不触发登录/验证码/凭据回写）

本模块不依赖 Qt；页面只传参、收结果与进度文本。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from toolbox.config_manager import get_export_dir, get_export_subdir
from toolbox.tcdoc import tables as tcdoc_tables

TCDOC_DOC_KEY = "DRnd3bkhkQUR1aEJr"
TCDOC_TAB_NAME = "线下活动"
TCDOC_TAB_ID_FALLBACK = "ss_e1kkm2"
LIVE_FIELDS = ("现场LIVE 1", "现场LIVE 2", "现场LIVE 3")
DOUYIN_ARCHIVE_SUBDIR = "抖音_文件"

SCENE_WORDS = [
    "线下", "现场", "漫展", "演唱会", "舞台", "live", "Live", "LIVE", "直拍",
    "全程", "完整", "片段", "清唱", "巡演", "音乐节", "生日会", "生日场",
    "返场", "返图", "vlog", "Vlog", "观众", "饭拍", "站姐", "舞台混剪",
    "个唱", "个演", "演出", "开唱", "乐队", "巡", "签售", "录屏", "录像", "回放",
]
STRONG_LIVE = [
    "现场", "直拍", "舞台", "演出", "全程", "返图", "结束啦", "饭拍", "实拍",
    "视角", "开场", "返场", "合唱", "清唱", "舞台混剪", "live现场", "Live现场",
    "LIVE现场", "录屏",
]
CORE_LIVE = [
    "现场", "直拍", "舞台", "全程", "返图", "结束啦", "饭拍", "实拍", "视角",
    "开场", "返场", "合唱", "清唱", "舞台混剪", "录屏",
]
PROMO_WORDS = [
    "预告", "开票", "门票", "售票", "购票", "周边展示", "周边预览", "须知", "歌单",
    "座位", "进场", "领养", "直播通知", "会员", "福利", "预约", "上线", "定档",
    "购票链接", "票档", "抽奖", "招募", "通知", "公告", "开票时间", "剩余", "线下群",
    "偷跑", "入场", "演出时间", "演出地点", "预S", "大🌾", "名称：", "地址：", "日期：",
    "舞台时间", "活动信息",
]
OFFLINE_SCENE_WORDS = [
    "漫展", "现场", "舞台", "演唱会", "音乐节", "巡演", "签售", "演出",
    "活动", "文旅", "生日会", "生日场", "返场", "路演", "嘉年华",
]
GUEST_HOSTS = ["江辰", "等什么君", "音阙诗听", "徐泽", "小精灵", "翁杰Winjay", "翁杰", "双笙"]
KNOWN_CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "长沙", "重庆",
    "西安", "郑州", "苏州", "合肥", "厦门", "福州", "济南", "青岛", "沈阳", "大连",
    "天津", "昆明", "南昌", "赣州", "太原", "石家庄", "温州", "金华", "乌鲁木齐",
    "无锡", "常州", "徐州", "连云港", "兰州", "唐山", "湖州", "绍兴", "宁波", "佛山",
    "东莞", "珠海", "海口", "三亚", "贵阳", "南宁", "哈尔滨", "长春", "呼和浩特",
    "银川", "西宁", "拉萨", "开封", "洛阳", "景德镇", "陵水", "海南", "新疆",
]
GENERIC_TOKENS = {
    "线下", "活动", "现场", "嘉宾", "生日", "生日会", "生日场", "演唱会", "音乐节",
    "漫展", "动漫", "游戏", "嘉年华", "文化", "国风", "音乐", "站", "场", "日",
    "第", "届", "年", "月", "季", "夜", "节", "会", "祭", "盛典", "巡演", "巡",
    "专场", "live", "Live", "LIVE", "2024", "2025", "2026",
}
GENERIC_SPLIT = (
    r"动漫游戏嘉年华|动漫游戏展|动漫嘉年华|动漫展|动漫节|音乐嘉年华|音乐节|音乐会|"
    r"演唱会|生日专场|生日会|生日场|国风盛典|国风|次元文化|文化节|嘉年华|博览会|博览|"
    r"巡演|个唱|专场|盛典|活动|线下|漫展|游戏|巡"
)


class OfflineEventsUnavailable(RuntimeError):
    """本地快照缺失或为空。"""


@dataclass(frozen=True)
class OfflineEvent:
    date: str
    name: str
    province: str = ""
    city: str = ""
    venue: str = ""
    status: str = ""
    kind: str = ""
    songs: tuple[str, ...] = ()
    existing_links: tuple[tuple[str, str], ...] = ()

    @property
    def year(self) -> str:
        return self.date[:4]

    @property
    def key(self) -> str:
        return f"{self.date}|{self.name}"

    @property
    def display(self) -> str:
        return f"{self.date}｜{self.name}"


@dataclass
class VideoResult:
    source: str
    platform: str
    title: str
    author: str = ""
    duration: int = 0
    duration_text: str = "-"
    publish_time: str = ""
    kind: str = ""
    score: int = 0
    reasons: tuple[str, ...] = ()
    url: str = ""
    note: str = ""
    video_id: str = ""
    guest_only: bool = False
    daily_live: bool = False
    recommended: bool = False


@dataclass
class SearchOutcome:
    results: list[VideoResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def _has_any(text: str, words: Iterable[str]) -> bool:
    return any(w in text for w in words if w)


def _norm_date(value) -> str:
    s = str(value or "").strip()
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s[:10]


def _clean_city(city: str, province: str = "") -> str:
    s = (city or "").strip()
    s = re.sub(r"(黎族自治县|自治县|自治州|地区|市|县|区)$", "", s)
    s = re.sub(r"(黎族|苗族|土家族|藏族|彝族|蒙古族|回族|壮族|维吾尔族)$", "", s)
    if s in {"北京", "上海", "天津", "重庆"}:
        return s
    if not s:
        s = re.sub(r"(维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$", "", (province or "").strip())
    return s


def _province_short(province: str) -> str:
    return re.sub(r"(维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$", "", (province or "").strip())


def _split_songs(value) -> tuple[str, ...]:
    out: list[str] = []
    for s in re.split(r"[、,，]", str(value or "")):
        s = re.sub(r"[（(].*?[)）]", "", s).strip()
        if s and not re.fullmatch(r"^\d+\.?$", s) and s not in {"延期", "无曲目"}:
            if s not in out:
                out.append(s)
    return tuple(out)


def _existing_link(value) -> tuple[str, str] | None:
    if not value:
        return None
    text, url = tcdoc_tables.split_link(str(value))
    url = (url or "").strip()
    if not url:
        m = re.search(r"https?://\S+", str(value))
        if m:
            url = m.group(0).rstrip("）)】]")
    if not url:
        return None
    return ((text or "").strip() or url, url)


def _parse_event(values: dict) -> OfflineEvent | None:
    date = _norm_date(values.get("活动日期"))
    name = str(values.get("活动名称") or "").strip()
    if not date or not name:
        return None
    links = tuple(link for link in (_existing_link(values.get(k)) for k in LIVE_FIELDS) if link)
    return OfflineEvent(
        date=date, name=name,
        province=str(values.get("省份") or "").strip(),
        city=str(values.get("城市") or "").strip(),
        venue=str(values.get("场馆名称") or "").strip(),
        status=str(values.get("活动状态") or "").strip(),
        kind=str(values.get("活动类型") or "").strip(),
        songs=_split_songs(values.get("标准化曲目") or values.get("演唱曲目")),
        existing_links=links,
    )


def load_events(years: Iterable[str] | None = None) -> list[OfflineEvent]:
    """从腾讯文档本地快照读取线下活动；按日期倒序。"""
    try:
        sheets = tcdoc_tables.sheets_of(TCDOC_DOC_KEY)
    except Exception as e:  # noqa: BLE001 - 统一转化为可读错误
        raise OfflineEventsUnavailable(f"读取线下活动本地快照失败：{e}") from e
    sheet = next((s for s in sheets if s.get("tab_name") == TCDOC_TAB_NAME), None)
    if sheet is None:
        sheet = next((s for s in sheets if s.get("tab_id") == TCDOC_TAB_ID_FALLBACK), None)
    if sheet is None:
        raise OfflineEventsUnavailable("本地快照里没有「线下活动」表，请先在腾讯文档页同步表格")
    tab_id = sheet.get("tab_id") or TCDOC_TAB_ID_FALLBACK
    rows: list[dict] = []
    offset = 0
    while True:
        page, total = tcdoc_tables.rows_page(TCDOC_DOC_KEY, tab_id, offset, 500)
        rows.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    events = [ev for ev in (_parse_event(r.get("values") or {}) for r in rows) if ev]
    if years:
        keep = {str(y) for y in years}
        events = [ev for ev in events if ev.year in keep]
    events.sort(key=lambda e: (e.date, e.name), reverse=True)
    return events


def _aliases(event: OfflineEvent) -> list[str]:
    aliases: list[str] = []
    city = _clean_city(event.city, event.province)
    province = _province_short(event.province)
    for a in (city, province):
        if a and a not in aliases:
            aliases.append(a)
    if not city:
        for extra in ("乌鲁木齐", "新疆", "陵水", "海南"):
            if extra in event.name and extra not in aliases:
                aliases.append(extra)
    return aliases


def _event_tokens(event: OfflineEvent) -> list[str]:
    raw = re.sub(r"^\d{4}封茗囧菌[·\s]*", "", event.name)
    raw = re.sub(r"^封茗囧菌\d{4}[·\s]*", "", raw)
    raw = re.sub(r"^[^·｜]{2,8}·嘉宾·", "", raw)
    parts = re.split(r"[·｜\s/、,，:：\-—()（）\[\]「」『』“”\"']+", raw)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        part = re.sub(r"^第[一二三四五六七八九十\d]+届", "", part)
        part = re.sub(r"^\d{4}(年)?", "", part)
        part = re.sub(r"(20\d{2})$", "", part)
        for a in re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", part):
            if a not in out:
                out.append(a)
        for c in KNOWN_CITIES:
            part = part.replace(c, "")
        for sp in re.split(GENERIC_SPLIT, part):
            sp = sp.strip("的·-— ")
            if len(sp) < 2 or sp in GENERIC_TOKENS or sp in {"首场", "第六届"}:
                continue
            if re.fullmatch(r"[\d.]+", sp):
                continue
            if sp not in out:
                out.append(sp)
    if event.kind == "生日会" and "生日会" not in out:
        out.append("生日会")
    return out


def _guest_hosts(event: OfflineEvent) -> list[str]:
    return [h for h in GUEST_HOSTS if h in event.name]


def _event_datetime(event: OfflineEvent) -> datetime:
    return datetime.strptime(event.date, "%Y-%m-%d")


def _date_patterns(event: OfflineEvent) -> list[str]:
    d = _event_datetime(event)
    return [
        f"{d.year}.{d.month:02d}.{d.day:02d}", f"{d.year}.{d.month}.{d.day}",
        f"{d.year}-{d.month:02d}-{d.day:02d}", f"{d.year}年{d.month}月{d.day}日",
        f"{d.year}{d.month:02d}{d.day:02d}", f"{d.month}.{d.day}",
        f"{d.month}月{d.day}日", f"{d.month:02d}{d.day:02d}",
    ]


def _full_dates_in(text: str) -> set[tuple[int, int, int]]:
    dates: set[tuple[int, int, int]] = set()
    for m in re.finditer(r"(20\d{2})[.\-/年](\d{1,2})[.\-/月](\d{1,2})", text):
        dates.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"(20\d{2})(\d{2})(\d{2})", text):
        dates.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return dates


def build_keywords(event: OfflineEvent) -> list[str]:
    aliases = _aliases(event)
    city = aliases[0] if aliases else ""
    d = _event_datetime(event)
    md = f"{d.month}.{d.day}"
    tokens = _event_tokens(event)
    kws: list[str] = []
    type_word = {
        "漫展": "漫展", "音乐节": "音乐节", "演唱会": "演唱会",
        "生日会": "生日会", "花车巡游": "线下",
    }.get(event.kind, "线下")
    if city:
        kws.append(f"封茗囧菌 {city} {type_word}")
    kws.append(f"封茗囧菌 {event.name}")
    if city:
        kws.append(f"封茗囧菌 {md} {city}")
    if tokens and city:
        kws.append(f"封茗囧菌 {tokens[0]} {city}")
    if event.songs and city:
        kws.append(f"封茗囧菌 {event.songs[0]} {city}")
    if city:
        kws.append(f"封茗囧菌 {city} 现场")
    for host in _guest_hosts(event):
        if city:
            kws.append(f"{host} {city} {md}")
            kws.append(f"{host} {city} 全程")
        extra = [t for t in tokens if t != host]
        if extra and city:
            kws.append(f"{host} {extra[0]} {city}")
    out: list[str] = []
    for k in kws:
        k = re.sub(r"\s+", " ", k).strip()
        if k and k not in out:
            out.append(k)
    return out[:9] if _guest_hosts(event) else out[:7]


def fmt_duration(sec: int) -> str:
    sec = int(sec or 0)
    if sec <= 0:
        return "-"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def classify(duration: int, title: str) -> str:
    dur = int(duration or 0)
    if dur >= 1800:
        return "完整"
    if dur and dur < 600:
        return "片段"
    if dur >= 600:
        if _has_any(title, ["片段", "直拍", "cut", "Cut", "一首", "清唱", "饭拍"]) and dur < 1200:
            return "片段"
        return "中长片段"
    if _has_any(title, ["全程", "完整", "整场", "Full", "full"]):
        return "完整"
    if _has_any(title, ["片段", "直拍", "cut", "Cut", "一首", "清唱", "饭拍"]):
        return "片段"
    return "未知"


def _score_bili(info, event: OfflineEvent) -> tuple[int, list[str], bool, bool]:
    title = getattr(info, "title", "") or ""
    desc = (getattr(info, "description", "") or "")[:300]
    author = getattr(info, "author", "") or ""
    tags = " ".join(getattr(info, "tags", []) or [])
    duration = int(getattr(info, "duration", 0) or 0)
    publish = getattr(info, "publish_time", "") or ""
    scene_hits = [w for w in SCENE_WORDS if w.lower() in f"{title} {desc}".lower()]
    daily_live = "直播" in title and not _has_any(title, OFFLINE_SCENE_WORDS)
    if daily_live:
        return -99, ["日常直播，非线下现场"], False, False
    aliases = _aliases(event)
    guest_hosts = _guest_hosts(event)
    evd = _event_datetime(event)

    name_hit = _has_any(f"{title} {desc} {author} {tags}", ["封茗囧菌", "囧菌"]) or "囧菌" in author
    host_hits = [h for h in guest_hosts if h in f"{title} {desc} {author} {tags}"]
    guest_only = not name_hit
    if guest_only and not host_hits:
        return -99, ["未提及囧菌"], False, False

    years = [int(y) for y in re.findall(r"(20\d{2})", title)]
    if years and evd.year not in years:
        return -99, [f"年份不符:{years}"], False, False
    fds = _full_dates_in(title)
    if fds and (evd.year, evd.month, evd.day) not in fds:
        return -99, [f"日期不符:{sorted(fds)[:2]}"], False, False
    tour_markers = {"一巡", "二巡", "三巡", "首巡"}
    title_tours = {m for m in tour_markers if m in title}
    event_tours = {m for m in tour_markers if m in event.name}
    if title_tours and not (title_tours & event_tours):
        return -99, [f"巡次不符:{sorted(title_tours)}"], False, False
    for day in ("Day1", "Day2"):
        if day.lower() in title.lower() and day.lower() not in event.name.lower():
            return -99, [f"场次不符:{day}"], False, False

    wrong_cities = [c for c in KNOWN_CITIES
                    if c in title and not any(c in a or a in c for a in aliases)]
    city_hits = [c for c in aliases if c and c in f"{title} {desc} {author} {tags}"]
    token_hits = [t for t in _event_tokens(event)
                  if t and t.lower() in f"{title} {desc} {author} {tags}".lower()]
    date_hits = [p for p in _date_patterns(event) if p in f"{title} {desc} {author} {tags}"]
    title_city_hits = [c for c in aliases if c and c in title]
    title_token_hits = [t for t in _event_tokens(event) if t and t.lower() in title.lower()]
    title_date_hits = [p for p in date_hits if p in title]
    title_full_date = any(p in title for p in date_hits[:5])
    city_name = _clean_city(event.city, event.province)
    if city_name and f"{city_name}场" in title:
        scene_hits.append("场次")
    if city_name and f"{city_name}站" in title:
        scene_hits.append("分站")

    if wrong_cities and not title_city_hits:
        return -99, ["标题含其他城市:" + "/".join(wrong_cities)], False, False
    if _has_any(title, PROMO_WORDS) and not _has_any(title, CORE_LIVE):
        return -99, ["宣传/通知类"], False, False

    try:
        pub = datetime.strptime(publish[:10], "%Y-%m-%d")
        if pub <= evd and _has_any(title, ["下一站", "明天见", "马上见", "今晚见",
                                           "待会儿见", "期待", "下次见", "倒计时"]):
            if not scene_hits:
                return -99, ["预告/下一站"], False, False
        if pub.year != evd.year and not any(y == evd.year for y in years) and abs((pub - evd).days) > 10:
            return -99, ["发布年份不符"], False, False
        if (not title_city_hits and not title_full_date
                and not title_date_hits and abs((pub - evd).days) > 2):
            return -99, ["仅活动词匹配且发布日远离"], False, False
        if (title_city_hits and not title_token_hits and not title_full_date
                and not title_date_hits and abs((pub - evd).days) > 45):
            return -99, ["仅标题城市匹配且发布日远离"], False, False
    except Exception:
        pass

    signal_pairs = [
        (city_hits, date_hits), (city_hits, scene_hits), (city_hits, token_hits),
        (date_hits, scene_hits), (date_hits, token_hits), (token_hits, scene_hits),
    ]
    if not any(a and b for a, b in signal_pairs):
        return -99, ["有效信号不足"], False, False

    if guest_only:
        if not (host_hits and scene_hits and (date_hits or token_hits) and (city_hits or date_hits)):
            return -99, ["嘉宾场信号不足"], False, False
        if duration < 1200:
            return -99, ["嘉宾短直拍未收录"], False, False
        if not (_has_any(title, ["全程", "完整", "录像", "录屏", "回放", "直拍"]) or duration >= 1800):
            return -99, ["嘉宾场非完整录像"], False, False
        score = 8
        reasons = ["嘉宾场:" + "/".join(host_hits), "标题/简介未提囧菌，需确认含其片段"]
        if city_hits:
            score += 2
            reasons.append("城市:" + "/".join(city_hits))
        if date_hits:
            score += 2
            reasons.append("日期:" + date_hits[0])
        if duration >= 1800:
            score += 2
        return score, reasons, True, daily_live

    score = 0
    reasons: list[str] = []
    if city_hits:
        score += 5
        reasons.append("城市:" + "/".join(city_hits))
    if token_hits:
        score += 6
        reasons.append("活动:" + "/".join(token_hits[:3]))
    if host_hits:
        score += 2
        reasons.append("同场嘉宾:" + "/".join(host_hits))
    if date_hits:
        full_hit = [p for p in date_hits[:5] if p in title]
        score += 4 if full_hit else 2
        reasons.append("日期:" + (full_hit[0] if full_hit else date_hits[0]))
    score += min(3, len(scene_hits))
    reasons.append("场景:" + "/".join(scene_hits[:4]))
    if duration >= 1800:
        score += 3
    elif duration >= 900:
        score += 2
    elif duration >= 300:
        score += 1
    try:
        pub = datetime.strptime(publish[:10], "%Y-%m-%d")
        if evd - timedelta(days=30) <= pub <= evd + timedelta(days=120):
            score += 2
            reasons.append("发布临近")
    except Exception:
        pass
    if _has_any(author, ["囧菌", "录播", "记录", "守护", "资讯", "搬运", "直播"]):
        score += 2
        reasons.append("上传者:" + author)
    if _has_any(title, ["直播回放", "直播录屏", "直播录像"]) and not _has_any(
            title, ["漫展", "现场", "演唱会", "舞台", "音乐节"]):
        score -= 6
        reasons.append("疑似日常直播")
    if wrong_cities and city_hits:
        score -= 4
        reasons.append("标题含其他城市:" + "/".join(wrong_cities))
    return score, reasons, False, daily_live


def _recap_city(title: str) -> str:
    for marker in ("结束啦", "谢幕啦", "圆满成功", "收官"):
        idx = title.find(marker)
        if idx < 0:
            continue
        prefix = title[max(0, idx - 16):idx]
        hits = [c for c in KNOWN_CITIES if c in prefix]
        if hits:
            return hits[-1]
    return ""


def _score_douyin(post: dict, event: OfflineEvent) -> tuple[int, list[str], str]:
    title = post.get("title") or ""
    author = post.get("author") or ""
    text = f"{title} {author}"
    typ = post.get("type") or "视频"
    if "视频" not in typ:
        return -99, ["非视频"], ""
    if _has_any(title, ["名称：", "地址：", "舞台时间", "入场时间", "演出时间", "演出地点", "日期："]) \
            and not _has_any(title, ["结束啦", "直拍", "全程", "返图", "饭拍", "实拍", "录屏"]):
        return -99, ["活动信息/宣传"], ""
    promo = _has_any(title, PROMO_WORDS)
    strong_live = [w for w in STRONG_LIVE if w.lower() in text.lower()]
    if promo and not _has_any(title, CORE_LIVE):
        return -99, ["宣传/通知类"], ""
    if "直播" in title and not _has_any(title, OFFLINE_SCENE_WORDS):
        return -99, ["日常直播，非线下现场"], ""

    name_hit = _has_any(text, ["封茗囧菌", "囧菌"])
    if not name_hit:
        return -99, ["未提及囧菌"], ""
    evd = _event_datetime(event)
    years = [int(y) for y in re.findall(r"(20\d{2})", title)]
    if years and evd.year not in years:
        return -99, [f"年份不符:{years}"], ""
    fds = _full_dates_in(title)
    if fds and (evd.year, evd.month, evd.day) not in fds:
        return -99, [f"日期不符:{sorted(fds)[:2]}"], ""

    aliases = _aliases(event)
    wrong_cities = [c for c in KNOWN_CITIES
                    if c in title and not any(c in a or a in c for a in aliases)]
    city_hits = [c for c in aliases if c and c in text]
    token_hits = [t for t in _event_tokens(event) if t and t.lower() in text.lower()]
    date_hits = [p for p in _date_patterns(event) if p in text]
    title_city_hits = [c for c in aliases if c and c in title]
    title_token_hits = [t for t in _event_tokens(event) if t and t.lower() in title.lower()]
    title_date_hits = [p for p in date_hits if p in title]
    title_full_date = any(p in title for p in date_hits[:5])
    for day in ("Day1", "Day2"):
        if day.lower() in title.lower() and day.lower() not in event.name.lower():
            return -99, [f"场次不符:{day}"], ""
    if wrong_cities and not title_city_hits:
        return -99, ["标题含其他城市:" + "/".join(wrong_cities)], ""
    rc = _recap_city(title)
    if rc and rc not in aliases:
        return -99, [f"上一站回顾:{rc}"], ""

    try:
        pub = datetime.strptime((post.get("publish_time") or "")[:10], "%Y-%m-%d")
        if pub <= evd and _has_any(title, ["下一站", "明天", "马上见", "今晚见",
                                           "待会儿见", "期待", "下次见", "倒计时", "速来"]) \
                and not strong_live:
            return -99, ["预告/下一站"], ""
        if pub.year != evd.year and not any(y == evd.year for y in years) and abs((pub - evd).days) > 10:
            return -99, ["发布年份不符"], ""
        if (not title_city_hits and not title_full_date
                and not title_date_hits and abs((pub - evd).days) > 2):
            return -99, ["仅活动词匹配且发布日远离"], ""
        if (title_city_hits and not title_token_hits and not title_full_date
                and not title_date_hits and abs((pub - evd).days) > 45):
            return -99, ["仅标题城市匹配且发布日远离"], ""
    except Exception:
        pass

    signal_pairs = [
        (city_hits, date_hits), (city_hits, strong_live), (city_hits, token_hits),
        (date_hits, strong_live), (date_hits, token_hits), (token_hits, strong_live),
    ]
    if not any(a and b for a, b in signal_pairs):
        return -99, ["有效信号不足"], ""

    score = 0
    reasons: list[str] = []
    if city_hits:
        score += 5
        reasons.append("城市:" + "/".join(city_hits))
    if token_hits:
        score += 6
        reasons.append("活动:" + "/".join(token_hits[:3]))
    if date_hits:
        full_hit = [p for p in date_hits[:5] if p in title]
        score += 4 if full_hit else 2
        reasons.append("日期:" + (full_hit[0] if full_hit else date_hits[0]))
    score += min(3, len(strong_live))
    reasons.append("场景:" + "/".join(strong_live[:4]))
    if strong_live:
        score += 3
    if _has_any(author, ["封茗囧菌", "囧菌", "资讯站", "的啊团", "录播", "记录", "守护"]):
        score += 2
        reasons.append("账号:" + author)
    try:
        pub = datetime.strptime((post.get("publish_time") or "")[:10], "%Y-%m-%d")
        if evd - timedelta(days=30) <= pub <= evd + timedelta(days=30):
            score += 2
            reasons.append("发布临近")
    except Exception:
        pass
    kind = "现场/回顾" if strong_live else "相关"
    return score, reasons, kind


_DOUYIN_CACHE: dict = {"ts": 0.0, "posts": []}
_DOUYIN_TTL = 600


def load_douyin_posts(force: bool = False) -> list[dict]:
    """读取本地抖音归档（去重视频帖），10 分钟缓存。"""
    now = time.time()
    if not force and _DOUYIN_CACHE["posts"] and now - _DOUYIN_CACHE["ts"] < _DOUYIN_TTL:
        return _DOUYIN_CACHE["posts"]
    base = get_export_subdir("video_data") / DOUYIN_ARCHIVE_SUBDIR
    posts: dict[str, dict] = {}
    if base.exists():
        for path in base.rglob("*.json"):
            if path.name.endswith("_raw.json"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                vid = str(item.get("视频ID") or "").strip()
                if not vid or vid in posts:
                    continue
                posts[vid] = {
                    "video_id": vid,
                    "title": item.get("标题") or item.get("正文") or "",
                    "author": item.get("作者") or "",
                    "publish_time": item.get("发布时间") or "",
                    "type": item.get("类型") or "",
                    "link": f"https://www.douyin.com/video/{vid}",
                    "likes": item.get("点赞数") or 0,
                }
    out = list(posts.values())
    _DOUYIN_CACHE.update({"ts": now, "posts": out})
    return out


def _default_bili_search(keyword: str, count: int):
    from platforms.video_search import search_bilibili
    return search_bilibili(keyword, count)


def _user_error(exc: Exception) -> str:
    text = str(exc or "").strip() or exc.__class__.__name__
    text = text.replace("「Cookie 管理」", "「账号与登录」").replace("Cookie 管理", "账号与登录")
    text = re.sub(r"(?i)cookie", "登录态", text)
    return text[:160]


def _kind_rank(kind: str) -> int:
    if "已登记" in kind:
        return 0
    if "完整" in kind:
        return 1
    if "中长" in kind:
        return 2
    if "现场" in kind:
        return 3
    if "片段" in kind:
        return 4
    return 5


def _dedupe_key(result: VideoResult) -> tuple[str, str]:
    return result.source, result.video_id or result.url


def search_event_videos(
    event: OfflineEvent,
    platforms: Iterable[str] = ("bilibili", "douyin_archive"),
    *,
    bili_search: Callable[[str, int], list] | None = None,
    douyin_posts: list[dict] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancel=None,
) -> SearchOutcome:
    """搜索单场活动的现场视频。

    `bili_search` / `douyin_posts` 供测试注入；默认使用真实适配器与本地归档。
    `cancel` 为 threading.Event 风格对象（有 is_set 方法）。
    """
    platforms = tuple(platforms)
    outcome = SearchOutcome()
    results: list[VideoResult] = []
    seen: set[tuple[str, str]] = set()

    def add(result: VideoResult):
        key = _dedupe_key(result)
        if key in seen:
            return
        seen.add(key)
        results.append(result)

    for title, url in event.existing_links:
        add(VideoResult(source="existing", platform="库内", title=title, url=url,
                        kind="已登记", score=999, reasons=("数据库「现场 LIVE」已登记",),
                        note="已在库内登记"))

    keywords = build_keywords(event) if "bilibili" in platforms else []
    total = len(keywords) + (1 if "douyin_archive" in platforms else 0)
    done = 0

    def emit(text: str):
        if progress:
            try:
                progress(done, max(total, 1), text)
            except Exception:
                pass

    if "bilibili" in platforms:
        search = bili_search or _default_bili_search
        for kw in keywords:
            if cancel is not None and cancel.is_set():
                outcome.cancelled = True
                break
            emit(f"B站：{kw}")
            try:
                items = search(kw, 20)
            except Exception as e:  # noqa: BLE001
                outcome.errors.append(f"B站「{kw}」：{_user_error(e)}")
                done += 1
                continue
            for info in items or []:
                score, reasons, guest_only, daily_live = _score_bili(info, event)
                if score < 0:
                    continue
                add(VideoResult(
                    source="bilibili", platform="B站",
                    title=getattr(info, "title", "") or "",
                    author=getattr(info, "author", "") or "",
                    duration=int(getattr(info, "duration", 0) or 0),
                    duration_text=fmt_duration(int(getattr(info, "duration", 0) or 0)),
                    publish_time=getattr(info, "publish_time", "") or "",
                    kind=classify(int(getattr(info, "duration", 0) or 0), getattr(info, "title", "") or ""),
                    score=score, reasons=tuple(reasons),
                    url=getattr(info, "video_url", "") or "",
                    video_id=getattr(info, "video_id", "") or "",
                    guest_only=guest_only, daily_live=daily_live,
                    note="嘉宾场录像，标题/简介未提囧菌，需确认含其片段" if guest_only else
                         ("疑似日常直播，需确认是否为现场" if daily_live else ""),
                ))
            done += 1

    if "douyin_archive" in platforms and not outcome.cancelled:
        emit("抖音：本地归档")
        try:
            posts = douyin_posts if douyin_posts is not None else load_douyin_posts()
        except Exception as e:  # noqa: BLE001
            posts = []
            outcome.errors.append(f"抖音本地归档：{_user_error(e)}")
        for post in posts or []:
            if cancel is not None and cancel.is_set():
                outcome.cancelled = True
                break
            score, reasons, kind = _score_douyin(post, event)
            if score < 0:
                continue
            add(VideoResult(
                source="douyin_archive", platform="抖音（本地归档）",
                title=post.get("title") or "", author=post.get("author") or "",
                publish_time=post.get("publish_time") or "",
                kind=kind, score=score, reasons=tuple(reasons),
                url=post.get("link") or "",
                video_id=post.get("video_id") or "",
            ))
        done += 1
        emit("整理结果")

    source_rank = {"existing": 0, "bilibili": 1, "douyin_archive": 3}
    results.sort(key=lambda r: (
        2 if r.guest_only else source_rank.get(r.source, 4),
        _kind_rank(r.kind),
        -r.score,
        r.publish_time or "",
    ))
    verified = [r for r in results
                if r.source == "bilibili" and not r.guest_only and not r.daily_live
                and "需人工归场" not in r.note and "未标 Day" not in r.note]
    picks = [r for r in verified
             if r.duration >= 600 or "完整" in r.kind or "中长" in r.kind][:2]
    for r in picks:
        r.recommended = True
    outcome.results = results
    return outcome


def _safe_filename(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text or "").strip(" ._")
    return (text[:limit] or "线下活动").strip("._")


def export_results_xlsx(event: OfflineEvent, results: list[VideoResult],
                        target: Path | str | None = None) -> Path:
    """导出当前结果为 Excel，默认写到 导出目录/线下现场视频/。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if target is None:
        out_dir = get_export_dir() / "线下现场视频"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = out_dir / f"封茗囧菌_线下现场视频_{event.date}_{_safe_filename(event.name)}_{stamp}.xlsx"
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "视频清单"
    headers = ["日期", "活动", "城市", "平台", "类别", "标题", "作者", "时长",
               "发布日期", "匹配依据", "备注", "推荐", "链接"]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in results:
        ws.append([
            event.date, event.name, _clean_city(event.city, event.province),
            r.platform, r.kind, r.title, r.author, r.duration_text,
            (r.publish_time or "")[:10], "；".join(r.reasons), r.note,
            "推荐" if r.recommended else "", r.url,
        ])
    widths = {"A": 11, "B": 38, "C": 9, "D": 15, "E": 10, "F": 55, "G": 16,
              "H": 9, "I": 11, "J": 38, "K": 30, "L": 7, "M": 45}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(headers)):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        link_cell = row[-1]
        if link_cell.value:
            link_cell.hyperlink = link_cell.value
            link_cell.font = Font(color="0563C1", underline="single")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
    ws.row_dimensions[1].height = 28
    wb.save(path)
    return path
