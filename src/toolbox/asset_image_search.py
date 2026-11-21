# -*- coding: utf-8 -*-
"""资产页「图识别 · 以图搜图」用例。

给定「已获取资源」条目与一张查询图片：收集本地图片、读取/补齐特征缓存、
按余弦相似度返回视觉最接近的资产条目。不依赖 Qt；推理在 L4
image_embedding，特征缓存由 L5 asset_image_vectors 负责。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np

from toolbox import asset_image_vectors
from toolbox.image_embedding import IMAGE_EXTS, EmbeddingCancelled, ImageEmbedder

logger = logging.getLogger(__name__)

_INDEX_CACHE_BATCH = 1600
_INDEX_EMBED_BATCH = 256


class SearchCancelled(RuntimeError):
    """用户停止识别。"""


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _emit(progress, stage: str, done: int = 0, total: int = 0,
          message: str = "") -> None:
    if progress is not None:
        progress({"stage": stage, "done": int(done), "total": int(total),
                  "message": message})


def entry_image_paths(entry) -> list[str]:
    """条目里的本地图片：媒体图片 + 本地封面（去重保序）。"""
    paths: list[str] = []
    seen: set[str] = set()

    def add(value) -> None:
        text = str(value or "").strip()
        if not text or Path(text).suffix.lower() not in IMAGE_EXTS:
            return
        key = _norm(text)
        if key in seen:
            return
        seen.add(key)
        paths.append(text)

    for media in entry.get("media_files") or []:
        if str(media.get("type") or "") == "image":
            add(media.get("path"))
    cover = str(entry.get("cover_url") or "").strip()
    if cover and not cover.startswith(("http://", "https://")):
        add(cover)
    return paths


def collect_images(entries) -> list[tuple[int, str, float, int]]:
    """[(条目下标, 路径, mtime, size)]；同一张图片只归属第一个条目。"""
    images: list[tuple[int, str, float, int]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        for path in entry_image_paths(entry):
            key = _norm(path)
            if key in seen:
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            seen.add(key)
            images.append((index, path, stat.st_mtime, stat.st_size))
    return images


def build_index(entries, embedder, progress=None, cancel=None,
                use_cache: bool = True, on_ready=None) -> dict:
    """收集并缓存条目图片特征，返回矩阵与归属。

    on_ready(vectors, owners, paths, stats) 在读取缓存后和每个批次结束时回调，
    调用方可用已提取部分做暂定匹配；回调只读，不要修改传入列表。
    """
    return _build_index(entries, embedder, progress, cancel, use_cache, on_ready)


def _build_index(entries, embedder, progress=None, cancel=None,
                 use_cache: bool = True, on_ready=None, *, keep_vectors=True,
                 on_vectors=None) -> dict:
    _emit(progress, "collect", message="正在收集已获取资源的图片...")
    images = collect_images(entries)
    total = len(images)
    if cancel is not None and cancel.is_set():
        raise SearchCancelled("已停止")

    vectors: list[np.ndarray] = []
    owners: list[int] = []
    paths: list[str] = []
    cache_hits = 0
    indexed = embedded = skipped_count = 0
    skipped_items: list[tuple[str, str]] = []

    def snapshot() -> dict:
        return {
            "entries": len(entries), "images": total, "indexed": indexed,
            "cache_hits": cache_hits, "embedded": embedded,
            "skipped": skipped_count, "skipped_items": list(skipped_items),
        }

    if use_cache and images:
        _emit(progress, "cache", message=f"正在读取特征缓存（{total} 张图片）...")
        missing = []
        chunk_size = _INDEX_CACHE_BATCH
        for start in range(0, total, chunk_size):
            chunk = images[start:start + chunk_size]
            cache = asset_image_vectors.fetch_many(
                [(path, mtime, size) for _idx, path, mtime, size in chunk],
                embedder.model_id)
            if cancel is not None and cancel.is_set():
                raise SearchCancelled("已停止")
            batch_vectors, batch_owners, batch_paths = [], [], []
            for row in chunk:
                index, path, _mtime, _size = row
                vector = cache.get(_norm(path))
                if vector is None:
                    missing.append(row)
                    continue
                cache_hits += 1
                indexed += 1
                if keep_vectors:
                    vectors.append(np.asarray(vector, dtype=np.float32))
                    owners.append(index)
                    paths.append(path)
                if on_vectors is not None:
                    batch_vectors.append(vector)
                    batch_owners.append(index)
                    batch_paths.append(path)
            if on_vectors is not None and batch_vectors:
                on_vectors(batch_vectors, batch_owners, batch_paths, snapshot())
            del cache, vector
    else:
        missing = images
    if cancel is not None and cancel.is_set():
        raise SearchCancelled("已停止")

    if images:
        _emit(progress, "cache", cache_hits, total,
              f"已缓存 {cache_hits}/{total} 张，本次需提取 {len(missing)} 张")
    if on_ready is not None and vectors:
        on_ready(vectors, owners, paths, snapshot())

    if missing:
        owner_of = {_norm(path): idx for idx, path, _mtime, _size in missing}
        stamps = {_norm(path): (mtime, size)
                  for _idx, path, mtime, size in missing}
        pending_rows: list[tuple] = []

        def flush_rows() -> None:
            nonlocal embedded
            if not pending_rows:
                return
            asset_image_vectors.upsert_many(pending_rows)
            embedded += len(pending_rows)
            pending_rows.clear()

        def on_batch(batch) -> None:
            nonlocal indexed
            batch_vectors, batch_owners, batch_paths = [], [], []
            for path, vector in batch:
                key = _norm(path)
                stamp = stamps.get(key)
                index = owner_of.get(key)
                if stamp is None or index is None:
                    continue
                array = np.asarray(vector, dtype=np.float32)
                indexed += 1
                if keep_vectors:
                    vectors.append(array)
                    owners.append(index)
                    paths.append(path)
                if on_vectors is not None:
                    batch_vectors.append(array)
                    batch_owners.append(index)
                    batch_paths.append(path)
                pending_rows.append(
                    (path, stamp[0], stamp[1], embedder.model_id,
                     int(array.size), array))
            if len(pending_rows) >= 256:
                flush_rows()
            if on_ready is not None:
                on_ready(vectors, owners, paths, snapshot())
            if on_vectors is not None and batch_vectors:
                stats = snapshot()
                # 本批尾部会在 finally 落盘；取消后的暂定统计也应包括这部分。
                stats["embedded"] += len(pending_rows)
                on_vectors(batch_vectors, batch_owners, batch_paths, stats)

        skipped_paths: list[str] = []

        def collect_skip(path, reason) -> None:
            nonlocal skipped_count
            skipped_count += 1
            skipped_paths.append(str(path))
            if len(skipped_items) < 100:
                skipped_items.append((str(path), str(reason)))

        def embed_once(path_list, prefix: str = ""):
            def report(done, count):
                _emit(progress, "embed", done, count,
                      f"{prefix}正在提取图片特征 {done}/{count}...")

            if keep_vectors:
                embedder.embed_paths(path_list, progress=report, cancel=cancel,
                                     on_batch=on_batch, on_skip=collect_skip)
                return

            # embed_paths 会收集调用内的向量。预建只需写入缓存，按完整模型批次切分。
            batch_size = max(1, getattr(embedder, "batch_size", 1))
            chunk_size = batch_size * max(1, _INDEX_EMBED_BATCH // batch_size)
            count = len(path_list)

            for start in range(0, count, chunk_size):
                def on_embed(done, _count, start=start):
                    report(start + done, count)

                embedder.embed_paths(
                    path_list[start:start + chunk_size], progress=on_embed, cancel=cancel,
                    on_batch=on_batch, on_skip=collect_skip)

        _emit(progress, "embed", 0, len(missing),
              f"正在提取图片特征 0/{len(missing)}...")

        cancel_exc = None
        try:
            embed_once([path for _idx, path, _mtime, _size in missing])
        except EmbeddingCancelled as exc:
            cancel_exc = exc
        finally:
            flush_rows()
        if cancel_exc is not None:
            raise SearchCancelled(
                f"已停止，已缓存 {embedded} 张特征") from cancel_exc

        # 读取失败可能是文件正在写入/短暂占用，给一次重试机会
        if skipped_paths and not (cancel is not None and cancel.is_set()):
            retry_paths = list(skipped_paths)
            skipped_paths.clear()
            skipped_count = 0
            skipped_items.clear()
            _emit(progress, "embed", 0, len(retry_paths),
                  f"正在重试 {len(retry_paths)} 张不可读图片...")
            try:
                embed_once(retry_paths, prefix="重试：")
            except EmbeddingCancelled as exc:
                cancel_exc = exc
            finally:
                flush_rows()
            if cancel_exc is not None:
                raise SearchCancelled(
                    f"已停止，已缓存 {embedded} 张特征") from cancel_exc
    if cancel is not None and cancel.is_set():
        raise SearchCancelled(f"已停止，已缓存 {embedded} 张特征")

    if skipped_items:
        logger.warning(
            "图识别索引跳过 %s 张不可读图片：%s", skipped_count,
            "; ".join(f"{Path(path).name}: {reason}"
                      for path, reason in skipped_items[:20]))

    if not keep_vectors:
        return {"stats": snapshot()}
    matrix = np.vstack(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)
    return {
        "matrix": matrix,
        "owners": np.asarray(owners, dtype=np.int32),
        "paths": paths,
        "stats": snapshot(),
    }


def _rank(matrix, owners, paths, entries, query, top_k: int) -> list[dict]:
    """按条目聚合最高分，返回 top_k 命中。"""
    results: list[dict] = []
    if not len(matrix) or not entries:
        return results
    scores = matrix @ np.asarray(query, dtype=np.float32)
    best = np.full(len(entries), -1.0, dtype=np.float32)
    np.maximum.at(best, owners, scores)
    order = np.argsort(-best)[:top_k]
    for entry_index in order:
        score = float(best[entry_index])
        if score < 0:
            continue
        mask = owners == entry_index
        positions = np.nonzero(mask)[0]
        best_pos = int(positions[np.argmax(scores[mask])])
        results.append({
            "entry": entries[int(entry_index)],
            "score": score,
            "image": paths[best_pos],
        })
    return results


def search(entries, query_path, model_path=None, top_k: int = 30,
           progress=None, cancel=None, embedder=None, on_partial=None) -> dict:
    """查询图片与条目图片比对；索引过程中可按已提取部分返回暂定结果。"""
    started = time.perf_counter()
    entries = list(entries or [])
    top_k = max(1, min(int(top_k or 30), 200))
    embedder = embedder or ImageEmbedder(model_path)
    query = embedder.embed_query(query_path)

    best = np.full(len(entries), -1.0, dtype=np.float32)
    best_paths = [""] * len(entries)
    last_stats: dict = {}
    last_rank = 0.0

    def results() -> list[dict]:
        return [{"entry": entries[int(i)], "score": float(best[i]),
                 "image": best_paths[int(i)]}
                for i in np.argsort(-best)[:top_k] if best[i] >= 0]

    def publish(force=False) -> None:
        nonlocal last_rank
        now = time.perf_counter()
        if not force and last_rank and now - last_rank < 1.0:
            return
        last_rank = now
        if on_partial is not None:
            ranked = results()
            snapshot = dict(last_stats)
            snapshot["results"] = len(ranked)
            try:
                on_partial(ranked, snapshot)
            except Exception:  # noqa: BLE001 - 展示回调失败不影响索引
                pass

    def on_vectors(vectors, owners, paths, stats) -> None:
        nonlocal last_stats
        # 只计算新增块，不再复制/重排先前已提取的全部向量。
        scores = np.stack(vectors) @ np.asarray(query, dtype=np.float32)
        for owner, path, score in zip(owners, paths, scores):
            if score > best[owner]:
                best[owner] = score
                best_paths[owner] = path
        last_stats = dict(stats)
        publish()

    try:
        index = _build_index(entries, embedder, progress=progress, cancel=cancel,
                             keep_vectors=False, on_vectors=on_vectors)
    except SearchCancelled:
        last_results = results()
        if last_results:
            stats = dict(last_stats)
            stats["results"] = len(last_results)
            stats["seconds"] = round(time.perf_counter() - started, 2)
            stats["stopped"] = True
            return {"results": last_results, "stats": stats, "stopped": True}
        raise

    _emit(progress, "match", message="正在比对相似度...")
    stats = dict(index["stats"])
    last_stats = stats
    publish(force=True)
    ranked = results()
    stats["results"] = len(ranked)
    stats["seconds"] = round(time.perf_counter() - started, 2)
    return {"results": ranked, "stats": stats}


def prepare_index(entries, model_path=None, progress=None, cancel=None,
                  embedder=None) -> dict:
    """只建立/补齐特征索引，不需要查询图片。

    返回索引统计；取消时抛出 SearchCancelled（已缓存数量在消息里）。
    """
    started = time.perf_counter()
    embedder = embedder or ImageEmbedder(model_path)
    index = _build_index(entries, embedder, progress=progress, cancel=cancel, keep_vectors=False)
    stats = dict(index["stats"])
    stats["seconds"] = round(time.perf_counter() - started, 2)
    return stats
