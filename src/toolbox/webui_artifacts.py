"""WebUI 采集工具（只读）：浏览四个子页导出的本地产物。

只读目录与文件；抓取 / 导出仍在桌面端。文件操作限制在各自的导出根目录内。
"""

from __future__ import annotations

from pathlib import Path

from toolbox.config_manager import get_export_dir, get_export_subdir

_KINDS = {
    "comments": ("视频评论区", "评论区"),
    "bili": ("B站资源", "B站资源"),
    "wechat": ("公众号解析", "公众号文章"),
    "emoji": ("抖音表情包", "抖音表情包"),
    "taobao": ("淘宝商品", "淘宝商品"),
    "douyin_shop": ("抖音商城", "抖音商品"),
    "bili_shop": ("B站小店", "B站小店"),
    "xianyu": ("闲鱼商品", "闲鱼商品"),
}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAX_PREVIEW = 20 * 1024 * 1024


def kind_names() -> list[dict]:
    return [{"key": key, "name": value[0], "folder": value[1]}
            for key, value in _KINDS.items()]


def _root(kind: str) -> Path:
    if kind == "comments":
        return Path(get_export_subdir("video_comments"))
    if kind == "wechat":
        return Path(get_export_subdir("wechat"))
    if kind == "emoji":
        return Path(get_export_subdir("douyin_emoji"))
    if kind == "bili":
        return Path(get_export_dir()) / "B站资源"
    if kind == "taobao":
        return Path(get_export_dir()) / "淘宝商品"
    if kind == "douyin_shop":
        return Path(get_export_subdir("douyin_shop"))
    if kind == "bili_shop":
        return Path(get_export_subdir("bili_shop"))
    if kind == "xianyu":
        export = Path(get_export_dir()).resolve()
        root = export / "闲鱼商品"
        if root.resolve().parent != export:
            raise ValueError("闲鱼商品目录不在导出范围内")
        return root
    raise ValueError("未知产物类型")


def _file_row(path: Path, root: Path) -> dict:
    try:
        stat = path.stat()
    except OSError:
        return {}
    return {
        "name": path.name,
        "rel": path.relative_to(root).as_posix(),
        "size": int(stat.st_size),
        "mtime": stat.st_mtime,
        "is_image": path.suffix.lower() in _IMAGE_EXTS,
    }


def _walk_files(folder: Path, root: Path, limit: int) -> list[dict]:
    files = []
    try:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                row = _file_row(path, root)
                if row:
                    files.append(row)
                    if len(files) >= limit:
                        break
    except OSError:
        pass
    return files


def list_artifacts(kind: str, group_limit: int = 200,
                   file_limit: int = 200) -> dict:
    kind = str(kind or "").strip()
    if kind not in _KINDS:
        raise ValueError("未知产物类型")
    root = _root(kind)
    label = _KINDS[kind][1]
    if not root.is_dir():
        return {"kind": kind, "label": label, "groups": [], "total": 0,
                "note": ("尚未保存闲鱼商品；搜索或解析后勾选商品，再点「入库并下载」。"
                         if kind == "xianyu" else
                         f"暂无产物目录（{root.name}）。桌面端导出后刷新。")}
    group_limit = max(1, min(500, int(group_limit)))
    file_limit = max(1, min(1000, int(file_limit)))
    groups = []
    if kind == "comments":
        for platform_dir in sorted(root.iterdir()):
            if not platform_dir.is_dir():
                continue
            for video_dir in sorted(platform_dir.iterdir()):
                if not video_dir.is_dir():
                    continue
                files = _walk_files(video_dir, root, file_limit)
                groups.append({
                    "name": f"{platform_dir.name} / {video_dir.name}",
                    "rel": f"{platform_dir.name}/{video_dir.name}",
                    "files": files,
                    "total": len(files),
                    "images": sum(1 for item in files if item["is_image"]),
                })
                if len(groups) >= group_limit:
                    break
            if len(groups) >= group_limit:
                break
    else:
        root_files = [row for row in (_file_row(path, root) for path in
                                      sorted(root.iterdir()) if path.is_file())
                      if row]
        if root_files:
            groups.append({"name": "根目录", "rel": "",
                           "files": root_files[:file_limit],
                           "total": len(root_files),
                           "images": sum(1 for item in root_files
                                         if item["is_image"])})
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            files = _walk_files(folder, root, file_limit)
            groups.append({
                "name": folder.name,
                "rel": folder.name,
                "files": files,
                "total": len(files),
                "images": sum(1 for item in files if item["is_image"]),
            })
            if len(groups) >= group_limit:
                break
    return {
        "kind": kind, "label": label, "groups": groups,
        "total": sum(group["total"] for group in groups),
        "note": ("本地闲鱼商品资料与图片。" if kind == "xianyu"
                 else "只读浏览；抓取与导出仍在桌面端。"),
    }


def resolve_file(kind: str, rel: str, *, preview: bool = False) -> Path:
    kind = str(kind or "").strip()
    rel = str(rel or "").strip()
    if kind not in _KINDS:
        raise ValueError("未知产物类型")
    if not rel:
        raise ValueError("缺少文件路径")
    root = _root(kind).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("文件不在产物目录内") from exc
    if not target.is_file():
        raise ValueError("文件不存在")
    if preview:
        if target.suffix.lower() not in _IMAGE_EXTS:
            raise ValueError("只支持预览图片")
        try:
            if target.stat().st_size > _MAX_PREVIEW:
                raise ValueError("图片超过 20MB，请下载查看")
        except OSError as exc:
            raise ValueError("文件读取失败") from exc
    return target
