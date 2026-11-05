"""微博超话全量抓取入口。

py -3.12 weibo_super_export/scrape_super.py --phase meta
py -3.12 weibo_super_export/scrape_super.py --phase posts --flows sort_time --workers 6
py -3.12 weibo_super_export/scrape_super.py --phase all
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import super_lib  # noqa: E402
from super_lib import (BASE, FLOWS, META, OUTD, RAW, SCHEME, STATE, Store, TOPIC_NAME, TOPIC_UID,  # noqa: E402
                      log, parse_wb_time, plain)
import super_phases as SP  # noqa: E402


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


# ==================== 导出 ====================
def _topics_of(html_text: str):
    return re.findall(r'#(?:<a[^>]*>)?([^<#>]{1,40}?)(?:</a>)?[!#]', html_text or '') or \
        re.findall(r'#([^#\s<>]{1,30})#', re.sub(r'<[^>]+>', '', html_text or ''))


def _pics_of(raw: dict):
    """返回该帖图片的「最优档」URL 列表（mw2000 无水印原图优先）。"""
    urls = []
    for p in _pic_items(raw):
        u = _pic_best(p)
        if u:
            urls.append(u)
    return urls


_PIC_TIERS = ('mw2000', 'largest', 'original', 'large', 'bmiddle', 'thumbnail', 'url')


def _pic_items(raw: dict):
    pi = raw.get('pic_infos')
    items = list(pi.values()) if isinstance(pi, dict) else []
    if not items:
        items = [p for p in (raw.get('pics') or []) if isinstance(p, dict)]
    return items


def _tier_url(p: dict, tier: str) -> str:
    v = p.get(tier)
    if isinstance(v, dict):
        return v.get('url') or ''
    return v if isinstance(v, str) and v.startswith('http') else ''


def _pic_best(p: dict) -> str:
    for t in _PIC_TIERS:
        u = _tier_url(p, t)
        if u:
            return u
    large = p.get('large')
    if isinstance(large, dict):
        return large.get('url') or ''
    return ''


def _pic_geo(p: dict):
    for t in _PIC_TIERS:
        v = p.get(t)
        if isinstance(v, dict) and v.get('width'):
            return v.get('width', ''), v.get('height', '')
    g = p.get('geo') or {}
    if isinstance(g, dict):
        lg = (p.get('large') or {}).get('geo') if isinstance(p.get('large'), dict) else None
        src = lg if isinstance(lg, dict) and lg.get('width') else g
        return src.get('width', ''), src.get('height', '')
    return '', ''


def _pic_kind(p: dict, url: str) -> str:
    t = str(p.get('type') or '').lower()
    if t == 'gif' or url.lower().endswith('.gif'):
        return 'GIF'
    if t == 'livephoto':
        return '实况照片'
    if t in ('dynamic', 'video', 'gifvideos'):
        return '动态图'
    if t == 'pic' or not t:
        return '图片'
    return t


def _media_of(raw: dict, mid: str, ts: str, uid: str, src_tag: str = ''):
    """一帖的全部媒体行：图片（分档）+ 图片自带动图视频 + 视频（多码率）。"""
    rows = []
    for p in _pic_items(raw):
        u = _pic_best(p)
        if not u:
            continue
        w, hh = _pic_geo(p)
        rows.append({'mid': mid, '类型': _pic_kind(p, u), 'pid': p.get('pid') or p.get('pic_id') or '',
                     '首选URL': u,
                     '高清URL': _tier_url(p, 'mw2000') or _tier_url(p, 'largest') or _tier_url(p, 'original'),
                     '标清URL': _tier_url(p, 'bmiddle') or _tier_url(p, 'thumbnail'),
                     '封面URL': '', '播放页': '', '宽': w, '高': hh, '时长': '',
                     '动图视频': (p.get('video') if isinstance(p.get('video'), str) else _tier_url(p, 'video'))
                                 or p.get('videoSrc') or '',
                     '来源': src_tag or '原帖', '时间': ts, '作者UID': uid})
    pg = raw.get('page_info') or {}
    if isinstance(pg, dict) and (pg.get('media_info') or pg.get('type') == 'video'
                                 or pg.get('page_type') == 20 or str(pg.get('type')) in ('11',)):
        mi = pg.get('media_info') or {}
        cov = pg.get('page_pic')
        rows.append({'mid': mid, '类型': '视频', 'pid': pg.get('object_id') or mi.get('object_id') or '',
                     '首选URL': (mi.get('stream_url') or mi.get('mp4_hd_url') or mi.get('h264_mp4hdvod_url')
                                 or (pg.get('urls') or {}).get('mp4_hd') or ''),
                     '高清URL': mi.get('stream_url_hd') or mi.get('mp4_hd_url') or '',
                     '标清URL': mi.get('mp4_sd_url') or '',
                     '封面URL': cov.get('url', '') if isinstance(cov, dict) else '',
                     '播放页': pg.get('page_url') or '', '宽': '', '高': '',
                     '时长': str(mi.get('video_duration') or pg.get('video_total_duration') or ''),
                     '动图视频': '', '来源': src_tag or '原帖', '时间': ts, '作者UID': uid})
    return rows


def _attach_of(raw: dict, mid: str, ts: str, uid: str, src_tag: str = ''):
    """帖子挂的非视频卡片（超话/位置/网页/活动/粉丝大咖等），单独成表，避免只留在 raw 里看不见。"""
    pg = raw.get('page_info') or {}
    if not isinstance(pg, dict) or not pg:
        return []
    if pg.get('media_info') or pg.get('type') == 'video' or str(pg.get('type')) == '11':
        return []      # 视频已进媒体清单
    cov = pg.get('page_pic')
    return [{'mid': mid, '类型': str(pg.get('type') or pg.get('page_type') or 'card'),
             '标题': pg.get('page_title') or pg.get('title') or pg.get('content1') or '',
             '摘要': ' | '.join(str(pg.get(k) or '') for k in ('content1', 'content2', 'page_desc')
                              if pg.get(k)),
             'object_id': str(pg.get('object_id') or pg.get('page_id') or ''),
             'URL': pg.get('page_url') or pg.get('url_ori') or '',
             'PC地址': pg.get('page_pc_url') or '', 'scheme': pg.get('scheme') or '',
             '封面': cov.get('url', '') if isinstance(cov, dict) else '',
             '内嵌卡片数': len(pg.get('cards') or []), '来源': src_tag or '原帖',
             '时间': ts, '作者UID': uid}]


def _video_of(raw: dict):
    pi = raw.get('page_info') or {}
    if not isinstance(pi, dict):
        return '', ''
    if not (pi.get('media_info') or pi.get('type') == 'video' or pi.get('page_type') == 20
            or str(pi.get('type')) == '11'):
        return '', ''
    mi = pi.get('media_info') or {}
    v = (mi.get('stream_url') or mi.get('mp4_hd_url') or mi.get('h264_mp4hdvod_url')
         or (pi.get('urls') or {}).get('mp4_hd') or '')
    cover = pi.get('page_pic')
    return v, (cover.get('url', '') if isinstance(cover, dict) else '')


# 抽样实测的单文件均值（MB），仅用于「全量下载要占多少盘」的估算
_MEDIA_MB = {'图片': 0.54, '实况照片': 0.67, '动态图': 0.22, 'GIF': 1.43,
             'gifvideos': 2.18, '视频': 15.4, '视频封面': 0.10}
_LIVEVIDEO_MB = 2.31        # 实况照片自带的短视频


def _media_stats(media_rows):
    uniq = defaultdict(set)
    live = set()
    for m in media_rows:
        if m.get('首选URL'):
            uniq[m['类型']].add(m['首选URL'])
        if m.get('动图视频'):
            live.add(m['动图视频'])
    est = sum(len(uniq.get(k, ())) * mb for k, mb in _MEDIA_MB.items())
    est += len(live) * _LIVEVIDEO_MB
    return {'按类型唯一URL': {k: len(v) for k, v in sorted(uniq.items())},
            '动图自带视频': len(live),
            '估算下载体积GB': round(est / 1e3, 2)}


def _flowcard_rows():
    """图册/视频/直播流的卡片里抽媒体：这些卡片不是 mblog，帖子表收录不到，单独成表并入媒体清单。"""
    _KEYS = ('mid', 'id', 'itemid', 'mblog_id', 'status_id')
    out, seen = [], set()

    def walk(node, ctx):
        if isinstance(node, list):
            for v in node:
                walk(v, ctx)
            return
        if not isinstance(node, dict):
            return
        c2 = ctx
        for k in _KEYS:
            v = node.get(k)
            if isinstance(v, (str, int)) and re.fullmatch(r'\d{15,20}', str(v)):
                c2 = str(v)
                break
        streams = [node[k] for k in ('stream_url', 'stream_url_hd', 'mp4_hd_url', 'mp4_sd_url',
                                     'h264_mp4hdvod_url') if isinstance(node.get(k), str)]
        if streams:
            u = next((s for s in streams if s.startswith('http')), '')
            if u and (c2, u) not in seen:
                seen.add((c2, u))
                out.append({'mid': c2, '类型': '视频', 'pid': str(node.get('object_id') or node.get('fid') or ''),
                            '首选URL': u, '高清URL': node.get('stream_url_hd') or node.get('mp4_hd_url') or '',
                            '标清URL': node.get('mp4_sd_url') or '',
                            '封面URL': (node.get('cover_url') or node.get('page_pic') or ''),
                            '播放页': str(node.get('page_url') or ''), '宽': node.get('width', ''),
                            '高': node.get('height', ''), '时长': str(node.get('video_duration') or ''),
                            '动图视频': '', '来源': '卡片流', '时间': '', '作者UID': str(node.get('uid') or '')})
            return
        pic = node.get('url') if isinstance(node.get('url'), str) else ''
        if pic.startswith('http') and ('large' in node or 'thumbnail' in node or node.get('pid')
                                       or node.get('pic_id') or pic.lower().endswith(('.jpg', '.png', '.gif', '.webp'))):
            if (c2, pic) not in seen:
                seen.add((c2, pic))
                out.append({'mid': c2, '类型': _pic_kind(node, pic),
                            'pid': str(node.get('pid') or node.get('pic_id') or ''), '首选URL': pic,
                            '高清URL': _tier_url(node, 'mw2000') or _tier_url(node, 'largest')
                                     or _tier_url(node, 'original') or pic,
                            '标清URL': _tier_url(node, 'bmiddle') or _tier_url(node, 'thumbnail'),
                            '封面URL': '', '播放页': '', '宽': node.get('width', ''),
                            '高': node.get('height', ''), '时长': '',
                            '动图视频': (node.get('video') if isinstance(node.get('video'), str)
                                        else _tier_url(node, 'video')) or node.get('videoSrc') or '',
                            '来源': '卡片流', '时间': '', '作者UID': str(node.get('uid') or '')})
            return
        for v in node.values():
            walk(v, c2)

    for p in sorted(RAW.glob('flowcards_*.jsonl')):
        flow = p.stem.replace('flowcards_', '')
        for o in read_jsonl(p):
            card = o.get('card') or {}
            data = card.get('data') if isinstance(card.get('data'), dict) else card
            walk({'flow': flow, 'page': o.get('page'), 'data': data}, '')
    out.sort(key=lambda r: (str(r['mid']), r['类型']))
    return out


def export():
    posts = {p['mid']: p for p in read_jsonl(RAW / 'posts.jsonl')}
    details = {d['mid']: d for d in read_jsonl(RAW / 'details.jsonl')}
    longtext = {l['mid']: l for l in read_jsonl(RAW / 'longtext.jsonl')}
    videos = {v['mid']: v for v in read_jsonl(RAW / 'video_src.jsonl')}
    exts = {e['mid']: e for e in read_jsonl(RAW / 'ext.jsonl')}   # statuses/extend 增量面
    comments = list(read_jsonl(RAW / 'comments.jsonl'))
    replies = list(read_jsonl(RAW / 'replies.jsonl'))
    reposts = list(read_jsonl(RAW / 'reposts.jsonl'))
    users = {u['uid']: u for u in read_jsonl(RAW / 'users.jsonl')}
    cstats = list(read_jsonl(RAW / 'comment_stats.jsonl'))
    rr = STATE / 'replies_report.json'
    reply_report = json.loads(rr.read_text(encoding='utf-8')) if rr.exists() else {}
    log(f'export inputs: posts={len(posts)} details={len(details)} longtext={len(longtext)} ext={len(exts)} '
        f'comments={len(comments)} replies={len(replies)} reposts={len(reposts)} users={len(users)}')

    flows_by_mid = {}
    for f in STATE.glob('flows_*.json'):
        for mid, fl in json.loads(f.read_text(encoding='utf-8')).items():
            flows_by_mid.setdefault(mid, set()).update(fl)

    # ---- 帖子总表 ----
    rows = []
    media_rows = []
    attach_rows = []
    post_meta = {}
    for mid, p in posts.items():
        raw = p.get('raw') or {}
        det = (details.get(mid) or {}).get('raw') or {}
        ex = (exts.get(mid) or {}).get('raw') or {}
        merged = {**raw, **{k: v for k, v in det.items() if v not in (None, '', {})}} if det else raw
        text = ''
        if mid in longtext:
            text = longtext[mid].get('text') or ''
        if not text:
            text = merged.get('text_raw') or plain(merged.get('text') or '')
        ts = parse_wb_time(merged.get('created_at') or '')
        uid = str(((merged.get('user') or {}).get('id')) or '')
        post_meta[mid] = (ts, uid)
        rt = merged.get('retweeted_status') or {}
        pics = _pics_of(merged)
        vurl, vcover = _video_of(merged)
        vs = videos.get(mid) or {}
        vr = (vs.get('raw') or {}).get('data') or {}
        if not vurl:
            vurl = (vr.get('stream_url') or vr.get('mp4_sd_url') or '')
        rows.append({
            'mid': mid, '时间': ts, '日期': ts[:10], '年': ts[:4], '月': ts[:7],
            '作者UID': uid, '作者昵称': (merged.get('user') or {}).get('screen_name', ''),
            '正文': text, '是否长文': 1 if merged.get('isLongText') else 0,
            '是否转发': 1 if rt else 0, '被转发mid': str(rt.get('id') or ''),
            '被转发作者': ((rt.get('user') or {}).get('screen_name', '')) if rt else '',
            '被转发正文': (rt.get('text_raw') or plain(rt.get('text') or '')) if rt else '',
            '图数': len(pics) or int(merged.get('pic_num') or 0),
            '图片URL': ' | '.join(pics), '视频URL': vurl, '视频封面': vcover,
            '话题': ' | '.join(_topics_of(raw.get('text') or '')),
            '点赞': merged.get('attitudes_count', 0), '评论数': merged.get('comments_count', 0),
            '转发数': merged.get('reposts_count', 0), '阅读数': merged.get('read_count', '') or (det.get('read_count', '') if isinstance(det, dict) else ''),
            '收藏数': merged.get('favorites_count') or ex.get('favorites_count', 0),
            '表态分布': ';'.join(f"{a.get('type')}={a.get('count')}" for a in (ex.get('multi_attitude') or [])
                                  if isinstance(a, dict)),
            '最热表态': ex.get('most_attitude_type', ''), '可转载': int(bool(ex.get('can_reprint'))) if 'can_reprint' in ex else '',
            '图片类型': ex.get('pic_types', ''), '图片贴纸': ex.get('stickerID', ''),
            '图片滤镜': ex.get('filterID', ''), '图内视频素材': ex.get('pic_video', ''),
            '作者粉丝数快照': ex.get('followers_count', ''), '作者关注数快照': ex.get('friends_count', ''),
            'IP归属': merged.get('region_name', ''), '客户端': merged.get('source', ''),
            '可见性': json.dumps(merged.get('visible'), ensure_ascii=False) if merged.get('visible') else '',
            '链接': f"https://weibo.com/{uid}/{merged.get('bid') or mid}" if uid else f'https://m.weibo.cn/status/{mid}',
            '来源流': ' | '.join(sorted(flows_by_mid.get(mid, {p.get('flow', '')}))),
            '抓取时间': p.get('fetched_at', ''),
        })
        for m in _media_of(merged, mid, ts, uid):
            media_rows.append(m)
        if rt:
            media_rows.extend(_media_of(rt, mid, ts, uid, '转发内'))
        attach_rows.extend(_attach_of(merged, mid, ts, uid))
        if rt:
            attach_rows.extend(_attach_of(rt, mid, ts, uid, '转发内'))
    rows.sort(key=lambda r: r['时间'])
    _write(OUTD / '超话帖子.csv', rows)

    # 详情/视频接口补到的更完整媒体（同 mid 同 URL 不重复列）
    have = {(m['mid'], m['首选URL']) for m in media_rows}
    seen_att = {(a['mid'], a['类型'], a['URL']) for a in attach_rows}
    for mid, d in details.items():
        dr = d.get('raw') or {}
        pm = post_meta.get(mid, ('', ''))
        for m in _media_of(dr, mid, pm[0] or parse_wb_time(dr.get('created_at') or ''),
                           pm[1] or str(((dr.get('user') or {}).get('id')) or ''), '详情'):
            if m['首选URL'] and (mid, m['首选URL']) not in have:
                have.add((mid, m['首选URL']))
                media_rows.append(m)
        for a in _attach_of(dr, mid, pm[0], pm[1], '详情'):
            if (a['mid'], a['类型'], a['URL']) not in seen_att:
                seen_att.add((a['mid'], a['类型'], a['URL']))
                attach_rows.append(a)
    for mid, vd_ in videos.items():
        vd = ((vd_.get('raw') or {}).get('data') or {})
        pm = post_meta.get(mid, ('', ''))
        u = (vd.get('stream_url_hd') or vd.get('stream_url') or vd.get('mp4_hd_url')
             or vd.get('mp4_sd_url') or '')
        if u and (mid, u) not in have:
            have.add((mid, u))
            media_rows.append({'mid': mid, '类型': '视频', 'pid': vd.get('object_id', ''), '首选URL': u,
                               '高清URL': vd.get('stream_url_hd') or vd.get('mp4_hd_url') or '',
                               '标清URL': vd.get('mp4_sd_url') or '', '封面URL': vd.get('cover_url') or '',
                               '播放页': '', '宽': vd.get('width', ''), '高': vd.get('height', ''),
                               '时长': str(vd.get('video_duration') or ''), '动图视频': '',
                               '来源': 'getPcVideoInfo', '时间': pm[0], '作者UID': pm[1]})
    media_rows.sort(key=lambda m: (m['时间'], str(m['mid'])))

    attach_rows.sort(key=lambda a: (a['时间'], str(a['mid'])))
    _write(OUTD / '帖子附件卡片.csv', attach_rows)

    # ---- 图册/视频/直播流的卡片内容（这些流的卡片不是 mblog，帖子表里没有）----
    crows_media = _flowcard_rows()
    _write(OUTD / '超话图集与视频流.csv', crows_media)
    seen_url = {m['首选URL'] for m in media_rows}
    media_rows.extend(m for m in crows_media if m['首选URL'] not in seen_url)
    media_rows.sort(key=lambda m: (m['时间'], str(m['mid'])))
    _write(OUTD / '媒体清单.csv', media_rows)

    # ---- 评论表（顶层 + 子评论，按 reply_id 认父；跨文件按评论ID去重）----
    def _cmt_pics(r: dict) -> str:
        infos = r.get('pic_infos')
        items = list(infos.values()) if isinstance(infos, dict) else (infos or [])
        urls = [str(((p or {}).get('large') or {}).get('url') or (p or {}).get('bmiddle') or '')
                for p in items if isinstance(p, dict)]
        urls = [u for u in urls if u]
        if not urls:
            p = r.get('pic')
            if isinstance(p, dict) and p.get('url'):
                urls = [p['url']]
        return ' | '.join(urls)[:600]

    def crow(o):
        r = o.get('raw') or {}
        u = r.get('user') or {}
        parent = r.get('reply_id') or o.get('root') or ''
        return {'类型': '子评论' if parent else '顶层', '评论ID': r.get('id'),
                '所属帖子': o.get('post_mid') or o.get('root') or (r.get('mid') or ''),
                '父评论ID': parent, '根评论ID': r.get('rootid') or (parent or r.get('id') or ''),
                '楼层': r.get('floor_number', ''), '时间': parse_wb_time(r.get('created_at') or ''),
                'UID': u.get('id', ''), '昵称': u.get('screen_name', ''),
                '内容': r.get('text_raw') or plain(r.get('text') or ''),
                '点赞': r.get('attitudes_count', 0), '回复数': r.get('total_number', 0),
                'IP归属': r.get('ipcomment_location', '') or r.get('region_name', ''),
                '评论图片': _cmt_pics(r), '抓取时间': o.get('fetched_at', '')}
    by_cid = {}
    for o in comments:
        k = str((o.get('raw') or {}).get('id') or '')
        if k and k not in by_cid:
            by_cid[k] = crow(o)
    for o in replies:
        k = str((o.get('raw') or {}).get('id') or '')
        if k and k not in by_cid:
            by_cid[k] = crow(o)
    crows = list(by_cid.values())
    crows.sort(key=lambda x: (str(x['所属帖子']), x['类型'], str(x['楼层'])))
    _write(OUTD / '超话评论.csv', crows)

    # ---- 转发表 ----
    rrows = []
    for o in reposts:
        r = o.get('raw') or {}
        u = r.get('user') or {}
        rrows.append({'源帖mid': o.get('src_mid'), '转发ID': r.get('id'),
                      '时间': parse_wb_time(r.get('created_at') or ''), 'UID': u.get('id', ''),
                      '昵称': u.get('screen_name', ''), '正文': r.get('text_raw') or plain(r.get('text') or ''),
                      '点赞': r.get('attitudes_count', 0), '评论数': r.get('comments_count', 0),
                      'IP归属': r.get('region_name', ''),
                      '链接': f"https://weibo.com/{u.get('id','')}/{r.get('bid') or r.get('idstr','')}",
                      '抓取时间': o.get('fetched_at', '')})
    rrows.sort(key=lambda x: x['时间'])
    _write(OUTD / '超话转发.csv', rrows)

    # ---- 作者表 ----
    urows = []
    for uid, u in users.items():
        info = ((u.get('info') or {}).get('data') or {}).get('user') or {}
        detail = ((u.get('detail') or {}).get('data') or {}).get('user') or {}
        mi = (((u.get('m_index') or {}).get('data') or {}).get('userInfo') or {}).get('user') or {}
        g = lambda k, *srcs: next((s.get(k) for s in srcs if isinstance(s, dict) and s.get(k) not in (None, '')), '')  # noqa: E731
        urows.append({'UID': uid, '昵称': g('screen_name', info, detail, mi),
                      '粉丝数': g('followers_count', info, detail, mi), '关注数': g('follow_count', info, detail, mi),
                      '微博数': g('statuses_count', info, detail, mi), '认证': g('verified_reason', info, detail, mi),
                      '认证类型': g('verified_type', info, detail, mi), '性别': g('gender', info, detail, mi),
                      '简介': g('description', info, detail, mi), '超话等级': g('mblogtype', info) or '',
                      '头像': g('avatar_hd', info, detail, mi), '主页': f'https://weibo.com/u/{uid}',
                      '抓取时间': u.get('fetched_at', '')})
    urows.sort(key=lambda x: str(x['昵称']))
    _write(OUTD / '超话作者.csv', urows)

    # ---- 名人堂 / 徽章墙 / 公告（登录后才有的元信息面）----
    mrows = []
    for fname, kind in (('fans_famous.json', '名人堂'), ('topicfriends.json', '超话好友'),
                        ('profile_inpage.json', 'TA在超话')):
        p = META / fname
        if not p.exists():
            continue
        seen = set()
        for o in json.loads(p.read_text(encoding='utf-8')):
            stack = [o]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    if x.get('id') and x.get('screen_name'):
                        k = (kind, str(x['id']))
                        if k not in seen:
                            seen.add(k)
                            mrows.append({'类别': kind, 'UID': x.get('id'), '昵称': x.get('screen_name'),
                                          '粉丝数': x.get('followers_count', ''), '备注': ''})
                    stack.extend(x.values())
                elif isinstance(x, list):
                    stack.extend(x)
    bp = META / 'profile_allbadge.json'
    if bp.exists():
        seen = set()
        stack = [json.loads(bp.read_text(encoding='utf-8'))]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if x.get('badge_id') or (x.get('name') and x.get('icon')):
                    nm = x.get('name') or ''
                    if nm and ('badge_id', nm) not in seen:
                        seen.add(('badge', nm))
                        mrows.append({'类别': '徽章', 'UID': x.get('badge_id', ''), '昵称': nm,
                                      '粉丝数': '', '备注': x.get('icon', '')})
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    mrows.sort(key=lambda r: (r['类别'], str(r['昵称'])))
    _write(OUTD / '超话成员与徽章.csv', mrows)

    # ---- 统计与对账 ----
    meta = json.loads((META / 'topic.json').read_text(encoding='utf-8')) if (META / 'topic.json').exists() else {}
    total_declared = (meta.get('pageInfo') or {}).get('total')
    by_year, by_month, by_author = Counter(), Counter(), Counter()
    for r in rows:
        by_year[r['年']] += 1
        by_month[r['月']] += 1
        by_author[f"{r['作者昵称']}({r['作者UID']})"] += 1
    stats = {
        '超话': {'名称': TOPIC_NAME, 'scheme': SCHEME, '官方帖子总数': total_declared,
                 '抓到唯一帖子数': len(rows), '覆盖率': round(len(rows) / total_declared, 4) if total_declared else None,
                 '缺口说明': '官方计数含已删除/仅自己可见/被权限屏蔽的帖子，游标链走到尽头即到底',
                 '时间跨度': [rows[0]['时间'], rows[-1]['时间']] if rows else None},
        '按年': dict(sorted(by_year.items())),
        '按月_近24': dict(sorted(by_month.items())[-24:]),
        '发帖Top50': by_author.most_common(50),
        '帖子': {'含图': sum(1 for r in rows if r['图数']), '含视频': sum(1 for r in rows if r['视频URL']),
                 '长文': sum(1 for r in rows if r['是否长文']), '转发帖': sum(1 for r in rows if r['是否转发']),
                 '超话专属人发帖': sum(1 for r in rows if r['作者UID'] == str(TOPIC_UID)),
                 '详情已补全': len(details), '长文已抓': len(longtext), 'extend已抓': len(exts),
                 '收藏数合计': sum(int(r['收藏数'] or 0) for r in rows),
                 '点赞合计': sum(int(r['点赞'] or 0) for r in rows),
                 '评论声明合计': sum(int(r['评论数'] or 0) for r in rows),
                 '转发声明合计': sum(int(r['转发数'] or 0) for r in rows)},
        '媒体': _media_stats(media_rows),
        '附件卡片': dict(Counter(a['类型'] for a in attach_rows).most_common(15)),
        '图集与视频流卡片': {'媒体条目': len(crows_media),
                             '含卡片流': sorted({p.stem.replace('flowcards_', '') for p in RAW.glob('flowcards_*.jsonl')})},
        '评论': {'唯一评论数': len(crows), '其中顶层': sum(1 for c in crows if c['类型'] == '顶层'),
                 '其中子评论': sum(1 for c in crows if c['类型'] == '子评论'),
                 '声明合计': sum(int(s.get('declared') or 0) for s in cstats),
                 '抓到合计': sum(int(s.get('uniq_got') or 0) for s in cstats),
                 '已抓齐帖子数': sum(1 for s in cstats if s.get('complete')),
                 '仍有缺口帖子数': sum(1 for s in cstats
                                      if int(s.get('declared') or 0) > int(s.get('uniq_got') or 0))},
        '转发': {'抓到': len(reposts)},
        '评论树对账': {k: v for k, v in reply_report.items() if k != '缺口Top50'} if reply_report else {},
        '作者': {'抓到资料': len(users)},
        '翻页状态': {f.stem.replace('pages_', ''): {'页数': len(json.loads(f.read_text(encoding='utf-8'))['done']),
                                                    '耗尽页': json.loads(f.read_text(encoding='utf-8')).get('exhausted_page')}
                    for f in STATE.glob('pages_*.json')},
        '游标链': {f.stem.replace('cursor_', ''): {k: v for k, v in json.loads(f.read_text(encoding='utf-8')).items()
                                                   if k in ('steps', 'page', 'new_total', 'card_total', 'exhausted')}
                  for f in STATE.glob('cursor_*.json')},
        '生成时间': datetime.now().isoformat(timespec='seconds'),
    }
    (OUTD / '统计.json').write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
    log('export done: ' + ', '.join(p.name for p in OUTD.glob('*')))

    # 可选 xlsx
    try:
        import pandas as pd
        with pd.ExcelWriter(OUTD / '超话数据.xlsx') as w:
            pd.DataFrame(rows).to_excel(w, sheet_name='帖子', index=False)
            if crows:
                pd.DataFrame(crows).to_excel(w, sheet_name='评论', index=False)
            if rrows:
                pd.DataFrame(rrows).to_excel(w, sheet_name='转发', index=False)
            if urows:
                pd.DataFrame(urows).to_excel(w, sheet_name='作者', index=False)
            if crows_media:
                pd.DataFrame(crows_media).to_excel(w, sheet_name='图集与视频流', index=False)
            if attach_rows:
                pd.DataFrame(attach_rows).to_excel(w, sheet_name='帖子附件卡片', index=False)
            if mrows:
                pd.DataFrame(mrows).to_excel(w, sheet_name='成员与徽章', index=False)
            pd.DataFrame(media_rows[:300000]).to_excel(w, sheet_name='媒体清单', index=False)
        log('xlsx written')
    except Exception as e:
        log(f'xlsx skipped: {type(e).__name__} {e}')


def _write(path: Path, rows):
    if not rows:
        log(f'skip empty {path.name}')
        return
    cols = list(rows[0].keys())
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log(f'wrote {path.name} rows={len(rows)}')


# ==================== main ====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', default='all',
                    help='meta|posts|detail|ext|comments|replies|reposts|users|export|all')
    ap.add_argument('--flows', default='', help='逗号分隔，仅抓这些流（sort_time,feed,...）')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--rate', type=float, default=3.5, help='全局每秒请求上限')
    ap.add_argument('--max-pages', type=int, default=9000)
    ap.add_argument('--limit', type=int, default=0, help='限制处理条目数（试跑）')
    ap.add_argument('--m-pages', action='store_true',
                    help='帖子流额外跑一轮 m 端 page 翻页（200 页后会吐重复窗口，只在需要交叉补漏时用）')
    args = ap.parse_args()

    logpath = STATE / f'run_{datetime.now():%Y%m%d_%H%M%S}.log'
    super_lib.LOGF = open(logpath, 'a', encoding='utf-8')
    SP.RATE = args.rate
    log(f'start phase={args.phase} workers={args.workers} rate={args.rate}/s log={logpath.name}')

    phs = [p.strip() for p in args.phase.split(',')] if args.phase != 'all' else \
        ['posts', 'detail', 'comments', 'replies', 'reposts', 'users', 'export', 'ext', 'export']
    lim = args.limit or None
    for p in phs:
        t0 = time.time()
        if p == 'meta':
            SP.phase_meta()
        elif p == 'posts':
            flows = [f.strip() for f in args.flows.split(',') if f.strip()] or None
            SP.phase_posts(flows, args.workers, args.max_pages, m_pages=args.m_pages)
        elif p == 'detail':
            SP.phase_detail(args.workers, lim)
        elif p == 'ext':
            SP.phase_ext(args.workers, lim)
        elif p == 'comments':
            SP.phase_comments(args.workers, lim)
        elif p == 'replies':
            SP.phase_replies(args.workers, lim)
        elif p == 'reposts':
            SP.phase_reposts(args.workers, lim)
        elif p == 'users':
            SP.phase_users(args.workers, lim)
        elif p == 'export':
            export()
        else:
            log(f'unknown phase {p}')
        log(f'phase {p} finished in {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
