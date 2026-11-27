"""公众号资料主库，唯一写入方（L5）。

路径 config/wechat_library.db（运行数据，不入 Git/安装包）；schema=1。
readers 只经 wechat_library / webui_wechat 用例访问。账号资料、文章元数据和
分页断点是 primary；不保存正文、图片、登录态、请求头或私密查询参数。
storage dedup: 长链按 (__biz, mid, idx)，短链按 slug，由 article_id 集中定义。
不做展示去重、跨库双写、旧数据迁移或自动清理；无删除接口。
连接短开短关，WAL + busy_timeout，线程/进程写锁 + 事务；取消保留已提交数据。
读取不存在的库不创建目录。新版本库拒绝写入，不回退覆盖。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from toolbox.durable_io import exclusive_file_lock

SCHEMA_VERSION = 1
STATUS_LABELS = {
    'archived': '已归档', 'exported': '已导出',
    'failed': '失败', 'pdf_pending': '待生成 PDF',
}
ACCOUNT_SORTS = {'biz': 'a.biz', 'name': 'a.name', 'remark': 'a.remark',
                 'article_count': 'article_count', 'last_sync': 'a.last_sync'}
ARTICLE_SORTS = {'id': 'r.id', 'biz': 'r.biz', 'account': 'r.account',
                 'title': 'r.title', 'publish_time': 'r.publish_time',
                 'status': 'r.status', 'url': 'r.url', 'error': 'r.error'}
_TZ = timezone(timedelta(hours=8))


def now_text():
    return datetime.now(_TZ).strftime('%Y-%m-%d %H:%M:%S')


def _path(path=None):
    if path is not None:
        return Path(path)
    from toolbox.config_manager import get_config_dir_readonly
    return get_config_dir_readonly() / 'wechat_library.db'


def clean_text(value, limit=512):
    text = str(value or '').replace('\x00', '')[:limit]
    return re.sub(r'(?i)([?&](?:key|uin|pass_ticket|appmsg_token|wxtoken|wap_sid2|token|ticket)=)[^&\s<>\"\']*',
                  r'\1[已隐藏]', text)


def validate_biz(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_+=/\-]{1,256}', value):
        raise ValueError('公众号标识无效，请使用文章链接识别')
    return value


def article_id(url):
    """主库唯一的文章身份键，不使用过期会话或标题。"""
    p = urlsplit(str(url))
    if (p.scheme != 'https' or p.netloc != 'mp.weixin.qq.com'
            or p.fragment or p.username or p.password):
        raise ValueError('文章链接格式无效')
    q = parse_qs(p.query, keep_blank_values=True)
    if set(q) - {'__biz', 'mid', 'idx', 'sn'} or any(len(v) != 1 for v in q.values()):
        raise ValueError('文章链接包含不允许保存的参数')
    if p.path == '/s' and q:
        biz = validate_biz(q.get('__biz', [''])[0])
        mid, idx = q.get('mid', [''])[0], q.get('idx', ['1'])[0]
        if not mid.isdigit() or not idx.isdigit() or len(mid) > 24 or len(idx) > 4:
            raise ValueError('文章链接缺少有效标识')
        identity = f'{biz}|{mid}|{idx}'
    elif re.fullmatch(r'/s/[A-Za-z0-9_\-]{1,256}', p.path) and not q:
        identity = p.path
    else:
        raise ValueError('请使用有效的公众号文章链接')
    return hashlib.sha256(identity.encode()).hexdigest()


@contextmanager
def _read(path=None):
    p = _path(path)
    if not p.is_file():
        yield None
        return
    conn = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=10000')
        if conn.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('资料库版本不兼容，请使用对应版本软件')
        yield conn
    finally:
        conn.close()


@contextmanager
def _write(path=None):
    p = _path(path)
    with exclusive_file_lock(p.with_suffix('.write.lock')):
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA busy_timeout=10000')
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError('资料库版本不兼容，请使用对应版本软件')
            conn.execute('PRAGMA journal_mode=WAL')
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('CREATE TABLE IF NOT EXISTS accounts ('
                             'biz TEXT PRIMARY KEY,name TEXT NOT NULL DEFAULT \'\','
                             'remark TEXT NOT NULL DEFAULT \'\',avatar TEXT NOT NULL DEFAULT \'\','
                             'last_sync TEXT NOT NULL DEFAULT \'\',cursor INTEGER NOT NULL DEFAULT 0,'
                             'history_done INTEGER NOT NULL DEFAULT 0)')
                conn.execute('CREATE TABLE IF NOT EXISTS articles ('
                             'id TEXT PRIMARY KEY,biz TEXT NOT NULL DEFAULT \'\','
                             'account TEXT NOT NULL DEFAULT \'\',title TEXT NOT NULL DEFAULT \'\','
                             'url TEXT NOT NULL,publish_time TEXT NOT NULL DEFAULT \'\','
                             'author TEXT NOT NULL DEFAULT \'\',status TEXT NOT NULL DEFAULT \'archived\','
                             'error TEXT NOT NULL DEFAULT \'\',updated_at TEXT NOT NULL)')
                conn.execute('CREATE INDEX IF NOT EXISTS ix_wx_account_date ON articles(biz,publish_time)')
                conn.execute('PRAGMA user_version=1')
                yield conn
        finally:
            conn.close()


def save_account(biz, name='', remark=None, avatar='', path=None):
    biz = validate_biz(biz)
    name, avatar = clean_text(name, 160), str(avatar or '')[:2048]
    # 头像不自动请求；只保留无查询的微信图床地址。
    if avatar:
        p = urlsplit(avatar)
        avatar = ('https://' + p.netloc + p.path) if (
            p.scheme == 'https' and p.netloc in ('mmbiz.qpic.cn', 'mmbiz.qlogo.cn')) else ''
    with _write(path) as conn:
        conn.execute('INSERT INTO accounts(biz,name,remark,avatar) VALUES(?,?,?,?) '
                     'ON CONFLICT(biz) DO UPDATE SET '
                     "name=CASE WHEN excluded.name!='' THEN excluded.name ELSE accounts.name END,"
                     'remark=CASE WHEN ? IS NULL THEN accounts.remark ELSE excluded.remark END,'
                     "avatar=CASE WHEN excluded.avatar!='' THEN excluded.avatar ELSE accounts.avatar END",
                     (biz, name, clean_text(remark, 500), avatar, remark))
    return get_account(biz, path)


def get_account(biz, path=None):
    with _read(path) as conn:
        row = conn.execute('SELECT * FROM accounts WHERE biz=?', (biz,)).fetchone() if conn else None
        return dict(row) if row else None


def upsert_article(record, path=None):
    url = str(record.get('url') or '')
    key = article_id(url)
    biz = str(record.get('biz') or '')
    if biz:
        validate_biz(biz)
    status = str(record.get('status') or 'archived')
    if status not in STATUS_LABELS:
        raise ValueError('文章状态无效')
    with _write(path) as conn:
        existed = conn.execute('SELECT 1 FROM articles WHERE id=?', (key,)).fetchone() is not None
        conn.execute('INSERT INTO articles(id,biz,account,title,url,publish_time,author,status,error,updated_at)'
                     ' VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                     "status=CASE WHEN articles.biz='' AND excluded.biz!='' AND articles.status='failed' "
                     "THEN 'archived' ELSE articles.status END,"
                     "error=CASE WHEN articles.biz='' AND excluded.biz!='' AND articles.status='failed' "
                     "THEN '' ELSE articles.error END,"
                     "biz=CASE WHEN excluded.biz!='' THEN excluded.biz ELSE articles.biz END,"
                     "account=CASE WHEN excluded.account!='' THEN excluded.account ELSE articles.account END,"
                     "title=CASE WHEN excluded.title!='' THEN excluded.title ELSE articles.title END,"
                     "publish_time=CASE WHEN excluded.publish_time!='' THEN excluded.publish_time ELSE articles.publish_time END,"
                     "author=CASE WHEN excluded.author!='' THEN excluded.author ELSE articles.author END,"
                     'updated_at=excluded.updated_at',
                     (key, biz, clean_text(record.get('account'), 160), clean_text(record.get('title'), 500),
                      url, clean_text(record.get('publish_time'), 32), clean_text(record.get('author'), 160),
                      status, '', now_text()))
    return key, not existed


def set_status(key, status, error='', path=None):
    if status not in STATUS_LABELS:
        raise ValueError('文章状态无效')
    with _write(path) as conn:
        conn.execute('UPDATE articles SET status=?,error=?,updated_at=? WHERE id=?',
                     (status, clean_text(error, 200), now_text(), str(key)))


def get_article(key, path=None):
    with _read(path) as conn:
        row = conn.execute('SELECT * FROM articles WHERE id=?', (str(key),)).fetchone() if conn else None
        return dict(row) if row else None


def has_article(url, path=None):
    return get_article(article_id(url), path) is not None


def has_metadata(url, path=None):
    """抓取失败的占位记录不能作为历史增量同步的停止边界。"""
    row = get_article(article_id(url), path)
    return bool(row and row.get('biz'))


def set_cursor(biz, cursor, done=False, path=None, touch_sync=True):
    with _write(path) as conn:
        conn.execute('UPDATE accounts SET cursor=?,history_done=?,'
                     'last_sync=CASE WHEN ? THEN ? ELSE last_sync END WHERE biz=?',
                     (max(0, int(cursor)), bool(done), bool(touch_sync), now_text(), validate_biz(biz)))


def touch_sync(biz, path=None):
    with _write(path) as conn:
        conn.execute('UPDATE accounts SET last_sync=? WHERE biz=?', (now_text(), validate_biz(biz)))


def _page(offset, limit):
    offset, limit = int(offset), int(limit)
    if offset < 0 or limit < 1 or limit > 200:
        raise ValueError('分页参数超出范围')
    return offset, limit


def _order(sort, order, allowed, default):
    if not sort or not order:
        return default
    if sort not in allowed or order.lower() not in ('asc', 'desc'):
        raise ValueError('排序参数无效')
    col = allowed[sort]
    tie = 'r.id' if col.startswith('r.') else 'a.biz'
    return f"({col} IS NULL OR {col}='') ASC, {col} COLLATE NOCASE {order.upper()}, {tie} ASC"


def list_accounts(keyword='', sort='', order='', offset=0, limit=200, path=None):
    offset, limit = _page(offset, limit)
    order_sql = _order(sort, order, ACCOUNT_SORTS, 'a.name COLLATE NOCASE ASC,a.biz ASC')
    pattern = '%' + str(keyword).replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
    where = " WHERE (a.name LIKE ? ESCAPE '!' OR a.biz LIKE ? ESCAPE '!' OR a.remark LIKE ? ESCAPE '!')"
    args = (pattern, pattern, pattern)
    with _read(path) as conn:
        if not conn:
            return {'items': [], 'total': 0}
        total = conn.execute('SELECT count(*) FROM accounts a' + where, args).fetchone()[0]
        rows = conn.execute('SELECT a.*,(SELECT count(*) FROM articles r WHERE r.biz=a.biz) AS article_count '
                            'FROM accounts a' + where + ' ORDER BY ' + order_sql + ' LIMIT ? OFFSET ?',
                            (*args, limit, offset)).fetchall()
        return {'items': [dict(row) for row in rows], 'total': total}


def _article_filter(biz='', keyword='', date_from='', date_to='', status='', **_):
    clauses, args = [], []
    if biz:
        clauses.append('r.biz=?')
        args.append(validate_biz(biz))
    if keyword:
        clauses.append("(r.title LIKE ? ESCAPE '!' OR r.account LIKE ? ESCAPE '!')")
        pattern = '%' + str(keyword).replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        args.extend((pattern, pattern))
    for value in (date_from, date_to):
        if value:
            try:
                if len(str(value)) != 10 or datetime.strptime(str(value), '%Y-%m-%d').strftime('%Y-%m-%d') != value:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('日期格式应为 YYYY-MM-DD') from None
    if date_from and date_to and date_from > date_to:
        raise ValueError('开始日期不能晚于结束日期')
    if date_from:
        clauses.append('r.publish_time>=?')
        args.append(date_from)
    if date_to:
        clauses.append('r.publish_time<?')
        end = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        args.append(end.strftime('%Y-%m-%d'))
    if status:
        if status not in STATUS_LABELS:
            raise ValueError('文章状态无效')
        clauses.append('r.status=?')
        args.append(status)
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args


def list_articles(biz='', keyword='', date_from='', date_to='', status='', sort='', order='',
                  offset=0, limit=200, path=None):
    offset, limit = _page(offset, limit)
    where, args = _article_filter(biz=biz, keyword=keyword, date_from=date_from, date_to=date_to, status=status)
    order_sql = _order(sort, order, ARTICLE_SORTS, 'r.publish_time DESC,r.id ASC')
    with _read(path) as conn:
        if not conn:
            return {'items': [], 'total': 0}
        total = conn.execute('SELECT count(*) FROM articles r' + where, args).fetchone()[0]
        rows = conn.execute('SELECT r.* FROM articles r' + where + ' ORDER BY ' + order_sql + ' LIMIT ? OFFSET ?',
                            (*args, limit, offset)).fetchall()
        return {'items': [dict(row) for row in rows], 'total': total}
