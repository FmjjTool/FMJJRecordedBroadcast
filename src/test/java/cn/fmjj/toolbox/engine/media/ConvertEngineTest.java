package cn.fmjj.toolbox.engine.media;

import cn.fmjj.toolbox.engine.media.ConvertEngine.CacheEntry;
import cn.fmjj.toolbox.engine.media.ConvertEngine.ConvertConfig;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 视频转换引擎测试。命令构造/输出路径/缓存/扫描均为纯逻辑；
 * 真实 ffmpeg 调用依赖本机安装，不在此处跑。
 */
class ConvertEngineTest {

    private final ConvertConfig cfg = ConvertConfig.defaults();

    @Test
    @DisplayName("get_output_path：无输出目录 = 源同目录换扩展名；有输出目录 = 保留相对结构")
    void outputPath(@TempDir Path tmp) throws IOException {
        Path root = tmp.resolve("work");
        Path src = root.resolve("20260608").resolve("录播.flv");
        Files.createDirectories(src.getParent());
        Files.writeString(src, "x");

        assertEquals(root.resolve("20260608").resolve("录播.mp4"),
                ConvertEngine.getOutputPath(src, root, cfg));

        Path outDir = tmp.resolve("out");
        ConvertConfig withDir = new ConvertConfig(".mp4", outDir.toString(), false, 2, 1, false,
                "libx264", 18, "fast", true, true, true, cfg.inputExtensions());
        assertEquals(outDir.resolve("20260608").resolve("录播.mp4"),
                ConvertEngine.getOutputPath(src, root, withDir));
        assertTrue(Files.isDirectory(outDir.resolve("20260608")), "输出子目录应被创建");
    }

    @Test
    @DisplayName("无损封装命令与 Python lossless_mux 逐参数一致")
    void losslessCommand(@TempDir Path tmp) {
        Path src = tmp.resolve("a.flv");
        Path dst = tmp.resolve("a.tmp.mp4");
        List<String> cmd = ConvertEngine.losslessMuxCommand(src, dst, cfg);
        assertEquals(List.of("-i", src.toString(),
                "-map", "0:v:0", "-map", "0:a:0",
                "-map_metadata", "0", "-map_chapters", "0",
                "-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart", "-f", "mp4", "-y", "-hide_banner", dst.toString()),
                cmd.subList(1, cmd.size()));
        // preserve_metadata=false 时不带 map_metadata
        ConvertConfig noMeta = new ConvertConfig(".mp4", "", false, 2, 1, false,
                "libx264", 18, "fast", true, false, true, cfg.inputExtensions());
        assertFalse(ConvertEngine.losslessMuxCommand(src, dst, noMeta).contains("-map_metadata"));
    }

    @Test
    @DisplayName("转码命令：编码器/CRF/preset/音频 copy|aac 与 Python encode_video 一致")
    void encodeCommand(@TempDir Path tmp) {
        Path src = tmp.resolve("a.flv");
        Path dst = tmp.resolve("a.tmp.mp4");
        List<String> cmd = ConvertEngine.encodeCommand(src, dst, cfg);
        assertEquals(List.of("-i", src.toString(), "-map", "0:v:0", "-map", "0:a:0",
                "-map_metadata", "0", "-map_chapters", "0",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "copy",
                "-movflags", "+faststart", "-y", "-hide_banner", dst.toString()),
                cmd.subList(1, cmd.size()));

        ConvertConfig reencode = new ConvertConfig(".mp4", "", false, 2, 1, false,
                "libx265", 22, "medium", false, true, true, cfg.inputExtensions());
        List<String> cmd2 = ConvertEngine.encodeCommand(src, dst, reencode);
        assertTrue(cmd2.containsAll(List.of("-c:v", "libx265", "-crf", "22", "-preset", "medium")));
        assertTrue(cmd2.containsAll(List.of("-c:a", "aac", "-b:a", "192k")));
        assertFalse(cmd2.contains("copy"), "音频改为重编码时不应出现 -c:a copy");
    }

    @Test
    @DisplayName("缓存：成功 + 大小/mtime 一致才命中；失败/变更后失效")
    void cache(@TempDir Path tmp) throws IOException {
        Path src = tmp.resolve("a.flv");
        Files.writeString(src, "12345");
        Map<String, CacheEntry> cache = new LinkedHashMap<>();

        assertFalse(ConvertEngine.isCached(cache, src));
        ConvertEngine.updateCache(cache, src, true, tmp.resolve("a.mp4"));
        assertTrue(ConvertEngine.isCached(cache, src));

        // 失败记录不命中
        Map<String, CacheEntry> failCache = new LinkedHashMap<>();
        ConvertEngine.updateCache(failCache, src, false, null);
        assertFalse(ConvertEngine.isCached(failCache, src));

        // mtime 变化 → 失效
        Files.setLastModifiedTime(src, FileTime.fromMillis(System.currentTimeMillis() + 5000));
        assertFalse(ConvertEngine.isCached(cache, src));
    }

    @Test
    @DisplayName("find_videos：按 input_extensions 递归筛选并排序")
    void findVideos(@TempDir Path tmp) throws IOException {
        Files.writeString(tmp.resolve("a.flv"), "x");
        Files.writeString(tmp.resolve("b.mkv"), "x");
        Files.writeString(tmp.resolve("c.txt"), "x");
        Files.createDirectories(tmp.resolve("sub"));
        Files.writeString(tmp.resolve("sub").resolve("d.TS"), "x");

        List<Path> found = ConvertEngine.findVideos(tmp, cfg);
        assertEquals(List.of(tmp.resolve("a.flv"), tmp.resolve("b.mkv"), tmp.resolve("sub").resolve("d.TS")),
                found);
    }

    @Test
    @DisplayName("默认配置与 Python DEFAULT_CONVERT_CONFIG_DICT 关键字段一致")
    void defaults() {
        ConvertConfig d = ConvertConfig.defaults();
        assertEquals(".mp4", d.outputExtension());
        assertEquals(2, d.retryCount());
        assertEquals(1, d.retryDelaySeconds());
        assertFalse(d.enableEncodeFallback());
        assertEquals("libx264", d.encodeVideoCodec());
        assertEquals(18, d.encodeCrf());
        assertEquals("fast", d.encodePreset());
        assertTrue(d.encodeAudioCopy());
        assertTrue(d.preserveMetadata());
        assertTrue(d.preserveTimestamps());
        assertEquals(List.of(".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".rmvb"), d.inputExtensions());
    }
}
