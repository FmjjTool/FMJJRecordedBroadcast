package cn.fmjj.toolbox.engine.media;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 直播预览 / FLV 录制（移植自 Python 版 toolbox/live_video.py 的 StreamRecorder 与
 * VideoStreamPlayer 核心）。
 *
 * <p>预览：ffmpeg 把直播流或本地视频解码为固定 640x360 RGB24 rawvideo，读到最新帧
 * 由 GUI 定时器取用；录制：`ffmpeg -c copy -f flv` 无损转存，stop 时向 stdin 写 q。
 */
public final class LiveVideoEngine {

    public static final String UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            + " (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36";

    private LiveVideoEngine() {}

    /** 一帧 RGB24 图像。 */
    public record Frame(int width, int height, byte[] rgb) {}

    public interface Listener {
        void onStatus(String state, String detail);
    }

    // ==================== 播放 ====================

    public static final class Player implements AutoCloseable {
        public static final int WIDTH = 640;
        public static final int HEIGHT = 360;
        public static final int FPS = 15;

        private volatile Process process;
        private volatile boolean closed;
        private volatile Frame latest;
        private Thread reader;
        private volatile String source = "";
        private volatile boolean live = true;
        private volatile double startSec;
        private volatile double speed = 1;
        private final AtomicLong framesRead = new AtomicLong();

        /** 启动解码；source 为直播 URL 或本地视频路径。失败返回 false。 */
        public boolean start(String source, boolean live, Listener listener) {
            return start(source, live, 0, 1, listener);
        }

        /** 指定起始秒与倍速（本地文件；直播忽略起始秒）。 */
        public boolean start(String source, boolean live, double startSec, double speed,
                             Listener listener) {
            stop();
            if (!Ffmpeg.available()) {
                if (listener != null) listener.onStatus("error", "未找到 FFmpeg，无法播放视频");
                return false;
            }
            String exe = Ffmpeg.ffmpegExe();
            this.source = source == null ? "" : source;
            this.live = live;
            this.startSec = live ? 0 : Math.max(0, startSec);
            this.speed = Math.max(0.1, speed);
            framesRead.set(0);
            closed = false;
            latest = null;
            List<String> args = new ArrayList<>(List.of(exe,
                    "-hide_banner", "-loglevel", "error", "-nostdin"));
            if (live) {
                args.addAll(List.of("-fflags", "nobuffer", "-flags", "low_delay",
                        "-probesize", "64k", "-analyzeduration", "0"));
                String headers = liveHeaders(source);
                if (!headers.isEmpty()) {
                    args.add("-headers");
                    args.add(headers);
                }
            } else if (this.startSec > 0) {
                args.addAll(List.of("-ss", String.format(java.util.Locale.ROOT, "%.3f",
                        this.startSec)));
            }
            if (!live) {
                // 本地文件按真实时间读取；倍速用 readrate，避免 rawvideo 瞬间解码到结尾
                args.addAll(List.of("-readrate", String.format(java.util.Locale.ROOT, "%.3f",
                        this.speed)));
            }
            StringBuilder filters = new StringBuilder();
            filters.append("scale=").append(WIDTH).append(':').append(HEIGHT)
                    .append(":force_original_aspect_ratio=decrease")
                    .append(",pad=").append(WIDTH).append(':').append(HEIGHT)
                    .append(":(ow-iw)/2:(oh-ih)/2")
                    .append(",fps=").append(FPS);
            args.addAll(List.of("-i", source, "-vf", filters.toString(),
                    "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"));
            try {
                ProcessBuilder pb = new ProcessBuilder(args)
                        .redirectErrorStream(false)
                        .redirectError(ProcessBuilder.Redirect.DISCARD)
                        .redirectInput(ProcessBuilder.Redirect.PIPE);
                process = pb.start();
            } catch (Exception e) {
                if (listener != null) listener.onStatus("error", "FFmpeg 启动失败: " + e.getMessage());
                return false;
            }
            Process p = process;
            reader = new Thread(() -> readFrames(p, listener), "live-video-player");
            reader.setDaemon(true);
            reader.start();
            return true;
        }

        private void readFrames(Process p, Listener listener) {
            int size = WIDTH * HEIGHT * 3;
            try (InputStream in = p.getInputStream()) {
                byte[] buf = new byte[size];
                while (!closed) {
                    int read = 0;
                    while (read < size) {
                        int n = in.read(buf, read, size - read);
                        if (n < 0) break;
                        read += n;
                    }
                    if (read < size) break;
                    latest = new Frame(WIDTH, HEIGHT, buf.clone());
                    framesRead.incrementAndGet();
                }
            } catch (Exception ignored) {
            }
            if (!closed && listener != null) {
                listener.onStatus("stopped", live ? "直播流中断" : "视频播放结束");
            }
        }

