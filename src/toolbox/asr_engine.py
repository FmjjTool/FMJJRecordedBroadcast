"""语音转字幕引擎。

在线（免费）：B 接口、J 接口；联网需 Key：Whisper API。
本地（离线）：faster-whisper 独立程序（whisper-faster.exe + ModelScope 模型）。

长音频统一按「块长 - 重叠」切片并发识别，再按时间轴合并，避免单次请求过大。
不依赖 pydub / openai 等额外包，切块与转码走 ffmpeg。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from .config_manager import get_app_root
from .utils import find_ffmpeg_exe, find_ffprobe_exe

# ==================== 引擎 ====================

ENGINE_BIJIAN = "bijian"
ENGINE_JIANYING = "jianying"
ENGINE_LOCAL = "local"
ENGINE_WHISPER_API = "whisper_api"

ASR_ENGINES: List[Tuple[str, str]] = [
    (ENGINE_BIJIAN, "B 接口"),
    (ENGINE_JIANYING, "J 接口"),
    (ENGINE_LOCAL, "本地 Whisper"),
    (ENGINE_WHISPER_API, "Whisper [API]"),
]

ENGINE_LABELS = dict(ASR_ENGINES)

LOCAL_MODELS: List[Tuple[str, str, int]] = [
    # (key, 显示名, 约等于 MB)
    ("tiny", "Tiny（最快·精度低）", 72),
    ("base", "Base（推荐·速度快）", 139),
    ("small", "Small（平衡）", 461),
    ("medium", "Medium（较准·较慢）", 1457),
    ("large-v3", "Large-v3（最准·最慢）", 2944),
]

DEFAULT_CHUNK_SEC = 600       # 每块 10 分钟
DEFAULT_OVERLAP_SEC = 10      # 块间重叠 10 秒
DEFAULT_CONCURRENCY = 3       # 在线引擎并发 3 块
ONLINE_POLL_INTERVAL = 1.0
ONLINE_POLL_TIMEOUT = 900     # 单块最长等待 15 分钟


class AsrError(RuntimeError):
    """识别失败。"""


class AsrCancelled(RuntimeError):
    """用户中止。"""


@dataclass
class AsrSegment:
    start_ms: int
    end_ms: int
    text: str


ProgressCb = Optional[Callable[[int, str], None]]
CancelCb = Optional[Callable[[], bool]]


def _noop_progress(_percent: int, _message: str) -> None:
    pass


def _never_cancel() -> bool:
    return False


def _check_cancel(cancel: CancelCb) -> None:
    if cancel and cancel():
        raise AsrCancelled("已停止")


# ==================== 输入校验 ====================

def validate_engine(engine: str, local_model: str = "base",
                    whisper_api: Optional[Dict[str, str]] = None) -> str:
    """返回可读的错误描述；配置齐全时返回空串。"""
    if engine == ENGINE_LOCAL:
        if not local_program_ready():
            return "本地识别程序未下载，请到「语音设置」里下载"
        if not local_model_ready(local_model):
            return f"本地模型 {local_model} 未下载，请到「语音设置」里下载"
    elif engine == ENGINE_WHISPER_API:
        cfg = whisper_api or {}
        if not (cfg.get("base_url") or "").strip():
            return "Whisper API 未填写 Base URL"
        if not (cfg.get("api_key") or "").strip():
            return "Whisper API 未填写 API Key"
        if not (cfg.get("model") or "").strip():
            return "Whisper API 未填写模型名"
    elif engine not in (ENGINE_BIJIAN, ENGINE_JIANYING):
        return f"未知识别引擎: {engine}"
    return ""


# ==================== 时间轴 / 输出 ====================

def format_srt_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def build_srt(segments: List[AsrSegment]) -> str:
    lines: List[str] = []
    for idx, seg in enumerate(segments, 1):
        text = seg.text.strip()
        if not text:
            continue
        start = format_srt_time(seg.start_ms)
        end = format_srt_time(max(seg.end_ms, seg.start_ms + 200))
        lines.extend([str(idx), f"{start} --> {end}", text, ""])
    return "\n".join(lines)


def build_plain_text(segments: List[AsrSegment]) -> str:
    return "\n".join(seg.text.strip() for seg in segments if seg.text.strip())


def write_subtitles(segments: List[AsrSegment], audio_path: Path,
                    with_txt: bool = True) -> List[Path]:
    """在音频同目录写 .srt（可选 .txt），返回写出的文件列表。"""
    written: List[Path] = []
    srt_path = audio_path.with_suffix(".srt")
    srt_path.write_text(build_srt(segments), encoding="utf-8")
    written.append(srt_path)
    if with_txt:
        txt_path = audio_path.with_suffix(".txt")
        txt_path.write_text(build_plain_text(segments), encoding="utf-8")
        written.append(txt_path)
    return written


_SRT_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str) -> List[AsrSegment]:
    segments: List[AsrSegment] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        time_line = next((ln for ln in lines if "-->" in ln), "")
        match = _SRT_TIME_RE.search(time_line)
        if not match:
            continue
        nums = [int(x) for x in match.groups()]
        start = ((nums[0] * 3600 + nums[1] * 60 + nums[2]) * 1000) + nums[3]
        end = ((nums[4] * 3600 + nums[5] * 60 + nums[6]) * 1000) + nums[7]
        idx = lines.index(time_line)
        body = " ".join(ln.strip() for ln in lines[idx + 1:]).strip()
        if body:
            segments.append(AsrSegment(start, end, body))
    return segments


# ==================== ffmpeg / ffprobe ====================

def media_duration(path: Path) -> float:
    """ffprobe 读取时长（秒），失败抛 AsrError。"""
    cmd = [
        find_ffprobe_exe(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                             creationflags=_no_window())
        value = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and value:
            return float(value[0])
    except Exception:
        pass
    raise AsrError(f"读取音频时长失败: {path.name}")


def extract_mp3_chunk(src: Path, start_sec: float, duration_sec: float, dst: Path) -> None:
    """截取一段音频并转成 16k 单声道 mp3（上传体积小、识别稳定）。"""
    cmd = [
        find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, start_sec):.3f}",
        "-t", f"{max(1.0, duration_sec):.3f}",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
        "-f", "mp3", str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800,
                          creationflags=_no_window())
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        err = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise AsrError(f"音频切块失败: {err or '未知错误'}")


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """带一次重试的请求封装。"""
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            return session.request("POST", url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(1.5)
    raise AsrError(f"网络请求失败: {last_exc}")


# ==================== 必剪 B 接口 ====================

class BcutClient:
    """B站必剪云端识别（免费、免登录）。"""

    API = "https://member.bilibili.com/x/bcut/rubick-interface"
    HEADERS = {
        "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
        "Content-Type": "application/json",
    }

    def __init__(self):
        self.session = requests.Session()

    def transcribe(self, mp3_path: Path, progress: ProgressCb = None,
                   cancel: CancelCb = None) -> List[AsrSegment]:
        progress = progress or _noop_progress
        data = mp3_path.read_bytes()
        if not data:
            raise AsrError("音频为空")

        progress(5, "上传音频")
        create = _post(self.session, self.API + "/resource/create", data=json.dumps({
            "type": 2, "name": "audio.mp3", "size": len(data),
            "ResourceFileType": "mp3", "model_id": "8",
        }), headers=self.HEADERS, timeout=30).json()
        info = create.get("data") or {}
        if not info.get("resource_id"):
            raise AsrError(f"B 接口创建上传失败: {create.get('message') or create}")

        etags: List[str] = []
        urls = info.get("upload_urls") or []
        if not urls:
            raise AsrError("B 接口未返回上传地址")
        per_size = int(info.get("per_size") or len(data))
        for idx, url in enumerate(urls):
            _check_cancel(cancel)
            chunk = data[idx * per_size:(idx + 1) * per_size]
            resp = self.session.put(url, data=chunk, headers=self.HEADERS,
                                    timeout=(15, 300))
            resp.raise_for_status()
            etag = resp.headers.get("Etag")
            if etag:
                etags.append(etag)
            progress(10 + int(30 * (idx + 1) / max(1, len(urls))), "上传音频")

        progress(45, "提交识别任务")
        commit = _post(self.session, self.API + "/resource/create/complete",
                       data=json.dumps({
                           "InBossKey": info.get("in_boss_key"),
                           "ResourceId": info.get("resource_id"),
                           "Etags": ",".join(etags),
                           "UploadId": info.get("upload_id"),
                           "model_id": "8",
                       }), headers=self.HEADERS, timeout=30).json()
        download_url = (commit.get("data") or {}).get("download_url")
        if not download_url:
            raise AsrError(f"B 接口提交上传失败: {commit.get('message') or commit}")

        task = _post(self.session, self.API + "/task",
                     json={"resource": download_url, "model_id": "8"},
                     headers=self.HEADERS, timeout=30).json()
        task_id = (task.get("data") or {}).get("task_id")
        if not task_id:
            raise AsrError(f"B 接口创建任务失败: {task.get('message') or task}")

        progress(55, "识别中")
        deadline = time.time() + ONLINE_POLL_TIMEOUT
        while time.time() < deadline:
            _check_cancel(cancel)
            resp = self.session.get(self.API + "/task/result",
                                    params={"model_id": 7, "task_id": task_id},
                                    headers=self.HEADERS, timeout=30).json()
            payload = resp.get("data") or {}
            state = payload.get("state")
            if state == 4:
                raw = payload.get("result")
                result = json.loads(raw) if isinstance(raw, str) else (raw or {})
                return [
                    AsrSegment(int(u.get("start_time", 0)), int(u.get("end_time", 0)),
                               (u.get("transcript") or "").strip())
                    for u in result.get("utterances", [])
                ]
            if state == 3:
                raise AsrError(f"B 接口识别失败: {str(payload)[:200]}")
            time.sleep(ONLINE_POLL_INTERVAL)
        raise AsrError("B 接口识别超时")


# ==================== 剪映 J 接口 ====================

class JianyingClient:
    """剪映云端识别（免费），签名由公开签名服务生成。"""

    BASE = "https://lv-pc-api-sinfonlinec.ulikecam.com"
    SIGN_URL = "https://asrtools-update.bkfeng.top/sign"
    UA = "FengMingToolbox/1.0 (ASR)"

    def __init__(self):
        self.session = requests.Session()
        self.tdid = self._make_tdid()

    @staticmethod
    def _make_tdid() -> str:
        """设备标识：剪映要求 16 位数字（年份尾数 + 固定/网卡派生）。"""
        tail = str(datetime.now().year)[3]
        prefix = 390 + int(tail)
        suffix = ("3278516897751" if int(tail) % 2 != 0
                  else f"{uuid.getnode():013d}")
        return f"{prefix}{suffix}"

    def _sign(self, path: str) -> Tuple[str, str]:
        now = str(int(time.time()))
        body = {"url": path, "current_time": now, "pf": "4",
                "appvr": "6.6.0", "tdid": self.tdid}
        try:
            resp = self.session.post(self.SIGN_URL, json=body, timeout=(10, 30),
                                     headers={"User-Agent": self.UA,
                                              "tdid": self.tdid, "t": now})
            resp.raise_for_status()
            sign = (resp.json() or {}).get("sign")
        except Exception as exc:  # noqa: BLE001
            raise AsrError(f"J 接口签名服务不可用: {exc}") from exc
        if not sign:
            raise AsrError("J 接口签名服务返回为空")
        return sign.lower(), now

    def _headers(self, sign: str, device_time: str) -> Dict[str, str]:
        return {
            "User-Agent": ("Cronet/TTNetVersion:d4572e53 2024-06-12 "
                           "QuicVersion:4bf243e0 2023-04-17"),
            "appvr": "6.6.0", "device-time": str(device_time), "pf": "4",
            "sign": sign, "sign-ver": "1", "tdid": self.tdid,
        }

    def _api(self, path: str, payload: Dict) -> Dict:
        sign, device_time = self._sign(path)
        resp = _post(self.session, self.BASE + path, json=payload,
                     headers=self._headers(sign, device_time), timeout=60)
        data = resp.json()
        if str(data.get("ret")) != "0":
            raise AsrError(f"J 接口错误: {data.get('errmsg') or data}")
        return data.get("data") or {}

    def transcribe(self, mp3_path: Path, progress: ProgressCb = None,
                   cancel: CancelCb = None) -> List[AsrSegment]:
        progress = progress or _noop_progress
        data = mp3_path.read_bytes()
        if not data:
            raise AsrError("音频为空")
        crc32_hex = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")

        progress(5, "获取上传凭证")
        sign, device_time = self._sign("/lv/v1/upload_sign")
        upload_sign = _post(
            self.session, self.BASE + "/lv/v1/upload_sign",
            data=json.dumps({"biz": "pc-recognition"}),
            headers=self._headers(sign, device_time), timeout=60,
        ).json()
        cred = upload_sign.get("data") or {}
        access_key = cred.get("access_key_id")
        secret_key = cred.get("secret_access_key")
        session_token = cred.get("session_token")
        if not (access_key and secret_key and session_token):
            raise AsrError("J 接口上传凭证获取失败")

        _check_cancel(cancel)
        progress(12, "申请上传地址")
        params = (f"Action=ApplyUploadInner&FileSize={len(data)}"
                  f"&FileType=object&IsInner=1&SpaceName=lv-mac-recognition"
                  f"&Version=2020-11-19&s=5y0udbjapi")
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        headers = {"x-amz-date": amz_date, "x-amz-security-token": session_token}
        signature = _aws_signature(secret_key, params, headers)
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{datestamp}/cn/vod/aws4_request, "
            f"SignedHeaders=x-amz-date;x-amz-security-token, Signature={signature}")
        resp = self.session.get(f"https://vod.bytedanceapi.com/?{params}",
                                headers=headers, timeout=60)
        upload = (resp.json().get("Result") or {}).get("UploadAddress") or {}
        store = (upload.get("StoreInfos") or [{}])[0]
        store_uri = store.get("StoreUri")
        auth = store.get("Auth")
        upload_id = store.get("UploadID")
        host = (upload.get("UploadHosts") or [""])[0]
        if not (store_uri and auth and upload_id and host):
            raise AsrError("J 接口上传地址获取失败")

        _check_cancel(cancel)
        progress(20, "上传音频")
        file_headers = {
            "User-Agent": "Mozilla/5.0",
            "Authorization": auth,
            "Content-CRC32": crc32_hex,
        }
        url = f"https://{host}/{store_uri}?partNumber=1&uploadID={upload_id}"
        up = self.session.put(url, data=data, headers=file_headers, timeout=(15, 600))
        up.raise_for_status()
        # 分片上报 CRC 即完成上传；随后的 commit 请求剪映固定返回 400，忽略即可
        self.session.post(f"https://{host}/{store_uri}?uploadID={upload_id}",
                          data=f"1:{crc32_hex}", headers=file_headers, timeout=60)

        _check_cancel(cancel)
        progress(45, "提交识别任务")
        duration_ms = int(media_duration(mp3_path) * 1000) or 600000
        submitted = self._api("/lv/v1/audio_subtitle/submit", {
            "adjust_endtime": 200,
            "audio": store_uri,
            "caption_type": 2,
            "client_request_id": str(uuid.uuid4()),
            "max_lines": 1,
            "songs_info": [{"end_time": duration_ms, "id": "", "start_time": 0}],
            "words_per_line": 16,
        })
        query_id = submitted.get("id")
        if not query_id:
            raise AsrError("J 接口任务提交失败")

        progress(55, "识别中")
        deadline = time.time() + ONLINE_POLL_TIMEOUT
        while time.time() < deadline:
            _check_cancel(cancel)
            result = self._api("/lv/v1/audio_subtitle/query",
                               {"id": query_id, "pack_options": {"need_attribute": True}})
            utterances = result.get("utterances")
            if utterances:
                return [
                    AsrSegment(int(u.get("start_time", 0)), int(u.get("end_time", 0)),
                               (u.get("text") or "").strip())
                    for u in utterances
                ]
            time.sleep(ONLINE_POLL_INTERVAL)
        raise AsrError("J 接口识别超时")


def _aws_signature(secret_key: str, request_parameters: str,
                   headers: Dict[str, str], region: str = "cn",
                   service: str = "vod") -> str:
    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    datestamp = headers["x-amz-date"].split("T")[0]
    signing_key = _hmac(("AWS4" + secret_key).encode("utf-8"), datestamp)
    for part in (region, service, "aws4_request"):
        signing_key = _hmac(signing_key, part)

    canonical_headers = "\n".join(f"{k}:{v}" for k, v in headers.items()) + "\n"
    signed_headers = ";".join(headers.keys())
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_request = (f"GET\n/\n{request_parameters}\n{canonical_headers}\n"
                         f"{signed_headers}\n{payload_hash}")
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{headers['x-amz-date']}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}")
    return hmac.new(signing_key, string_to_sign.encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ==================== Whisper API ====================

class WhisperApiClient:
    """OpenAI 兼容 /audio/transcriptions 接口。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model

    def transcribe(self, mp3_path: Path, progress: ProgressCb = None,
                   cancel: CancelCb = None, language: str = "zh") -> List[AsrSegment]:
        progress = progress or _noop_progress
        _check_cancel(cancel)
        progress(20, "上传识别")
        data = mp3_path.read_bytes()
        request_data = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if language:
            request_data["language"] = language
            if language == "zh":
                request_data["prompt"] = "你好，我们需要使用简体中文，以下是普通话的句子"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (mp3_path.name, data, "audio/mpeg")}
        try:
            resp = requests.post(f"{self.base_url}/audio/transcriptions",
                                 headers=headers, data=request_data, files=files,
                                 timeout=(15, 900))
            if resp.status_code >= 400:
                # 部分兼容服务不支持 verbose_json，退回 srt
                request_data["response_format"] = "srt"
                request_data.pop("timestamp_granularities[]", None)
                resp = requests.post(f"{self.base_url}/audio/transcriptions",
                                     headers=headers, data=request_data,
                                     files={"file": (mp3_path.name, data, "audio/mpeg")},
                                     timeout=(15, 900))
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise AsrError(f"Whisper API 请求失败: {exc}") from exc

        progress(85, "解析结果")
        content_type = resp.headers.get("Content-Type", "")
        if "json" in content_type:
            payload = resp.json()
            if isinstance(payload.get("segments"), list):
                return [
                    AsrSegment(int(float(s.get("start", 0)) * 1000),
                               int(float(s.get("end", 0)) * 1000),
                               (s.get("text") or "").strip())
                    for s in payload["segments"]
                ]
            if isinstance(payload.get("text"), str):
                return [AsrSegment(0, 0, payload["text"].strip())]
            raise AsrError("Whisper API 返回格式无法解析")
        return parse_srt(resp.text)


