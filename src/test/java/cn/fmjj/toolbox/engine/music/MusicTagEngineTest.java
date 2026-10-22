package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.engine.media.Ffmpeg;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 标签读写测试：用 ffmpeg 现场生成 1 秒静音音频（mp3/flac），
 * 写入标签后读回断言。无 ffmpeg 或被沙箱限制时自动跳过。
 */
class MusicTagEngineTest {

    private Path silence(Path dir, String name) {
        Path out = dir.resolve(name);
        var r = Ffmpeg.run(List.of(Ffmpeg.ffmpegExe(), "-y", "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=mono", "-t", "1", out.toString()), 60);
        Assumptions.assumeTrue(r.ok() && Files.isRegularFile(out) && Files.exists(out),
                "本机无法生成测试音频（ffmpeg 不可用或被限制）");
        return out;
    }

    @Test
    @DisplayName("MP3：写入标题/艺术家/专辑/年份/音轨/流派 → 读回一致")
    void mp3RoundTrip(@TempDir Path tmp) throws Exception {
        Path mp3 = silence(tmp, "t.mp3");
        MusicTagEngine.write(mp3, new MusicTagEngine.Tags("测试标题", "测试歌手", "测试专辑", "2026", "3", "流行", false));
        var t = MusicTagEngine.read(mp3);
        assertEquals("测试标题", t.title());
        assertEquals("测试歌手", t.artist());
        assertEquals("测试专辑", t.album());
        assertEquals("2026", t.year());
        assertEquals("3", t.track());
        assertEquals("流行", t.genre());
        assertFalse(t.hasCover());
    }

    @Test
    @DisplayName("FLAC：往返一致")
    void flacRoundTrip(@TempDir Path tmp) throws Exception {
        Path flac = silence(tmp, "t.flac");
        MusicTagEngine.write(flac, new MusicTagEngine.Tags("FLAC 标题", "歌手B", "专辑B", "2025", "", "", false));
        var t = MusicTagEngine.read(flac);
        assertEquals("FLAC 标题", t.title());
        assertEquals("歌手B", t.artist());
        assertEquals("", t.track());
    }

    @Test
    @DisplayName("空字段清除：再次写入空标题后读回为空")
    void clearFields(@TempDir Path tmp) throws Exception {
        Path mp3 = silence(tmp, "c.mp3");
        MusicTagEngine.write(mp3, new MusicTagEngine.Tags("有标题", "有歌手", "", "", "", "", false));
        assertEquals("有标题", MusicTagEngine.read(mp3).title());

        MusicTagEngine.write(mp3, new MusicTagEngine.Tags("", "", "", "", "", "", false));
        assertEquals("", MusicTagEngine.read(mp3).title());
        assertEquals("", MusicTagEngine.read(mp3).artist());
    }

    @Test
    @DisplayName("封面：写入图片 → hasCover=true；移除后 false")
    void cover(@TempDir Path tmp) throws Exception {
        Path mp3 = silence(tmp, "cover.mp3");
        // 1x1 PNG
        byte[] png = java.util.Base64.getDecoder().decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==");
        Path img = tmp.resolve("cover.png");
        Files.write(img, png);

        MusicTagEngine.writeCover(mp3, img);
        assertTrue(MusicTagEngine.read(mp3).hasCover());

        MusicTagEngine.removeCover(mp3);
        assertFalse(MusicTagEngine.read(mp3).hasCover());
    }

    @Test
    @DisplayName("isSupported：扩展名白名单")
    void supported() {
        assertTrue(MusicTagEngine.isSupported(Path.of("a.mp3")));
        assertTrue(MusicTagEngine.isSupported(Path.of("a.FLAC")));
        assertTrue(MusicTagEngine.isSupported(Path.of("a.m4a")));
        assertFalse(MusicTagEngine.isSupported(Path.of("a.txt")));
        assertFalse(MusicTagEngine.isSupported(Path.of("noext")));
    }
}
