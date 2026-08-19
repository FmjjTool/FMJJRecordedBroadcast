"""
音乐解密：Unlock Music 全格式算法移植（Python 版）。

支持格式（按平台）：
- ncm             网易云音乐（RC4 + AES-128-ECB）
- qmc0/qmc3/qmcflac/qmcogg   QQ音乐 v1（静态密码箱 XOR）
- mflac/mflac0/mgg/mgg0/mgg1 QQ音乐 v2（MapCipher / RC4Cipher）
- kgm/kgma/vpr    酷狗音乐（动态 mask）
- kwm             酷我音乐（XOR + 固定密钥）
- xm              虾米音乐（减法 + XOR）
- tm0/tm2/tm3/tm6 喜马拉雅（头部修复）
- x2m/x3m         喜马拉雅 xmly（索引重排 + XOR）

算法与密钥表来源：um-web.extension（Unlock Music 浏览器扩展）+ unlock-music 开源项目。
"""

import base64
import json
import math
import re
from pathlib import Path

from Crypto.Cipher import AES

# ==================== 密钥表加载 ====================
_KEYS = None


def _load_keys() -> dict:
    global _KEYS
    if _KEYS is None:
        p = Path(__file__).parent / "toolbox" / "_unlock_keys.json"
        with open(p, "r", encoding="utf-8") as f:
            _KEYS = json.load(f)
    return _KEYS


def _k(name) -> list:
    return _load_keys()[name]


# ==================== NCM 固定密钥 ====================
CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")  # "hzHRAmso5kInbaxW"
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")  # "#14ljk_!\]&0U<'("
NCM_HEADER = b"CTENFDAM"


def _aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.decrypt(data)


def _aes_ecb_decrypt_pkcs7(key: bytes, data: bytes) -> bytes:
    raw = _aes_ecb_decrypt(key, data)
    if not raw:
        return raw
    pad = raw[-1]
    if 0 < pad <= 16:
        return raw[:-pad]
    return raw


# ==================== TEA ====================
_TEA_DELTA = 0x9E3779B9