# ==================== 本地 faster-whisper ====================

PROGRAM_FILE = "whisper-faster.exe"
PROGRAM_URL = ("https://www.modelscope.cn/models/bkfengg/whisper-cpp"
               "/resolve/master/whisper-faster.exe")
MODEL_REPO_TPL = "pengzhendong/faster-whisper-{key}"
MODELSCOPE_FILE_API = "https://www.modelscope.cn/api/v1/models/{repo}/repo/files"
MODELSCOPE_RESOLVE = "https://www.modelscope.cn/models/{repo}/resolve/master/{path}"
LOCAL_READY_MARK = ".ready.json"


def get_models_dir() -> Path:
    """模型根目录：程序目录/models，不可写时退到 AppData。"""
    candidates = [get_app_root() / "models"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "FengMingToolbox" / "models")
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            continue
    return candidates[-1]


def local_program_path() -> Path:
    return get_models_dir() / PROGRAM_FILE


def local_program_ready() -> bool:
    exe = local_program_path()
    return exe.exists() and exe.stat().st_size > 10 * 1024 * 1024


def local_model_dir(model_key: str) -> Path:
    return get_models_dir() / f"faster-whisper-{model_key}"


def local_model_ready(model_key: str) -> bool:
    folder = local_model_dir(model_key)
    return ((folder / LOCAL_READY_MARK).exists()
            and (folder / "model.bin").exists())


