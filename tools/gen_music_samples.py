"""
生成解密测试样本（加密方向），供 Java 版跨语言验证。

产出（src/test/resources/music/）：
  sample.ncm / sample.ncm.expected   网易云 NCM
  sample.kgm / sample.kgm.expected   酷狗 KGM
  sample.kwm / sample.kwm.expected   酷我 KWM
  qmc_vectors.txt                    QMC 静态表 / Map 密码的 mask 向量
"""
import base64
import json
import os
import struct
import sys

from Crypto.Cipher import AES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "test", "resources", "music")
KEYS = json.load(open(os.path.join(ROOT, "src", "main", "resources", "cn", "fmjj", "toolbox",
                                   "engine", "music", "unlock_keys.json"), encoding="utf-8"))

CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")


def aes_ecb_enc(key, data):
    pad = 16 - len(data) % 16
    return AES.new(key, AES.MODE_ECB).encrypt(data + bytes([pad]) * pad)


def rc4_keystream(key):
    n = len(key)
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % n]) & 0xFF
        s[i], s[j] = s[j], s[i]
    ks = bytearray(256)
    for idx in range(256):
        i = (idx + 1) % 256
        si = s[i]
        sj = s[(i + si) & 0xFF]
        ks[idx] = s[(si + sj) & 0xFF]
    return bytes(ks)


def write(name, data):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "wb") as f:
        f.write(data)


def gen_ncm():
    plain = b"ID3\x04\x00" + bytes(range(256)) * 2
    rc4key = bytes(range(16))
    key_plain = b"neteasecloudmusic" + rc4key          # 17 字节前缀 + 16 字节 RC4 key
    key_data = bytes(b ^ 0x64 for b in aes_ecb_enc(CORE_KEY, key_plain))
    kb = rc4_keystream(rc4key)

    meta = json.dumps({"musicName": "测试歌曲", "artist": [["测试歌手", 1]], "album": "专辑"},
                      ensure_ascii=False)
    # 内层明文格式：music163:{json}（Python 解密按首个冒号切分）
    meta_plain = b"music163:" + meta.encode("utf-8")
    blob = b"163 key(Don't modify):" + base64.b64encode(aes_ecb_enc(META_KEY, meta_plain))
    assert len(b"163 key(Don't modify):") == 22
    meta_enc = bytes(b ^ 0x63 for b in blob)

    audio_enc = bytes(b ^ kb[i & 0xFF] for i, b in enumerate(plain))
    data = (b"CTENFDAM" + b"\x01\x70"
            + struct.pack("<I", len(key_data)) + key_data
            + struct.pack("<I", len(meta_enc)) + meta_enc
            + b"\x00\x00\x00\x00" + b"\x00" + struct.pack("<I", 0)
            + audio_enc)
    write("sample.ncm", data)
    write("sample.ncm.expected", plain)
    print("ncm:", len(data), "bytes")


def T(x):
    return (x ^ ((x & 0xF) << 4)) & 0xFF


def kgm_mask(pos):
    t1, t2, pre = KEYS["KGM_TABLE1"], KEYS["KGM_TABLE2"], KEYS["KGM_MASK_V2_PREDEF"]
    offset = pos >> 4
    value = 0
    while offset >= 0x11:
        value ^= t1[offset % 272]
        offset >>= 4
        value ^= t2[offset % 272]
        offset >>= 4
    return pre[pos % 272] ^ value


def gen_kgm():
    header = bytes(KEYS["KGM_KGM_HEADER"])
    key16 = bytes(range(0x30, 0x40))
    key = list(key16) + [0]
    plain = b"fLaC" + bytes(range(200))
    # 解密: out = T(key ^ body) ^ T(mask)  →  body = key ^ T(plain ^ T(mask))
    body = bytes(key[i % 17] ^ T(plain[i] ^ T(kgm_mask(i))) for i in range(len(plain)))
    data = header + struct.pack("<I", 0x2C) + b"\x00" * 8 + key16 + body
    assert len(data) == 0x2C + len(body)
    write("sample.kgm", data)
    write("sample.kgm.expected", plain)
    print("kgm:", len(data), "bytes")


def gen_kwm():
    header = b"yeelion-kuwo-tme"
    file_key = 1234567890
    key_str = str(file_key)
    while len(key_str) < 32:
        key_str += str(file_key)
    key_str = key_str[:32]
    secret = "MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"
    mask = bytes(ord(secret[i]) ^ ord(key_str[i]) for i in range(32))
    plain = b"ID3\x03\x00" + bytes(range(120))
    audio = bytes(plain[i] ^ mask[i % 32] for i in range(len(plain)))
    data = header + b"\x00" * (0x18 - 16) + struct.pack("<Q", file_key) + b"\x00" * (0x400 - 0x20) + audio
    write("sample.kwm", data)
    write("sample.kwm.expected", plain)
    print("kwm:", len(data), "bytes")


def gen_qmc_vectors():
    box = KEYS["QMC_STATIC_CIPHER_BOX"]
    offsets = [0, 1, 2, 27, 100, 1000, 32766, 32767, 32768, 65535]

    def smask(off):
        if off > 0x7FFF:
            off %= 0x7FFF
        return box[(off * off + 27) & 0xFF]

    # MapCipher：用固定 key（16 字节）算 mask
    key = bytes(range(16))
    n = len(key)

    def mmask(off):
        if off > 0x7FFF:
            off %= 0x7FFF
        idx = (off * off + 71214) % n
        rotate = ((idx & 0x7) + 4) % 8
        v = key[idx]
        return ((v << rotate) | (v >> rotate)) & 0xFF

    lines = ["static|" + "|".join(f"{o}:{smask(o)}" for o in offsets),
             "map|" + "|".join(f"{o}:{mmask(o)}" for o in offsets)]
    write("qmc_vectors.txt", ("\n".join(lines) + "\n").encode("ascii"))
    print("qmc vectors:", lines)


if __name__ == "__main__":
    gen_ncm()
    gen_kgm()
    gen_kwm()
    gen_qmc_vectors()
