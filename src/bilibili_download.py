"""
B站视频下载模块：playurl API 拿 DASH 流 + ffmpeg 合并，支持画质选择（默认最高画质）。

画质 qn 值（accept_quality 返回可用列表，越靠前越高）：
  120=4K, 116=1080P60, 112=1080P+, 80=1080P, 74=720P60, 64=720P, 32=480P, 16=360P, 6=240P
"""

import os
import re
import time
import subprocess
import tempfile

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

# 画质描述兜底（优先用 API 返回的 accept_description）
QUALITY_DESC = {
    127: "8K 超高清", 126: "杜比视界", 125: "HDR 真彩",
    120: "4K 超清", 116: "1080P 60帧", 112: "1080P+", 80: "1080P",
    74: "720P 60帧", 64: "720P", 32: "480P", 16: "360P", 6: "240P",
}


def _headers(bvid: str, cookie: str = "") -> dict:
    h = {
        "User-Agent": UA,
        "Referer": f"https://www.bilibili.com/video/{bvid}",
        "Origin": "https://www.bilibili.com",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def _sanitize_filename(name: str, max_len: int = 80) -> str:
    """安全文件名（统一实现见 toolbox.utils.sanitize_filename，默认截断 80 回退 video）"""
    from toolbox.utils import sanitize_filename
    return sanitize_filename(name, max_len, "video")


def get_bilibili_play_info(bvid: str, cid, cookie: str = "") -> dict:
    """获取播放信息，返回 {qualities, video_streams, audio_streams}。

    qualities: [(qn, 描述), ...] 可用画质列表（从高到低）
    """
    # fnval=4048：DASH + 杜比视界(64) + 杜比全景声(128) + Hi-Res(256) + 8K(1024) 等全部流
    params = {"bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1}
    r = httpx.get(PLAYURL_API, headers=_headers(bvid, cookie), params=params, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(d.get("message") or "获取播放地址失败")
    data = d.get("data") or {}
    aq = data.get("accept_quality") or []
    ad = data.get("accept_description") or []
    qualities = []
    for qn, desc in zip(aq, ad):
        qualities.append((int(qn), desc or QUALITY_DESC.get(int(qn), f"画质{qn}")))
    dash = data.get("dash") or {}
    return {
        "qualities": qualities,
        "video_streams": dash.get("video", []),
        "audio_streams": dash.get("audio", []),
    }


def _pick_video_stream(video_list, target_qn):
    """从视频流里选目标画质的流，同一画质优先 AVC(codecid=7，兼容性最好)。"""
    candidates = [s for s in video_list if int(s.get("id", 0)) == int(target_qn)]
    if not candidates:
        # 目标画质不可用，退回所有流里选最高的
        candidates = sorted(video_list, key=lambda s: -int(s.get("id", 0)))
    # 优先 AVC
    for s in candidates:
        if s.get("codecid") == 7:
            return s
    # 否则选带宽最低（省流量）
    return min(candidates, key=lambda s: s.get("bandwidth", 0))


def _pick_audio_stream(audio_list):
    """选最高音质的音频流。"""
    if not audio_list:
        return None
    return max(audio_list, key=lambda s: s.get("bandwidth", 0))


def _download_to_file(url: str, path: str, headers: dict, progress_cb=None,
                       backup_urls: list | None = None) -> str:
    """下载到文件，返回文件路径。主链接失败自动尝试 backupUrl 镜像。"""
    urls = [url] + (backup_urls or [])
    last_err = None
    for i, u in enumerate(urls):
        try:
            with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
                with client.stream("GET", u) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0) or 0)
                    done = 0
                    with open(path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            done += len(chunk)
                            if progress_cb:
                                progress_cb(done, total)
                    return path
        except Exception as e:
            last_err = e
            if i < len(urls) - 1:
                continue  # 还有镜像可试
            raise last_err


def get_cid(video_id: str, cookie: str = "") -> tuple:
    """根据 bvid 或 av 号拿 (cid, bvid)。"""
    s = str(video_id)
    params = {"aid": s[2:]} if s.lower().startswith("av") else {"bvid": s}
    r = httpx.get(
        "https://api.bilibili.com/x/web-interface/view",
        headers=_headers(s, cookie), params=params, timeout=15,
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(d.get("message") or "获取视频信息失败")
    data = d.get("data") or {}
    return data.get("cid", 0), data.get("bvid", s)


def download_bilibili_by_bvid(
    video_id: str,
    quality: int | None = None,
    cookie: str = "",
    out_dir: str = "",
    title: str = "",
    progress_cb=None,
    author: str = "",
    publish_time: str = "",
) -> str:
    """便捷入口：根据 bvid/av 号下载（内部自动拿 cid）。quality=None 表示最高画质。"""
    cid, bvid = get_cid(video_id, cookie)
    return download_bilibili_video(bvid, cid, quality, cookie, out_dir, title, progress_cb, author, publish_time)


def _stream_urls(stream: dict) -> list:
    """流的全部可用链接：baseUrl + backupUrl 镜像（503 时逐个回退）。"""
    urls = []
    for k in ("baseUrl", "base_url"):
        if stream.get(k):
            urls.append(stream[k])
    for k in ("backupUrl", "backup_url"):
        v = stream.get(k)
        if isinstance(v, list):
            urls.extend(u for u in v if u)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def download_bilibili_video(
    bvid: str,
    cid,
    quality: int | None = None,
    cookie: str = "",
    out_dir: str = "",
    title: str = "",
    progress_cb=None,
    author: str = "",
    publish_time: str = "",
) -> str:
    """下载 B站视频（视频流 + 音频流 ffmpeg 合并），返回输出文件路径。

    quality: 目标画质 qn（None=最高画质）
    author / publish_time: 用于组织目录与文件名（[平台][用户]{datetime}_{发布者}_{标题}.mp4）
    """
    info = get_bilibili_play_info(bvid, cid, cookie)
    video_streams = info["video_streams"]
    audio_streams = info["audio_streams"]
    if not video_streams:
        raise RuntimeError("未获取到视频流")

    # 默认最高画质
    if quality is None:
        quality = info["qualities"][0][0]
    video = _pick_video_stream(video_streams, quality)
    audio = _pick_audio_stream(audio_streams)

    # 目录：B站_媒体/[用户]；文件名 {datetime}_{发布者}_{标题}.mp4
    out_dir = out_dir or os.getcwd()
    date_str = re.sub(r'[^0-9]', '', (publish_time or "")[:10]) or time.strftime("%Y%m%d")
    author_safe = _sanitize_filename(author or "未知")
    title_safe = _sanitize_filename(title or bvid)
    user_dir = os.path.join(out_dir, "B站_媒体", author_safe)
    os.makedirs(user_dir, exist_ok=True)
    out_path = os.path.join(user_dir, f"{date_str}_{author_safe}_{title_safe}.mp4")

    headers = _headers(bvid, cookie)

    tmpdir = tempfile.mkdtemp(prefix="bili_dl_")
    video_tmp = os.path.join(tmpdir, "video.m4s")
    audio_tmp = os.path.join(tmpdir, "audio.m4s")
    try:
        # 下载视频流（主链接 503/超时自动回退 backupUrl 镜像）
        vurls = _stream_urls(video)
        _download_to_file(vurls[0], video_tmp, headers, progress_cb, vurls[1:])
        # 下载音频流
        if audio and audio.get("baseUrl"):
            aurls = _stream_urls(audio)
            _download_to_file(aurls[0], audio_tmp, headers, progress_cb, aurls[1:])
        # ffmpeg 合并
        if audio and os.path.exists(audio_tmp):
            cmd = ["ffmpeg", "-y", "-i", video_tmp, "-i", audio_tmp, "-c", "copy", out_path]
        else:
            cmd = ["ffmpeg", "-y", "-i", video_tmp, "-c", "copy", out_path]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 合并失败: {r.stderr[-300:]}")
        return out_path
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
