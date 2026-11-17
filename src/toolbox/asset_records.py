"""资产数据库记录导出（L3）：列映射、链接归一化与 JSON 落盘。"""

from __future__ import annotations

import json
from datetime import datetime


def norm_dy_source_link(platform: str, video_id: str, url: str) -> str:
    """抖音旧数据兜底：时效 CDN 直链替换为永久作品页；其他原样返回。"""
    if platform == "douyin" and video_id and url and "douyin.com" not in url:
        return f"https://www.douyin.com/video/{video_id}"
    return url or ""


def rows_to_records(rows: list, platform: str) -> list:
    """increment.db 行 → 中文键记录；raw_json 全量优先，DB 列覆盖核心字段。"""
    out = []
    for row in rows:
        record = {}
        try:
            record = json.loads(row[21]) if row[21] else {}
        except Exception:
            record = {}
        record.update({
            "平台": platform, "获取时间": row[1], "类型": row[2],
            "视频ID": row[3], "标题": row[4], "作者": row[5],
            "发布时间": row[6], "播放量": row[7], "点赞数": row[8],
            "评论数": row[9], "分享数": row[10], "收藏数": row[11],
            "弹幕数": row[12], "投币数": row[13], "时长": row[14],
            "正文": row[15], "封面URL": row[16],
            "链接": norm_dy_source_link(platform, str(row[3] or ""), row[17] or ""),
            "标签": row[18], "分类": row[19], "音乐": row[20],
        })
        out.append(record)
    return out


def export_rows_json(rows: list, platform: str, out_dir) -> str:
    """导出勾选记录为 JSON，返回文件路径。"""
    records = rows_to_records(rows, platform)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"数据库导出_{platform}_{len(records)}条_{ts}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)