class LocalWhisperClient:
    """调用 whisper-faster.exe 输出 SRT。"""

    def __init__(self, model_key: str, language: str = "zh"):
        self.model_key = model_key
        self.language = language
        self._proc: Optional[subprocess.Popen] = None

    def kill(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def transcribe(self, audio_path: Path, progress: ProgressCb = None,
                   cancel: CancelCb = None) -> List[AsrSegment]:
        progress = progress or _noop_progress
        exe = local_program_path()
        if not exe.exists():
            raise AsrError("本地识别程序缺失，请到「语音设置」里下载")
        if not local_model_ready(self.model_key):
            raise AsrError(f"本地模型 {self.model_key} 未下载")

        line_width = "30" if self.language in ("zh", "ja", "ko") else "90"
        with tempfile.TemporaryDirectory(prefix="fmjj_whisper_") as tmp:
            cmd = [
                # whisper-faster 的 -m 是模型名（tiny/base/small...），
                # 实际目录由 --model_dir 拼成 {model_dir}/faster-whisper-{模型名}
                str(exe), "-m", self.model_key,
                "--model_dir", str(get_models_dir()),
                str(audio_path), "-d", "cpu",
                "--output_format", "srt", "-o", tmp,
                "--vad_filter", "true",
                "--sentence", "--max_line_width", line_width,
                "--max_line_count", "1", "--print_progress", "--beep_off",
            ]
            if self.language:
                cmd.extend(["-l", self.language])
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="ignore",
                creationflags=_no_window(),
            )
            output: queue.Queue = queue.Queue()

            def _reader():
                assert self._proc and self._proc.stdout
                for line in self._proc.stdout:
                    output.put(line)
                output.put(None)

            threading.Thread(target=_reader, daemon=True).start()

            lines: List[str] = []
            while True:
                if cancel and cancel():
                    self.kill()
                    raise AsrCancelled("已停止")
                try:
                    line = output.get(timeout=0.3)
                except queue.Empty:
                    continue
                if line is None:
                    break
                lines.append(line)
                match = re.search(r"(\d+(?:\.\d+)?)%", line)
                if match:
                    percent = min(95, int(5 + float(match.group(1)) * 0.85))
                    progress(percent, "本地识别中")

            rc = self._proc.wait() if self._proc else -1
            self._proc = None
            srt_path = Path(tmp) / f"{Path(audio_path).stem}.srt"
            if not srt_path.exists():
                tail = "".join(lines)[-400:]
                raise AsrError(f"本地识别失败（返回码 {rc}）: {tail}")
            progress(98, "解析结果")
            return parse_srt(srt_path.read_text(encoding="utf-8", errors="ignore"))


