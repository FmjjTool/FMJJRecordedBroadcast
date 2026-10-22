package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.Ffmpeg;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * B 站视频下载编排：详情 → playurl → 选流 → 下载 m4s → ffmpeg 合并。
 * 移植自 Python 版 bilibili_download.download_bilibili_video。
 */
public final class BiliDownloader {

    /** 下载结果。 */
    public record Outcome(boolean success, Path path, String error,
                          BiliParser.VideoInfo info, int qualityQn) {}

    private final BiliParser parser = new BiliParser();
    private final FileDownloader downloader = new FileDownloader();

    /** 画质 0 表示最高可用画质。 */
    public Outcome download(String idOrBvid, int targetQn, String cookie, Path outRoot,
                            Consumer<String> log, Consumer<FileDownloader.Progress> progress,
                            BooleanSupplier stop) {
        Consumer<String> logger = log == null ? s -> {} : log;
        Path tmpDir = null;
        try {
            BiliParser.VideoInfo info = parser.videoInfo(idOrBvid, cookie);
            logger.accept("📺 " + info.title() + "（UP：" + info.owner() + "，" + info.duration() + "s）");

            BiliParser.PlayInfo play = parser.playInfo(info.bvid(), info.cid(), cookie);
            if (play.video().isEmpty()) return new Outcome(false, null, "未获取到视频流", info, 0);
            int qn = targetQn > 0 ? targetQn
                    : (play.qualities().isEmpty() ? 0 : play.qualities().get(0).qn());
            BiliParser.StreamInfo video = BiliParser.pickVideoStream(play.video(), qn);
            BiliParser.StreamInfo audio = BiliParser.pickAudioStream(play.audio());
            if (video == null) return new Outcome(false, null, "无可用视频流", info, qn);
            logger.accept("🎞 画质：" + video.qualityText()
                    + (audio != null ? "，音频：" + audio.bandwidth() / 1000 + " kbps" : "，无独立音频流"));

            tmpDir = Files.createTempDirectory("bili_dl_");
            Path videoTmp = tmpDir.resolve("video.m4s");
            Path audioTmp = tmpDir.resolve("audio.m4s");

            var vr = downloader.download(video.urls(), videoTmp,
                    BiliParser.headers(info.bvid(), cookie), progress, stop);
            if (!vr.success()) return new Outcome(false, null, "视频流下载失败：" + vr.error(), info, qn);
            logger.accept("✅ 视频流完成（" + (vr.bytes() / 1024 / 1024) + " MB）");

            boolean hasAudio = false;
            if (audio != null && !audio.urls().isEmpty()) {
                var ar = downloader.download(audio.urls(), audioTmp,
                        BiliParser.headers(info.bvid(), cookie), progress, stop);
                if (!ar.success()) return new Outcome(false, null, "音频流下载失败：" + ar.error(), info, qn);
                hasAudio = true;
                logger.accept("✅ 音频流完成（" + (ar.bytes() / 1024 / 1024) + " MB）");
            }

            Path outDir = outRoot.resolve("B站_媒体")
                    .resolve(BiliParser.sanitizeFilename(
                            info.owner() == null || info.owner().isBlank() ? "未知" : info.owner(), 80, "未知"));
            Files.createDirectories(outDir);
            Path outFile = outDir.resolve(BiliParser.outputFileName(
                    info.publishText(), info.owner(), info.title(), info.bvid()));

            List<String> cmd = hasAudio
                    ? List.of(Ffmpeg.ffmpegExe(), "-y", "-i", videoTmp.toString(),
                            "-i", audioTmp.toString(), "-c", "copy", outFile.toString())
                    : List.of(Ffmpeg.ffmpegExe(), "-y", "-i", videoTmp.toString(),
                            "-c", "copy", outFile.toString());
            logger.accept("🔧 ffmpeg 合并中…");
            var merge = Ffmpeg.run(cmd, 3600);
            if (!merge.ok() || !Files.isRegularFile(outFile)) {
                String detail = merge.stderr();
                return new Outcome(false, outFile, "ffmpeg 合并失败:"
                        + (detail == null ? "" : tail(detail, 300)), info, qn);
            }
            logger.accept("🎉 已保存：" + outFile);
            return new Outcome(true, outFile, "", info, qn);
        } catch (Exception e) {
            return new Outcome(false, null, e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage(),
                    null, 0);
        } finally {
            if (tmpDir != null) deleteTree(tmpDir);
        }
    }

    private static String tail(String s, int max) {
        return s.length() > max ? s.substring(s.length() - max) : s;
    }

    private static void deleteTree(Path dir) {
        try (var s = Files.walk(dir)) {
            s.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (IOException ignored) {
                }
            });
        } catch (IOException ignored) {
        }
    }
}
