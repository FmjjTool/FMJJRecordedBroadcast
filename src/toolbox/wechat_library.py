"""公众号资料与文章归档用例（L3，Qt-free）。

桌面与 WebUI 共用，元数据仅经 wechat_store 写入；网络只经平台适配。
同步只读取用户当前获准的阅读会话，逐页检查身份，不持久化/刷新登录态。
同一公众号同步和同一导出根目录均有跨进程排他锁。导出不包含请求快照。
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile
import threading
import time
from pathlib import Path

from toolbox import wechat_store as store
from toolbox.durable_io import exclusive_file_lock

STATUS_LABELS = store.STATUS_LABELS
MAX_LINKS = 30
MAX_EXPORT = 200
MAX_INDEX = 50000
FORMATS = ('html', 'docx', 'md', 'pdf')
# “抓取全部”持续读取到平台返回历史末尾，同时保留异常保护上限，
# 防止平台返回不断前进的伪分页时任务无限运行。
MAX_ALL_SYNC_PAGES = 1000


class WechatCancelled(ValueError):
    code = 'Cancelled'


class WechatUserError(ValueError):
    """本用例定义的安全提示，不能装入外部异常文本。"""


def extract_urls(text):
    from platforms.wechat_article import extract_urls as extract
    return extract(str(text or ''))


def current_export_dir():
    from toolbox.config_manager import get_export_subdir, load_convert_config
    cfg = load_convert_config()
    if cfg.get('wechat_use_custom_dir') and cfg.get('wechat_custom_dir'):
        return str(Path(str(cfg['wechat_custom_dir'])).resolve())
    return str(get_export_subdir('wechat'))


def list_accounts(keyword='', sort='', order='', offset=0, limit=200):
    return store.list_accounts(keyword, sort, order, offset, limit)


def save_account(biz, name='', remark=''):
    return store.save_account(biz, name, remark)


def list_articles(biz='', keyword='', date_from='', date_to='', status='', sort='', order='', offset=0, limit=200):
    return store.list_articles(biz, keyword, date_from, date_to, status, sort, order, offset, limit)


def safe_error(exc):
    """不把网络异常的请求 URL、响应体或磁盘绝对路径传到日志/前端。"""
    if isinstance(exc, WechatUserError):
        return str(exc)
    code = getattr(exc, 'code', '')
    if code in ('AuthRequired', 'auth_required'):
        return '需要登录，请在登录状态可用后重试'
    if code in ('RateLimited', 'rate_limited'):
        return '请求过于频繁，请稍后重试'
    if code in ('Blocked', 'blocked'):
        return '平台暂时拒绝访问，请稍后重试'
    if code in ('NotFound', 'not_found'):
        return '文章不存在或已删除'
    if code in ('ParseChanged', 'parse_changed'):
        return '文章结构可能已变化，请检查链接后重试'
    if code in ('Unsupported', 'unsupported'):
        return '当前文章类型或历史读取方式暂不支持'
    if isinstance(exc, PermissionError):
        return '导出位置不可写，请选择其他位置后重试'
    if isinstance(exc, WechatCancelled):
        return '已取消'
    return '抓取或导出失败，请检查网络、文章权限及导出位置后重试'


user_error = safe_error


def _check(cancel):
    if cancel and cancel():
        raise WechatCancelled('已取消')


def _progress(callback, done, total, text):
    if callback:
        callback(int(done), int(total), str(text))


def _log(callback, text):
    if callback:
        callback(store.clean_text(text, 700))


def _cooldown(cancel, seconds=1.2):
    deadline = time.monotonic() + seconds
    event = threading.Event()
    while time.monotonic() < deadline:
        _check(cancel)
        event.wait(min(.1, max(0., deadline - time.monotonic())))


def _urls(text):
    if len(str(text or '')) > 200000:
        raise WechatUserError('链接文本过长，请分批处理')
    urls = extract_urls(text)
    if not urls:
        raise WechatUserError('未识别到公众号文章链接，请检查后重试')
    if len(urls) > MAX_LINKS:
        raise WechatUserError('单次最多 30 篇文章，请分批处理')
    return urls


def _record(article):
    from platforms.wechat_article import canonical_article_url
    article.url = canonical_article_url(article.url)
    record = {key: getattr(article, key, '') for key in
              ('url', 'title', 'account', 'biz', 'author', 'publish_time')}
    # 对 metadata 中的完整 URL 同样做去敏，正文由平台层清洗。
    for key in ('title', 'account', 'author'):
        record[key] = store.clean_text(record[key], 500 if key == 'title' else 160)
        setattr(article, key, record[key])
    biz = str(record.get('biz') or '')
    if biz:
        store.save_account(biz, record.get('account') or '', remark=None,
                           avatar=getattr(article, 'avatar', '') or getattr(article, 'avatar_url', ''))
    return store.upsert_article(record)[0]


def collect_links(text, on_progress=None, on_log=None, is_cancelled=None):
    from platforms.wechat_article import fetch_article
    urls = _urls(text)
    ok = failed = 0
    cancelled = False
    for n, url in enumerate(urls, 1):
        try:
            _check(is_cancelled)
            _progress(on_progress, n - 1, len(urls), f'抓取文章 {n}/{len(urls)}')
            article = fetch_article(url)
            _check(is_cancelled)
            _record(article)
            ok += 1
            _log(on_log, f'[{n}/{len(urls)}] 已归档：{article.title}')
        except WechatCancelled:
            cancelled = True
            break
        except Exception as exc:
            failed += 1
            key = store.article_id(url)
            if store.get_article(key) is None:
                store.upsert_article({'url': url, 'title': '待重试文章'})
            store.set_status(key, 'failed', safe_error(exc))
            _log(on_log, f'[{n}/{len(urls)}] {safe_error(exc)}')
        try:
            _progress(on_progress, n, len(urls), f'已归档 {ok} 篇，失败 {failed} 篇')
            if n < len(urls):
                _cooldown(is_cancelled)
        except WechatCancelled:
            cancelled = True
            break
    summary = f'{"已取消" if cancelled else "已完成"}：归档 {ok} 篇，失败 {failed} 篇'
    return {'ok': ok, 'failed': failed, 'cancelled': cancelled, 'summary': summary}


def sync_account(biz, mode='latest', max_pages=10, on_progress=None, on_log=None, is_cancelled=None):
    from platforms.wechat_article import fetch_history_page
    from wechat_session import get_session, is_current
    biz = store.validate_biz(biz)
    if mode not in ('latest', 'continue', 'all') or not 1 <= int(max_pages) <= 50:
        raise WechatUserError('同步模式或页数范围无效')
    account = store.get_account(biz)
    if not account:
        raise WechatUserError('请先从文章链接识别并保存公众号资料')
    session = get_session()
    if session is None:
        raise WechatUserError('需要登录，请在登录状态可用后重试')
    cursor = int(account.get('cursor') or 0) if mode in ('continue', 'all') else 0
    if mode in ('continue', 'all') and account.get('history_done'):
        return {'ok': 0, 'failed': 0, 'summary': '历史记录已读取完毕，可同步最新文章'}
    digest = hashlib.sha256(biz.encode()).hexdigest()[:20]
    lock = store._path().parent / ('wechat_sync_' + digest + '.lock')
    added = pages = 0
    cancelled = False
    stopped_at_existing = False
    exhausted = False
    page_limit = MAX_ALL_SYNC_PAGES if mode == 'all' else int(max_pages)
    limit_reached = False
    seen_cursors = set()
    with exclusive_file_lock(lock, timeout=0):
        for _ in range(page_limit):
            try:
                _check(is_cancelled)
                if not is_current(session):
                    raise WechatUserError('登录状态已变化，请重新开始本次同步')
                _progress(on_progress, pages, int(max_pages), f'抓取第 {pages + 1} 页')
                page = fetch_history_page(biz, cursor, session)
                _check(is_cancelled)
                if not is_current(session):
                    raise WechatUserError('登录状态已变化，本次同步已停止')
                records = page.get('articles')
                if not isinstance(records, list) or len(records) > 200:
                    raise WechatUserError('文章列表结构可能已变化，请稍后重试')
                for record in records:
                    _check(is_cancelled)
                    if str(record.get('biz') or '') != biz:
                        raise WechatUserError('返回的公众号与本次目标不一致，已停止同步')
                    if store.has_metadata(record['url']):
                        stopped_at_existing = mode == 'latest'
                        if stopped_at_existing:
                            break
                        continue
                    record = dict(record)
                    if not record.get('account'):
                        record['account'] = account.get('name') or ''
                    store.upsert_article(record)
                    added += 1
                pages += 1
                more = bool(page.get('has_more'))
                next_cursor = int(page.get('next_offset', cursor))
                if more and next_cursor <= cursor:
                    raise WechatUserError('历史分页未前进，已停止同步，请稍后重试')
                exhausted = not more
                if mode in ('continue', 'all'):
                    store.set_cursor(biz, next_cursor, done=exhausted)
                elif not account.get('cursor') and not stopped_at_existing:
                    # 首次最新同步建立继续读取断点；后续最新不覆盖旧的历史断点。
                    store.set_cursor(biz, next_cursor, done=exhausted)
                else:
                    store.touch_sync(biz)
                _log(on_log, f'第 {pages} 页已完成，新增 {added} 篇')
                _progress(on_progress, pages, int(max_pages) if mode != 'all' else 0,
                          f'已读取 {pages} 页，新增 {added} 篇')
                cursor = next_cursor
                if stopped_at_existing or exhausted:
                    break
                if mode == 'all' and next_cursor in seen_cursors:
                    limit_reached = True
                    break
                seen_cursors.add(cursor)
                _cooldown(is_cancelled)
            except WechatCancelled:
                cancelled = True
                break
        else:
            limit_reached = True
    note = ('（遇到已归档文章）' if stopped_at_existing else
            '（已到历史末尾）' if exhausted else
            '（已读取保护上限，可继续抓取）' if limit_reached else
            '（可从断点继续）')
    return {'ok': added, 'failed': 0, 'pages': pages, 'cancelled': cancelled,
            'limit_reached': limit_reached,
            'summary': f'{"已取消" if cancelled else "已完成"}：新增 {added} 篇{note}'}


def _formats(formats):
    if not isinstance(formats, (list, tuple)):
        raise WechatUserError('请选择导出格式')
    result = list(dict.fromkeys(str(f) for f in formats))
    if not result or any(f not in FORMATS for f in result):
        raise WechatUserError('请选择支持的导出格式')
    return result


def _export_one(article, formats, out_dir, on_log, on_progress, is_cancelled, index, total):
    from platforms.wechat_article import download_images
    from exporters import wechat_writer
    key = _record(article)
    _check(is_cancelled)
    downloaded, failed = download_images(
        article, progress=lambda n, m, title: _progress(
            on_progress, index - 1, total, f'文章 {index}/{total} · 图片 {n}/{m}'),
        should_cancel=is_cancelled or (lambda: False), threads=6)
    _check(is_cancelled)
    if failed:
        raise WechatUserError('部分图片抓取失败，请稍后重试')
    rel_dir, stem = wechat_writer.build_article_path(article, str(out_dir))
    target = (out_dir / rel_dir).resolve()
    if not target.is_relative_to(out_dir.resolve()):
        raise WechatUserError('导出目录无效')
    target.parent.mkdir(parents=True, exist_ok=True)
    # 在相同卷暂存整篇输出，成功后再逐文件原子替换；不删除用户已有文件。
    with tempfile.TemporaryDirectory(prefix='.wechat-', dir=target.parent) as tmp:
        stage = Path(tmp) / stem
        stage.mkdir()
        if 'html' in formats:
            wechat_writer.export_html(article, str(stage / (stem + '.html')))
        if 'docx' in formats:
            wechat_writer.export_docx(article, str(stage / (stem + '.docx')))
        if 'md' in formats:
            wechat_writer.export_markdown(article, str(stage))
        _check(is_cancelled)
        target.mkdir(parents=True, exist_ok=True)
        for source in stage.rglob('*'):
            if source.is_file():
                destination = target / source.relative_to(stage)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
    pdf_job = None
    if 'pdf' in formats:
        pdf_job = {'html': wechat_writer.build_full_html(article),
                   'path': str(target / (stem + '.pdf')), 'title': article.title, 'id': key}
    store.set_status(key, 'pdf_pending' if pdf_job else 'exported')
    _log(on_log, f'[{index}/{total}] 已导出：{article.title}' + ('（PDF 待生成）' if pdf_job else ''))
    return key, pdf_job


def _export_urls(urls, formats, out_dir, on_progress=None, on_log=None, is_cancelled=None):
    from platforms.wechat_article import fetch_article
    formats = _formats(formats)
    out_dir = Path(out_dir or current_export_dir()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    pdf_jobs = []
    cancelled = False
    with exclusive_file_lock(out_dir / '.wechat-export.lock', timeout=0):
        for n, url in enumerate(urls, 1):
            key = store.article_id(url)
            try:
                _check(is_cancelled)
                _progress(on_progress, n - 1, len(urls), f'抓取文章 {n}/{len(urls)}')
                article = fetch_article(url)
                key = _record(article)
                _, pdf_job = _export_one(article, formats, out_dir, on_log, on_progress, is_cancelled, n, len(urls))
                if pdf_job:
                    pdf_jobs.append(pdf_job)
                ok += 1
            except WechatCancelled:
                cancelled = True
                break
            except Exception as exc:
                failed += 1
                if store.get_article(key) is None:
                    store.upsert_article({'url': url, 'title': '待重试文章'})
                store.set_status(key, 'failed', safe_error(exc))
                _log(on_log, f'[{n}/{len(urls)}] {safe_error(exc)}')
            try:
                _progress(on_progress, n, len(urls), f'已导出 {ok} 篇，失败 {failed} 篇')
                if n < len(urls):
                    _cooldown(is_cancelled)
            except WechatCancelled:
                cancelled = True
                break
    return {'ok': ok, 'failed': failed, 'cancelled': cancelled, 'pdf_jobs': pdf_jobs,
            'summary': f'{"已取消" if cancelled else "已完成"}：导出 {ok} 篇，失败 {failed} 篇'}


def export_links(text, formats, out_dir=None, on_progress=None, on_log=None, is_cancelled=None):
    return _export_urls(_urls(text), formats, out_dir, on_progress, on_log, is_cancelled)


def export_articles(ids, formats, out_dir=None, on_progress=None, on_log=None, is_cancelled=None):
    if not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= MAX_EXPORT:
        raise WechatUserError('请勾选 1–200 篇文章后导出')
    records = [store.get_article(key) for key in dict.fromkeys(str(key) for key in ids)]
    if any(r is None for r in records):
        raise WechatUserError('部分文章不存在，请刷新列表后重试')
    return _export_urls([r['url'] for r in records], formats, out_dir, on_progress, on_log, is_cancelled)


def retry_failed(biz='', formats=(), out_dir=None, on_progress=None, on_log=None, is_cancelled=None):
    records = list_articles(biz=biz, status='failed', limit=MAX_EXPORT)['items']
    if not records:
        return {'ok': 0, 'failed': 0, 'pdf_jobs': [], 'summary': '没有需要重试的失败文章'}
    return export_articles([r['id'] for r in records], formats, out_dir, on_progress, on_log, is_cancelled)


def finish_pdf(key, success, error=''):
    store.set_status(key, 'exported' if success else 'failed', '' if success else 'PDF 生成失败，请重试')


def _csv_cell(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


def index_csv(filters=None):
    filters = {k: v for k, v in dict(filters or {}).items() if k in
               ('biz', 'keyword', 'date_from', 'date_to', 'status', 'sort', 'order')}
    first = list_articles(**filters, offset=0, limit=200)
    if first['total'] > MAX_INDEX:
        raise WechatUserError('清单超过 50 000 条，请缩小筛选范围后导出')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['格式版本', '公众号', '标题', '发布时间', '状态', '公开链接', '说明'])
    rows = first['items']
    count = 0
    while True:
        for row in rows:
            writer.writerow([_csv_cell(v) for v in ('1', row['account'], row['title'], row['publish_time'],
                             STATUS_LABELS.get(row['status'], row['status']), row['url'], row['error'])])
            count += 1
        if count >= first['total'] or not rows:
            break
        rows = list_articles(**filters, offset=count, limit=200)['items']
        if count + len(rows) > MAX_INDEX:
            raise WechatUserError('清单数量已变化，请缩小范围后重试')
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def export_index(filters, out_path):
    data = index_csv(filters)
    target = Path(out_path)
    if target.suffix.lower() != '.csv':
        target = target.with_suffix('.csv')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.wechat-index-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    count = max(0, sum(1 for _ in csv.reader(io.StringIO(data.decode('utf-8-sig')))) - 1)
    return {'count': count, 'path': str(target), 'summary': f'已导出 {count} 条清单记录'}