# ==================== 模型下载 ====================

def program_downloaded_size() -> int:
    exe = local_program_path()
    return exe.stat().st_size if exe.exists() else 0


def _repo_file_list(model_key: str) -> List[Dict]:
    repo = MODEL_REPO_TPL.format(key=model_key)
    resp = requests.get(MODELSCOPE_FILE_API.format(repo=repo),
                        params={"Revision": "master"}, timeout=60)
    resp.raise_for_status()
    files = (resp.json().get("Data") or {}).get("Files") or []
    return [f for f in files
            if f.get("Path") and not f["Path"].startswith(".")
            and f["Path"] not in ("README.md", "configuration.json")]


DOWNLOAD_THREADS = 4  # 大文件分段并行数（复用媒体下载器的 Range 分段实现）


def _download_stream(url: str, dst: Path, progress: Callable[[int, str], None],
                     cancel: CancelCb, label: str, base: int, span: int,
                     threads: int = DOWNLOAD_THREADS) -> None:
    """下载单个文件：大文件走多线程分段，其余单流兜底。"""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"),
    }
    if threads > 1:
        try:
            from media_downloader import (
                SEG_MIN_FILE_SIZE, _download_ranged, _probe_range,
            )
            total, range_ok, _content_type = _probe_range(url, headers)
            if range_ok and total and total >= SEG_MIN_FILE_SIZE:
                last = [0.0]

                def _on_bytes(done: int, total_bytes: int) -> None:
                    now = time.monotonic()
                    if now - last[0] < 0.2 and done < total_bytes:
                        return
                    last[0] = now
                    percent = base + int(span * done / max(1, total_bytes))
                    progress(min(99, percent),
                             f"{label} {done // 1048576}MB/{total_bytes // 1048576}MB")

                _path, ok = _download_ranged(
                    url, str(dst), headers, min(threads, 8), total,
                    progress_cb=_on_bytes, cancel_check=cancel)
                if ok:
                    return
                _check_cancel(cancel)
        except AsrCancelled:
            raise
        except Exception:
            pass  # 分段不可用/失败 → 单流兜底

    tmp = dst.with_suffix(dst.suffix + ".part")
    downloaded = 0
    with requests.get(url, stream=True, timeout=(15, 120), headers=headers) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if cancel and cancel():
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    raise AsrCancelled("已停止下载")
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = base + int(span * downloaded / total)
                    progress(min(99, percent),
                             f"{label} {downloaded // 1048576}MB/{total // 1048576}MB")
    tmp.replace(dst)


