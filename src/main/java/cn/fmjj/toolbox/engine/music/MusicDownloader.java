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

    public MusicDownloader() { this(new KuwoScraper()); }

    public MusicDownloader(KuwoScraper kuwo) { this.kuwo = kuwo; }

    /**
     * 下载一首（酷我）。
     *
     * @param outRoot  输出根目录（会再建 歌手/ 子目录）
     * @param bitrate  如 "128kmp3" / "320kmp3" / "flac"
     */
    public Outcome download(MusicSong song, Path outRoot, String bitrate,
                            Consumer<FileDownloader.Progress> progress, BooleanSupplier stop) {
        String url = kuwo.getPlayUrl(song.songId(), bitrate);
        if (url.isEmpty()) return new Outcome(false, null, null, "获取直链失败（可能受版权/地区限制）");

        Path dir = outRoot.resolve(safe(song.singer().isBlank() ? "未知歌手" : song.singer()));
        String base = safe(song.fileNameBase());
        Path audio = dir.resolve(base + ".mp3");

        var r = downloader.download(List.of(url), audio,
                Map.of("Referer", "https://www.kuwo.cn/", "User-Agent", "Mozilla/5.0"), progress, stop);
        if (!r.success()) return new Outcome(false, audio, null, r.error());

        Path lyric = null;
        String lrc = kuwo.getLyric(song.songId());
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
}
