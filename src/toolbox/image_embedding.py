# -*- coding: utf-8 -*-
"""本地图像特征引擎（图识别 · 以图搜图）。

只读取用户自行下载的 CLIP 图像编码器 ONNX（输入 pixel_values，NCHW 224×224），
用 onnxruntime 在 CPU 推理；不联网下载模型，也不读写任何凭据。
L4：只负责图片预处理、推理与特征归一化，不依赖 Qt、页面或业务存储。
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from PIL import Image, ImageOps

try:
    import onnxruntime as ort
except ImportError:  # 可选依赖缺失时由调用方给出安装提示
    ort = None

CLIP_MEAN = np.array((0.48145466, 0.4578275, 0.40821073), dtype=np.float32)
CLIP_STD = np.array((0.26862954, 0.26130258, 0.27577711), dtype=np.float32)
DEFAULT_SIZE = 224
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# 一键下载的推荐模型：优先国内镜像，失败再走官方；文件由用户主动下载到本机
MODEL_DOWNLOAD_URLS = (
    "https://hf-mirror.com/Xenova/clip-vit-base-patch32/resolve/main/onnx/"
    "vision_model_quantized.onnx",
    "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/"
    "vision_model_quantized.onnx",
)
MODEL_MIN_BYTES = 20 * 1024 * 1024
_DOWNLOAD_CHUNK = 256 * 1024

_SESSIONS: dict[tuple, "ort.InferenceSession"] = {}
_SESSION_LOCK = threading.Lock()


class EmbeddingError(RuntimeError):
    """可直接展示给用户的特征提取错误。"""


class ModelMissingError(EmbeddingError):
    """模型文件或推理依赖缺失。"""


class EmbeddingCancelled(RuntimeError):
    """用户停止特征提取。"""


class DownloadCancelled(RuntimeError):
    """用户停止模型下载；已下载部分保留用于续传。"""


class DownloadError(EmbeddingError):
    """模型下载或校验失败。"""


def default_model_dir() -> Path:
    """放模型的默认目录（config/models，用户自行下载放入）。"""
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "models"


def find_model_files(root: Path | None = None) -> list[Path]:
    """列出模型目录下的 .onnx（先普通版，后量化版）。"""
    directory = Path(root) if root else default_model_dir()
    if not directory.is_dir():
        return []
    files = [path for path in directory.rglob("*.onnx") if path.is_file()]
    files.sort(key=lambda path: ("quantized" in path.name.lower(), str(path).lower()))
    return files


def resolve_model_path(configured: str = "") -> Path | None:
    """配置路径有效时优先使用，否则在默认模型目录里找第一个 .onnx。"""
    text = str(configured or "").strip()
    if text:
        path = Path(text).expanduser()
        if path.is_file() and path.suffix.lower() == ".onnx":
            return path
    files = find_model_files()
    return files[0] if files else None


def model_status(configured: str = "") -> dict:
    """模型可用性：ready / path / size / reason。"""
    if ort is None:
        return {"ready": False, "path": "", "size": 0,
                "reason": "缺少 onnxruntime 依赖，请先安装：pip install onnxruntime"}
    path = resolve_model_path(configured)
    if path is None:
        return {"ready": False, "path": "", "size": 0,
                "reason": "未找到 ONNX 模型，请先下载并放入模型目录"}
    try:
        size = int(path.stat().st_size)
    except OSError:
        size = 0
    return {"ready": True, "path": str(path), "size": size, "reason": ""}


def model_id(path) -> str:
    """模型标识：文件名 + 大小 + 修改时间，用于特征缓存失效判断。"""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise ModelMissingError(f"模型文件不可用：{path}") from exc
    return f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}"


def _load_session(path: Path):
    if ort is None:
        raise ModelMissingError("缺少 onnxruntime 依赖，请先安装：pip install onnxruntime")
    path = Path(path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise ModelMissingError(f"模型文件不可用：{path}") from exc
    key = (str(path.resolve()), stat.st_size, int(stat.st_mtime))
    with _SESSION_LOCK:
        cached = _SESSIONS.get(key)
        if cached is not None:
            return cached
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) - 1))
    options.inter_op_num_threads = 1
    try:
        session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001 - 模型损坏/格式不符统一提示
        raise EmbeddingError(f"模型加载失败：{exc}") from exc
    with _SESSION_LOCK:
        if len(_SESSIONS) >= 3:
            _SESSIONS.clear()
        _SESSIONS[key] = session
    return session


def _pixel_input(session):
    """找图像输入：优先名字含 pixel，其次 4 维输入（兼容整模型导出）。"""
    try:
        inputs = session.get_inputs()
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(f"模型输入读取失败：{exc}") from exc
    for item in inputs:
        if "pixel" in str(item.name).lower():
            return item
    for item in inputs:
        if isinstance(item.shape, (list, tuple)) and len(item.shape) == 4:
            return item
    raise EmbeddingError("模型缺少图像输入（需要 pixel_values，4 维张量）")


def _input_size(pixel_input) -> int:
    shape = getattr(pixel_input, "shape", None)
    if isinstance(shape, (list, tuple)) and len(shape) >= 4:
        height = shape[-2]
        if isinstance(height, int) and height >= 64:
            return height
    return DEFAULT_SIZE


def _fixed_batch(pixel_input) -> int:
    """固定批大小（部分导出模型 batch=1）；动态批返回 0。"""
    shape = getattr(pixel_input, "shape", None)
    if isinstance(shape, (list, tuple)) and shape:
        batch = shape[0]
        if isinstance(batch, int) and batch > 0:
            return batch
    return 0


def preprocess_image(path, size: int = DEFAULT_SIZE) -> np.ndarray:
    """单张图片 → CLIP 预处理后的 NCHW float32 数组（3, size, size）。"""
    target = int(size or DEFAULT_SIZE)
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            width, height = image.size
            if width <= 0 or height <= 0:
                raise EmbeddingError("图片尺寸无效")
            scale = target / min(width, height)
            resized = (max(target, int(round(width * scale))),
                       max(target, int(round(height * scale))))
            image = image.resize(resized, Image.BICUBIC)
            left = (resized[0] - target) // 2
            top = (resized[1] - target) // 2
            image = image.crop((left, top, left + target, top + target))
            array = np.asarray(image, dtype=np.float32) / 255.0
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - 损坏/不支持格式按单张失败
        raise EmbeddingError(f"无法读取图片：{Path(path).name}") from exc
    array = (array - CLIP_MEAN) / CLIP_STD
    return np.transpose(array, (2, 0, 1))


def _output_vectors(session, outputs) -> np.ndarray:
    """从模型输出里取图像特征并做 L2 归一化。"""
    candidates = list(zip(session.get_outputs(), outputs))
    chosen = None
    for keyword in ("image_embeds", "pooler", "vision"):
        for output, value in candidates:
            if keyword in str(getattr(output, "name", "")).lower():
                chosen = value
                break
        if chosen is not None:
            break
    if chosen is None:
        for _output, value in candidates:
            array = np.asarray(value)
            if array.ndim == 2:
                chosen = array
                break
    if chosen is None:
        for _output, value in candidates:
            array = np.asarray(value)
            if array.ndim == 3:
                chosen = array.mean(axis=1)
                break
    if chosen is None:
        raise EmbeddingError("模型输出无法识别为图像特征（需要 pooler_output 或 [批,标记,维度]）")
    vectors = np.asarray(chosen, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors[None, :]
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


class ImageEmbedder:
    """CLIP 图像编码器：批量提特征，返回单位向量。"""

    def __init__(self, model_path, batch_size: int = 16):
        self.path = Path(model_path)
        self.model_id = model_id(self.path)
        self.batch_size = max(1, int(batch_size))
        self._session = _load_session(self.path)
        pixel_input = _pixel_input(self._session)
        self._input_name = pixel_input.name
        self._size = _input_size(pixel_input)
        self._fixed_batch = _fixed_batch(pixel_input)
        if self._fixed_batch:
            self.batch_size = self._fixed_batch
        self._output_names = [output.name for output in self._session.get_outputs()]

    def embed_paths(self, paths, progress=None, cancel=None):
        """批量提特征，返回 ([(路径, 向量)], 跳过数)。"""
        all_paths = list(paths)
        total = len(all_paths)
        results = []
        skipped = 0
        for start in range(0, total, self.batch_size):
            if cancel is not None and cancel.is_set():
                raise EmbeddingCancelled("已停止特征提取")
            chunk = all_paths[start:start + self.batch_size]
            batch, valid = [], []
            for path in chunk:
                try:
                    batch.append(preprocess_image(path, self._size))
                except EmbeddingError:
                    skipped += 1
                    continue
                valid.append(path)
            if batch:
                feed = np.stack(batch)
                if self._fixed_batch and len(batch) < self._fixed_batch:
                    pad = np.repeat(feed[-1:], self._fixed_batch - len(batch), axis=0)
                    feed = np.concatenate([feed, pad], axis=0)
                try:
                    outputs = self._session.run(
                        self._output_names, {self._input_name: feed})
                except Exception as exc:  # noqa: BLE001 - 推理失败统一提示
                    raise EmbeddingError(f"模型推理失败：{exc}") from exc
                vectors = _output_vectors(self._session, outputs)[:len(valid)]
                if len(vectors) != len(valid):
                    raise EmbeddingError("模型输出批次与输入不一致")
                results.extend(zip(valid, vectors))
            if progress is not None:
                progress(min(start + len(chunk), total), total)
        return results, skipped

    def embed_query(self, path) -> np.ndarray:
        array = preprocess_image(path, self._size)[None, ...]
        try:
            outputs = self._session.run(
                self._output_names, {self._input_name: array})
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"模型推理失败：{exc}") from exc
        return _output_vectors(self._session, outputs)[0]


def download_default_model(target_dir=None, urls=None, progress=None,
                           cancel=None, min_bytes: int = MODEL_MIN_BYTES,
                           validate: bool = True) -> Path:
    """一键下载推荐 CLIP 视觉编码器，支持断点续传与取消。

    progress(done_bytes, total_bytes)；cancel 为 threading.Event。
    先写 <模型名>.part，校验通过后原子替换为正式文件。
    """
    url_list = [str(url) for url in (urls or MODEL_DOWNLOAD_URLS) if url]
    if not url_list:
        raise DownloadError("没有可用的下载地址")
    directory = Path(target_dir) if target_dir else default_model_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"无法创建模型目录：{exc}") from exc
    name = Path(urlsplit(url_list[0]).path).name or "vision_model_quantized.onnx"
    target = directory / name
    part = target.with_name(name + ".part")
    if target.is_file() and target.stat().st_size >= min_bytes:
        return target

    errors = []
    completed = False
    for url in url_list:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("已停止下载")
        try:
            _download_one(url, part, progress, cancel)
            completed = True
            break
        except DownloadCancelled:
            raise
        except DownloadError as exc:
            errors.append(str(exc))
    if not completed:
        detail = "；".join(errors[-2:]) if errors else "未知错误"
        raise DownloadError(f"下载失败：{detail}")

    size = part.stat().st_size if part.is_file() else 0
    if size < min_bytes:
        part.unlink(missing_ok=True)
        raise DownloadError(f"下载文件不完整（{size} 字节），请重试")
    if validate:
        try:
            _load_session(part)
        except EmbeddingError as exc:
            part.unlink(missing_ok=True)
            raise DownloadError(f"模型校验失败：{exc}") from exc
    try:
        os.replace(part, target)
    except OSError as exc:
        raise DownloadError(f"无法保存模型：{exc}") from exc
    return target


def _download_one(url, part: Path, progress, cancel) -> None:
    try:
        import requests
    except ImportError as exc:  # requests 属于既有依赖，缺失时给出明确提示
        raise DownloadError("缺少 requests 依赖，请先安装：pip install requests") from exc
    offset = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": "Mozilla/5.0 FengMingToolbox"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        with requests.get(url, headers=headers, stream=True, allow_redirects=True,
                          timeout=(20, 60)) as response:
            if response.status_code == 416 and offset:
                return
            if response.status_code not in (200, 206):
                raise DownloadError(f"下载源返回 HTTP {response.status_code}")
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type:
                raise DownloadError("下载源返回了网页而不是模型文件")
            mode = "ab" if offset and response.status_code == 206 else "wb"
            if mode == "ab":
                match = re.match(r"bytes\s+(\d+)-",
                                 response.headers.get("Content-Range") or "")
                if match and int(match.group(1)) != offset:
                    mode = "wb"
            if mode == "wb":
                offset = 0
            try:
                total = offset + int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            done = offset
            with open(part, mode) as handle:
                for chunk in response.iter_content(_DOWNLOAD_CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled("已停止下载")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
    except requests.RequestException as exc:
        raise DownloadError(f"连接下载源失败：{exc}") from exc