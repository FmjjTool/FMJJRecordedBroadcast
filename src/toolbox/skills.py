"""内置 AI Skill 库：定位、枚举、导出（设置页「下载 Skill」功能的数据层）。

skill 资源位于 src/skills/<skill-name>/（SKILL.md + 任意子目录），
打包时经 build.py ADD_DATA 收进 exe（frozen 后落在 sys._MEIPASS/skills）。
「下载」= 把选中 skill 的整个目录复制到用户指定的位置，供 AI 助手挂载使用。
"""

import shutil
import sys
from pathlib import Path


def skills_root() -> Path:
    """内置 skill 资源根目录（开发模式 src/skills，打包后 _MEIPASS/skills）。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "skills"
    return Path(__file__).resolve().parent.parent / "skills"


def _parse_skill_md(md_path: Path) -> dict:
    """解析 SKILL.md：frontmatter 的 name/description + 正文首个 # 标题作显示名。"""
    meta = {"id": md_path.parent.name, "name": md_path.parent.name,
            "display_name": md_path.parent.name, "description": ""}
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return meta
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            s = line.strip()
            if s == "---":
                break
            if s.startswith("name:") and len(s) > 5:
                meta["name"] = s[5:].strip().strip('"').strip("'")
            elif s.startswith("description:") and len(s) > 12:
                meta["description"] = s[12:].strip().strip('"').strip("'")
    for line in lines:
        s = line.strip()
        if s.startswith("# ") and len(s) > 2:
            meta["display_name"] = s[2:].strip()
            break
    return meta


def list_skills() -> list:
    """扫描内置 skill 目录，返回元数据列表（无 SKILL.md 的目录跳过）。"""
    root = skills_root()
    result = []
    if not root.is_dir():
        return result
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        meta = _parse_skill_md(md)
        files = [p for p in d.rglob("*") if p.is_file()]
        meta["dir"] = d
        meta["file_count"] = len(files)
        meta["size_bytes"] = sum(p.stat().st_size for p in files)
        result.append(meta)
    return result


def export_skill(skill_id: str, dest_dir: str) -> Path:
    """把指定 skill 目录完整复制到 dest_dir/<skill_id>/（已存在则覆盖合并）。"""
    src = skills_root() / skill_id
    if not src.is_dir() or not (src / "SKILL.md").is_file():
        raise FileNotFoundError(f"内置 Skill 不存在: {skill_id}")
    target = Path(dest_dir) / skill_id
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target, dirs_exist_ok=True)
    return target


def skill_total_size_text() -> str:
    """所有内置 skill 的总大小（KB 文本，用于设置页概览）。"""
    total = sum(m.get("size_bytes", 0) for m in list_skills())
    return f"{total / 1024:.0f} KB"
