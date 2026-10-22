package cn.fmjj.toolbox.engine.store;

import cn.fmjj.toolbox.engine.platform.MediaItem;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** SQLite 持久化测试（@TempDir 独立库）。 */
class MediaStoreTest {

    private static MediaItem item(String platform, String id, String title) {
        return MediaItem.of(platform, id, title, "UP主", "https://cover/x.jpg",
                "2026-06-08 20:00:00", 3600, "简介", List.of(), "{}");
    }

    @Test
    @DisplayName("upsert / list / count：同平台同 ID 覆盖，不同平台独立")
    void crud(@TempDir Path tmp) {
        try (MediaStore store = new MediaStore(tmp.resolve("collected.db"))) {
            store.upsert(item("bilibili", "BV1", "标题一"), "/out/a.mp4");
            store.upsert(item("bilibili", "BV2", "标题二"), "");
            store.upsert(item("douyin", "aweme1", "抖音一"), "");

            assertEquals(3, store.count(null));
            assertEquals(2, store.count("bilibili"));
            assertEquals(1, store.count("douyin"));

            // 同 ID 再写 → 覆盖标题，不新增（id 不变，排序按 id DESC）
            store.upsert(item("bilibili", "BV1", "标题一（改名）"), "/out/a.mp4");
            assertEquals(2, store.count("bilibili"));
            List<MediaStore.Row> rows = store.list("bilibili", 0);
            MediaStore.Row bv1 = rows.stream().filter(r -> "BV1".equals(r.mediaId())).findFirst().orElseThrow();
            assertEquals("标题一（改名）", bv1.title());
            assertEquals(2, rows.size());
        }
    }

    @Test
    @DisplayName("list limit / delete / 空库")
    void limitAndDelete(@TempDir Path tmp) {
        try (MediaStore store = new MediaStore(tmp.resolve("x.db"))) {
            assertEquals(0, store.count(null));
            for (int i = 0; i < 5; i++) store.upsert(item("bilibili", "BV" + i, "t" + i), "");
            assertEquals(5, store.count("bilibili"));
            assertEquals(2, store.list("bilibili", 2).size());

            long id = store.list("bilibili", 1).get(0).id();
            assertTrue(store.delete(id));
            assertEquals(4, store.count("bilibili"));
        }
    }

    @Test
    @DisplayName("重复打开同一库不丢数据（建表幂等）")
    void reopen(@TempDir Path tmp) {
        Path db = tmp.resolve("y.db");
        try (MediaStore store = new MediaStore(db)) {
            store.upsert(item("weibo", "m1", "微博一"), "");
        }
        try (MediaStore store = new MediaStore(db)) {
            assertEquals(1, store.count("weibo"));
        }
    }
}
