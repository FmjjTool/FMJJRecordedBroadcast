"""歌词转换引擎。

支持解析：LRC / KRC(酷狗，含解密) / KSC(酷我) / QRC(QQ音乐) / TRC·ELRC(增强LRC) / 纯文本
支持生成：纯文本 TXT / LRC / SRT / ASS / KSC / KRC(未加密)

统一中间结构：
    lines = [{"start": 秒(float), "end": 秒(float), "text": str,
              "words": [(start_ms:int, dur_ms:int, word:str), ...]}]
"""

import re
import zlib
from typing import Dict, List

# 酷狗 KRC 固定异或密钥（16 字节循环）
KRC_KEY = [64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105]

# 片头/片尾署名（转换输出时自动在开头和结尾各加一条；带时间轴格式用 [00:00:00] 时间戳）
CREDIT_LINE = "-歌词文件由封茗囧菌数据汇总库收集-"


def encode_krc(text: str) -> bytes:
    """明文歌词 → 酷狗 KRC：UTF-8 → zlib 压缩 → 循环 XOR → 加 4 字节头。"""
    compressed = zlib.compress(text.encode("utf-8"))
    out = bytearray(len(compressed))
    for i, b in enumerate(compressed):
        out[i] = b ^ KRC_KEY[i % 16]
    return b"krc1" + bytes(out)


def decode_krc(content: bytes) -> str:
    """酷狗 KRC 解密：跳过 4 字节头 → 循环 XOR → zlib 解压（失败则直接解码）。"""
    data = content[4:] if len(content) > 4 else content
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ KRC_KEY[i % 16]
    raw = bytes(out)
    try:
        return zlib.decompress(raw).decode("utf-8", errors="ignore")
    except Exception:
        try:
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return raw.decode("gbk", errors="ignore")


def _fmt_lrc_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"


