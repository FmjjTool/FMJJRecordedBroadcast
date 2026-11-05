"""超话抓取各阶段实现。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from super_lib import (API_TOTAL_POSTS, BASE, FLOWS, H, Http, META, OUTD, PageState, RAW,
                      SCHEME, STATE, Store, T32, TOPIC_NAME, TOPIC_UID, log, parse_wb_time,
                      plain, _ts)
import super_lib

M_GI = 'https://m.weibo.cn/api/container/getIndex'
M_EXTEND = 'https://m.weibo.cn/statuses/extend'
M_HOTFLOW = 'https://m.weibo.cn/comments/hotflow'
M_GETITEM = 'https://m.weibo.cn/api/container/getItem'
PC = 'https://weibo.com'
PROGRESS_BY_CARDS = {'album', 'video', 'live'}   # 卡片即内容，无 mblog 也算有下一页
LOW_YIELD = 3                                    # 单页帖子数低于此值按限流残页处理，进补抓队列


RATE = 3.5


def http() -> Http:
    if super_lib.H is None:
        from src.auth import load_cookies
        super_lib.H = Http(load_cookies().get('weibo', ''), rate=RATE)
    return super_lib.H


# ==================== 1. 元信息 / 榜单 / 公告 ====================
def phase_meta():
    h = http()
    saved = {}

    def put(name, obj):
        (META / f'{name}.json').write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')
        saved[name] = True
        log(f'meta saved {name} ({len(json.dumps(obj, ensure_ascii=False))}B)')

    # m 端主页容器（pageInfo 全字段 + 非帖子卡片）
    j, _ = h.get_json('m', M_GI, {'containerid': SCHEME}, 'https://m.weibo.cn/')
    if j:
        put('m_home', j)
    # 每个流的 m 端首页（含 filter_group/async 卡片清单）
    for name, cid in FLOWS.items():
        j2, _ = h.get_json('m', M_GI, {'containerid': cid, 'page': 1}, 'https://m.weibo.cn/')
        if j2:
            put(f'm_flow_{name}', j2)
    # PC 超话页（header/channelInfo/items 完整结构）
    for name, cid in [('base', SCHEME)] + list(FLOWS.items()):
        j3, _ = h.get_json('pc', f'{PC}/ajax_proxy/chaohua/page', {'flowId': cid},
                           f'https://weibo.com/p/{SCHEME}')
        if j3:
            put(f'pc_flow_{name}', j3)
    # PC 扩展（推荐超话、是否主持人）
    j4, _ = h.get_json('pc', f'{PC}/ajax_proxy/chaohua/page/extend',
                       {'page_id': T32, 'show_recommend': 1}, f'https://weibo.com/p/{SCHEME}')
    if j4:
        put('pc_extend', j4)

    # 名人堂 / 超话里的好友 / 成员页 / 公告等异步模块
    asyncs = {
        'fans_famous': f'231140{T32}_-_fans_famous',
        'topicfriends': f'231140{T32}_-_topicfriends',
        'profile_inpage': f'231140{T32}_-_profile_inpage',
        'profile_allbadge': f'231140{T32}_-_profile_allbadge',
        'contribute': f'231140{T32}_-_contribute',
    }
    for name, cid in asyncs.items():
        pg, allc = 1, []
        sid, prev = None, None
        while pg <= 60:
            params = {'containerid': cid, 'page': pg}
            if name.startswith('profile_'):
                params['extparam'] = f'target_uid#{TOPIC_UID}'   # 双编码会让服务端忽略翻页
            if sid:
                params['since_id'] = sid
            jj, _ = h.get_json('m', M_GI, params, 'https://m.weibo.cn/')
            d = (jj or {}).get('data') or {}
            cs = d.get('cards') or []
            dump = json.dumps(cs, ensure_ascii=False)
            if not cs or dump == prev:      # 空页或原样重复＝到底
                break
            prev = dump
            allc.append(jj)
            sid = (d.get('cardlistInfo') or {}).get('since_id')
            pg += 1
            if not sid:
                break
            time.sleep(0.3)
        put(name, allc)
        if name.startswith('profile_'):
            log(f'meta {name}: 抓到 {len(allc)} 页')

    itemids = [f'232476{T32}__wishenable__sgtotal_enable__0__0_-_page_infeed_asyncmix',
               f'231140{T32}_-_infeed_notice_puttop',
               f'231140{T32}_-_infeed_pagemanual3',
               f'231140{T32}_-_infeed_may_interest_in_1',
               f'231140{T32}_-_infeed_weibo_mall',
               f'231140{T32}__no_visit_card_-_assess_progress',
               f'231140{T32}__no_visit_card_-_great_creator_apply',
               f'231140{T32}__no_visit_card_-_puttop_main',
               f'232476{T32}_-_NewGuideWelcomeCard',
               f'232476{T32}__famous_-_invite']
    for it in itemids:
        ji, _ = h.get_json('m', M_GETITEM, {'itemid': it, 'download': ''}, 'https://m.weibo.cn/')
        if ji:
            (META / f'async_{re.sub(r"[^0-9a-zA-Z]", "_", it)[:70]}.json').write_text(
                json.dumps(ji, ensure_ascii=False, indent=1), encoding='utf-8')
            log(f'meta saved async {it[:50]}')

    # 超话社区总榜 / 分类榜（本超话排名上下文）
    for name, params in {'sg_home': {'containerid': '100803'},
                         'sg_super': {'containerid': '100803_-_super'},
                         'sg_ctg1_97': {'containerid': '100803_-_super', 'extparam': 'ctg1_97|scorll_1'},
                         'sg_ctg2_97022': {'containerid': '100803_-_super', 'extparam': 'ctg1_97|ctg2_97022|scorll_1'},
                         'sg_rank_hot': {'containerid': '100803_-_top'},
                         'sg_rank_new': {'containerid': '100803_-_rise'},
                         'sg_rank_out': {'containerid': '100803_-_oversea'},
                         'sg_rank_star': {'containerid': '100803_-_star'}}.items():
        jj, _ = h.get_json('m', M_GI, params, 'https://m.weibo.cn/')
        if jj:
            put(name, jj)
        time.sleep(0.3)

    # 汇总关键元信息
    topic = {'scheme': SCHEME, 'topic_id': f'1022:{SCHEME}', 'name': TOPIC_NAME,
             'url': f'https://weibo.com/p/{SCHEME}', 'snapshot_at': _ts()}
    ph = ((j or {}).get('data') or {}).get('pageInfo') or {}
    if not ph.get('total'):
        st = META / 'm_flow_sort_time.json'
        if st.exists():
            ph2 = ((json.loads(st.read_text(encoding='utf-8')).get('data') or {}).get('pageInfo') or {})
            if ph2.get('total'):
                ph = {**ph2, **{k: v for k, v in ph.items() if v}}
    topic['pageInfo'] = ph
    hd = (((j4 or {}).get('data') if isinstance(j4, dict) else None) or {})
    pcbase = None
    pf = META / 'pc_flow_base.json'
    if pf.exists():
        pcbase = json.loads(pf.read_text(encoding='utf-8'))
    if pcbase:
        topic['pc_header'] = pcbase.get('header')
        topic['pc_channelInfo'] = pcbase.get('channelInfo')
        topic['pc_moreInfo'] = pcbase.get('moreInfo')
    topic['flows'] = FLOWS
    (META / 'topic.json').write_text(json.dumps(topic, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'meta done: total_posts={ph.get("total")} desc_more={ph.get("desc_more")}')


# ==================== 2. 帖子流全量翻页 ====================
def _flow_posts(j, keep_all_cards: bool = False):
    d = (j or {}).get('data') or {}
    out, other = [], []

    def dig(card, depth=0):
        if not isinstance(card, dict):
            return
        mb = card.get('mblog')
        if isinstance(mb, dict) and mb.get('id'):
            out.append(mb)
            return
        if depth >= 3:
            return
        for sub in (card.get('card_group') or []) + (card.get('cards') or []) + (card.get('group') or []):
            dig(sub, depth + 1)

    for c in d.get('cards') or []:
        if not isinstance(c, dict):
            continue
        n0 = len(out)
        dig(c)
        if len(out) == n0 and (keep_all_cards or c.get('card_group') or c.get('cards') or c.get('pic_items')):
            other.append(c)
    return out, other, d


def crawl_flow(name: str, cid: str, workers: int, max_pages: int, stop_empty: int = 30):
    h = http()
    st = PageState(name)
    store = Store('posts', 'mid')
    seen_flows: dict[str, set] = {}

    def fetch_page(pg: int):
        j, code = h.get_json('m', M_GI, {'containerid': cid, 'page': pg}, 'https://m.weibo.cn/')
        if j is None:
            log(f'{name} p{pg} FAILED http={code}')
            return pg, -1, None
        posts, other, d = _flow_posts(j, name in PROGRESS_BY_CARDS)
        n_prog = len(posts) + (len(other) if name in PROGRESS_BY_CARDS else 0)
        if n_prog < LOW_YIELD:
            # 可能是瞬时限流造成的空页/残页：慢速重验一次再记账
            time.sleep(2.0)
            j, code = h.get_json('m', M_GI, {'containerid': cid, 'page': pg}, 'https://m.weibo.cn/')
            posts, other, d = _flow_posts(j or {}, name in PROGRESS_BY_CARDS)
            n_prog = len(posts) + (len(other) if name in PROGRESS_BY_CARDS else 0)
        for mb in posts:
            mid = str(mb.get('id'))
            seen_flows.setdefault(mid, set()).add(name)
            store.write({'mid': mid, 'flow': name, 'page': pg, 'fetched_at': _ts(), 'raw': mb})
        if other:
            with open(RAW / f'flowcards_{name}.jsonl', 'a', encoding='utf-8') as cf:
                for o in other:
                    cf.write(json.dumps({'flow': name, 'page': pg, 'card': o}, ensure_ascii=False) + '\n')
        degraded = name not in PROGRESS_BY_CARDS and n_prog < LOW_YIELD
        st.mark(pg, -1 if degraded else n_prog)
        return pg, 0 if degraded else n_prog, (d.get('pageInfo') or {}).get('since_id')

    done = st.done()
    frontier = max(done) if done else 0
    empty_streak = 0
    degraded_streak = 0
    log(f'{name}: resume done_pages={len(done)} frontier={frontier} posts_stored={store.n}')
    while True:
        batch = [p for p in range(frontier + 1, frontier + 1 + workers * 4) if p not in done and p <= max_pages]
        if not batch:
            break
        empties = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for pg, n, _sid in ex.map(fetch_page, batch):
                done.add(pg)
                frontier = max(frontier, pg)
                if n <= 0:
                    empty_streak += 1
                    degraded_streak += 1
                    empties.append(pg)
                else:
                    empty_streak = 0
                    degraded_streak = 0
        st.save()
        if len(done) % 200 < len(batch):
            log(f'{name}: pages={len(done)} frontier={frontier} posts={store.n} empty_streak={empty_streak}')
        if degraded_streak >= 60:
            log(f'{name}: 连续 {degraded_streak} 页残空，判定被限流，停下等下一轮补抓')
            break
        if empty_streak >= stop_empty:
            # 先怀疑是限流/瞬时空页：串行慢速重验一次
            re_hit = [pg for pg, n, _ in [fetch_page(p) for p in empties] if n > 0]
            if re_hit:
                log(f'{name}: {len(re_hit)} empty pages revived on recheck -> continue')
                frontier = max(frontier, max(re_hit))
                empty_streak = 0
                continue
            st.d['exhausted_page'] = frontier + stop_empty
            log(f'{name}: exhausted after {stop_empty} confirmed empty pages (frontier={frontier})')
            break
    st.save()
    # 补抓残页/空页：限流会留下洞，只有超过耗尽点之后的连续空页才是真的到底
    if not st.d.get('zero_recovered'):
        bound = st.d.get('exhausted_page') or 10 ** 9
        for rnd in range(3):
            holes = sorted(int(k) for k, v in st.d['done'].items() if v <= 0 and int(k) < bound)
            if not holes:
                break
            log(f'{name}: 第{rnd + 1}轮补抓 {len(holes)} 个残页')
            with ThreadPoolExecutor(max_workers=max(1, workers // 3)) as ex:
                list(ex.map(fetch_page, holes))
            st.save()
        left = sum(1 for v in st.d['done'].values() if v <= 0)
        log(f'{name}: 补抓结束，仍残页 {left} 个')
        if left == 0:
            st.d['zero_recovered'] = True
        st.save()
    (STATE / f'flows_{name}.json').write_text(
        json.dumps({k: sorted(v) for k, v in seen_flows.items()}, ensure_ascii=False), encoding='utf-8')
    return store.n


CARD_FLOW_CAPS = {'album': 2000, 'video': 2000, 'live': 800}


def crawl_flow_pc(name: str, flow_id: str, max_steps: int = 6000, dup_stop: int = 8,
                  min_steps: int = 40):
    """PC 超话页游标链：/ajax_proxy/chaohua/page 用 moreInfo.params 逐页往回翻。

    m 端 container/getIndex 的 page 参数翻到 200 页之后会吐重复窗口，PC 游标链不会；
    返回的 status 对象字段也比 m 端卡片更全。可按 state/cursor_<flow>.json 续跑。
    """
    h = http()
    store = Store('posts', 'mid')
    cpath = STATE / f'cursor_{name}.json'
    cur = {'flow': name, 'flow_id': flow_id, 'page': 1, 'since_id': '', 'max_id': 0,
           'steps': 0, 'posts_total': 0, 'new_total': 0, 'card_total': 0, 'exhausted': False,
           'saved_at': _ts()}
    if cpath.exists():
        cur = json.loads(cpath.read_text(encoding='utf-8'))
    if cur.get('exhausted'):
        log(f'{name}(PC): 已翻到底，跳过（steps={cur["steps"]} posts={cur["new_total"]}）')
        return store.n
    seen_flows: dict[str, set] = {}
    if cpath.with_name(f'flows_{name}.json').exists():
        try:
            seen_flows = {k: set(v) for k, v in json.loads(
                cpath.with_name(f'flows_{name}.json').read_text(encoding='utf-8')).items()}
        except Exception:
            pass
    log(f'{name}(PC): resume steps={cur["steps"]} page={cur["page"]} new={cur["new_total"]} posts={store.n}')
    dup_streak = 0
    cards_path = RAW / f'flowcards_{name}.jsonl'
    card_seen = set()
    if cards_path.exists():          # 尾部会反复吐同一张卡片，靠指纹去重才能识别「真的没有新内容」
        with open(cards_path, encoding='utf-8') as cf:
            for ln in cf:
                try:
                    c = (json.loads(ln) or {}).get('card')
                    if c is not None:
                        card_seen.add(hashlib.md5(json.dumps(c, ensure_ascii=False, sort_keys=True)
                                                  .encode('utf-8')).hexdigest())
                except Exception:
                    continue
    cardsf = open(cards_path, 'a', encoding='utf-8')
    while cur['steps'] < max_steps:
        params = {'flowId': flow_id, 'page': cur['page'] or 1, 'count': 15}
        if cur.get('since_id'):
            params['since_id'] = cur['since_id']
            params['max_id'] = cur.get('max_id', 0)
        j, code = h.get_json('pc', PC + '/ajax_proxy/chaohua/page', params, f'https://weibo.com/p/{SCHEME}')
        data = (j.get('data') if isinstance(j, dict) and 'data' in j else j) or {}
        if not isinstance(data, dict):
            data = {}
        items = data.get('items') or []
        if not items and (j is None or (isinstance(j, dict) and j.get('ok') == 0)):
            log(f'{name}(PC): p{cur["page"]} 取不到数据（http={code}），5s 后重试本游标')
            time.sleep(5)
            continue
        new = 0
        ncards = 0
        for it in items:
            d = it.get('data') if isinstance(it, dict) else None
            if isinstance(d, dict) and (d.get('mid') or d.get('id')) and (d.get('user') or d.get('created_at')):
                mid = str(d.get('mid') or d.get('id'))
                if mid == '0':
                    continue
                before = store.has(mid)
                store.write({'mid': mid, 'flow': name, 'page': cur['page'], 'fetched_at': _ts(), 'raw': d})
                if not before:
                    new += 1
                seen_flows.setdefault(mid, set()).add(name)
            elif isinstance(it, dict):
                # 指纹只按卡片内容算（页码会变，尾部会反复吐同一张卡）
                fp = hashlib.md5(json.dumps(it, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                if fp not in card_seen:
                    card_seen.add(fp)
                    cardsf.write(json.dumps({'flow': name, 'page': cur['page'], 'card': it},
                                            ensure_ascii=False) + '\n')
                    ncards += 1
        cur['steps'] += 1
        cur['page'] = int(cur['page'] or 1) + 1
        cur['new_total'] = int(cur.get('new_total') or 0) + new
        cur['saved_at'] = _ts()
        mi = data.get('moreInfo') or {}
        nxt = mi.get('params') or {}
        if nxt.get('since_id') or nxt.get('page'):
            cur['page'] = nxt.get('page', cur['page'])
            cur['since_id'] = nxt.get('since_id', '')
            cur['max_id'] = nxt.get('max_id', 0)
        else:
            cur['exhausted'] = True
        # 卡片流（album/video/live）没有 mblog，空跑判定必须把卡片算进去，否则会被误判为到底
        dup_streak = dup_streak + 1 if (new == 0 and ncards == 0) else 0
        cur['card_total'] = int(cur.get('card_total') or 0) + ncards
        if cur['steps'] % 25 == 0 or cur['exhausted']:
            cpath.write_text(json.dumps(cur, ensure_ascii=False), encoding='utf-8')
            (STATE / f'flows_{name}.json').write_text(
                json.dumps({k: sorted(v) for k, v in seen_flows.items()}, ensure_ascii=False), encoding='utf-8')
            log(f'{name}(PC): steps={cur["steps"]} page={cur["page"]} new={new} cards={ncards} '
                f'累计新增={cur["new_total"]} 累计卡片={cur["card_total"]} posts={store.n}')
        if dup_stop and not cur['exhausted'] and dup_streak >= dup_stop and cur['steps'] >= min_steps:
            cur['exhausted'] = True
            log(f'{name}(PC): 连续 {dup_streak} 页无新帖（与其它流重合），就此收')
            cpath.write_text(json.dumps(cur, ensure_ascii=False), encoding='utf-8')
            break
        time.sleep(0.1)
    cardsf.close()
    cpath.write_text(json.dumps(cur, ensure_ascii=False), encoding='utf-8')
    (STATE / f'flows_{name}.json').write_text(
        json.dumps({k: sorted(v) for k, v in seen_flows.items()}, ensure_ascii=False), encoding='utf-8')
    log(f'FLOW {name}(PC) done: steps={cur["steps"]} new={cur["new_total"]} unique_posts={store.n}')
    return store.n


def phase_posts(flows=None, workers: int = 6, max_pages: int = 9000, m_pages: bool = False):
    for name, cid in (FLOWS.items() if flows is None else {f: FLOWS[f] for f in flows}.items()):
        steps = min(max_pages * 3, 20000)
        if name == 'sort_time':
            # 时间序流是历史全集的底，容忍长时间的重合页，只认「连续 150 步既无新帖也无新卡片」
            crawl_flow_pc(name, cid, max_steps=steps, dup_stop=150, min_steps=200)
        else:
            crawl_flow_pc(name, cid, max_steps=steps, dup_stop=40, min_steps=150)
        if m_pages:
            cap = min(max_pages, CARD_FLOW_CAPS.get(name, max_pages))
            crawl_flow(name, cid, workers, cap)
        log(f'phase posts 进度：唯一帖={Store("posts", "mid").n}')
    log(f'posts phase done: unique={Store("posts", "mid").n}')


# ==================== 3. 帖子详情 / 长文 / 视频 ====================
def phase_detail(workers: int = 6, limit: int | None = None):
    h = http()
    posts = Store('posts', 'mid')
    det = Store('details', 'mid')
    lt = Store('longtext', 'mid')
    vid = Store('video_src', 'mid')
    mids = posts.keys()
    if limit:
        mids = mids[:limit]
    todo = [m for m in mids if not det.has(m)]
    log(f'detail: total_posts={len(mids)} todo={len(todo)} have={det.n}')

    def work(mid):
        j, code = h.get_json('pc', f'{PC}/ajax/statuses/show', {'id': mid},
                             f'https://weibo.com/detail/{mid}')
        if not j or (isinstance(j, dict) and j.get('error')):
            j2, _ = h.get_json('m', 'https://m.weibo.cn/statuses/show', {'id': mid}, 'https://m.weibo.cn/')
            if j2 and j2.get('ok') == 1:
                det.write({'mid': mid, 'src': 'm', 'fetched_at': _ts(), 'raw': j2.get('data')})
                j = j2
            else:
                return mid, 0
        raw = j.get('data') if (j.get('ok') == 1 and 'data' in j) else j
        det.write({'mid': mid, 'src': 'pc', 'fetched_at': _ts(), 'raw': raw})
        n = 1
        islt = bool((raw or {}).get('isLongText'))
        if islt and not lt.has(mid):
            jl, _ = h.get_json('pc', f'{PC}/ajax/statuses/longtext', {'id': mid}, 'https://weibo.com/')
            txt = ((jl or {}).get('data') or {}).get('longTextContent') if isinstance(jl, dict) else None
            if not txt:
                je, _ = h.get_json('m', M_EXTEND, {'id': mid}, 'https://m.weibo.cn/')
                txt = ((je or {}).get('data') or {}).get('longTextContent') if isinstance(je, dict) else None
            if txt:
                lt.write({'mid': mid, 'fetched_at': _ts(), 'text_html': txt, 'text': plain(txt)})
                n += 1
        pi = (raw or {}).get('page_info') or {}
        if isinstance(pi, dict) and (pi.get('type') == 'video' or pi.get('page_type') == 20 or pi.get('media_info')
                                     or str(pi.get('type')) == '11'):
            mi = pi.get('media_info') or {}
            # getPcVideoInfo 现在只回 ai_highlight_menu_status，没有直链；帖子/详情里的 media_info 才是正源
            if not (mi.get('stream_url') or mi.get('mp4_hd_url') or mi.get('h264_mp4hdvod_url')
                    or (pi.get('urls') or {}).get('mp4_hd')) and not vid.has(mid):
                jv, _ = h.get_json('pc', f'{PC}/ajax/multimedia/getPcVideoInfo',
                                   {'mid': (raw or {}).get('mid') or mid}, 'https://weibo.com/')
                if jv and (jv.get('data') or {}).get('stream_url'):
                    vid.write({'mid': mid, 'fetched_at': _ts(), 'raw': jv, 'page_info': pi})
                    n += 1
        return mid, n

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (mid, n) in enumerate(pool.map(work, todo), 1):
            if i % 500 == 0:
                log(f'detail: {i}/{len(todo)} details={det.n} longtext={lt.n} video={vid.n}')
    log(f'detail done: details={det.n} longtext={lt.n} video={vid.n}')


# ==================== 3b. statuses/extend 增量面（表态分布/贴纸滤镜/粉丝快照） ====================
def phase_ext(workers: int = 6, limit: int | None = None):
    """详情接口之外的另一份口径：multi_attitude、pic_types/stickerID/filterID、followers 快照等。"""
    h = http()
    posts = Store('posts', 'mid')
    xt = Store('ext', 'mid')
    mids = posts.keys()
    if limit:
        mids = mids[:limit]
    todo = [m for m in mids if not xt.has(m)]
    log(f'ext: total_posts={len(mids)} todo={len(todo)} have={xt.n}')

    def work(mid):
        jx, _ = h.get_json('pc', f'{PC}/ajax/statuses/extend', {'id': mid},
                           f'https://weibo.com/detail/{mid}')
        jx = jx if isinstance(jx, dict) else {}
        data = jx.get('data') if (jx.get('data') and not jx.get('text')) else jx
        if isinstance(data, dict) and (data.get('multi_attitude') or data.get('item_category') == 'status'):
            xt.write({'mid': mid, 'fetched_at': _ts(), 'raw': data})
            return 1
        return 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, n in enumerate(pool.map(work, todo), 1):
            if i % 500 == 0:
                log(f'ext: {i}/{len(todo)} saved={xt.n}')
    log(f'ext done: saved={xt.n}')


# ==================== 4. 评论（顶层 + 子评论） ====================
def _build_comments_pages(h, ident, flow, key_prefix, store, keyfield, root=None, cap=200,
                          rp=None, post_mid=''):
    """PC buildComments 链式翻页；返回条数。rp 用于收每条顶层评论里内嵌的回复。"""
    got = 0
    max_id = ''
    pages = 0
    while pages < cap:
        params = {'id': ident, 'is_reload': 1, 'is_show_bulletin': 2, 'flow': flow,
                  'count': 20, 'max_id_type': 0}
        if max_id:
            params['max_id'] = max_id
        j, code = h.get_json('pc', f'{PC}/ajax/statuses/buildComments', params, 'https://weibo.com/')
        if not j or (isinstance(j, dict) and j.get('ok') != 1):
            break
        data = j.get('data') or []
        if not data:
            break
        for c in data:
            cid = c.get('id')
            key = f'{key_prefix}_{cid}'
            if not store.has(key):
                store.write({store.key: key, 'root': root, 'post_mid': post_mid,
                             'flow': flow, 'fetched_at': _ts(), 'raw': c})
            got += 1
            harvest_replies(rp, c, post_mid)
        pages += 1
        max_id = j.get('max_id') or 0
        if not max_id:
            break
        time.sleep(0.05)
    return got, pages


def harvest_replies(rp, c: dict, post_mid: str):
    """把顶层评论内嵌的 comments[]（前 2 条回复）先存下来，省一次请求。"""
    if rp is None:
        return
    cid = str(c.get('id'))
    for rc in (c.get('comments') or []):
        if not isinstance(rc, dict) or not rc.get('id'):
            continue
        key = f'{cid}_{rc.get("id")}'
        if not rp.has(key):
            rp.write({rp.key: key, 'root': cid, 'post_mid': post_mid,
                      'embedded': 1, 'fetched_at': _ts(), 'raw': rc})


def read_jsonl(path: Path):
    if not path.exists():
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


M_COMMENTSHOW = 'https://m.weibo.cn/api/comments/show'


def _m_comment_tree(h, mid, cm, rp, cap=250):
    """m 端 api/comments/show 一次翻完整棵评论树（顶层 + 全部回复，靠 reply_id 认父）。"""
    page = 1
    got = new = 0
    declared = 0
    while page <= cap:
        j, code = h.get_json('m', M_COMMENTSHOW, {'id': mid, 'page': page}, f'https://m.weibo.cn/detail/{mid}')
        d = ((j or {}).get('data') or {}) if isinstance(j, dict) else {}
        lst = d.get('data') or []
        if not lst:
            break
        declared = int(d.get('total_number') or declared)
        for c in lst:
            cid = c.get('id')
            if not cid:
                continue
            got += 1
            key = f't{mid}_{cid}'
            if not cm.has(key):
                new += 1
                cm.write({cm.key: key, 'root': str(c.get('reply_id') or ''), 'post_mid': mid,
                          'flow': 'm_tree', 'fetched_at': _ts(), 'raw': c})
            if c.get('reply_id'):
                rkey = f'{c["reply_id"]}_{cid}'
                if not rp.has(rkey):
                    rp.write({rp.key: rkey, 'root': str(c['reply_id']), 'post_mid': mid,
                              'fetched_at': _ts(), 'raw': c})
        mp = int(d.get('max') or page)
        if page >= mp:
            break
        page += 1
    return got, new, declared, page


def phase_comments(workers: int = 6, limit: int | None = None):
    h = http()
    posts = Store('posts', 'mid')
    cm = Store('comments', 'cid')
    rp = Store('replies', 'rid')
    stats = Store('comment_stats', 'mid')

    items = []
    for line in open(posts.path, encoding='utf-8'):
        try:
            p = json.loads(line)
        except Exception:
            continue
        cc = int((p.get('raw') or {}).get('comments_count') or 0)
        if cc > 0:
            items.append((p['mid'], cc))
    items.sort(key=lambda x: -x[1])
    done_mid = set()
    if stats.path.exists():
        for line in open(stats.path, encoding='utf-8'):
            try:
                done_mid.add(json.loads(line)['mid'])
            except Exception:
                continue
    items = [it for it in items if it[0] not in done_mid]
    if limit:
        items = items[:limit]
    log(f'comments: posts={posts.n} todo={len(items)} stored_comments={cm.n} stored_replies={rp.n}')

    def work(mid_cc):
        mid, cc = mid_cc
        # 先走 m 端评论树（一次拿到顶层+全部回复），只有没取齐的帖子才补 PC 热度/时间序
        n_tree, new_tree, declared, pages3 = _m_comment_tree(h, mid, cm, rp)
        pg1 = pg2 = n_time = n_hot = 0
        want = max(cc, declared)
        uniq = len([k for k in cm.keys() if k.startswith(f't{mid}_')])
        if uniq < want:
            n_time, pg1 = _build_comments_pages(h, mid, 1, f't{mid}', cm, 'cid', rp=rp, post_mid=mid)
            n_hot, pg2 = _build_comments_pages(h, mid, 0, f't{mid}', cm, 'cid', rp=rp, post_mid=mid, cap=5)
            uniq = len([k for k in cm.keys() if k.startswith(f't{mid}_')])
        stats.write({'mid': mid, 'declared': want, 'top_got': max(n_time, n_hot),
                     'tree_got': n_tree, 'uniq_got': uniq, 'pages': pg1 + pg2 + pages3,
                     'complete': uniq >= want, 'fetched_at': _ts()})
        return mid, uniq

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (mid, n) in enumerate(ex.map(work, items), 1):
            if i % 500 == 0:
                log(f'comments: {i}/{len(items)} stored={cm.n} replies={rp.n}')
    log(f'comments done: top={cm.n} replies_embedded={rp.n}')


def phase_replies(workers: int = 5, limit: int | None = None):
    """评论树对账：声明数 > 已存数的帖子再深翻一轮 m 端评论树，剩余缺口写 state/replies_report.json。"""
    h = http()
    cm = Store('comments', 'cid')
    rp = Store('replies', 'rid')
    stats = {}
    for line in read_jsonl(RAW / 'comment_stats.jsonl'):
        stats[line['mid']] = line
    gap = [(m, s.get('declared', 0), s.get('uniq_got', 0)) for m, s in stats.items()
           if int(s.get('declared') or 0) > int(s.get('uniq_got') or 0)]
    gap.sort(key=lambda x: -(x[1] - x[2]))
    if limit:
        gap = gap[:limit]
    log(f'replies(对账): 未达标的帖子={len(gap)} stored_comments={cm.n} stored_replies={rp.n}')

    def work(item):
        mid, declared, got = item
        n, new, d2, pages = _m_comment_tree(h, mid, cm, rp, cap=400)
        uniq = len([k for k in cm.keys() if k.startswith(f't{mid}_')])
        return {'mid': mid, 'declared': max(declared, d2), 'before': got, 'after': uniq,
                'gap': max(declared, d2) - uniq}

    res = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(work, gap), 1):
            res.append(r)
            if i % 300 == 0:
                log(f'replies: {i}/{len(gap)} comments={cm.n} replies={rp.n}')
    res.sort(key=lambda x: -x['gap'])
    (STATE / 'replies_report.json').write_text(json.dumps(
        {'生成时间': _ts(), '对账帖子数': len(res), '仍有缺口数': sum(1 for r in res if r['gap'] > 0),
         '缺口合计': sum(max(0, r['gap']) for r in res), '缺口Top50': res[:50]},
        ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'replies done: comments={cm.n} replies={rp.n} 仍有缺口={sum(1 for r in res if r["gap"] > 0)}')


# ==================== 5. 转发 ====================
def phase_reposts(workers: int = 5, limit: int | None = None):
    h = http()
    posts = Store('posts', 'mid')
    rps = Store('reposts', 'rid')
    todo = []
    for line in open(posts.path, encoding='utf-8'):
        try:
            p = json.loads(line)
        except Exception:
            continue
        raw = p.get('raw') or {}
        rc = int(raw.get('reposts_count') or 0)
        if rc > 0:
            todo.append((p['mid'], rc))
    todo.sort(key=lambda x: -x[1])
    if limit:
        todo = todo[:limit]
    log(f'reposts: targets={len(todo)} stored={rps.n}')

    def work(item):
        mid, rc = item
        page = 1
        got = 0
        while page <= 200:
            j, code = h.get_json('pc', f'{PC}/ajax/statuses/repostTimeline',
                                 {'id': mid, 'page': page}, 'https://weibo.com/')
            if not j or j.get('ok') != 1:
                break
            data = j.get('data') or []
            if not data:
                break
            for r in data:
                key = f'{mid}_{r.get("id")}'
                if not rps.has(key):
                    rps.write({rps.key: key, 'src_mid': mid, 'page': page, 'fetched_at': _ts(), 'raw': r})
                got += 1
            mp = int(j.get('max_page') or 1)
            if page >= mp:
                break
            page += 1
        return mid, got

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (mid, got) in enumerate(ex.map(work, todo), 1):
            if i % 500 == 0:
                log(f'reposts: {i}/{len(todo)} stored={rps.n}')
    log(f'reposts done: {rps.n}')


# ==================== 6. 作者资料 ====================
def phase_users(workers: int = 6, limit: int | None = None):
    h = http()
    us = Store('users', 'uid')
    uids = set()
    for path, get in [(RAW / 'posts.jsonl', lambda o: (o.get('raw') or {}).get('user', {}).get('id')),
                      (RAW / 'comments.jsonl', lambda o: (o.get('raw') or {}).get('user', {}).get('id')),
                      (RAW / 'replies.jsonl', lambda o: (o.get('raw') or {}).get('user', {}).get('id')),
                      (RAW / 'reposts.jsonl', lambda o: (o.get('raw') or {}).get('user', {}).get('id'))]:
        if not path.exists():
            continue
        for line in open(path, encoding='utf-8'):
            try:
                u = get(json.loads(line))
            except Exception:
                continue
            if u:
                uids.add(str(u))
    # 名人堂
    ff = META / 'fans_famous.json'
    if ff.exists():
        for c in re.findall(r'"id":\s*(\d{6,})', ff.read_text(encoding='utf-8')):
            uids.add(c)
    todo = [u for u in sorted(uids) if not us.has(u)]
    if limit:
        todo = todo[:limit]
    log(f'users: discovered={len(uids)} todo={len(todo)} have={us.n}')

    def work(uid):
        rec = {'uid': uid, 'fetched_at': _ts()}
        j1, _ = h.get_json('pc', f'{PC}/ajax/profile/info', {'uid': uid}, 'https://weibo.com/')
        j2, _ = h.get_json('pc', f'{PC}/ajax/profile/detail', {'uid': uid}, 'https://weibo.com/')
        j3 = None
        if not (j1 and j2):
            # PC 资料取不到才退回 m 端主页容器
            j3, _ = h.get_json('m', M_GI, {'type': 'uid', 'value': uid}, 'https://m.weibo.cn/')
        rec['info'] = j1
        rec['detail'] = j2
        if j3:
            rec['m_index'] = j3
        if not (j1 or j2 or j3):
            return uid, False
        us.write(rec)
        return uid, True

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (uid, okk) in enumerate(ex.map(work, todo), 1):
            if i % 500 == 0:
                log(f'users: {i}/{len(todo)} stored={us.n}')
    log(f'users done: {us.n}')
