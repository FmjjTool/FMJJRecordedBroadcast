package cn.fmjj.toolbox.engine.media;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 音频提取引擎测试：命令构造 / 按日期分组扫描 / 目标路径 / 删除（回收站）。 */
class AudioEngineTest {

    @Test
    @DisplayName("提取命令与 Python extract_audio_one 逐参数一致")
    void extractCommand(@TempDir Path tmp) {
        Path src = tmp.resolve("a.flv");
        Path dst = tmp.resolve("a.m4a");
        List<String> cmd = AudioEngine.extractCommand(src, dst);
        assertEquals(List.of("-i", src.toString(), "-vn", "-acodec", "copy", "-y", "-hide_banner",
                dst.toString()), cmd.subList(1, cmd.size()));
    }

    @Test
    @DisplayName("scan_audio_by_date：根目录 + YYYYMMDD 子目录，日期升序，非日期目录忽略")
    void scanByDate(@TempDir Path tmp) throws IOException {
        Files.writeString(tmp.resolve("根上.m4a"), "x");
        Files.writeString(tmp.resolve("说明.txt"), "x");
        Files.createDirectories(tmp.resolve("20260610"));
        Files.writeString(tmp.resolve("20260610").resolve("b.mp3"), "x");
        Files.createDirectories(tmp.resolve("20260608"));
        Files.writeString(tmp.resolve("20260608").resolve("a.mp3"), "x");
        Files.createDirectories(tmp.resolve("20260608").resolve("sub"));
        Files.writeString(tmp.resolve("20260608").resolve("sub").resolve("c.flac"), "x");
        Files.createDirectories(tmp.resolve("2026-06-09")); // 非 8 位日期目录
        Files.writeString(tmp.resolve("2026-06-09").resolve("d.mp3"), "x");

        LinkedHashMap<String, List<Path>> groups = AudioEngine.scanAudioByDate(tmp);
        assertEquals(List.of("根目录", "20260608", "20260610"), List.copyOf(groups.keySet()));
        assertEquals(1, groups.get("根目录").size());
        assertEquals(2, groups.get("20260608").size());
        assertEquals(1, groups.get("20260610").size());
    }

    @Test
    @DisplayName("find_audio_videos / isVideo / audioTarget")
    void helpers(@TempDir Path tmp) throws IOException {
        Files.writeString(tmp.resolve("a.flv"), "x");
        Files.writeString(tmp.resolve("b.mp4"), "x");
        Files.writeString(tmp.resolve("c.txt"), "x");
        Files.createDirectories(tmp.resolve("sub"));
        Files.writeString(tmp.resolve("sub").resolve("d.mkv"), "x");

        assertEquals(3, AudioEngine.findAudioVideos(tmp).size());
        assertTrue(AudioEngine.isVideo(tmp.resolve("a.flv")));
        assertTrue(!AudioEngine.isVideo(tmp.resolve("c.txt")));
        assertEquals(tmp.resolve("a.m4a"), AudioEngine.audioTarget(tmp.resolve("a.flv")));
        assertEquals(tmp.resolve("noext.m4a"), AudioEngine.audioTarget(tmp.resolve("noext")));
    }

    @Test
    @DisplayName("formatSize：KB/MB/GB 展示")
    void formatSize() {
        assertEquals("0 B", AudioEngine.formatSize(0));
        assertEquals("512 B", AudioEngine.formatSize(512));
        assertEquals("1 KB", AudioEngine.formatSize(1024));
        assertEquals("1.0 MB", AudioEngine.formatSize(1024L * 1024));
        assertEquals("1.50 GB", AudioEngine.formatSize((long) (1.5 * 1024 * 1024 * 1024)));
    }

    @Test
    @DisplayName("deleteFilesSafe：删除临时文件计数（回收站或直删均计成功）")
    void deleteSafe(@TempDir Path tmp) throws IOException {
        Path a = tmp.resolve("a.bin");
        Path b = tmp.resolve("b.bin");
        Files.writeString(a, "x");
        Files.writeString(b, "x");
        var r = AudioEngine.deleteFilesSafe(List.of(a, b));
        assertEquals(2, r.ok());
        assertTrue(r.failed().isEmpty(), r.failed().toString());
        assertTrue(!Files.exists(a) && !Files.exists(b));
    }

    @Test
    @DisplayName("scan_audio_by_date：目录不存在返回空")
    void scanMissing() {
        assertTrue(AudioEngine.scanAudioByDate(Path.of("D:/__not_exists__")).isEmpty());
    }
}
