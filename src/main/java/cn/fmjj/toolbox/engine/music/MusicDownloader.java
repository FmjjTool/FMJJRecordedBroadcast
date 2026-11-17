package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.engine.download.FileDownloader;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * 音乐下载：搜索结果的直链 → 保存为「歌手 - 歌名.mp3」+ 同名 .lrc。
 * 对应 Python 版 music/downloader.py 的单曲下载核心。
 */
public final class MusicDownloader {

    /** 下载结果。 */
    public record Outcome(boolean success, Path audio, Path lyric, String error) {}

    private final FileDownloader downloader = new FileDownloader();
    private final KuwoScraper kuwo;
    private final KugouScraper kugou;
    private final NeteaseScraper netease;
    private final QQMusicScraper qq;
    private final FiveSingScraper fivesing = new FiveSingScraper();
    private final KugouLiteScraper kugouLite; 

    public MusicDownloader() {
        this(new KuwoScraper(), new KugouScraper(), new NeteaseScraper(), new QQMusicScraper());
    }

    public MusicDownloader(KuwoScraper kuwo) {
        this(kuwo, new KugouScraper(), new NeteaseScraper(), new QQMusicScraper());
    }

    public MusicDownloader(KuwoScraper kuwo, KugouScraper kugou) {
        this(kuwo, kugou, new NeteaseScraper(), new QQMusicScraper());
    }

    public MusicDownloader(KuwoScraper kuwo, KugouScraper kugou,
                           NeteaseScraper netease, QQMusicScraper qq) {
        this.kuwo = kuwo;
        this.kugou = kugou;
        this.netease = netease;
        this.qq = qq;
        this.kugouLite = new KugouLiteScraper();
    }

    /**
     * 下载一首（酷我 / 酷狗 / 5sing / 网易云免费曲 / QQ 免费曲）。
     *
     * @param outRoot  输出根目录（会再建 歌手/ 子目录）
     * @param bitrate  如 "128kmp3" / "320kmp3" / "flac"（游客直链以平台实际返回为准）
     */
    public Outcome download(MusicSong song, Path outRoot, String bitrate,
                            Consumer<FileDownloader.Progress> progress, BooleanSupplier stop) {
        String url;
        String ext = ".mp3";
        String referer;
        String lrc;
        if ("kugou".equals(song.platform())) {
            KugouScraper.PlayInfo info = kugou.playInfo(song.songId());
            if (!info.error().isEmpty()) return new Outcome(false, null, null, info.error());
            url = info.url();
            ext = info.ext().isEmpty() ? ".mp3" : info.ext();
            referer = "https://www.kugou.com/";
            lrc = kugou.getLyric(song.songId());
        } else if ("kugou_lite".equals(song.platform())) {
            KugouLiteScraper.PlayInfo info = kugouLite.playInfo(song.songId(), liteQuality(bitrate));
            if (!info.error().isEmpty()) return new Outcome(false, null, null, info.error());
            url = info.url();
            ext = info.ext().isEmpty() ? ".mp3" : info.ext();
            referer = "https://www.kugou.com/";
            lrc = kugou.getLyric(song.songId());
        } else if ("5sing".equals(song.platform())) {
            FiveSingScraper.PlayInfo info = fivesing.playInfo(song.songId());
            if (!info.error().isEmpty()) return new Outcome(false, null, null, info.error());
            url = info.url();
            ext = info.ext().isEmpty() ? ".mp3" : info.ext();
            referer = "https://5sing.kugou.com/";
            lrc = song.songId().contains("/") ? "" : kugou.getLyric(song.songId());
        } else if ("netease".equals(song.platform())) {
            NeteaseScraper.PlayInfo info = netease.playInfo(song.songId());
            if (!info.error().isEmpty()) return new Outcome(false, null, null, info.error());
            url = info.url();
            referer = "https://music.163.com/";
            lrc = netease.getLyric(song.songId());
        } else if ("qq".equals(song.platform())) {
            QQMusicScraper.PlayInfo info = qq.playInfo(song.songId());
            if (!info.error().isEmpty()) return new Outcome(false, null, null, info.error());
            url = info.url();
            ext = info.ext().isEmpty() ? ".mp3" : info.ext();
            referer = "https://y.qq.com/";
            lrc = qq.getLyric(song.songId());
        } else {
            url = kuwo.getPlayUrl(song.songId(), bitrate);
            referer = "https://www.kuwo.cn/";
            lrc = kuwo.getLyric(song.songId());
        }
        if (url.isEmpty()) return new Outcome(false, null, null, "获取直链失败（可能受版权/地区限制）");

        Path dir = outRoot.resolve(safe(song.singer().isBlank() ? "未知歌手" : song.singer()));
        String base = safe(song.fileNameBase());
        Path audio = dir.resolve(base + ext);

        var r = downloader.download(List.of(url), audio,
                Map.of("Referer", referer, "User-Agent", "Mozilla/5.0"), progress, stop);
        if (!r.success()) return new Outcome(false, audio, null, r.error());

        Path lyric = null;
        if (!lrc.isBlank()) {
            lyric = dir.resolve(base + ".lrc");
            try {
                Files.writeString(lyric, lrc, StandardCharsets.UTF_8);
            } catch (IOException ignored) {
                lyric = null;
            }
        }
        return new Outcome(true, audio, lyric, "");
    }

    private static String safe(String name) {
        return MusicUnlock.safeName(name);
    }

    /** UI 音质档 → 概念版 v5/url 的 quality。 */
    static String liteQuality(String bitrate) {
        String b = bitrate == null ? "" : bitrate.toLowerCase(java.util.Locale.ROOT);
        if (b.startsWith("flac")) return "flac";
        if (b.startsWith("320")) return "320";
        return "128";
    }
}