def download_program(progress: Callable[[int, str], None] = None,
                     cancel: CancelCb = None) -> Path:
    progress = progress or (lambda _p, _m: None)
    dst = local_program_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    progress(1, "下载本地识别程序（约 84MB）")
    _download_stream(PROGRAM_URL, dst, progress, cancel, "程序", 1, 99)
    progress(100, "程序下载完成")
    return dst


def download_model(model_key: str, progress: Callable[[int, str], None] = None,
                   cancel: CancelCb = None) -> Path:
    progress = progress or (lambda _p, _m: None)
    folder = local_model_dir(model_key)
    folder.mkdir(parents=True, exist_ok=True)
    progress(1, "读取模型文件列表")
    files = _repo_file_list(model_key)
    if not files:
        raise AsrError("模型文件列表为空，请检查网络")
    repo = MODEL_REPO_TPL.format(key=model_key)
    total_bytes = sum(int(f.get("Size") or 0) for f in files) or 1
    done_bytes = 0
    for item in files:
        _check_cancel(cancel)
        rel = item["Path"]
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        size = int(item.get("Size") or 0)
        base = int(99 * done_bytes / total_bytes)
        span = max(1, int(99 * size / total_bytes))
        _download_stream(MODELSCOPE_RESOLVE.format(repo=repo, path=rel),
                         target, progress, cancel, rel, base, span)
        done_bytes += size
    (folder / LOCAL_READY_MARK).write_text(
        json.dumps({"model": model_key, "files": [f["Path"] for f in files]},
                   ensure_ascii=False), encoding="utf-8")
    progress(100, "模型下载完成")
    return folder


