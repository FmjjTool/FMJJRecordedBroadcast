# -*- coding: utf-8 -*-
"""资产页「图识别 · 以图搜图」用例。

给定「已获取资源」条目与一张查询图片：收集本地图片、读取/补齐特征缓存、
按余弦相似度返回视觉最接近的资产条目。不依赖 Qt；推理在 L4
image_embedding，特征缓存由 L5 asset_image_vectors 负责。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from toolbox import asset_image_vectors
from toolbox.image_embedding import IMAGE_EXTS, ImageEmbedder


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
                use_cache: bool = True) -> dict:
    """收集并缓存条目图片特征，返回矩阵与归属。"""
    _emit(progress, "collect", message="正在收集已获取资源的图片...")
    images = collect_images(entries)
    total = len(images)
    if cancel is not None and cancel.is_set():
        raise SearchCancelled("已停止识别")

    cache: dict[str, np.ndarray] = {}
    if use_cache and images:
        _emit(progress, "cache", message=f"正在读取特征缓存（{total} 张图片）...")
        cache = asset_image_vectors.fetch_many(
            [(path, mtime, size) for _idx, path, mtime, size in images],
            embedder.model_id)
    if cancel is not None and cancel.is_set():
        raise SearchCancelled("已停止识别")

    missing = [(idx, path, mtime, size) for idx, path, mtime, size in images
               if _norm(path) not in cache]
    embedded = 0
    skipped = 0
    if missing:
        _emit(progress, "embed", 0, len(missing),
              f"正在提取图片特征 0/{len(missing)}...")

        def on_embed(done, count):
            _emit(progress, "embed", done, count,
                  f"正在提取图片特征 {done}/{count}...")

        results, skipped = embedder.embed_paths(
            [path for _idx, path, _mtime, _size in missing],
            progress=on_embed, cancel=cancel)
        stamps = {_norm(path): (mtime, size)
                  for _idx, path, mtime, size in missing}
        rows = []
        for path, vector in results:
            stamp = stamps.get(_norm(path))
            if stamp is None:
                continue
            cache[_norm(path)] = vector
            rows.append((path, stamp[0], stamp[1], embedder.model_id,
                         int(np.asarray(vector).size), np.asarray(vector)))
        asset_image_vectors.upsert_many(rows)
        embedded = len(rows)
    if cancel is not None and cancel.is_set():
        raise SearchCancelled("已停止识别")

    vectors = []
    owners = []
    paths = []
    for index, path, _mtime, _size in images:
        vector = cache.get(_norm(path))
        if vector is None:
            continue
        vectors.append(np.asarray(vector, dtype=np.float32))
        owners.append(index)
        paths.append(path)
    matrix = np.vstack(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)
    return {
        "matrix": matrix,
        "owners": np.asarray(owners, dtype=np.int32),
        "paths": paths,
        "stats": {
            "entries": len(entries),
            "images": total,
            "indexed": len(vectors),
            "cache_hits": len(vectors) - embedded,
            "embedded": embedded,
            "skipped": skipped,
        },
    }


def search(entries, query_path, model_path=None, top_k: int = 30,
           progress=None, cancel=None, embedder=None) -> dict:
    """查询图片与条目图片比对，返回按条目聚合最高分的命中列表。"""
    started = time.perf_counter()
    entries = list(entries or [])
    top_k = max(1, min(int(top_k or 30), 200))
    embedder = embedder or ImageEmbedder(model_path)
    index = build_index(entries, embedder, progress=progress, cancel=cancel)
    if cancel is not None and cancel.is_set():
        raise SearchCancelled("已停止识别")

    _emit(progress, "match", message="正在比对相似度...")
    matrix = index["matrix"]
    owners = index["owners"]
    results: list[dict] = []
    if len(matrix) and entries:
        query = embedder.embed_query(query_path)
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
                "image": index["paths"][best_pos],
            })

    stats = dict(index["stats"])
    stats["results"] = len(results)
    stats["seconds"] = round(time.perf_counter() - started, 2)
    return {"results": results, "stats": stats}