"""自动整理引擎 - 按日期整理文件到文件夹"""
import os
from typing import Dict, List, Tuple

from .utils import extract_date_from_filename


VIDEO_EXTS = {".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".webm", ".ogg"}


def scan_files(root_path: str) -> Tuple[Dict[str, List[Dict]], List[str]]:
    """扫描目录下的文件，按日期分组"""
    groups = {}
    no_date_files = []
    
    if not os.path.isdir(root_path):
        return groups, no_date_files
    
    try:
        for name in os.listdir(root_path):
            full_path = os.path.join(root_path, name)
            if not os.path.isfile(full_path):
                continue
            if name.startswith('.'):
                continue
            
            ext = os.path.splitext(name)[1].lower()
            if name.endswith('.py') or name.endswith('.json') or name.endswith('.log'):
                continue
            
            date_str = extract_date_from_filename(name)
            
            if date_str:
                if date_str not in groups:
                    groups[date_str] = []
                groups[date_str].append({
                    "name": name,
                    "path": full_path,
                    "size": os.path.getsize(full_path),
                    "ext": ext
                })
            else:
                no_date_files.append(name)
    except Exception:
        pass
    
    sorted_groups = dict(sorted(groups.items()))
    return sorted_groups, no_date_files


def execute_organize(root_path: str, groups: Dict[str, List[Dict]]) -> Tuple[int, int, List[str]]:
    """执行整理操作"""
    success_count = 0
    fail_count = 0
    failed_files = []
    
    for date_str, files in groups.items():
        folder_path = os.path.join(root_path, date_str)
        try:
            os.makedirs(folder_path, exist_ok=True)
        except Exception:
            fail_count += len(files)
            failed_files.extend([f["name"] for f in files])
            continue
        
        for f in files:
            src = f["path"]
            dst = os.path.join(folder_path, f["name"])
            
            if os.path.exists(dst):
                base, ext = os.path.splitext(f["name"])
                counter = 1
                while os.path.exists(dst):
                    new_name = f"{base}_{counter}{ext}"
                    dst = os.path.join(folder_path, new_name)
                    counter += 1
            
            try:
                os.rename(src, dst)
                success_count += 1
            except Exception:
                fail_count += 1
                failed_files.append(f["name"])
    
    return success_count, fail_count, failed_files