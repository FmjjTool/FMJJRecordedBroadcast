"""只读视频信息用例；桌面与 Web 共用摘要和标签，不保存配置或媒体。"""

from pathlib import Path

from .edit_engine import read_metadata
from .rename_engine import extract_time_str
from .utils import get_video_info


def read_video_details(path: Path) -> dict:
    """复用现有探测/时间识别，返回不含绝对路径的紧凑摘要与可编辑标签。"""
    path = Path(path)
    info = get_video_info(str(path))
    if not isinstance(info, dict) or info.get("error"):
        raise ValueError("读取视频信息失败，请检查文件是否完整后重试")
    meta = read_metadata(path)
    audio = info.get("audio_codec") or ("未知" if info.get("has_audio") else "无音轨")
    return {
        "summary_lines": [
            path.name,
            f"时间: {extract_time_str(path.name)}",
            f"{info.get('resolution') or '未知'} | {info.get('orientation') or '未知'}",
            f"{info.get('duration_str') or '未知'} | {info.get('fps', '未知')} fps",
            f"{info.get('file_size_str') or '未知'} | {audio}",
        ],
        "tags": {key: str(meta.get(key) or "")
                 for key in ("title", "comment", "artist", "date")},
        "rotation": int(meta.get("rotation") or 0),
    }
