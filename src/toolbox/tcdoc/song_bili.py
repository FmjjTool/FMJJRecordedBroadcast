"""B站歌曲链接匹配：歌名 + 演唱者 → B站视频链接（搜索 + 打分，不静默取首条）。

B站没有「歌曲实体」，同名翻唱 / 搬运 / 切片很多，直接取搜索结果第一条必然错链，
所以只在高置信时给出链接：

  歌名命中（标题归一化后包含歌名）
  + 时长一致（与音乐平台给的歌曲时长相差 ≤ 3 秒）
  + 演唱者命中（标题或 UP 主含演唱者）

三条同时成立且候选唯一时才算「已匹配」；其余一律 state=ambiguous/none，链接留空，
把候选清单交给页面上的「B站候选」下拉人工选，页面不弹窗。

搜索默认走 platforms.video_search.search_bilibili（WBI search/type，游客被限流时
自动接 Cookie 重试）；searcher 参数可注入，离线单测不触网。
"""

from __future__ import annotations

import html
import re

BILI_VIDEO_URL = "https://www.bilibili.com/video/{bvid}"
# 秒；文档里的歌曲时长来自音乐平台，B站视频常带前奏/尾奏静音，实测真值相差 0~8 秒，
# 超过 10 秒就算「不符」，只留候选让人工选。
DURATION_TOLERANCE = 10
DURATION_NEAR = 25          # 秒；只算「接近」，不给高置信
MAX_CANDIDATES = 20

# 演唱者多值分隔：文档里常见「封茗囧菌、双笙」，标题里可能写「双笙&封茗囧菌」
_SINGER_SPLIT_RE = re.compile(r"[、,，/／&＆+＋|｜·・]|\s+vs\.?\s+|\s+feat\.?\s+", re.I)

# 标题里的版本/画质/搬运噪声：归一化时去掉，避免「4K修复版」之类干扰歌名比对
_NOISE_WORDS = (
    "动态歌词", "歌词版", "纯享版", "完整版", "高音质", "无损音质", "无损",
    "1080p", "720p", "2160p", "超清", "高清", "修复版", "修复", "重制版", "重制",
    "搬运", "切片", "片段", "合集", "投稿", "官方", "原曲", "纯音乐", "伴奏",
    "翻唱", "翻自", "现场", "音频", "歌曲", "完整", "版",
)
# 英文噪声按词边界匹配，避免误伤 Alive / Movie 这类歌名
_NOISE_ASCII_RE = re.compile(
    r"(?<![a-z])(?:hires|hi-res|4k|8k|instrumental|off\s*vocal|offvocal|cover|mv|pv|live)"
    r"(?![a-z])", re.I)
_ACCOMPANIMENT_RE = re.compile(r"伴奏|纯音乐|instrumental|off\s*vocal|无人声", re.I)
_COVER_RE = re.compile(r"翻唱|cover|翻自", re.I)
# 只去圆括号/方括号/书名号外的成对符号；《》「」『』在中文标题里是歌名本身，必须保留
_BRACKET_RE = re.compile(r"[（(【\[［{｛][^）)】\]］}｝]*[）)】\]］}｝]")


# ==================== 归一化 ====================

def _strip_brackets(text: str) -> str:
    """去掉成对括号及其内容（可能嵌套，多跑几轮）。"""
    changed = True
    while changed:
        changed = False
        new = _BRACKET_RE.sub(" ", text)
        if new != text:
            text, changed = new, True
    return text


def clean_title(raw) -> str:
    """B站标题 / 歌名 → 只留主体文字（小写、去括号补充、去版本噪声、去标点）。"""
    text = str(raw or "").lower()
    text = _strip_brackets(text)
    text = _NOISE_ASCII_RE.sub(" ", text)
    for word in _NOISE_WORDS:
        text = text.replace(word, " ")
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def singer_parts(singer) -> list[str]:
    """「封茗囧菌、双笙」→ ['封茗囧菌', '双笙']；单个名字原样返回。"""
    text = str(singer or "").strip()
    parts = [part.strip() for part in _SINGER_SPLIT_RE.split(text) if part.strip()]
    return parts or ([text] if text else [])


