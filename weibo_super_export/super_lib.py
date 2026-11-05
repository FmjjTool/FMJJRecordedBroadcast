"""微博超话全量抓取 —— 封茗囧菌超话（10080857b3593b49a1cef3b2585f65295e0419）

分层落盘：
  download/微博超话数据/封茗囧菌超话/原始数据/   接口原始 JSONL + meta JSON
  download/微博超话数据/封茗囧菌超话/成品/       CSV/XLSX 表格 + 统计

用法：
  py -3.12 weibo_super_export/scrape_super.py --phase all
  可单跑 --phase meta|posts|detail|comments|reposts|users|export
  --workers N 并发；全部阶段可中断续跑（按 key 去重 + 页码状态）。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from src.auth import load_cookies  # noqa: E402

SCHEME = '10080857b3593b49a1cef3b2585f65295e0419'
T32 = SCHEME[6:]
TOPIC_NAME = '封茗囧菌超话'
TOPIC_UID = 3056354293          # 超话专属人（封茗囧菌）
API_TOTAL_POSTS = None          # 运行时从 pageInfo.total 读取

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / 'download' / '微博超话数据' / TOPIC_NAME
RAW = BASE / '原始数据'
META = RAW / 'meta'
STATE = RAW / 'state'
OUTD = BASE / '成品'
for d in (RAW, META, STATE, OUTD):
    d.mkdir(parents=True, exist_ok=True)

UA_M = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
        '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')
UA_PC = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# 帖子流容器：名称 -> containerid 后缀
FLOWS = {
    'sort_time': f'{SCHEME}_-_sort_time',      # 全部·最新发帖（时间序，~48327）
    'feed': f'{SCHEME}_-_feed',                # 全部·最新评论
    'soul': f'{SCHEME}_-_soul',                # 精华
    'recommend': f'{SCHEME}_-_recommend',      # 热门
    'shuitie': f'{SCHEME}__10010001_-_tag_comment_sort',   # 水帖专区
    'zhoukan': f'{SCHEME}__10120001_-_tag_comment_sort',   # 周刊
    'video': f'{SCHEME}_-_video',              # 视频
    'album': f'{SCHEME}_-_album',              # 图册（~5007）
    'live': f'{SCHEME}_-_live',                # 直播卡片
}

_log_lock = threading.Lock()
LOGF = None


def log(msg: str):
    line = f'[{datetime.now():%H:%M:%S}] {msg}'
    with _log_lock:
        print(line.encode('utf-8', 'backslashreplace').decode('utf-8', 'replace'), flush=True)
        if LOGF:
            LOGF.write(line + '\n')
            LOGF.flush()


# ==================== HTTP：限流 + 退避 ====================
class Gate:
    """全局令牌桶：限制每秒请求数，失败时整体冷却。"""

    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last = time.time()
        self.lock = threading.Lock()
        self.cooldown_until = 0.0

    def acquire(self):
        while True:
            with self.lock:
                now = time.time()
                if now < self.cooldown_until:
                    wait = self.cooldown_until - now
                else:
                    self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
                    self.last = now
                    if self.tokens >= 1:
                        self.tokens -= 1
                        wait = 0.0
                    else:
                        wait = (1 - self.tokens) / self.rate
            if wait <= 0:
                return
            time.sleep(min(wait, 1.0))

    def penalize(self, seconds: float = 3.0):
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.time() + seconds)
            self.tokens = 0


class Http:
    def __init__(self, cookie: str, rate: float = 3.5):
        self.gate = Gate(rate)
        self.s = requests.Session()
        self.local = threading.local()
        self.cookie = cookie
        xs = re.search(r'XSRF-TOKEN=([^;]+)', cookie)
        self.xsrf = xs.group(1) if xs else ''
        self.fail = STATE / 'http_fail.jsonl'
        self._b_lock = threading.Lock()
        self.block_streak = 0

    def _headers(self, kind: str, referer: str):
        if kind == 'm':
            return {'User-Agent': UA_M, 'Referer': referer, 'Accept': 'application/json',
                    'Cookie': self.cookie, 'MWeibo-Pwa': '1', 'X-Requested-With': 'XMLHttpRequest'}
        return {'User-Agent': UA_PC, 'Referer': referer, 'Accept': 'application/json, text/plain, */*',
                'Cookie': self.cookie, 'X-Requested-With': 'XMLHttpRequest', 'X-XSRF-TOKEN': self.xsrf,
                'client-version': 'v1.1.255', 'server-version': 'v2026.10.29.2'}

    def get_json(self, kind: str, url: str, params: dict | None = None,
                 referer: str = 'https://weibo.com/', tries: int = 5):
        """返回 (json, status)。全部失败返回 (None, status)。"""
        last = 0
        for attempt in range(tries):
            self.gate.acquire()
            time.sleep(random.uniform(0.02, 0.18))
            try:
                r = self.s.get(url, params=params, headers=self._headers(kind, referer), timeout=25)
            except Exception as e:
                last = -1
                self.gate.penalize(2.0)
                log(f'neterr {url.split("?")[0]} {type(e).__name__} retry={attempt}')
                time.sleep(1.5 + attempt * 2.5)
                continue
            last = r.status_code
            if r.status_code in (403, 412, 414, 418, 429, 503):
                with self._b_lock:
                    self.block_streak += 1
                    cd = min(150.0, 4.0 * 1.7 ** self.block_streak)
                self.gate.penalize(cd)
                if attempt == tries - 1:
                    log(f'blocked {r.status_code} {url.split("?")[0]} 已冷却 {cd:.0f}s')
                continue
            try:
                j = json.loads(r.content.decode('utf-8'))
            except Exception:
                txt = r.content.decode('utf-8', 'replace').strip()
                m = re.search(r'\{.*\}|\[.*\]', txt, re.S)   # JSONP / 括号包裹
                if m:
                    try:
                        j = json.loads(m.group(0))
                    except Exception:
                        self.gate.penalize(1.0)
                        time.sleep(0.8)
                        continue
                    with self._b_lock:
                        self.block_streak = 0
                    return j, last
                self.gate.penalize(1.0)
                time.sleep(0.8)
                continue
            with self._b_lock:
                self.block_streak = 0
            if isinstance(j, dict) and j.get('ok') == 0:
                msg = str(j.get('msg') or j.get('message') or '')
                if any(k in msg for k in ('操作过于频繁', '请求过于频繁', 'bus', 'Frequent', '系统繁忙')):
                    self.gate.penalize(min(25.0, 2.0 ** attempt * 2.5))
                    time.sleep(1.0 + attempt * 1.5)
                    continue
            return j, last
        with _log_lock:
            with open(self.fail, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'url': url, 'params': params, 'status': last,
                                    'ts': datetime.now().isoformat()}, ensure_ascii=False) + '\n')
        return None, last


# ==================== JSONL 存储（可续跑） ====================
class Store:
    def __init__(self, name: str, key: str):
        self.path = RAW / f'{name}.jsonl'
        self.key = key
        self.seen = set()
        self.lock = threading.Lock()
        self.n = 0
        if self.path.exists():
            with open(self.path, encoding='utf-8') as f:
                for line in f:
                    try:
                        self.seen.add(str(json.loads(line)[key]))
                    except Exception:
                        continue
            self.n = len(self.seen)

    def has(self, k) -> bool:
        return str(k) in self.seen

    def write(self, obj: dict):
        k = str(obj[self.key])
        with self.lock:
            if k in self.seen:
                return False
            self.seen.add(k)
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(obj, ensure_ascii=False) + '\n')
            self.n += 1
            return True

    def keys(self):
        return list(self.seen)


class PageState:
    """帖子流翻页状态：<flow>.json = {"done": {page: n_posts}}"""

    def __init__(self, flow: str):
        self.path = STATE / f'pages_{flow}.json'
        self.d = {'flow': flow, 'done': {}, 'exhausted_page': None}
        if self.path.exists():
            self.d = json.loads(self.path.read_text(encoding='utf-8'))
        self.lock = threading.Lock()

    def done(self) -> set[int]:
        return {int(k) for k in self.d['done']}

    def mark(self, page: int, n_posts: int):
        with self.lock:
            self.d['done'][str(page)] = n_posts

    def count(self) -> int:
        return len(self.d['done'])

    def save(self):
        with self.lock:
            self.path.write_text(json.dumps(self.d, ensure_ascii=False), encoding='utf-8')

    @property
    def exhausted(self):
        return self.d.get('exhausted_page')


# ==================== 解析辅助 ====================
TAG_RE = re.compile(r'<[^>]+>')
SPACER_RE = re.compile(r'[\u200b\u200c\u200d\ufeff]')


def plain(text_html: str) -> str:
    """微博 HTML 正文 → 纯文本（表情转 [表情名]，话题/超话/链接保留文字）。"""
    if not text_html:
        return ''
    t = text_html
    t = re.sub(r'<img[^>]*alt="\[([^\]]*)\]"[^>]*>', r'[\1]', t)
    t = re.sub(r'<img[^>]*title="\[([^\]]*)\]"[^>]*>', r'[\1]', t)
    t = re.sub(r'<br\s*/?>', '\n', t)
    t = TAG_RE.sub('', t)
    t = html.unescape(t)
    t = SPACER_RE.sub('', t)
    return re.sub(r'[ \t]+\n', '\n', t).strip()


def parse_wb_time(s: str) -> str:
    """'Wed Nov 04 18:05:16 +0800 2026' -> '2026-11-04 18:05:16'；中文相对时间尽力解析。"""
    if not s:
        return ''
    s = s.strip()
    m = re.match(r'\w{3} (\w{3}) (\d{2}) (\d{2}):(\d{2}):(\d{2}) \+0800 (\d{4})', s)
    if m:
        mon = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
               'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}[m.group(1)]
        return f'{m.group(6)}-{mon:02d}-{int(m.group(2)):02d} {m.group(3)}:{m.group(4)}:{m.group(5)}'
    now = datetime.now()
    m = re.match(r'(\d+)分钟前', s)
    if m:
        return (now - timedelta_min(int(m.group(1)))).strftime('%Y-%m-%d %H:%M:%S')
    m = re.match(r'(\d+)秒前', s)
    if m:
        return (now - timedelta_sec(int(m.group(1)))).strftime('%Y-%m-%d %H:%M:%S')
    m = re.match(r'今天 (\d{2}):(\d{2})', s)
    if m:
        return f'{now:%Y-%m-%d} {m.group(1)}:{m.group(2)}:00'
    m = re.match(r'(\d+)-(\d+) (\d{2}):(\d{2})', s)
    if m:
        return f'{now.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d} {m.group(3)}:{m.group(4)}:00'
    m = re.match(r'(\d{4})-(\d+)-(\d+) (\d{2}):(\d{2})', s)
    if m:
        return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {m.group(4)}:{m.group(5)}:00'
    return s


def timedelta_min(n):
    from datetime import timedelta
    return timedelta(minutes=n)


def timedelta_sec(n):
    from datetime import timedelta
    return timedelta(seconds=n)


def post_url(mid, user_id, bid=''):
    return f'https://weibo.com/{user_id}/{bid}' if bid else f'https://m.weibo.cn/status/{mid}'


# ==================== 全局实例 ====================
H = None
ST = {}


def _ts():
    return datetime.now().isoformat(timespec='seconds')
