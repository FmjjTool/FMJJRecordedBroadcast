package cn.fmjj.toolbox.engine.asset;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 资产扫描测试（临时目录构造，纯本地）。 */
class AssetScannerTest {

    @Test
    @DisplayName("scan：日期文件夹排序 + 根目录散落文件 + 分类计数")
    void scan(@TempDir Path tmp) throws IOException {
        // 20260610：2 图 1 视频
        Path d1 = Files.createDirectories(tmp.resolve("20260610"));
        Files.writeString(d1.resolve("a.jpg"), "x".repeat(100));
        Files.writeString(d1.resolve("b.png"), "x".repeat(200));
        Files.writeString(d1.resolve("c.mp4"), "x".repeat(300));
        // 20260608：1 音频 + 子目录 1 图
        Path d2 = Files.createDirectories(tmp.resolve("20260608"));
        Files.writeString(d2.resolve("d.mp3"), "x".repeat(50));
        Path sub = Files.createDirectories(d2.resolve("sub"));
        Files.writeString(sub.resolve("e.webp"), "x".repeat(10));
        // 非日期目录忽略
        Path other = Files.createDirectories(tmp.resolve("notes"));
        Files.writeString(other.resolve("f.jpg"), "x");
        // 根目录散落
        Files.writeString(tmp.resolve("root.flac"), "x".repeat(20));

        List<AssetScanner.FolderStat> stats = AssetScanner.scan(tmp);
        assertEquals(3, stats.size());
        assertEquals("20260608", stats.get(0).name());
        assertEquals(1, stats.get(0).audios());
        assertEquals(1, stats.get(0).images());
        assertEquals(60, stats.get(0).bytes());

        assertEquals("20260610", stats.get(1).name());
        assertEquals(2, stats.get(1).images());
        assertEquals(1, stats.get(1).videos());
        assertEquals(600, stats.get(1).bytes());
        assertEquals(3, stats.get(1).totalFiles());

        assertEquals("根目录", stats.get(2).name());
        assertEquals(1, stats.get(2).audios());
        assertEquals(20, stats.get(2).bytes());
    }

    @Test
    @DisplayName("totals：汇总各列")
    void totals(@TempDir Path tmp) throws IOException {
        Path d = Files.createDirectories(tmp.resolve("20260101"));
        Files.writeString(d.resolve("a.jpg"), "x".repeat(10));
        Files.writeString(d.resolve("b.mp4"), "x".repeat(20));
        Files.writeString(d.resolve("c.wav"), "x".repeat(30));

        List<AssetScanner.FolderStat> stats = AssetScanner.scan(tmp);
        var t = AssetScanner.totals(stats);
        assertEquals(1, t.folders());
        assertEquals(1, t.images());
        assertEquals(1, t.videos());
        assertEquals(1, t.audios());
        assertEquals(60, t.bytes());
    }

    @Test
    @DisplayName("scan：目录不存在 / 空目录")
    void empty(@TempDir Path tmp) {
        assertTrue(AssetScanner.scan(Path.of("D:/__not_exists_asset__")).isEmpty());
        assertTrue(AssetScanner.scan(tmp).isEmpty());
        assertEquals(0, AssetScanner.totals(List.of()).folders());
    }

    @Test
    @DisplayName("扩展名分类：大小写不敏感")
    void extCase(@TempDir Path tmp) throws IOException {
        Path d = Files.createDirectories(tmp.resolve("20260101"));
        Files.writeString(d.resolve("A.JPG"), "x");
        Files.writeString(d.resolve("B.MP4"), "x");
        Files.writeString(d.resolve("C.FLAC"), "x");
        var stat = AssetScanner.scan(tmp).get(0);
        assertEquals(1, stat.images());
        assertEquals(1, stat.videos());
        assertEquals(1, stat.audios());
    }
}