        /** 当前播放位置（秒；直播返回 startSec=0）。 */
        public double positionSec() {
            if (live) return 0;
            return startSec + framesRead.get() / (double) FPS;
        }

        public double speed() {
            return speed;
        }

        public boolean isLive() {
            return live;
        }

        public Frame latest() {
            return latest;
        }

        public boolean isRunning() {
            Process p = process;
            return p != null && p.isAlive();
        }

        public void stop() {
            closed = true;
            Process p = process;
            process = null;
            if (p != null) {
                p.destroy();
                try {
                    if (!p.waitFor(3, TimeUnit.SECONDS)) p.destroyForcibly();
                } catch (Exception ignored) {
                    p.destroyForcibly();
                }
            }
            Thread r = reader;
            reader = null;
            if (r != null) {
                try {
                    r.join(1500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            latest = null;
        }

        @Override
        public void close() {
            stop();
        }
    }

    // ==================== 录制 ====================

    public static final class Recorder implements AutoCloseable {
        private volatile Process process;
        private volatile long startTs;
        private volatile Path path;

        public boolean start(String url, Path outPath) {
            stop();
            if (!Ffmpeg.available()) return false;
            String exe = Ffmpeg.ffmpegExe();
            List<String> args = new ArrayList<>(List.of(exe,
                    "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer"));
            String headers = liveHeaders(url);
            if (!headers.isEmpty()) {
                args.add("-headers");
                args.add(headers);
            }
            args.addAll(List.of("-i", url, "-c", "copy", "-f", "flv", "-y",
                    outPath.toString()));
            try {
                Files.createDirectories(outPath.getParent());
                ProcessBuilder pb = new ProcessBuilder(args)
                        .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                        .redirectError(ProcessBuilder.Redirect.DISCARD)
                        .redirectInput(ProcessBuilder.Redirect.PIPE);
                process = pb.start();
                path = outPath;
                startTs = System.currentTimeMillis();
                return true;
            } catch (Exception e) {
                process = null;
                return false;
            }
        }

        public boolean isRunning() {
            Process p = process;
            return p != null && p.isAlive();
        }

        public long startTs() {
            return startTs;
        }

        public Path path() {
            return path;
        }

        /** 优雅停止（写 q），超时后强制结束。 */
        public void stop() {
            Process p = process;
            process = null;
            if (p == null) return;
            try (OutputStream in = p.getOutputStream()) {
                in.write('q');
                in.flush();
            } catch (Exception ignored) {
            }
            try {
                if (!p.waitFor(8, TimeUnit.SECONDS)) p.destroyForcibly();
            } catch (Exception ignored) {
                p.destroyForcibly();
            }
        }

        @Override
        public void close() {
            stop();
        }
    }

    /** 本地视频时长（秒），ffprobe 读取；失败返回 0。 */
    public static double durationSeconds(Path file) {
        if (file == null || !Files.isRegularFile(file)) return 0;
        try {
            Process p = new ProcessBuilder(Ffmpeg.ffprobeExe(), "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", file.toString())
                    .redirectErrorStream(true).start();
            String out = new String(p.getInputStream().readAllBytes(),
                    java.nio.charset.StandardCharsets.UTF_8).strip();
            p.waitFor(15, TimeUnit.SECONDS);
            String first = out.lines().findFirst().orElse("0").strip();
            return Double.parseDouble(first);
        } catch (Exception e) {
            return 0;
        }
    }

    /** 直播拉流请求头（ffmpeg -headers，需 CRLF 结尾）。 */
    static String liveHeaders(String url) {
        if (url == null || !(url.startsWith("http://") || url.startsWith("https://"))) return "";
        String host;
        try {
            host = java.net.URI.create(url).getHost();
        } catch (Exception e) {
            return "User-Agent: " + UA + "\r\n";
        }
        String referer = "";
        if (host != null && host.contains("bilivideo")) referer = "https://live.bilibili.com/";
        else if (host != null && host.contains("douyin")) referer = "https://live.douyin.com/";
        StringBuilder sb = new StringBuilder();
        if (!referer.isEmpty()) sb.append("Referer: ").append(referer).append("\r\n");
        sb.append("User-Agent: ").append(UA).append("\r\n");
        return sb.toString();
    }
}