def tea_decrypt_block(block: bytes, key: bytes, rounds: int = 32) -> bytes:
    """标准 TEA 解密一个 8 字节块（16 字节 key）。"""
    v0 = int.from_bytes(block[:4], "big")
    v1 = int.from_bytes(block[4:8], "big")
    k0 = int.from_bytes(key[:4], "big")
    k1 = int.from_bytes(key[4:8], "big")
    k2 = int.from_bytes(key[8:12], "big")
    k3 = int.from_bytes(key[12:16], "big")
    summ = (_TEA_DELTA * (rounds // 2)) & 0xFFFFFFFF
    for _ in range(rounds // 2):
        v1 = (v1 - (((v0 << 4) + k2) ^ (v0 + summ) ^ ((v0 >> 5) + k3))) & 0xFFFFFFFF
        v0 = (v0 - (((v1 << 4) + k0) ^ (v1 + summ) ^ ((v1 >> 5) + k1))) & 0xFFFFFFFF
        summ = (summ - _TEA_DELTA) & 0xFFFFFFFF
    return v0.to_bytes(4, "big") + v1.to_bytes(4, "big")


def tea_decrypt_cbc(data: bytes, key: bytes, rounds: int = 32) -> bytes:
    """TarsCpp TCTEA（CBC 模式，带 PadLen/Salt/Zero）解密，返回明文 body。"""
    if len(data) < 16 or len(data) % 8 != 0:
        raise ValueError("invalid tea cipher data length")

    dest = bytearray(tea_decrypt_block(data[:8], key, rounds))
    pad_len = dest[0] & 0x7
    out_len = len(data) - 1 - pad_len - 2 - 7  # 去掉 PadLen(1)+Padding+Salt(2)+Zero(7)
    if out_len < 0:
        raise ValueError("estimated plaindata length is less than 0")
    out = bytearray(out_len)

    iv_prev = bytearray(8)
    iv_cur = bytearray(data[:8])
    data_pos = 8
    dest_idx = 1 + pad_len

    def crypt_block():
        nonlocal data_pos, dest_idx
        iv_prev[:] = iv_cur[:]
        iv_cur[:] = data[data_pos:data_pos + 8]
        xored = bytes(dest[:8][j] ^ iv_cur[j] for j in range(8))
        dest[:] = tea_decrypt_block(xored, key, rounds)
        data_pos += 8
        dest_idx = 0

    # 跳过 salt（2 字节）
    i = 1
    while i <= 2:
        if dest_idx < 8:
            dest_idx += 1
            i += 1
        else:
            crypt_block()

    # 还原明文
    out_pos = 0
    while out_pos < out_len:
        if dest_idx < 8:
            out[out_pos] = dest[dest_idx] ^ iv_prev[dest_idx]
            out_pos += 1
            dest_idx += 1
        else:
            crypt_block()

    return bytes(out)


# ==================== 通用工具 ====================
def _detect_format(audio: bytes) -> str:
    """从音频数据魔数判断格式，兜底返回 mp3。"""
    if audio[:4] == b"fLaC":
        return "flac"
    if audio[:4] == b"OggS":
        return "ogg"
    if audio[:4] == b"\x00\x00\x00\x20ftyp" or audio[4:8] == b"ftyp":
        return "m4a"
    if audio[:4] == b"RIFF" or audio[:4] == b"ID3":
        return "mp3"
    if len(audio) > 1 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0:
        return "mp3"
    if len(audio) > 4 and audio[4:8] == b"ftyp":
        return "m4a"
    return "mp3"


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", str(name)).strip() or "未命名"


# ==================== NCM（网易云）====================
def _ncm_rc4_keystream(key: bytes) -> bytes:
    """NCM 的 RC4（meta_keystream 变体），返回 256 字节密钥流。"""
    key_len = len(key)
    S = bytearray(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % key_len]) & 0xFF
        S[i], S[j] = S[j], S[i]
    ks = bytearray(256)
    for i, idx in enumerate(range(256), start=1):
        i %= 256
        si = S[i]
        sj = S[(i + si) & 0xFF]
        ks[idx] = S[(si + sj) & 0xFF]
    return bytes(ks)


def decrypt_ncm_file(path) -> tuple:
    """解密 ncm 文件，返回 (音频字节, meta dict)。"""
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 16 or data[:8] != NCM_HEADER:
        raise ValueError(f"不是有效的 ncm 文件: {path.name}")

    pos = 10
    # keyData
    key_len = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    key_data = bytes(b ^ 0x64 for b in data[pos:pos + key_len])
    pos += key_len
    key_box = _ncm_rc4_keystream(_aes_ecb_decrypt_pkcs7(CORE_KEY, key_data)[17:])

    # metaData
    meta_len = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    meta = {}
    if meta_len > 0:
        meta_enc = bytes(b ^ 0x63 for b in data[pos:pos + meta_len])
        pos += meta_len
        try:
            raw = _aes_ecb_decrypt_pkcs7(META_KEY, base64.b64decode(meta_enc[22:]))
            text = raw.decode("utf-8", errors="ignore")
            idx = text.find(":")
            if idx >= 0 and text[:idx] == "dj":
                meta = json.loads(text[idx + 1:]).get("mainMusic", {})
            elif idx >= 0:
                meta = json.loads(text[idx + 1:])
        except Exception:
            meta = {}
    else:
        pos += meta_len

    # CRC(4) + 0x00(1) + 图片大小(4) + 图片数据
    pos += 5
    img_size = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4 + img_size

    # RC4 解密音频
    audio = bytes(b ^ key_box[i & 0xFF] for i, b in enumerate(data[pos:]))
    return audio, meta


def ncm_meta_name(meta: dict, default: str = "") -> str:
    title = meta.get("musicName") or meta.get("title") or ""
    artists = meta.get("artist") or []
    artist = ""
    if artists and isinstance(artists[0], (list, tuple)):
        artist = ", ".join(str(a[0]) for a in artists if a)
    elif artists and isinstance(artists, str):
        artist = artists
    title = str(title).strip()
    artist = str(artist).strip()
    if title and artist:
        return f"{title} - {artist}"
    return title or default


# ==================== QMC（QQ音乐）====================
def _qmc_simple_make_key(salt: int, length: int) -> bytes:
    return bytes(0xFF & int(abs(math.tan(salt + i * 0.1)) * 100.0) for i in range(length))


def _qmc_decrypt_v2_key(key: bytes) -> bytes:
    """解密 EncV2 的主密钥（两次 TEA + base64）。"""
    if len(key) < 18 or key[:18] != b"QQMusic EncV2,Key:":
        return key
    out = tea_decrypt_cbc(key[18:], bytes(_k("QMC_MIX_KEY1")))
    out = tea_decrypt_cbc(out, bytes(_k("QMC_MIX_KEY2")))
    return base64.b64decode(out)


def _qmc_derive_key(raw_key: bytes) -> bytes:
    """QMC 主密钥推导（对应 QmcDeriveKey）。"""
    raw_dec = base64.b64decode(raw_key)
    if len(raw_dec) < 16:
        raise ValueError("key length is too short")
    raw_dec = _qmc_decrypt_v2_key(raw_dec)

    simple_key = _qmc_simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i << 1] = simple_key[i]
        tea_key[(i << 1) + 1] = raw_dec[i]
    sub = tea_decrypt_cbc(bytes(raw_dec[8:]), bytes(tea_key))
    return bytes(raw_dec[:8]) + sub


class _QmcStaticCipher:
    """QMC v1 静态密码箱。"""
    _box = None

    @classmethod
    def box(cls):
        if cls._box is None:
            cls._box = _k("QMC_STATIC_CIPHER_BOX")
        return cls._box

    def mask(self, offset: int) -> int:
        if offset > 0x7FFF:
            offset %= 0x7FFF
        return self.box()[(offset * offset + 27) & 0xFF]


class _QmcMapCipher:
    """QMC Map 密码（key <= 300 字节）。"""
    def __init__(self, key: bytes):
        self.key = key
        self.n = len(key)

    def mask(self, offset: int) -> int:
        if offset > 0x7FFF:
            offset %= 0x7FFF
        idx = (offset * offset + 71214) % self.n
        rotate = ((idx & 0x7) + 4) % 8
        value = self.key[idx]
        left = value << rotate
        right = value >> rotate
        return (left | right) & 0xFF


class _QmcRC4Cipher:
    """QMC RC4 密码（key > 300 字节），分段 5120 字节。"""
    FIRST_SEGMENT_SIZE = 0x80
    SEGMENT_SIZE = 5120

    def __init__(self, key: bytes):
        self.key = key
        self.n = len(key)
        self.S = bytearray(i & 0xFF for i in range(self.n))
        j = 0
        for i in range(self.n):
            j = (self.S[i] + j + self.key[i % self.n]) % self.n
            self.S[i], self.S[j] = self.S[j], self.S[i]
        self.hash = 1
        for i in range(self.n):
            value = self.key[i]
            if not value:
                continue
            next_hash = (self.hash * value) & 0xFFFFFFFF
            if next_hash == 0 or next_hash <= self.hash:
                break
            self.hash = next_hash

    def _segment_key(self, idx: int) -> int:
        seed = self.key[idx % self.n]
        if seed == 0:
            return 0
        return int((self.hash / ((idx + 1) * seed)) * 100.0) % self.n

    def _enc_first_segment(self, buf: bytearray, offset: int, start: int, end: int):
        for i in range(start, end):
            buf[i] ^= self.key[self._segment_key(offset + (i - start))]

    def _enc_a_segment(self, buf: bytearray, offset: int, start: int, end: int):
        S = self.S[:]
        skip_len = (offset % self.SEGMENT_SIZE) + self._segment_key(offset // self.SEGMENT_SIZE)
        j = 0
        k = 0
        for i in range(-skip_len, end - start):
            j = (j + 1) % self.n
            k = (S[j] + k) % self.n
            S[j], S[k] = S[k], S[j]
            if i >= 0:
                buf[start + i] ^= S[(S[j] + S[k]) % self.n]

    def decrypt(self, buf: bytearray, offset: int = 0):
        to_process = len(buf)
        processed = 0

        if offset < self.FIRST_SEGMENT_SIZE:
            ln = min(len(buf), self.FIRST_SEGMENT_SIZE - offset)
            self._enc_first_segment(buf, offset, processed, processed + ln)
            processed += ln
            offset += ln
            to_process -= ln
            if to_process == 0:
                return

        if offset % self.SEGMENT_SIZE != 0:
            ln = min(self.SEGMENT_SIZE - (offset % self.SEGMENT_SIZE), to_process)
            self._enc_a_segment(buf, offset, processed, processed + ln)
            processed += ln
            offset += ln
            to_process -= ln

        while to_process > self.SEGMENT_SIZE:
            self._enc_a_segment(buf, offset, processed, processed + self.SEGMENT_SIZE)
            processed += self.SEGMENT_SIZE
            offset += self.SEGMENT_SIZE
            to_process -= self.SEGMENT_SIZE

        if to_process > 0:
            self._enc_a_segment(buf, offset, processed, processed + to_process)


_QMC_EXT_MAP = {
    "mgg": "ogg", "mgg0": "ogg", "mgg1": "ogg", "mggl": "ogg",
    "mflac": "flac", "mflac0": "flac", "mmp4": "m4a",
    "qmcflac": "flac", "qmcogg": "ogg",
    "qmc0": "mp3", "qmc2": "ogg", "qmc3": "mp3", "qmc4": "ogg",
    "qmc6": "ogg", "qmc8": "ogg",
    "tkm": "m4a",
    "bkcmp3": "mp3", "bkcm4a": "m4a", "bkcflac": "flac", "bkcwav": "wav",
}


def decrypt_qmc_file(path) -> tuple:
    """解密 qmc/mflac/mgg 文件，返回 (音频字节, meta dict, 扩展名)。"""
    path = Path(path)
    data = path.read_bytes()
    size = len(data)
    ext = path.suffix.lstrip(".").lower()
    out_ext = _QMC_EXT_MAP.get(ext, "mp3")

    last4 = data[-4:]
    audio_size = size
    cipher = None

    if last4 == b"STag":
        raise ValueError("文件中没有写入密钥，无法解锁（请降级 App 后重新下载）")
    elif last4 == b"QTag":
        key_size = int.from_bytes(data[-8:-4], "big")
        audio_size = size - key_size - 8
        raw_key = data[audio_size:size - 8]
        comma = raw_key.find(b",")
        if comma < 0:
            raise ValueError("invalid key")
        key_dec = _qmc_derive_key(raw_key[:comma])
    else:
        key_size = int.from_bytes(last4, "little")
        if key_size < 0x400:
            audio_size = size - key_size - 4
            raw_key = data[audio_size:size - 4]
            key_dec = _qmc_derive_key(raw_key)
        else:
            key_dec = b""

    if key_dec:
        if len(key_dec) > 300:
            cipher = _QmcRC4Cipher(key_dec)
        else:
            cipher = _QmcMapCipher(key_dec)
    else:
        cipher = _QmcStaticCipher()

    audio = bytearray(data[:audio_size])
    if isinstance(cipher, _QmcStaticCipher):
        for i in range(len(audio)):
            audio[i] ^= cipher.mask(i)
    elif isinstance(cipher, _QmcMapCipher):
        for i in range(len(audio)):
            audio[i] ^= cipher.mask(i)
    else:
        cipher.decrypt(audio)

    return bytes(audio), {}, out_ext


# ==================== KGM / VPR（酷狗）====================
def _kgm_get_mask(pos: int) -> int:
    table1 = _k("KGM_TABLE1")
    table2 = _k("KGM_TABLE2")
    predef = _k("KGM_MASK_V2_PREDEF")
    offset = pos >> 4
    value = 0
    while offset >= 0x11:
        value ^= table1[offset % 272]
        offset >>= 4
        value ^= table2[offset % 272]
        offset >>= 4
    return predef[pos % 272] ^ value


def decrypt_kgm_file(path) -> tuple:
    """解密 kgm/kgma/vpr 文件，返回 (音频字节, meta, 扩展名)。"""
    path = Path(path)
    data = path.read_bytes()
    ext = path.suffix.lstrip(".").lower()
    is_vpr = ext == "vpr"

    header = bytes(_k("KGM_VPR_HEADER")) if is_vpr else bytes(_k("KGM_KGM_HEADER"))
    if not data[:16] == header:
        raise ValueError(f"不是有效的 {ext} 文件: {path.name}")

    header_len = int.from_bytes(data[0x10:0x14], "little")
    key = bytearray(data[0x1C:0x2C])
    key.append(0)

    body = bytearray(data[header_len:])
    vpr_diff = _k("KGM_VPR_MASK_DIFF")
    for i in range(len(body)):
        idx = i
        med8 = key[idx % 17] ^ body[i]
        med8 ^= (med8 & 0xF) << 4
        msk8 = _kgm_get_mask(idx)
        msk8 ^= (msk8 & 0xF) << 4
        body[i] = med8 ^ msk8
        if is_vpr:
            body[i] ^= vpr_diff[idx % 17]

    return bytes(body), {}, ""


# ==================== KWM（酷我）====================
_KWM_HEADER1 = b"yeelion-kuwo-tme"
_KWM_HEADER2 = b"yeelion-kuwo\x00\x00\x00\x00"
_KWM_KEY = "MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"


def decrypt_kwm_file(path) -> tuple:
    """解密 kwm 文件。"""
    path = Path(path)
    data = path.read_bytes()
    if not (data[:16] == _KWM_HEADER1 or data[:16] == _KWM_HEADER2):
        raise ValueError(f"不是有效的 kwm 文件: {path.name}")

    file_key = data[0x18:0x20]
    key_str = str(int.from_bytes(file_key, "little"))
    if len(key_str) > 32:
        key_str = key_str[:32]
    elif len(key_str) < 32:
        # JS padEnd(32, keyRaw)：key_str 重复填充到 32 字符
        key_str = (key_str * (32 // len(key_str) + 1))[:32]
    mask = bytes(ord(_KWM_KEY[i]) ^ ord(key_str[i]) for i in range(32))

    audio = bytearray(data[0x400:])
    for i in range(len(audio)):
        audio[i] ^= mask[i % 0x20]
    return bytes(audio), {}, ""


# ==================== XM（虾米）====================
_XM_MAGIC = b"ifmt"
_XM_MAGIC2 = b"\xfe\xfe\xfe\xfe"
_XM_TYPE_MAP = {" WAV": "wav", "FLAC": "flac", " MP3": "mp3", " A4M": "m4a"}


def decrypt_xm_file(path) -> tuple:
    """解密 xm 文件。"""
    path = Path(path)
    data = path.read_bytes()
    if data[:4] != _XM_MAGIC or data[8:12] != _XM_MAGIC2:
        raise ValueError(f"不是有效的 xm 文件: {path.name}")

    type_text = data[4:8].decode("latin-1")
    if type_text not in _XM_TYPE_MAP:
        raise ValueError(f"未知的 xm 文件类型: {type_text}")

    key = data[0xF]
    data_offset = data[0xC] | (data[0xD] << 8) | (data[0xE] << 16)
    audio = bytearray(data[0x10:])
    for cur in range(data_offset, len(audio)):
        audio[cur] = ((audio[cur] - key) & 0xFF) ^ 0xFF
    return bytes(audio), {}, _XM_TYPE_MAP[type_text]


# ==================== TM（喜马拉雅）====================
_TM_HEADER = b"\x00\x00\x00\x20ftyp"


def decrypt_tm_file(path) -> tuple:
    """解密 tm0/tm2/tm3/tm6 文件（头部修复为 ftyp）。"""
    path = Path(path)
    data = bytearray(path.read_bytes())
    for cur in range(8):
        data[cur] = _TM_HEADER[cur]
    return bytes(data), {}, "m4a"


# ==================== x2m / x3m（喜马拉雅 xmly）====================
def decrypt_xmly_file(path) -> tuple:
    """解密 x2m/x3m 文件（索引重排 + XOR）。"""
    path = Path(path)
    data = bytearray(path.read_bytes())
    ext = path.suffix.lstrip(".").lower()
    if ext == "x2m":
        table = _k("XMLY_X2M_AE")
        xor_key = b"xmly"
    else:
        table = _k("XMLY_X3M_OE")
        xor_key = b"3989d111aad5613940f4fc44b639b292"

    n = min(1024, len(data))
    orig = data[:n]
    for i in range(n):
        data[i] = orig[table[i]] ^ xor_key[i % len(xor_key)]
    return bytes(data), {}, "m4a"


# ==================== 统一入口 ====================
# 扩展名 → 解密函数
_EXT_HANDLERS = {
    "ncm": decrypt_ncm_file,
    "kwm": decrypt_kwm_file,
    "xm": decrypt_xm_file,
    "vpr": decrypt_kgm_file,
    "kgm": decrypt_kgm_file,
    "kgma": decrypt_kgm_file,
    "x2m": decrypt_xmly_file,
    "x3m": decrypt_xmly_file,
}
_QMC_EXTS = {
    "qmc0", "qmc2", "qmc3", "qmc4", "qmc6", "qmc8",
    "qmcflac", "qmcogg", "mflac", "mflac0", "mgg", "mgg0", "mgg1",
    "mggl", "mmp4", "tkm", "bkcmp3", "bkcm4a", "bkcflac", "bkcwav",
}
_TM_EXTS = {"tm0", "tm2", "tm3", "tm6"}


def detect_handler(ext: str):
    ext = ext.lower().lstrip(".")
    if ext in _EXT_HANDLERS:
        return _EXT_HANDLERS[ext]
    if ext in _QMC_EXTS:
        return decrypt_qmc_file
    if ext in _TM_EXTS:
        return decrypt_tm_file
    return None


def decrypt_file(path) -> tuple:
    """根据扩展名自动识别并解密，返回 (音频字节, meta dict, 输出扩展名)。"""
    path = Path(path)
    ext = path.suffix.lstrip(".").lower()
    handler = detect_handler(ext)
    if handler is None:
        raise ValueError(f"不支持的格式: .{ext}")

    result = handler(path)
    if len(result) == 2:
        audio, meta = result
        out_ext = ""
    else:
        audio, meta, out_ext = result
    if not out_ext:
        out_ext = _detect_format(audio)
    return audio, meta, out_ext


def decrypt_to(path, out_dir: str) -> str:
    """解密文件并保存到 out_dir，返回输出路径。"""
    path = Path(path)
    audio, meta, out_ext = decrypt_file(path)

    name = ncm_meta_name(meta, path.stem) if meta else path.stem
    name = _safe_name(name)
    out_path = Path(out_dir) / f"{name}.{out_ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 避免重名覆盖
    if out_path.exists():
        out_path = Path(out_dir) / f"{name}_{path.stem}.{out_ext}"
    out_path.write_bytes(audio)
    return str(out_path)
