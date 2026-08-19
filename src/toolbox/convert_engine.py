"""视频转换引擎 - 基于 ffmpeg 的视频批量转换"""
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from dataclasses import dataclass

from .utils import preserve_timestamps, classify_ffmpeg_error


@dataclass
class ConvertResult:
    src: Path
    dst: Path
    rel: Path
    success: bool = False
    mode: str = ""  # lossless, encode, skip, failed, interrupted
    error_type: str = ""
    error_detail: str = ""
    interrupted: bool = False


# ==================== 缓存操作 ====================

def is_convert_cached(cache: Dict, src: Path) -> bool:
    key = str(src.resolve())
    if key not in cache:
        return False
    entry = cache[key]
    if not entry.get("success", False):
        return False
    try:
        st = src.stat()
        if entry.get("size") == st.st_size and abs(entry.get("mtime", 0) - st.st_mtime) < 1:
            return True
    except Exception:
        pass
    return False


def update_convert_cache(cache: Dict, src: Path, success: bool, output: Path = None):
    key = str(src.resolve())
    try:
        st = src.stat()
        cache[key] = {
            "size": st.st_size,
            "mtime": st.st_mtime,
            "success": success,
            "output": str(output.resolve()) if output else "",
            "timestamp": time.time(),
        }
    except Exception:
        pass


# ==================== ffmpeg 操作 ====================

def lossless_mux(src: Path, dst: Path, config: Dict) -> Tuple[bool, str, str]:
    """无损封装到 MP4"""
    cmd = ["ffmpeg", "-i", str(src)]
    cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    if config.get("preserve_metadata", True):
        cmd += ["-map_metadata", "0", "-map_chapters", "0"]
    cmd += [
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-f", "mp4",
        "-y",
        "-hide_banner",
        str(dst),
    ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = p.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return False, "超时", "转换超时（超过 10 分钟）"
        
        stderr_str = stderr.decode("utf-8", errors="ignore")
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return True, "", ""
        err_type, err_detail = classify_ffmpeg_error(stderr_str)
        return False, err_type, err_detail
    except FileNotFoundError:
        return False, "ffmpeg 未找到", "请确认 ffmpeg 已安装并加入系统 PATH"
    except Exception as e:
        return False, "未知异常", type(e).__name__ + ": " + str(e)


def encode_video(src: Path, dst: Path, config: Dict) -> Tuple[bool, str, str]:
    """转码视频"""
    cmd = ["ffmpeg", "-i", str(src)]
    cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    if config.get("preserve_metadata", True):
        cmd += ["-map_metadata", "0", "-map_chapters", "0"]
    cmd += [
        "-c:v", config.get("encode_video_codec", "libx264"),
        "-crf", str(config.get("encode_crf", 18)),
        "-preset", config.get("encode_preset", "fast"),
    ]
    if config.get("encode_audio_copy", True):
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", "-y", "-hide_banner", str(dst)]
    
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = p.communicate(timeout=3600)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return False, "超时", "转码超时（超过 1 小时）"
        
        stderr_str = stderr.decode("utf-8", errors="ignore")
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return True, "", ""
        detail = stderr_str[-500:] if len(stderr_str) > 500 else stderr_str
        return False, "转码失败", detail
    except FileNotFoundError:
        return False, "ffmpeg 未找到", "请确认 ffmpeg 已安装并加入系统 PATH"
    except Exception as e:
        return False, "未知异常", type(e).__name__ + ": " + str(e)


def get_output_path(src: Path, root: Path, config: Dict) -> Path:
    """获取输出路径"""
    out_ext = config.get("output_extension", ".mp4")
    output_dir = config.get("output_dir", "")
    if output_dir:
        rel = src.relative_to(root)
        dst = Path(output_dir) / rel.with_suffix(out_ext)
        dst.parent.mkdir(parents=True, exist_ok=True)
        return dst
    else:
        return src.with_suffix(out_ext)


def convert_one_file(
    src: Path,
    root: Path,
    config: Dict,
    progress_callback: Callable[[str], None] = None,
    should_stop: Callable[[], bool] = None,
) -> ConvertResult:
    """转换单个文件"""
    dst = get_output_path(src, root, config)
    tmp = dst.parent / (dst.stem + ".tmp.mp4")
    rel = src.relative_to(root)
    
    result = ConvertResult(src=src, dst=dst, rel=rel)
    
    if dst.exists():
        result.success = True
        result.mode = "skip"
        if progress_callback:
            progress_callback(f"📂 {rel}\n✅ 已存在，跳过")
        return result
    
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    
    if progress_callback:
        progress_callback(f"📂 {rel}")
    
    max_retries = config.get("retry_count", 2) + 1
    for attempt in range(max_retries):
        if should_stop and should_stop():
            result.interrupted = True
            break
        
        if attempt > 0:
            if progress_callback:
                progress_callback(f"   🔄 第 {attempt} 次重试...")
            time.sleep(config.get("retry_delay_seconds", 1))
        
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        
        ok, err_type, err_detail = lossless_mux(src, tmp, config)
        
        if should_stop and should_stop():
            result.interrupted = True
            break
        
        if ok:
            try:
                tmp.replace(dst)
            except Exception as e:
                result.error_type = "重命名失败"
                result.error_detail = str(e)
                continue
            
            if config.get("preserve_timestamps", True):
                preserve_timestamps(src, dst)
            
            if config.get("delete_source_after_success", False):
                try:
                    src.unlink()
                except Exception:
                    pass
            
            result.success = True
            result.mode = "lossless"
            if progress_callback:
                progress_callback("✅ 无损完成")
            return result
        
        if config.get("enable_encode_fallback", False) and not (should_stop and should_stop()):
            if progress_callback:
                progress_callback("   ⚠️ 无损失败，转码中...")
            
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            
            ok_enc, err_type_enc, err_detail_enc = encode_video(src, tmp, config)
            
            if should_stop and should_stop():
                result.interrupted = True
                break
            
            if ok_enc:
                try:
                    tmp.replace(dst)
                except Exception as e:
                    result.error_type = "重命名失败"
                    result.error_detail = str(e)
                    continue
                
                if config.get("preserve_timestamps", True):
                    preserve_timestamps(src, dst)
                
                if config.get("delete_source_after_success", False):
                    try:
                        src.unlink()
                    except Exception:
                        pass
                
                result.success = True
                result.mode = "encode"
                if progress_callback:
                    progress_callback("✅ 转码完成")
                return result
            else:
                err_type = err_type_enc
                err_detail = err_detail_enc
        
        result.error_type = err_type
        result.error_detail = err_detail
    
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    
    if result.interrupted:
        result.mode = "interrupted"
        if progress_callback:
            progress_callback("⏸️ 已中止")
    else:
        result.mode = "failed"
        if progress_callback:
            progress_callback(f"❌ 失败 [{result.error_type}]")
    
    return result


def find_videos(root: Path, config: Dict) -> List[Path]:
    """查找需要转换的视频文件"""
    exts = tuple(e.lower() for e in config.get("input_extensions", [".flv", ".mkv", ".ts"]))
    videos = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in exts:
            videos.append(path)
    return sorted(videos)
