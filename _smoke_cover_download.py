"""冒烟测试：下载歌曲时自动保存对应平台封面（离屏，不发真实下载请求）。"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

failures = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        failures.append(name)


# 1) 四平台引擎：模块导入 + 封面函数逻辑
from music.downloader import _download_cover, download_song, _COVER_EXTS
from music.models import SongInfo
from music import downloader as mdl

check("music.downloader 导入且 _download_cover 存在", callable(_download_cover))

# cover_url 为空 → 直接返回 ""
check("无封面 URL 时返回空串", _download_cover("", "https://www.kugou.com/", "x.mp3") == "")

# 本地 HTTP 服务模拟封面下载（真实 IO 路径）
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/bad":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/cover.png" or self.path.startswith("/cover"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
            return
        if self.path == "/tiny":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ab")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), _H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

with tempfile.TemporaryDirectory() as td:
    # 正常下载：存为同名 .png
    audio = os.path.join(td, "歌手 - 歌名 .mp3")
    open(audio, "wb").write(b"x")
    p = _download_cover(f"http://127.0.0.1:{port}/cover.png", "", audio)
    check("封面保存为同名 .png", p == os.path.splitext(audio)[0] + ".png" and os.path.exists(p))
    # 已存在 → 不重下，返回同一路径
    open(audio, "wb").write(b"y")  # mtime 变化也不应覆盖
    p2 = _download_cover(f"http://127.0.0.1:{port}/cover.png", "", audio)
    check("封面已存在时跳过", p2 == p)

    # URL 无扩展名 → 默认 .jpg
    audio2 = os.path.join(td, "歌手 - 歌二 .mp3")
    open(audio2, "wb").write(b"x")
    p3 = _download_cover(f"http://127.0.0.1:{port}/cover?id=1", "", audio2)
    check("无扩展名 URL 默认 .jpg", p3.endswith(".jpg") and os.path.exists(p3))

    # HTTP 404 / 内容过小 → 静默失败返回 ""
    audio3 = os.path.join(td, "歌手 - 歌三 .mp3")
    open(audio3, "wb").write(b"x")
    check("HTTP 404 静默失败", _download_cover(f"http://127.0.0.1:{port}/bad", "", audio3) == "")
    check("内容过小静默失败", _download_cover(f"http://127.0.0.1:{port}/tiny", "", audio3) == "")
    # 连接异常也静默
    check("连接失败静默", _download_cover("http://127.0.0.1:1/x.png", "", audio3) == "")

# download_song 成功路径挂钩：monkeypatch resolver 验证封面被调用
with tempfile.TemporaryDirectory() as td:
    called = {}

    def fake_resolver(song, quality):
        called["cover"] = song.cover_url
        return mdl._UrlResult(url=f"http://127.0.0.1:{port}/cover.png", ext=".mp3")

    def fake_dl_to(url, referer, base, ext, out_dir, expected_seconds=0):
        path = os.path.join(out_dir, f"{base}{ext}")
        open(path, "wb").write(b"audio-bytes")
        return mdl.DownloadResult(success=True, file_path=path)

    orig_resolver, orig_dl_to = mdl._RESOLVERS["kugou"], mdl._download_to
    mdl._RESOLVERS["kugou"] = fake_resolver
    mdl._download_to = fake_dl_to
    try:
        song = SongInfo(song_id="abc", song_name="歌名", singer="歌手",
                        cover_url=f"http://127.0.0.1:{port}/cover.png", platform="kugou")
        r = download_song(song, td, "128k")
        check("download_song 成功且触发封面下载",
              r.success and called.get("cover", "").endswith("cover.png"))
        # base 经 strip + 压空白后为「歌手 - 歌名」（无尾随空格）
        check("封面文件与音频同名",
              os.path.exists(os.path.join(td, "歌手 - 歌名.png")))
        # cover_url 为空不触发封面逻辑
        song2 = SongInfo(song_id="abc", song_name="歌名", singer="歌手", platform="kugou")
        r2 = download_song(song2, td, "128k")
        check("无 cover_url 的歌正常下载", r2.success)
    finally:
        mdl._RESOLVERS["kugou"] = orig_resolver
        mdl._download_to = orig_dl_to
srv.shutdown()

# 2) 网易云引擎：模块导入 + 封面单次下载复用 + 另存文件逻辑
from netease.music_downloader import MusicDownloader, MusicInfo
mi = MusicInfo(id=1, name="n", artists="a", album="al",
               pic_url=f"http://127.0.0.1:{port}/cover.png", duration=1, track_number=1,
               download_url="", file_type="mp3", file_size=0, quality="standard")
srv_thread2 = srv  # srv 已 shutdown，重新起一个
srv2 = HTTPServer(("127.0.0.1", 0), _H)
port2 = srv2.server_address[1]
threading.Thread(target=srv2.serve_forever, daemon=True).start()
mi.pic_url = f"http://127.0.0.1:{port2}/cover.png"

check("MusicDownloader._fetch_cover_bytes 成功", MusicDownloader._fetch_cover_bytes(mi.pic_url) == PNG)
check("MusicDownloader._fetch_cover_bytes 失败返回 None",
      MusicDownloader._fetch_cover_bytes(f"http://127.0.0.1:{port2}/bad") is None)
check("MusicDownloader._fetch_cover_bytes 空 URL 返回 None",
      MusicDownloader._fetch_cover_bytes("") is None)

with tempfile.TemporaryDirectory() as td:
    from pathlib import Path
    fp = Path(td) / "歌手 - 歌名.mp3"
    fp.write_bytes(b"audio")
    # 已传字节 → 不再发请求，直接另存
    MusicDownloader._ensure_cover_file(fp, mi, PNG)
    cov = Path(td) / "歌手 - 歌名.png"
    check("封面另存为同名 .png", cov.exists() and cov.read_bytes() == PNG)
    # 已存在 → 跳过（内容不被覆盖）
    cov.write_bytes(b"old")
    MusicDownloader._ensure_cover_file(fp, mi, PNG)
    check("网易云封面已存在时跳过", cov.read_bytes() == b"old")
    # 未传字节 → 内部下载
    fp2 = Path(td) / "歌手 - 歌二.mp3"
    fp2.write_bytes(b"audio")
    mi2 = MusicInfo(id=1, name="n", artists="a", album="al",
                    pic_url=f"http://127.0.0.1:{port2}/cover.png", duration=1, track_number=1,
                    download_url="", file_type="mp3", file_size=0, quality="standard")
    MusicDownloader._ensure_cover_file(fp2, mi2, None)
    check("未传字节时内部下载封面", (Path(td) / "歌手 - 歌二.png").exists())
    # pic_url 为空 → 不生成
    fp3 = Path(td) / "歌手 - 歌三.mp3"
    fp3.write_bytes(b"audio")
    mi3 = MusicInfo(id=1, name="n", artists="a", album="al", pic_url="",
                    duration=1, track_number=1, download_url="", file_type="mp3",
                    file_size=0, quality="standard")
    MusicDownloader._ensure_cover_file(fp3, mi3, None)
    check("pic_url 为空不生成封面", not (Path(td) / "歌手 - 歌三.png").exists())
    # 标签写入函数接受 cover_bytes 参数（不真正写标签，仅验签名）
    import inspect
    for m in ("_write_music_tags", "_write_mp3_tags", "_write_flac_tags", "_write_m4a_tags"):
        params = list(inspect.signature(getattr(MusicDownloader, m)).parameters)
        check(f"{m} 含 cover_bytes 参数", "cover_bytes" in params)

srv2.shutdown()

# 3) GUI 兜底构造 SongInfo 补传封面（源码级断言）
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "src", "gui_qt", "pages", "netease.py"), encoding="utf-8").read()
check("GUI 批量下载兜底 SongInfo 传 cover_url",
      'cover_url=item.get("picUrl")' in src)

print()
if failures:
    print(f"结果：{len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果：全部通过")