# ==================== 分块 + 调度 ====================

def plan_chunks(duration: float, chunk_sec: int, overlap_sec: int
                ) -> List[Tuple[float, float]]:
    """返回 [(起始秒, 时长秒), ...]，块间保留 overlap 秒重叠。"""
    chunk_sec = max(30, int(chunk_sec))
    overlap_sec = max(0, min(int(overlap_sec), chunk_sec // 2))
    if duration <= chunk_sec + overlap_sec:
        return [(0.0, duration)]
    chunks: List[Tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + chunk_sec, duration)
        chunks.append((start, end - start))
        if end >= duration:
            break
        start += chunk_sec - overlap_sec
    return chunks


def _client_for(engine: str, local_model: str, whisper_api: Optional[Dict[str, str]],
                language: str):
    if engine == ENGINE_BIJIAN:
        return BcutClient()
    if engine == ENGINE_JIANYING:
        return JianyingClient()
    if engine == ENGINE_WHISPER_API:
        cfg = whisper_api or {}
        return WhisperApiClient(cfg.get("base_url", ""), cfg.get("api_key", ""),
                                cfg.get("model", "whisper-1"))
    if engine == ENGINE_LOCAL:
        return LocalWhisperClient(local_model, language)
    raise AsrError(f"未知识别引擎: {engine}")


def transcribe_audio(audio_path: Path, engine: str, *,
                     local_model: str = "base",
                     whisper_api: Optional[Dict[str, str]] = None,
                     language: str = "zh",
                     chunk_sec: int = DEFAULT_CHUNK_SEC,
                     overlap_sec: int = DEFAULT_OVERLAP_SEC,
                     concurrency: int = DEFAULT_CONCURRENCY,
                     progress: ProgressCb = None,
                     cancel: CancelCb = None) -> List[AsrSegment]:
    """识别整段音频，返回合并后的分段。"""
    progress = progress or _noop_progress
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise AsrError(f"音频不存在: {audio_path}")

    duration = media_duration(audio_path)
    chunks = plan_chunks(duration, chunk_sec, overlap_sec)
    workers = 1 if engine == ENGINE_LOCAL else max(1, min(int(concurrency), 5))

    progress(2, f"准备音频（{len(chunks)} 块 / {duration / 60:.1f} 分钟）")
    workdir = Path(tempfile.mkdtemp(prefix="fmjj_asr_"))
    try:
        mp3_files: List[Path] = []
        for idx, (start, length) in enumerate(chunks):
            _check_cancel(cancel)
            dst = workdir / f"chunk_{idx:03d}.mp3"
            extract_mp3_chunk(audio_path, start, length, dst)
            mp3_files.append(dst)
            progress(2 + int(6 * (idx + 1) / len(chunks)), "音频切块")

        results: List[Optional[List[AsrSegment]]] = [None] * len(chunks)
        errors: List[str] = []
        lock = threading.Lock()
        chunk_progress = [0] * len(chunks)

        def _run_chunk(idx: int) -> None:
            # 每块独立客户端：requests.Session / 本地子进程都不跨线程共享
            client = _client_for(engine, local_model, whisper_api, language)

            def _cb(percent: int, message: str) -> None:
                with lock:
                    chunk_progress[idx] = max(chunk_progress[idx], percent)
                    overall = 8 + int(88 * sum(chunk_progress) / (100 * len(chunks)))
                progress(min(97, overall),
                         f"识别 {idx + 1}/{len(chunks)}：{message}"
                         if len(chunks) > 1 else message)

            last_error: Optional[Exception] = None
            for attempt in range(2):
                _check_cancel(cancel)
                try:
                    results[idx] = client.transcribe(mp3_files[idx], _cb, cancel)
                    return
                except AsrCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt == 0:
                        time.sleep(2)
            with lock:
                errors.append(f"第 {idx + 1} 块: {last_error}")

        if workers == 1:
            for idx in range(len(chunks)):
                _check_cancel(cancel)
                _run_chunk(idx)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_run_chunk, i) for i in range(len(chunks))]
                for future in as_completed(futures):
                    future.result()

        if errors and not any(results):
            raise AsrError("；".join(errors))
        if errors:
            progress(97, f"部分块失败（{'；'.join(errors)[:120]}）")

        offsets = [int(start * 1000) for start, _ in chunks]
        segments = _merge_segments(results, offsets, int(overlap_sec * 1000))
        if not segments:
            raise AsrError("未识别到任何文字（音频可能是静音）")
        progress(100, f"识别完成，共 {len(segments)} 句")
        return segments
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _merge_segments(results: List[Optional[List[AsrSegment]]],
                    offsets: List[int], overlap_ms: int) -> List[AsrSegment]:
    merged: List[AsrSegment] = []
    for idx, part in enumerate(results):
        if not part:
            continue
        offset = offsets[idx]
        cut = offset + (overlap_ms // 2 if idx > 0 else 0)
        for seg in part:
            start = seg.start_ms + offset
            end = seg.end_ms + offset
            if start < cut:
                continue
            merged.append(AsrSegment(start, max(end, start + 200), seg.text.strip()))

    merged.sort(key=lambda s: s.start_ms)
    cleaned: List[AsrSegment] = []
    for seg in merged:
        if not seg.text:
            continue
        # 切块边界会把一句话截成短碎片：后一条完整覆盖且明显更长时，丢掉碎片
        while cleaned:
            prev = cleaned[-1]
            if (prev.end_ms > seg.start_ms and seg.end_ms >= prev.end_ms
                    and (prev.end_ms - prev.start_ms) * 2 < (seg.end_ms - seg.start_ms)):
                cleaned.pop()
                continue
            break
        if cleaned:
            prev = cleaned[-1]
            if prev.end_ms > seg.start_ms:
                prev.end_ms = max(prev.start_ms + 200, seg.start_ms)
            if (seg.text == prev.text and seg.start_ms - prev.end_ms < 800
                    and abs(seg.end_ms - seg.start_ms - (prev.end_ms - prev.start_ms)) < 500):
                continue
        cleaned.append(seg)
    return cleaned
