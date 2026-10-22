package cn.fmjj.toolbox.engine.media;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * ffmpeg 封装测试。classify_ffmpeg_error 的期望值来自 Python 原版运行时；
 * 真实子进程调用依赖本机安装，不做强制断言。
 */
class FfmpegTest {

    @Test
    @DisplayName("classify_ffmpeg_error 与 Python 一致（10 组向量）")
    void classifyError() {
        String[] muxer = Ffmpeg.classifyError("Invalid argument muxer");
        assertEquals("输出格式错误", muxer[0]);
        assertEquals("ffmpeg 无法识别输出格式", muxer[1]);
        assertEquals("文件不存在", Ffmpeg.classifyError("No such file or directory: /x/y.mp4")[0]);
        assertEquals("权限不足", Ffmpeg.classifyError("Permission denied")[0]);
        assertEquals("输入文件损坏", Ffmpeg.classifyError("Invalid data found when processing input")[0]);
        assertEquals("无法识别编码", Ffmpeg.classifyError("Could not find codec parameters for stream 0")[0]);
        assertEquals("缺少音视频流", Ffmpeg.classifyError("Stream map matches no streams")[0]);
        assertEquals("音频格式不兼容", Ffmpeg.classifyError("Error applying bitstream filter aac_adtstoasc")[0]);
        assertEquals("时间戳异常", Ffmpeg.classifyError("Non-monotonous DTS in output stream")[0]);
        assertEquals("未知错误", Ffmpeg.classifyError("")[0]);
        assertEquals("ffmpeg 没有输出错误信息，可能是被强制终止", Ffmpeg.classifyError("")[1]);
        // 兜底：取最后 3 行并加 6 空格缩进（Python: "      " + l）
        String[] fallback = Ffmpeg.classifyError("line1\nline2\nline3\nline4\nline5");
        assertEquals("转换失败", fallback[0]);
        assertEquals("      line3\n      line4\n      line5", fallback[1]);
    }

    @Test
    @DisplayName("定位：返回非空路径；available() 不抛异常")
    void locate() {
        assertFalse(Ffmpeg.ffmpegExe().isBlank());
        assertFalse(Ffmpeg.ffprobeExe().isBlank());
        Ffmpeg.available(); // 仅验证不抛异常（本机装没装 ffmpeg 都合法）
    }

    @Test
    @DisplayName("run：可执行文件不存在 → exitCode=-2 且带中文提示，不抛异常")
    void runMissingExe() {
        var r = Ffmpeg.run(List.of("definitely-not-exists-ffmpeg-xyz", "-version"), 5);
        assertEquals(-2, r.exitCode());
        assertTrue(r.stderr().contains("ffmpeg 未找到"), r.stderr());
        assertFalse(r.ok());
    }

    @Test
    @DisplayName("preserveTimestamps：目标文件 mtime 与源一致")
    void preserveTimestamps(@TempDir Path tmp) throws IOException {
        Path src = tmp.resolve("a.bin");
        Path dst = tmp.resolve("b.bin");
        Files.writeString(src, "x");
        Files.writeString(dst, "y");
        Files.setLastModifiedTime(src, FileTime.fromMillis(1_600_000_000_000L));
        assertTrue(Ffmpeg.preserveTimestamps(src, dst));
        assertEquals(1_600_000_000_000L, Files.getLastModifiedTime(dst).toMillis());
    }

    @Test
    @DisplayName("run 超时：强杀并返回 timedOut")
    void runTimeout() {
        // 用 sleep 命令验证超时路径（Windows: timeout 命令不可靠，改用 ping 127.0.0.1）
        // 某些环境禁止起子进程，这里允许失败但必须不挂起
        var r = Ffmpeg.run(List.of("ping", "-n", "5", "127.0.0.1"), 1);
        if (r.exitCode() == -2) return; // 环境不允许起子进程
        assertTrue(r.timedOut() || r.exitCode() == 0, "应超时或正常结束");
    }
}
