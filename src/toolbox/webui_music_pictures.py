"""Qt-free WebUI music picture drafts and validated reads.

The caller resolves the audio path inside the music root. This module only
reads that file and returns ``tags_io.Pic`` drafts; ``tags_io.save_tags`` remains
the writer. There is no configuration lookup, persistent draft, or new store.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import warnings
from pathlib import Path, PureWindowsPath

from music import tags_io

MAX_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PICTURES = 64
MAX_PIXELS = 40_000_000
_MAX_BASE64 = 4 * ((MAX_BYTES + 2) // 3)
_IMAGE_MIMES = {
    "JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif",
    "WEBP": "image/webp", "BMP": "image/bmp", "TIFF": "image/tiff",
}


class _PictureError(ValueError):
    """A deliberately user-facing validation message, safe to pass through."""


def _stat_key(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino


def _capture(path: Path) -> tuple[str, list[tags_io.Pic]]:
    """Read a stable picture list and identify its audio file and metadata."""
    try:
        before = _stat_key(path)
        pictures = tags_io.load_pictures(str(path))
        after = _stat_key(path)
        if before != after:
            raise _PictureError("音乐文件正在变化，请重新读取后再编辑图片")
        digest = hashlib.sha256()
        digest.update(json.dumps(before).encode("ascii"))
        digest.update(len(pictures).to_bytes(8, "big"))
        for picture in pictures:
            meta = json.dumps(
                [picture.mime, int(picture.type), picture.desc],
                ensure_ascii=True, separators=(",", ":"),
            ).encode("ascii")
            digest.update(len(meta).to_bytes(8, "big"))
            digest.update(meta)
            digest.update(len(picture.data).to_bytes(8, "big"))
            digest.update(picture.data)
        return digest.hexdigest(), pictures
    except _PictureError:
        raise
    except Exception:
        raise ValueError("读取内嵌图片失败，请重新读取音乐文件后重试") from None


def _current(path: Path, revision: str) -> list[tags_io.Pic]:
    actual, pictures = _capture(path)
    if not isinstance(revision, str) or revision != actual:
        raise ValueError("音乐文件已变化，请重新读取图片后再操作")
    return pictures


def snapshot(path: Path) -> dict:
    """Return metadata only, including malformed pictures for preservation."""
    revision, pictures = _capture(path)
    names = tags_io.PICTURE_TYPE_NAMES
    rows = []
    for index, picture in enumerate(pictures):
        picture_type = int(picture.type)
        rows.append({
            "index": index, "type": picture_type,
            "type_name": names[picture_type]
            if 0 <= picture_type < len(names) else "Other",
            "mime": picture.mime, "desc": picture.desc,
            "size": len(picture.data),
        })
    return {"revision": revision, "pictures": rows, "limits": {
        "max_bytes": MAX_BYTES, "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_pictures": MAX_PICTURES, "max_pixels": MAX_PIXELS,
    }}


def _checked_image(data: bytes, *, thumbnail: bool = False) -> tuple[bytes, str]:
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("图片为空或超过 20 MB，请选择较小的图片")
    try:
        from PIL import Image
    except ImportError:
        raise ValueError("图片组件不可用，请在全局设置中检查依赖") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                image_format = probe.format
                if image_format not in _IMAGE_MIMES:
                    raise _PictureError("只支持 JPEG、PNG、GIF、WebP、BMP 和 TIFF 图片")
                if (probe.width <= 0 or probe.height <= 0
                        or probe.width * probe.height > MAX_PIXELS):
                    raise _PictureError("图片尺寸超过 4000 万像素，请选择较小的图片")
                probe.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                if thumbnail:
                    source.thumbnail((360, 360))
                    # A new image drops metadata, including EXIF and ICC payloads.
                    rendered = Image.new("RGBA", source.size)
                    rendered.paste(source.convert("RGBA"))
                    output = io.BytesIO()
                    rendered.save(output, format="PNG")
                    return output.getvalue(), "image/png"
        return data, _IMAGE_MIMES[image_format]
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("图片尺寸过大，无法安全读取，请选择较小的图片") from None
    except _PictureError:
        raise
    except Exception:
        raise ValueError("图片已损坏或无法解码，请换一张图片后重试") from None


def read_picture(path: Path, index: int, revision: str,
                 thumbnail: bool = False) -> tuple[bytes, str]:
    """Read one validated original or a small PNG without image metadata."""
    pictures = _current(path, revision)
    if type(index) is not int or not 0 <= index < len(pictures):
        raise ValueError("请选择有效的内嵌图片")
    if type(thumbnail) is not bool:
        raise ValueError("图片预览参数无效，请重新选择图片")
    return _checked_image(pictures[index].data, thumbnail=thumbnail)


def _upload_name(value) -> str:
    if (not isinstance(value, str) or not value or len(value) > 255
            or value in (".", "..") or value.endswith((".", " "))
            or any(ord(char) < 32 for char in value)
            or any(char in value for char in '/\\:*?"<>|')
            or PureWindowsPath(value).is_reserved()
            or PureWindowsPath(value).name != value):
        raise ValueError("图片名称无效，请只提供文件名")
    return value


def _upload_data(value) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_BASE64:
        raise ValueError("图片数据为空或超过 20 MB，请重新选择图片")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("图片数据格式无效，请重新选择图片") from None
    if not data or len(data) > MAX_BYTES:
        raise ValueError("图片数据为空或超过 20 MB，请重新选择图片")
    return data


def resolve_pictures(path: Path, descriptors: list[dict],
                     revision: str) -> list[tags_io.Pic]:
    """Resolve a draft list, retaining unedited source bytes and metadata.

    Existing entries use ``source`` indices; new entries contain strict base64
    and a filename. Their list order is authoritative. ``front=True`` only
    changes that picture's type to 3, matching the desktop picture dialog.
    """
    if not isinstance(descriptors, list) or len(descriptors) > MAX_PICTURES:
        raise ValueError("图片列表无效，最多可保存 64 张图片")
    pictures = _current(path, revision)
    result = []
    total_uploaded = 0
    for descriptor in descriptors:
        if (not isinstance(descriptor, dict)
                or ("front" in descriptor and type(descriptor["front"]) is not bool)):
            raise ValueError("图片编辑参数无效，请重新读取图片")
        front = descriptor.get("front", False)
        if "source" in descriptor:
            if set(descriptor) - {"source", "front"}:
                raise ValueError("图片编辑参数无效，请重新读取图片")
            index = descriptor["source"]
            if type(index) is not int or not 0 <= index < len(pictures):
                raise ValueError("原图片已失效，请重新读取图片")
            source = pictures[index]
            picture = copy.copy(source)
            if front:
                picture.type = 3
            result.append(picture)
        else:
            if (set(descriptor) - {"data", "name", "front"}
                    or "data" not in descriptor or "name" not in descriptor):
                raise ValueError("图片编辑参数无效，请重新选择图片")
            name = _upload_name(descriptor["name"])
            data = _upload_data(descriptor["data"])
            total_uploaded += len(data)
            if total_uploaded > MAX_UPLOAD_BYTES:
                raise ValueError("本次新增图片合计超过 20 MB，请减少图片后重试")
            data, mime = _checked_image(data)
            result.append(tags_io.Pic(data, mime, 3, name))
    # Decoding can take time. Recheck before returning drafts to the writer.
    _current(path, revision)
    return result