def seconds(value) -> int:
    """时长归一化：int / float / "3:45" / "1:03:45" → 秒；无法识别返回 0。"""
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").strip()
    if not text:
        return 0
    if ":" in text:
        parts = [p for p in text.split(":") if p.strip().isdigit()]
        try:
            numbers = [int(p) for p in parts]
        except ValueError:
            return 0
        total = 0
        for number in numbers:
            total = total * 60 + number
        return total
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return 0


def _value(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def candidate_of(item) -> dict:
    """搜索结果（VideoInfo 或等形 dict）→ 匹配用候选。"""
    bvid = str(_value(item, "video_id", "") or "").strip()
    url = str(_value(item, "video_url", "") or "").strip()
    if not url and bvid:
        url = BILI_VIDEO_URL.format(bvid=bvid)
    try:
        play = int(_value(item, "play_count", 0) or 0)
    except (TypeError, ValueError):
        play = 0
    return {
        "title": html.unescape(re.sub(r"<[^>]+>", "",
                                      str(_value(item, "title", "") or ""))).strip(),
        "author": str(_value(item, "author", "") or "").strip(),
        "url": url,
        "duration": seconds(_value(item, "duration", 0)),
        "play": play,
        "cover": str(_value(item, "cover_url", "") or "").strip(),
    }


# ==================== 打分 / 置信度 ====================

def evaluate(title: str, singer: str, candidate: dict, duration=0) -> dict:
    """单个候选 → 事实 + 分数 + 置信度；歌名不命中的直接判 0 分。"""
    target = clean_title(title)
    key = clean_title(candidate.get("title"))
    raw_title = str(candidate.get("title") or "").lower()
    author = str(candidate.get("author") or "").lower()
    song_dur = seconds(duration)
    cand_dur = seconds(candidate.get("duration"))
    reasons: list[str] = []

    parts = [part.lower() for part in singer_parts(singer)]
    # 标题里带演唱者前缀时（「【封茗囧菌】某某」）把名字剔掉再比，避免漏判「标题一致」
    key_sans_singer = key
    for part in parts:
        key_sans_singer = key_sans_singer.replace(clean_title(part), "")

    exact = bool(target) and (key == target or key_sans_singer == target)
    hit = bool(target) and (target in key or (key and key in target))
    if not hit:
        return {"candidate": candidate, "score": 0, "confidence": "none",
                "title_hit": False, "singer_hit": False, "duration_diff": None,
                "reasons": ["标题不含歌名"]}

    score = 100 if exact else 60
    reasons.append("标题一致" if exact else "标题包含歌名")

    singers_in_title = [part for part in parts if part and part in raw_title]
    singers_in_author = [part for part in parts if part and part in author]
    singer_hit = bool(singers_in_title or singers_in_author)
    if singers_in_title:
        score += 30
        reasons.append(f"标题含演唱者（{singers_in_title[0]}）")
    elif singers_in_author:
        score += 30
        reasons.append("UP主为演唱者")
    author_hit = bool(singers_in_author)

    duration_diff = None
    duration_ok = False
    if song_dur and cand_dur:
        duration_diff = abs(cand_dur - song_dur)
        if duration_diff <= DURATION_TOLERANCE:
            score += 40
            duration_ok = True
            reasons.append(f"时长一致 {cand_dur}s")
        elif duration_diff <= DURATION_NEAR:
            score += 10
            reasons.append(f"时长接近差 {duration_diff}s")
        else:
            score -= 40
            reasons.append(f"时长不符差 {duration_diff}s")

    conflict = False
    if _ACCOMPANIMENT_RE.search(raw_title) and not _ACCOMPANIMENT_RE.search(str(title or "")):
        score -= 60
        conflict = True
        reasons.append("疑似伴奏")
    # 别人标的「翻唱」不是这首歌本身；本人（演唱者命中）的翻唱投稿就是该链接
    cover_flag = False
    if (_COVER_RE.search(raw_title) and not _COVER_RE.search(str(title or ""))
            and not singer_hit):
        score -= 20
        cover_flag = True
        reasons.append("他人翻唱")

    # 高置信必须同时满足：歌名命中 + 演唱者命中 + 时长一致，且不是伴奏/他人翻唱。
    # 实测只靠「标题一致」会命中洛天依原创版之类的同名他人作品，所以演唱者不可省。
    if hit and not conflict and not cover_flag and duration_ok and singer_hit:
        confidence = "high"
    elif hit and (duration_ok or singer_hit):
        confidence = "medium"
    else:
        confidence = "low"
    return {"candidate": candidate, "score": score, "confidence": confidence,
            "title_hit": True, "singer_hit": singer_hit, "author_hit": author_hit,
            "duration_diff": duration_diff, "reasons": reasons}


def pick(title: str, singer: str, candidates, duration=0) -> dict:
    """候选 → 唯一高置信才给链接；多条高置信视为有歧义，留给人工确认。"""
    evaluated = [evaluate(title, singer, item, duration)
                 for item in candidates if str((item or {}).get("url") or "").startswith("http")]
    evaluated = [item for item in evaluated if item["score"] > 0]
    evaluated.sort(key=lambda item: (
        -item["score"],
        item["duration_diff"] if item["duration_diff"] is not None else 999,
        -(item["candidate"].get("play") or 0),
    ))
    high = [item for item in evaluated if item["confidence"] == "high"]
    # 多条高置信时优先认定 UP 主就是演唱者的那条（本人投稿 / 官方号），其余视为转载
    if len(high) > 1:
        official = [item for item in high if item.get("author_hit")]
        if len(official) == 1:
            high = official
    if len(high) == 1:
        best = high[0]
        return {"state": "matched", "url": best["candidate"]["url"],
                "reason": "；".join(best["reasons"]), "candidates": evaluated}
    if len(high) > 1:
        return {"state": "ambiguous", "url": "",
                "reason": f"有 {len(high)} 条高置信候选，同名视频太多，需人工确认",
                "candidates": evaluated}
    return {"state": "none", "url": "",
            "reason": "没有同时满足歌名、时长、演唱者的候选" if evaluated else "未搜到候选",
            "candidates": evaluated}


# ==================== 搜索 ====================

def _default_searcher(query: str, limit: int):
    from platforms.video_search import search_bilibili
    return search_bilibili(query, count=limit)


def _query_list(title: str, singer: str) -> list[str]:
    queries = []
    singer = str(singer or "").strip()
    if singer:
        queries.append(f"{singer} {title}".strip())
    queries.append(title)
    return queries


def search_song_bili(title: str, singer: str = "", duration=0, searcher=None,
                     limit: int = MAX_CANDIDATES) -> dict:
    """歌名 + 演唱者 → {state, url, reason, candidates, query}。

    state: matched（唯一高置信，可直接写 B站链接）/ ambiguous（多候选待确认）/
    none（无候选或都不达标准）/ error（搜索请求失败）。
    先搜「演唱者 歌名」，没到高置信再搜纯歌名，候选按 url 去重后统一打分。
    """
    title = str(title or "").strip()
    if not title:
        return {"state": "none", "url": "", "reason": "歌曲名为空",
                "candidates": [], "query": ""}
    search = searcher or _default_searcher
    merged: list[dict] = []
    seen: set[str] = set()
    used: list[str] = []
    errors: list[str] = []
    result = None
    for query in _query_list(title, singer):
        try:
            raw = search(query, limit) or []
        except Exception as exc:  # noqa: BLE001 - 单个通道失败不阻断下一档
            errors.append(f"{query}：{exc}")
            continue
        used.append(query)
        for item in raw:
            candidate = candidate_of(item)
            if not candidate["url"] or candidate["url"] in seen:
                continue
            seen.add(candidate["url"])
            merged.append(candidate)
        result = pick(title, singer, merged, duration)
        if result["state"] == "matched":
            break
    if result is None:
        return {"state": "error", "url": "", "reason": "；".join(errors) or "B站搜索失败",
                "candidates": [], "query": "、".join(_query_list(title, singer))}
    result["query"] = "、".join(used)
    if errors:
        result["reason"] = f"{result['reason']}（{'；'.join(errors)}）"
    return result