def _fmt_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class LyricConverter:
    """歌词解析与多格式转换。"""

    # ================= 解析 =================

    def _parse_tagged(self, text: str, time_re, line_cb):
        """通用逐行解析框架。time_re 用于匹配行内时间戳，line_cb 处理单行。"""
        lines = []
        for raw in text.split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            # 跳过元数据标签 [ti:] [ar:] [al:] [by:] [offset:] [id:] [hash:] [sign:] [total:] 等
            if re.match(r"^\[(ti|ar|al|by|offset|id|hash|sign|total|re|ve|length|language):", raw):
                continue
            got = line_cb(raw, time_re)
            if got:
                if isinstance(got, list):
                    lines.extend(got)
                else:
                    lines.append(got)
        lines.sort(key=lambda x: x["start"])
        return lines

    def parse_lrc(self, content) -> List[Dict]:
        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
        time_re = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

        def cb(raw, tre):
            matches = list(tre.finditer(raw))
            if not matches:
                return None
            lyric = raw[matches[-1].end():].strip()
            if not lyric:
                return None
            out = []
            for m in matches:
                mm = int(m.group(1)); ss = int(m.group(2)); frac = m.group(3)
                if frac is None:
                    sec = mm * 60 + ss
                elif len(frac) == 3:
                    sec = mm * 60 + ss + int(frac) / 1000
                else:
                    sec = mm * 60 + ss + int(frac.ljust(3, "0")) / 1000
                out.append({"start": sec, "end": sec + 5.0, "text": lyric, "words": []})
            return out
        return self._parse_tagged(text, time_re, cb)

    def parse_krc(self, content) -> List[Dict]:
        text = decode_krc(content) if isinstance(content, bytes) else content
        # 行级时间戳 [start_ms,dur_ms] 或 [start_ms,dur_ms,flag]
        line_re = re.compile(r"^\[(\d+),(\d+)(?:,\d+)?\](.*)$")
        # 逐字 <start_ms,dur_ms,flag>
        word_re = re.compile(r"<(\d+),(\d+)(?:,\d+)?>")

        lines = []
        for raw in text.split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            if re.match(r"^\[(ti|ar|al|by|offset|id|hash|sign|total|re|ve|language):", raw):
                continue
            m = line_re.match(raw)
            if not m:
                continue
            start_ms = int(m.group(1))
            dur_ms = int(m.group(2))
            body = m.group(3).strip()
            if not body:
                continue
            # 逐字拆解：<start,dur,flag>字 <start,dur,flag>字
            words = []
            plain = word_re.sub("", body)
            for wm in word_re.finditer(body):
                pass
            # 重新逐字解析：用 finditer 分割
            pos = 0
            wtexts = []
            for wm in word_re.finditer(body):
                seg = body[pos:wm.start()]
                if seg:
                    wtexts.append(seg)
                pos = wm.end()
                # 该时间戳对应的字 = 下一个非时间戳片段
            tail = body[pos:]
            if tail:
                wtexts.append(tail)
            # 简化：提取纯文本（去掉所有 <...>）
            plain_text = word_re.sub("", body).strip()
            lines.append({
                "start": start_ms / 1000.0,
                "end": (start_ms + dur_ms) / 1000.0,
                "text": plain_text,
                "words": [(int(wm.group(1)), int(wm.group(2)), "")
                          for wm in word_re.finditer(body)],
            })
        lines.sort(key=lambda x: x["start"])
        return lines

    def parse_ksc(self, content) -> List[Dict]:
        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
        # 行格式 [mm:ss.xx]<word_start,dur,flag>word<...>word
        line_re = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\](.*)$")
        word_re = re.compile(r"<(\d+),(\d+)(?:,\d+)?>")

        lines = []
        for raw in text.split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            if raw.startswith("karaoke.") or re.match(r"^\[(ti|ar|al|by|offset|id|hash|sign|total):", raw):
                continue
            m = line_re.match(raw)
            if not m:
                continue
            mm = int(m.group(1)); ss = int(m.group(2)); frac = m.group(3)
            if frac is None:
                sec = mm * 60 + ss
            elif len(frac) == 3:
                sec = mm * 60 + ss + int(frac) / 1000
            else:
                sec = mm * 60 + ss + int(frac.ljust(3, "0")) / 1000
            body = m.group(4)
            plain = word_re.sub("", body).strip()
            if not plain:
                continue
            words = [(int(wm.group(1)), int(wm.group(2)), "") for wm in word_re.finditer(body)]
            end = sec
            if words:
                end = sec + (words[-1][0] + words[-1][1]) / 1000.0
            else:
                end = sec + 5.0
            lines.append({"start": sec, "end": end, "text": plain, "words": words})
        lines.sort(key=lambda x: x["start"])
        return lines

    def parse_qrc(self, content) -> List[Dict]:
        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
        # 行级 [start_ms,dur_ms]  + 逐字 (start_ms,dur_ms)
        line_re = re.compile(r"^\[(\d+),(\d+)\](.*)$")
        word_re = re.compile(r"\((\d+),(\d+)\)")

        lines = []
        for raw in text.split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            if re.match(r"^\[(ti|ar|al|by|offset|id|hash|sign|total):", raw):
                continue
            m = line_re.match(raw)
            if not m:
                continue
            start_ms = int(m.group(1)); dur_ms = int(m.group(2))
            body = m.group(3)
            plain = word_re.sub("", body).strip()
            if not plain:
                continue
            words = [(int(wm.group(1)), int(wm.group(2)), "") for wm in word_re.finditer(body)]
            lines.append({
                "start": start_ms / 1000.0,
                "end": (start_ms + dur_ms) / 1000.0,
                "text": plain,
                "words": words,
            })
        lines.sort(key=lambda x: x["start"])
        return lines

    def parse_trc(self, content) -> List[Dict]:
        """TRC / ELRC：每字前带 <mm:ss.xx> 时间标记。"""
        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
        line_re = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\](.*)$")
        word_re = re.compile(r"<(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?>")

        lines = []
        for raw in text.split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            if re.match(r"^\[(ti|ar|al|by|offset|id|hash|sign|total):", raw):
                continue
            m = line_re.match(raw)
            if not m:
                continue
            mm = int(m.group(1)); ss = int(m.group(2)); frac = m.group(3)
            if frac is None:
                sec = mm * 60 + ss
            elif len(frac) == 3:
                sec = mm * 60 + ss + int(frac) / 1000
            else:
                sec = mm * 60 + ss + int(frac.ljust(3, "0")) / 1000
            body = m.group(4)
            plain = word_re.sub("", body).strip()
            if not plain:
                continue
            # 逐字时间（秒）
            words = []
            for wm in word_re.finditer(body):
                wmm = int(wm.group(1)); wss = int(wm.group(2)); wfrac = wm.group(3)
                wsec = wmm * 60 + wss + (int(wfrac) / 1000 if wfrac and len(wfrac) == 3 else int((wfrac or "0").ljust(3, "0")) / 1000)
                words.append((int(wsec * 1000), 0, ""))
            lines.append({"start": sec, "end": sec + 5.0, "text": plain, "words": words})
        lines.sort(key=lambda x: x["start"])
        return lines

    def parse_plain(self, content) -> List[Dict]:
        """纯文本：按行，每行当作一句，无时间轴。"""
        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
        lines = []
        for raw in text.split("\n"):
            raw = raw.strip()
            if raw:
                lines.append({"start": 0.0, "end": 0.0, "text": raw, "words": []})
        return lines

    def parse(self, filename: str, content) -> List[Dict]:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext == "krc":
            return self.parse_krc(content)
        if ext == "ksc":
            return self.parse_ksc(content)
        if ext == "qrc":
            return self.parse_qrc(content)
        if ext in ("trc", "elrc"):
            return self.parse_trc(content)
        if ext == "lrc":
            return self.parse_lrc(content)
        # 默认先按 LRC 尝试，再按纯文本
        try:
            r = self.parse_lrc(content)
            if r:
                return r
        except Exception:
            pass
        return self.parse_plain(content)

    # ================= 生成 =================

    def krc_to_krcd(self, content) -> str:
        """KRC（酷狗加密）→ KRCD（明文）。"""
        return decode_krc(content) if isinstance(content, bytes) else content

    def krcd_to_krc(self, text) -> bytes:
        """KRCD（明文）→ KRC（酷狗加密）。"""
        return encode_krc(text)

    def to_text(self, lines: List[Dict]) -> bytes:
        body = "\n".join([ln["text"] for ln in lines if ln.get("text")])
        return f"{CREDIT_LINE}\n{body}\n{CREDIT_LINE}".encode("utf-8")

    def to_lrc(self, lines: List[Dict]) -> bytes:
        out = [f"[00:00:00]{CREDIT_LINE}"]
        for ln in lines:
            if ln.get("text"):
                out.append(f"[{_fmt_lrc_time(ln['start'])}]{ln['text']}")
        out.append(f"[00:00:00]{CREDIT_LINE}")
        return "\n".join(out).encode("utf-8")

    def to_srt(self, lines: List[Dict]) -> bytes:
        body = [x for x in lines if x.get("text")]
        out = ["1", f"{_fmt_srt_time(0.0)} --> {_fmt_srt_time(0.0)}", CREDIT_LINE, ""]
        for i, ln in enumerate(body, 2):
            start = ln["start"]
            end = ln.get("end") or (start + 5.0)
            out.append(str(i))
            out.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
            out.append(ln["text"])
            out.append("")
        out.append(str(len(body) + 2))
        out.append(f"{_fmt_srt_time(0.0)} --> {_fmt_srt_time(0.0)}")
        out.append(CREDIT_LINE)
        out.append("")
        return "\n".join(out).encode("utf-8")

    def to_ass(self, lines: List[Dict]) -> bytes:
        header = """[Script Info]
Title: Converted Lyric
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,0
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines_out = [header]
        lines_out.append(f"Dialogue: 0,{_fmt_lrc_time(0.0)},{_fmt_lrc_time(0.0)},Default,,0,0,0,,{CREDIT_LINE}")
        for ln in lines:
            if not ln.get("text"):
                continue
            start = ln["start"]; end = ln.get("end") or (start + 5.0)
            t = ln["text"].replace("\n", " ")
            lines_out.append(f"Dialogue: 0,{_fmt_lrc_time(start)},{_fmt_lrc_time(end)},Default,,0,0,0,,{t}")
        lines_out.append(f"Dialogue: 0,{_fmt_lrc_time(0.0)},{_fmt_lrc_time(0.0)},Default,,0,0,0,,{CREDIT_LINE}")
        return "\n".join(lines_out).encode("utf-8")

    def to_ksc(self, lines: List[Dict]) -> bytes:
        """生成酷我 KSC（逐字）。无逐字信息时整句当一字。开头结尾加署名。"""
        out = [f"[00:00:00]{CREDIT_LINE}"]
        for ln in lines:
            if not ln.get("text"):
                continue
            words = ln.get("words")
            if words:
                # 逐字 <dur,start,flag>
                parts = []
                for ws, wd, _ in words:
                    parts.append(f"{ln['text'][0] if ln['text'] else ''}")
                # 简化：用 words 数量切分 text
                segs = self._split_text(ln["text"], len(words))
                body = ""
                for (ws, wd, _), seg in zip(words, segs):
                    body += f"{seg}<{wd},{ws},0>"
            else:
                body = f"{ln['text']}<{(int((ln.get('end', ln['start'] + 5) - ln['start']) * 1000))},0,0>"
            out.append(f"[{_fmt_lrc_time(ln['start'])}]{body}")
        out.append(f"[00:00:00]{CREDIT_LINE}")
        return "\n".join(out).encode("utf-8")

    def _split_text(self, text: str, n: int) -> List[str]:
        if n <= 0:
            return [text]
        if n >= len(text):
            return list(text) + [""] * (n - len(text))
        per = len(text) / n
        segs = []
        for i in range(n):
            segs.append(text[int(i * per):int((i + 1) * per)])
        return segs

    def convert(self, filename: str, content, output_format: str):
        """转换单个文件，返回 {"success", "message", "output_files": [{"format","filename","content"}]}"""
        try:
            lines = self.parse(filename, content)
            if not lines:
                return {"success": False, "message": "未解析到歌词内容", "output_files": []}
            base = filename.rsplit(".", 1)[0] if "." in filename else filename
            gen = {
                "txt": ("_歌词.txt", self.to_text),
                "lrc": (".lrc", self.to_lrc),
                "srt": (".srt", self.to_srt),
                "ass": (".ass", self.to_ass),
                "ksc": (".ksc", self.to_ksc),
            }
            if output_format not in gen:
                return {"success": False, "message": f"不支持的输出格式: {output_format}", "output_files": []}
            suffix, fn = gen[output_format]
            if output_format == "txt":
                out_name = f"{base}{suffix}"
            else:
                out_name = f"{base}{suffix}"
            return {"success": True, "message": "ok", "output_files": [{
                "format": output_format, "filename": out_name, "content": fn(lines)}]}
        except Exception as e:
            return {"success": False, "message": f"转换失败: {e}", "output_files": []}
