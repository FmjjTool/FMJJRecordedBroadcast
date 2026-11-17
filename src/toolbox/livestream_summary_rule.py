# -*- coding: utf-8 -*-
"""直播总结 · 纯规则保底脚本（不依赖任何大模型）。

把直播转录（TXT / SRT）处理成《封茗囧菌直播总结》的规范骨架：

1. SRT 去掉字幕序号与时间戳，TXT 自动尝试 UTF-8 / UTF-16 / GB18030；
2. 按 `correction-map.md` 的固定词表做替换式纠错（解析失败时用内置兜底词对）；
3. 从文件名优先、正文其次识别直播日期，生成标题；
4. 按关键词把句子摘录到 1—7 级维度，6 级提取书名号歌名、7 级提取身高体重等数值；
5. 按 `template.md` 的行序输出纯文本，末尾补固定标注。

**定位是保底**：纯规则只能做机械清洗、词表纠错与原文摘录，分类名由关键词命中，
不是语义总结，也不做概括。结果必须经人工或 Agent 复核后再发布。

用法：
    python -m toolbox.livestream_summary_rule 直播.txt
    python src/toolbox/livestream_summary_rule.py 直播.srt -o 结果.txt
    python -m toolbox.livestream_summary_rule 直播.txt --stdout
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date as date_cls
from pathlib import Path

# 与 template.md 保持一致的固定标注
FIXED_LINE = "【腾讯文档】封茗囧菌数据汇总库"
FIXED_LINK = "@share-html:https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"

DIMENSION_TITLES = {
    1: "1 级：待发布内容详情",
    2: "2 级：直播中提及的「个人生活事件」",
    3: "3 级：直播中提及的「恋爱相关事件」",
    4: "4 级：直播中提及的「学校相关事件」",
    5: "5 级：直播中提及的「社交关系相关事件」",
    6: "6 级：直播中演唱的歌曲",
    7: "7 级：直播中提及的个人基础数据",
}

# 内置兜底词对：correction-map.md 解析失败或打包缺失时仍能工作
BUILTIN_CORRECTIONS = {
    "蜂蜜酒精": "封茗囧菌",
    "蜂蜜种均": "封茗囧菌",
    "封迷久精": "封茗囧菌",
    "蜂茗囧菌": "封茗囧菌",
    "封明囧菌": "封茗囧菌",
    "封茗囧君": "封茗囧菌",
    "封茗久菌": "封茗囧菌",
    "风茗囧菌": "封茗囧菌",
    "冯茗囧菌": "封茗囧菌",
    "丰茗囧菌": "封茗囧菌",
    "封铭囧菌": "封茗囧菌",
    "封茗囧": "封茗囧菌",
    "茗囧菌": "封茗囧菌",
    "封囧菌": "封茗囧菌",
    "封茗囧菌咨询站": "封茗囧菌咨讯站",
    "封茗囧菌资讯站": "封茗囧菌咨讯站",
    "某易云音乐": "网易云音乐",
    "企鹅音乐": "QQ 音乐",
}

# 固定专有名词：纠错时先保护，避免「封茗囧菌」被自己的子串误替换
FIXED_TERMS = ("封茗囧菌咨讯站", "封茗囧菌的啊团", "月老馒不懂",
               "封茗囧菌", "宫姒", "左左")

_SRT_TIME = re.compile(
    r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])|\n+")
_FULL_DATE = re.compile(r"(20\d{2})[-_.年](\d{1,2})[-_.月](\d{1,2})日?")
_COMPACT_DATE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_TODAY_DATE = re.compile(r"今天是?\s*(20\d{2})年(\d{1,2})月(\d{1,2})[日号]")

_CITY = re.compile(
    r"(?:在|去)(北京|上海|广州|深圳|杭州|成都|重庆|武汉|南京|西安|长沙|"
    r"苏州|天津|郑州|青岛|厦门|福州|济南|昆明|合肥|南昌|沈阳|大连|"
    r"哈尔滨|三亚|海口)(?:市)?")

# 维度判定规则：顺序即优先级（先判实际演唱与基础数据，再判待发布）
_SONG_CTX = re.compile(r"(唱|演唱|清唱|翻唱)")
_FRAGMENT = re.compile(r"(片段|一小段|几句|副歌)")
_BOOK_TITLE = re.compile(r"《([^》]{1,40})》")

_LEVEL1 = re.compile(
    r"(新歌|新单曲|单曲|原创|专辑|EP|发布|上线|发行|首发|联动|合作|代言|品牌|"
    r"周边|预售|上架|演唱会|巡演|漫展|签售|线下|见面会|生日会|福利|发糖|预告|"
    r"投稿|翻唱|造型|计划)")
_FUTURE = re.compile(
    r"(要|会|打算|准备|即将|后面|下次|到时候|之后|下个?月|下周|明天|今年|明年|"
    r"年底|月底|最近|以后)")
_PAST = re.compile(r"(以前|去年|上次|曾经|小时候|上一次|之前)")

_LOVE = re.compile(r"(恋爱|男朋友|女朋友|喜欢的人|表白|脱单|感情|情侣|网恋|CP|"
                   r"恋爱观|情感)")
_SCHOOL = re.compile(r"(学校|上课|老师|同学|考试|作业|大学|高中|初中|毕业|宿舍|"
                     r"专业|留学|校园|课程|学业)")
_OTHER = re.compile(
    r"(粉丝|大家|你们|观众|妈妈|爸爸|父母|姐姐|妹妹|弟弟|哥哥|家人|四姐|苗苗|"
    r"啊团|左左|宫姒|朋友|闺蜜|好友|同事|工作人员|助理|团队|合作方|歌手|"
    r"UP主|连麦|礼物|老师|同学)")
_SELF_LIFE = re.compile(
    r"(吃|喝|睡|买|购物|快递|妆|化妆|护肤|发型|头发|衣服|鞋|美甲|游戏|王者|"
    r"原神|宠物|兔|猫|狗|身体|健康|嗓子|感冒|失眠|情绪|心情|出门|逛街|旅行|"
    r"搬家|追剧|动漫|童年|减肥|健身|生日|日常)")

_LEVEL1_NAMES = (
    (re.compile(r"(新歌|新单曲|单曲|原创|翻唱)"), "新歌发布"),
    (re.compile(r"(专辑|EP)"), "专辑发行"),
    (re.compile(r"(演唱会|巡演|漫展|签售|线下|见面会|生日会)"), "线下活动"),
    (re.compile(r"(周边|预售|上架|娃娃|海报)"), "周边售卖"),
    (re.compile(r"(联动|合作|代言|品牌)"), "品牌合作"),
    (re.compile(r"(福利|发糖|抽奖)"), "粉丝福利"),
    (re.compile(r"(造型|cos|Cos|COS|服装)"), "造型规划"),
    (re.compile(r"(视频|投稿|直播|内容)"), "内容规划"),
)
_LIFE_NAMES = (
    (re.compile(r"(妆|化妆|护肤|美瞳|睫毛|双眼皮|口红|底妆)"), "妆造分享"),
    (re.compile(r"(发型|头发|染发|剪发|理发)"), "发型调整"),
    (re.compile(r"(嗓子|开嗓|发声|唱歌技巧)"), "发声技巧"),
    (re.compile(r"(吃|喝|饮食|奶茶|咖啡|外卖)"), "饮食作息"),
    (re.compile(r"(睡|失眠|作息)"), "作息状态"),
    (re.compile(r"(健康|感冒|生病|身体|心悸|头晕)"), "健康状态"),
    (re.compile(r"(游戏|王者|原神|瓦|抽卡)"), "游戏经历"),
    (re.compile(r"(宠物|兔|猫|狗|小软)"), "宠物轶事"),
    (re.compile(r"(童年|小时候|动画|动漫|追剧)"), "童年回忆"),
    (re.compile(r"(买|购物|快递|逛街|衣服|鞋)"), "购物经历"),
    (re.compile(r"(情绪|心情|焦虑|迷茫)"), "情绪状态"),
)
_SOCIAL_NAMES = (
    (re.compile(r"(礼物)"), "礼物往来"),
    (re.compile(r"(四姐|苗苗|啊团|左左|宫姒|工作人员|助理|团队|妆娘|摄影)"),
     "工作人员互动"),
    (re.compile(r"(妈妈|爸爸|父母|姐姐|妹妹|弟弟|哥哥|家人)"), "家庭事件"),
    (re.compile(r"(朋友|闺蜜|好友|库罗宾|若水|小琉璃)"), "好友互动"),
    (re.compile(r"(合作|联动|歌手|UP主)"), "合作往来"),
    (re.compile(r"(连麦)"), "连麦互动"),
    (re.compile(r"(粉丝|大家|你们|观众)"), "粉丝关系"),
)
_LOVE_NAMES = (
    (re.compile(r"(建议|奉劝|提醒)"), "情感建议"),
    (re.compile(r"(辟谣|澄清|不是情侣)"), "情感辟谣"),
    (re.compile(r"(态度|观点|看法|觉得)"), "情感态度"),
    (re.compile(r"(创作|写歌|灵感)"), "创作心境"),
)

_HEIGHT = re.compile(r"(\d{2,3})\s*(cm|CM|厘米|公分)")
_WEIGHT = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(kg|KG|公斤|斤)")
_AGE = re.compile(r"(\d{1,2})\s*岁")
_BIRTHDAY = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_ZODIAC = re.compile(r"(白羊|金牛|双子|巨蟹|狮子|处女|天秤|天蝎|射手|摩羯|水瓶|双鱼)座")
_MBTI = re.compile(r"(?<![A-Za-z])([EI][NS][TF][JP])(?![A-Za-z])", re.IGNORECASE)
_NATION = re.compile(r"(汉族|满族|回族|苗族|彝族|壮族|藏族|蒙古族|维吾尔族)")


# ==================== 输入处理 ====================

def read_transcript(path: Path) -> str:
    """读取 TXT / SRT；按 BOM 与常见中文编码依次尝试。"""
    raw = path.read_bytes()
    encodings = ["utf-8-sig"]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    encodings.extend(["utf-8", "gb18030"])
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def strip_srt(text: str) -> str:
    """去掉 SRT 的序号行、时间戳行与空行；普通 TXT 原样返回。"""
    if "-->" not in text:
        return text.strip()
    lines = []
    for line in text.splitlines():
        value = line.strip()
        if not value or _SRT_TIME.search(value):
            continue
        if value.isdigit():
            continue
        lines.append(value)
    return "\n".join(lines)


def split_sentences(text: str) -> list[str]:
    """按中文句末标点与换行切句，过滤过短碎片。"""
    result = []
    for piece in _SENTENCE_SPLIT.split(text):
        sentence = (piece or "").strip()
        if len(sentence) < 4:
            continue
        if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", sentence):
            continue
        result.append(sentence)
    return result


# ==================== 日期 ====================

def _valid_date(year: int, month: int, day: int) -> str | None:
    try:
        return date_cls(year, month, day).strftime("%Y%m%d")
    except ValueError:
        return None


def extract_date(filename: str, text: str) -> tuple[str | None, str]:
    """返回 (YYYYMMDD 或 None, 来源说明)。文件名优先，其次正文明确日期。"""
    for pattern in (_FULL_DATE, _COMPACT_DATE):
        for match in pattern.finditer(filename):
            value = _valid_date(int(match.group(1)), int(match.group(2)),
                                int(match.group(3)))
            if value:
                return value, "文件名"
    head = text[:300]
    match = _TODAY_DATE.search(head) or _FULL_DATE.search(head)
    if match:
        value = _valid_date(int(match.group(1)), int(match.group(2)),
                            int(match.group(3)))
        if value:
            return value, "正文"
    return None, "未识别"


def build_title(date_text: str | None) -> str:
    if date_text:
        return f"封茗囧菌 {date_text} 直播总结"
    return "封茗囧菌暂未公开（直播日期）直播总结"


# ==================== 纠错 ====================

def _clean_candidate(value: str) -> str:
    value = re.sub(r"[「」《》\"'“”‘’（）()\s]", "", value)
    return value.strip("，,。.；;：:、")


def parse_correction_map(path: Path) -> dict[str, str]:
    """从 correction-map.md 提取「误识词 → 标准词」词对。"""
    pairs: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return pairs
    for line in lines:
        if "→" not in line:
            continue
        left, right = line.split("→", 1)
        left = re.sub(r"^\s*[-*#\d.]+\s*", "", left)
        right_terms = re.findall(r"[「《]([^」》]{1,20})[」》]", right)
        if right_terms:
            target = _clean_candidate(right_terms[0])
        else:
            target = _clean_candidate(re.split(r"[（(，,。；;]", right, 1)[0])
        if not (2 <= len(target) <= 20):
            continue
        # 左侧候选：优先「」内词，其次括号内顿号列表，再次冒号后的列表
        quoted = re.findall(r"[「《]([^」》]{1,20})[」》]", left)
        if quoted:
            candidates = quoted
        else:
            body = left.split("：")[-1] if "：" in left else left
            body = body.split("误识词")[-1]
            candidates = re.split(r"[、，,;；]", body)
        for candidate in candidates:
            wrong = _clean_candidate(candidate)
            if wrong and wrong != target and 2 <= len(wrong) <= 20:
                pairs[wrong] = target
    return pairs


def load_corrections() -> dict[str, str]:
    """内置兜底 + 解析 skill 纠错表；后者覆盖前者。"""
    pairs = dict(BUILTIN_CORRECTIONS)
    try:
        from toolbox.skills import skills_root
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from toolbox.skills import skills_root
    path = skills_root() / "fengming-livestream-summary" / "references" / "correction-map.md"
    pairs.update(parse_correction_map(path))
    return pairs


def apply_corrections(text: str, pairs: dict[str, str]) -> str:
    """最长匹配替换：标准词映射到自身，避免「封茗囧菌」被自己的子串误替换。"""
    text = re.sub(r"封\s*茗\s*囧\s*菌", "封茗囧菌", text)
    if not text:
        return text
    mapping = dict(pairs)
    for term in set(FIXED_TERMS) | set(pairs.values()):
        mapping.setdefault(term, term)
    words = sorted(mapping, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(word) for word in words))
    return pattern.sub(lambda match: mapping[match.group(0)], text)


# ==================== 分类摘录 ====================

def _pick_name(sentence: str, rules, default: str) -> str:
    for pattern, name in rules:
        if pattern.search(sentence):
            return name
    return default


def _song_lines(sentences: list[str]) -> list[str]:
    songs: list[str] = []
    for sentence in sentences:
        if not _SONG_CTX.search(sentence):
            continue
        for title in _BOOK_TITLE.findall(sentence):
            name = title.strip()
            if not name:
                continue
            line = f"《{name}》（片段）" if _FRAGMENT.search(sentence) else f"《{name}》"
            if line not in songs:
                songs.append(line)
    return songs


def _profile_lines(text: str) -> dict[str, str]:
    """7 级：只取直播中明确说出的数值/信息，缺失统一「未公开」。"""
    data = {key: "未公开" for key in ("身高", "体重", "年龄", "生日", "民族", "星座", "MBTI")}
    match = _HEIGHT.search(text)
    if match:
        data["身高"] = f"{match.group(1)}{match.group(2)}"
    match = _WEIGHT.search(text)
    if match:
        data["体重"] = f"{match.group(1)}{match.group(2)}"
    match = _AGE.search(text)
    if match:
        data["年龄"] = f"{match.group(1)}岁"
    match = _BIRTHDAY.search(text)
    if match:
        data["生日"] = f"{match.group(1)}月{match.group(2)}日"
    match = _ZODIAC.search(text)
    if match:
        data["星座"] = f"{match.group(1)}座"
    match = _MBTI.search(text)
    if match:
        data["MBTI"] = match.group(1).upper()
    match = _NATION.search(text)
    if match:
        data["民族"] = match.group(1)
    return data


def _level1_detail(sentence: str) -> str:
    """1 级五要素：能识别的填实值，其余逐字段写「暂未公开」。"""
    place = "暂未公开"
    match = _CITY.search(sentence)
    if match:
        place = match.group(1)
    when = "暂未公开"
    match = re.search(r"(下周|下个月|月底|今年|明年|明天|后天|最近)", sentence)
    if match:
        when = match.group(1)
    detail = sentence if len(sentence) <= 100 else sentence[:100] + "…"
    return (f"时间：{when}，地点：{place}，核心内容：{detail}，"
            f"合作方：暂未公开，关键细节：{detail}")


def classify(sentences: list[str], max_per_dim: int = 30) -> dict[int, list[str]]:
    """逐句按关键词归入 1—7 级；同一句只归一个维度。"""
    buckets: dict[int, list[str]] = {level: [] for level in range(1, 8)}
    for sentence in sentences:
        level = 0
        if (_HEIGHT.search(sentence) or _WEIGHT.search(sentence)
                or _AGE.search(sentence) or _BIRTHDAY.search(sentence)
                or _ZODIAC.search(sentence) or _MBTI.search(sentence)
                or _NATION.search(sentence)):
            continue  # 基础数据统一在 7 级输出
        if _LEVEL1.search(sentence) and not _PAST.search(sentence):
            level = 1
            name = _pick_name(sentence, _LEVEL1_NAMES, "内容规划")
            line = f"[{name}]：{_level1_detail(sentence)}"
        elif _LOVE.search(sentence):
            level = 3
            name = _pick_name(sentence, _LOVE_NAMES, "恋爱观")
            line = f"[{name}]：{sentence}"
        elif _SCHOOL.search(sentence):
            level = 4
            name = _pick_name(sentence, (
                (re.compile(r"(课程|上课|专业)"), "课程内容"),
                (re.compile(r"(考试|作业|学业)"), "学业现状"),
                (re.compile(r"(校园|宿舍|同学)"), "校园轶事"),
            ), "学校事件")
            line = f"[{name}]：{sentence}"
        elif _SONG_CTX.search(sentence) and _BOOK_TITLE.search(sentence):
            continue  # 实际演唱统一在 6 级输出
        elif _OTHER.search(sentence):
            level = 5
            name = _pick_name(sentence, _SOCIAL_NAMES, "社交互动")
            line = f"[{name}]：{sentence}"
        elif _SELF_LIFE.search(sentence) and "我" in sentence:
            level = 2
            name = _pick_name(sentence, _LIFE_NAMES, "个人日常")
            line = f"[{name}]：{sentence}"
        if level and len(buckets[level]) < max_per_dim:
            buckets[level].append(line)
    buckets[6] = []
    buckets[7] = []
    return buckets


def render(date_text: str | None, buckets: dict[int, list[str]],
           songs: list[str], profile: dict[str, str]) -> str:
    """按 template.md 的行序拼装纯文本。"""
    lines = [build_title(date_text), FIXED_LINE, FIXED_LINK, ""]
    for level in range(1, 8):
        title = DIMENSION_TITLES[level]
        if level == 6:
            lines.append(title)
            lines.extend(songs or ["未演唱歌曲"])
        elif level == 7:
            lines.append(title)
            for key in ("身高", "体重", "年龄", "生日", "民族", "星座", "MBTI"):
                lines.append(f"[{key}]：{profile.get(key, '未公开')}")
        else:
            entries = buckets.get(level) or []
            lines.append(title if entries else f"{title}（未提及）")
            lines.extend(entries)
        lines.append("")
    lines.extend([FIXED_LINE, FIXED_LINK])
    return "\n".join(lines).rstrip() + "\n"


# ==================== 入口 ====================

def summarize_text(text: str, filename: str = "",
                   max_per_dim: int = 30) -> tuple[str, dict]:
    """纯函数入口：返回 (总结文本, 统计信息)，便于测试与后续页面复用。"""
    body = strip_srt(text)
    date_text, date_source = extract_date(filename, body)
    corrected = apply_corrections(body, load_corrections())
    sentences = split_sentences(corrected)
    buckets = classify(sentences, max_per_dim=max_per_dim)
    songs = _song_lines(sentences)
    profile = _profile_lines(corrected)
    output = render(date_text, buckets, songs, profile)
    stats = {
        "chars": len(corrected),
        "sentences": len(sentences),
        "date": date_text or "",
        "date_source": date_source,
        "counts": {level: (len(songs) if level == 6 else
                           (sum(1 for v in profile.values() if v != "未公开")
                            if level == 7 else len(buckets.get(level) or [])))
                   for level in range(1, 8)},
    }
    return output, stats


def _safe_filename(title: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip() + ".txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="直播总结纯规则保底：清洗转录、词表纠错、按维度摘录并输出模板文本。")
    parser.add_argument("input", help="直播转录文件（.txt / .srt）")
    parser.add_argument("-o", "--output", help="输出文件路径（默认与源文件同目录）")
    parser.add_argument("--stdout", action="store_true", help="只打印到终端，不写文件")
    parser.add_argument("--max-per-dim", type=int, default=30,
                        help="每个维度最多摘录多少条（默认 30）")
    args = parser.parse_args(argv)

    source = Path(args.input).expanduser()
    if not source.is_file():
        print(f"找不到输入文件：{source}", file=sys.stderr)
        return 1
    try:
        text = read_transcript(source)
    except OSError as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 1

    output, stats = summarize_text(text, source.name, max_per_dim=args.max_per_dim)
    if args.stdout:
        print(output)
    else:
        target = Path(args.output) if args.output else source.with_name(
            _safe_filename(output.splitlines()[0]))
        target.write_text(output, encoding="utf-8")
        print(f"已输出：{target}")
    print(
        "纯规则保底：字符 {chars} · 句子 {sentences} · 1级 {c1} / 2级 {c2} / "
        "3级 {c3} / 4级 {c4} / 5级 {c5} / 6级 {c6} / 7级 {c7} · 日期来源 {date_source}".format(
            **{f"c{i}": stats["counts"][i] for i in range(1, 8)}, **stats))
    print("提示：条目为原文关键词摘录，分类名由规则匹配，发布前需人工复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
