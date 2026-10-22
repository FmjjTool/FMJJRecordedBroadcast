package cn.fmjj.toolbox.engine.store;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.engine.platform.MediaItem;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

/**
 * 已获取数据持久化（SQLite）。
 * 对应 Python 版「已获取」页的增量数据库（accounts/increment_db）。
 *
 * 表结构（media）：platform + media_id 唯一，重复解析走 UPSERT 更新。
 */
public final class MediaStore implements AutoCloseable {

    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /** 列表行。 */
    public record Row(long id, String platform, String mediaId, String title, String author,
                      String publishTime, int duration, String savedPath, String createdAt) {}

    private final Connection conn;

    public MediaStore() {
        this(AppPaths.collectedDbFile());
    }

    public MediaStore(Path dbFile) {
        try {
            if (dbFile.getParent() != null) java.nio.file.Files.createDirectories(dbFile.getParent());
            conn = DriverManager.getConnection("jdbc:sqlite:" + dbFile.toAbsolutePath());
            init();
        } catch (Exception e) {
            throw new IllegalStateException("打开数据库失败：" + e.getMessage(), e);
        }
    }

    private void init() throws SQLException {
        try (Statement st = conn.createStatement()) {
            st.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL,
                        media_id TEXT NOT NULL,
                        title TEXT,
                        author TEXT,
                        cover TEXT,
                        publish_time TEXT,
                        duration INTEGER,
                        saved_path TEXT,
                        raw_json TEXT,
                        created_at TEXT,
                        UNIQUE(platform, media_id)
                    )""");
            st.executeUpdate("CREATE INDEX IF NOT EXISTS idx_media_platform ON media(platform, id DESC)");
        }
    }

    /** 写入/更新一条（Python 增量库语义：同平台同 ID 覆盖）。 */
    public synchronized void upsert(MediaItem item, String savedPath) {
        String now = LocalDateTime.now().format(TS);
        String sql = """
                INSERT INTO media (platform, media_id, title, author, cover, publish_time, duration,
                                   saved_path, raw_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform, media_id) DO UPDATE SET
                    title=excluded.title, author=excluded.author, cover=excluded.cover,
                    publish_time=excluded.publish_time, duration=excluded.duration,
                    saved_path=excluded.saved_path, raw_json=excluded.raw_json""";
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setString(1, item.platform());
            ps.setString(2, item.mediaId());
            ps.setString(3, item.title());
            ps.setString(4, item.author());
            ps.setString(5, item.cover());
            ps.setString(6, item.publishTime());
            ps.setInt(7, item.durationSeconds());
            ps.setString(8, savedPath == null ? "" : savedPath);
            ps.setString(9, item.rawJson() == null ? "" : item.rawJson());
            ps.setString(10, now);
            ps.executeUpdate();
        } catch (SQLException e) {
            throw new IllegalStateException("写入失败：" + e.getMessage(), e);
        }
    }

    /** 列表（platform 为空 = 全部；limit<=0 = 不限）。 */
    public synchronized List<Row> list(String platform, int limit) {
        String sql = "SELECT id, platform, media_id, title, author, publish_time, duration, saved_path, created_at"
                + " FROM media"
                + (platform == null || platform.isBlank() ? "" : " WHERE platform = ?")
                + " ORDER BY id DESC"
                + (limit > 0 ? " LIMIT " + limit : "");
        List<Row> rows = new ArrayList<>();
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            if (platform != null && !platform.isBlank()) ps.setString(1, platform);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    rows.add(new Row(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4),
                            rs.getString(5), rs.getString(6), rs.getInt(7), rs.getString(8), rs.getString(9)));
                }
            }
        } catch (SQLException e) {
            throw new IllegalStateException("查询失败：" + e.getMessage(), e);
        }
        return rows;
    }

    /** 计数（platform 为空 = 全部）。 */
    public synchronized int count(String platform) {
        String sql = "SELECT COUNT(*) FROM media"
                + (platform == null || platform.isBlank() ? "" : " WHERE platform = ?");
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            if (platform != null && !platform.isBlank()) ps.setString(1, platform);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getInt(1) : 0;
            }
        } catch (SQLException e) {
            return 0;
        }
    }

    /** 删除一条。 */
    public synchronized boolean delete(long id) {
        try (PreparedStatement ps = conn.prepareStatement("DELETE FROM media WHERE id = ?")) {
            ps.setLong(1, id);
            return ps.executeUpdate() > 0;
        } catch (SQLException e) {
            return false;
        }
    }

    @Override
    public synchronized void close() {
        try {
            conn.close();
        } catch (SQLException ignored) {
        }
    }
}
