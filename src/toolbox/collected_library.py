"""采集目录与按需读取（L3）。

主数据仍是 collected_store 独占写入的 collected.db，schema 和去重键不变。
本对象只常驻稳定键/平台/日期，展示页按页读取轻量对象；显式导出/同步再读完整数据。
未成功落盘的本次采集结果保留在内存等待重试，不丢弃、不用旧快照覆盖数据库。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace, is_dataclass
from datetime import datetime

from toolbox import collected_store as store
from toolbox.dedup import collect_key

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")
_KNOWN = {"douyin", "bilibili", "weibo"}


def parse_publish(raw):
    raw = str(raw or "").strip()
    if raw:
        candidate = raw[:19] if " " in raw else raw[:10]
        if (len(candidate) in (10, 16, 19) and candidate[4:5] == "-"
                and candidate[7:8] == "-" and (len(candidate) == 10 or candidate[10] == " ")):
            try:
                return datetime.fromisoformat(candidate), raw[:16] if " " in raw else raw[:10]
            except ValueError:
                pass
        for fmt in _FORMATS:
            try:
                dt = datetime.strptime(raw[:19] if " " in raw else raw[:10], fmt)
                return dt, raw[:16] if " " in raw else raw[:10]
            except ValueError:
                continue
        if raw.isdigit():
            try:
                ts = int(raw)
                if ts > 10 ** 12:
                    ts //= 1000
                dt = datetime.fromtimestamp(ts)
                return dt, dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                pass
    return datetime.min, "无日期"


@dataclass(frozen=True, slots=True)
class _Record:
    storage_key: str
    key: str
    platform: str
    published: datetime


class CollectedView(Sequence):
    def __init__(self, library, records):
        self.library = library
        self.records = records

    @property
    def keys(self):
        return (record.key for record in self.records)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return list(self.library._read(self.records[index], include_raw=False))
        return next(self.library._read([self.records[index]], include_raw=False))

    def __iter__(self):
        return self.library._read(self.records, include_raw=False)

    def selected(self, keys):
        records = [record for record in self.records if record.key in keys]
        return list(self.library._read(records, include_raw=True))


class CollectedLibrary(Sequence):
    def __init__(self, db_path=None):
        self.db_path = db_path
        self._records = []
        self._by_key = {}
        self._aliases = {}
        self._pending = {}
        self.revision = 0

    def initialize(self):
        records, by_key, aliases = [], {}, {}
        for storage_key, canonical, platform, published in store.iter_metadata(self.db_path):
            key = ((storage_key if canonical == storage_key else canonical)
                   if canonical else f"\0stored:{storage_key}")
            if storage_key != key:
                aliases.setdefault(key, []).append(storage_key)
            if key in by_key:
                continue
            record = _Record(storage_key, key, platform, parse_publish(published)[0])
            records.append(record)
            by_key[key] = record
        for key, info in list(self._pending.items()):
            if key in by_key:
                self._pending.pop(key)
                continue
            record = self._record(info, key)
            records.append(record)
            by_key[key] = record
        self._records, self._by_key, self._aliases = records, by_key, aliases
        self.revision += 1

    @staticmethod
    def _record(info, key):
        return _Record(collect_key(info), key, info.platform or "", parse_publish(info.publish_time)[0])

    def __len__(self):
        return len(self._records)

    def __iter__(self):
        return self._read(self._records, include_raw=True)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return list(self._read(self._records[index], include_raw=True))
        return next(self._read([self._records[index]], include_raw=True))

    def _read(self, records, *, include_raw):
        for start in range(0, len(records), 200):
            chunk = records[start:start + 200]
            rows = store.read_by_keys((r.storage_key for r in chunk if r.key not in self._pending),
                                      include_raw=include_raw, db_path=self.db_path)
            for record in chunk:
                info = self._pending.get(record.key) or rows.get(record.storage_key)
                if info is not None:
                    if not include_raw and record.key in self._pending and is_dataclass(info):
                        info = replace(info, raw_data={})
                    info._collection_key = record.key
                    yield info

    def view(self, platform):
        records = [record for record in self._records if
                   (record.platform == platform if platform != "other" else record.platform not in _KNOWN)]
        records.sort(key=lambda record: record.published, reverse=True)
        return CollectedView(self, records)

    def reveal(self, infos, *, persist=True):
        new, seen = [], set()
        for info in infos:
            if info is None:
                continue
            key = collect_key(info) or f"\0pending:{id(info)}"
            if key in self._by_key or key in seen:
                continue
            seen.add(key)
            new.append((key, info))
        if not new:
            return 0
        if persist:
            store.insert_items([info for _key, info in new], db_path=self.db_path)
        try:
            saved = store.existing_keys((collect_key(info) for _key, info in new), self.db_path)
        except Exception:
            saved = set()
        for key, info in new:
            record = self._record(info, key)
            self._records.append(record)
            self._by_key[key] = record
            if record.storage_key and record.storage_key != key:
                self._aliases.setdefault(key, []).append(record.storage_key)
            if record.storage_key not in saved or not collect_key(info):
                self._pending[key] = info
        self.revision += 1
        return len(new)

    def append(self, info):
        self.reveal([info])

    def extend(self, infos):
        self.reveal(infos)

    def flush(self):
        if not self._pending:
            return
        store.insert_items(self._pending.values(), db_path=self.db_path)
        try:
            saved = store.existing_keys((collect_key(i) for i in self._pending.values()), self.db_path)
        except Exception:
            return
        self._pending = {key: info for key, info in self._pending.items()
                         if not collect_key(info) or collect_key(info) not in saved}

    def remove_keys(self, keys):
        keys = set(keys)
        storage_keys = {storage for key in keys for storage in self._aliases.get(key, ())}
        storage_keys.update(key for key in keys if not key.startswith("\0"))
        store.delete_keys(storage_keys, db_path=self.db_path)
        self._records = [record for record in self._records if record.key not in keys]
        for key in keys:
            self._by_key.pop(key, None)
            self._aliases.pop(key, None)
            self._pending.pop(key, None)
        self.revision += 1

    def clear(self):
        store.clear_items(db_path=self.db_path)
        self._records.clear()
        self._by_key.clear()
        self._aliases.clear()
        self._pending.clear()
        self.revision += 1
