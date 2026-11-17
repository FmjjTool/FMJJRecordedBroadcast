package cn.fmjj.toolbox.engine.platform.xiaohongshu;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.net.HttpClient;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * 小红书笔记媒体探测（默认跳过）：{@code FMJJ_NET_TEST=1 mvn test -Dtest=XiaohongshuProbeIT}
 *
 * <p>验证 CDP + 登录 Cookie 能否拿到 og:video / 正文图片，并真实下载视频与图片。
 * 要复用真实登录态，额外传 {@code -Dfmjj.home=<项目根>}。
 */
class XiaohongshuProbeIT {

    /** 视频笔记。 */
    private static final String VIDEO_NOTE =
            "https://www.xiaohongshu.com/explore/6adb6fa7000000003301268c"
                    + "?xsec_token=ABMtnotzw00vLEJkFNhVqsopTD416pPTa5BX3d_Dd7YaE=&xsec_source=pc_feed";

    /** 多图笔记。 */
    private static final String IMAGE_NOTE =
            "https://www.xiaohongshu.com/explore/6abf5565000000001c02ef8c"
                    + "?xsec_token=ABA_DbujqmCHYlfTj8R8mbH6V2cmEzWY-ee2sVe6KTHCw=&xsec_source=pc_feed";

    @Test
    @DisplayName("CDP 打开笔记页 → og:video/正文图片 → 下载")
    void probe() throws Exception {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")),
                "设置 FMJJ_NET_TEST=1 才跑");

        String cookie = System.getenv().getOrDefault("FMJJ_XHS_COOKIE", CookieStore.get("xiaohongshu"));
        System.out.println("  appRoot=" + cn.fmjj.toolbox.core.AppPaths.appRoot());
        System.out.println("  cookieLen=" + (cookie == null ? 0 : cookie.length()));

        Path dump = Path.of("target", "xhs-probe.txt");
        Files.createDirectories(dump.getParent());
        StringBuilder sb = new StringBuilder();
        try {
            var feed = new XiaohongshuParser().exploreFeed(cookie);
            sb.append("feed=").append(feed.size()).append('\n');
            for (var n : feed) {
                sb.append("  ").append(n.type()).append(" | ").append(n.title()).append(" | ")
                        .append(n.id()).append(" | token=").append(n.xsecToken()).append('\n');
            }
        } catch (Exception e) {
            sb.append("feed error: ").append(e.getMessage()).append('\n');
        }

        XiaohongshuParser.NoteMedia video = null;
        XiaohongshuParser.NoteMedia images = null;
        for (String url : new String[] {VIDEO_NOTE, IMAGE_NOTE}) {
            sb.append("\n==== ").append(url).append('\n');
            XiaohongshuParser.NoteMedia media = new XiaohongshuParser().fetchNoteMedia(url, cookie);
            if (media == null) {
                sb.append("no media\n");
                continue;
            }
            sb.append("title=").append(media.title()).append('\n');
            sb.append("desc=").append(media.desc()).append('\n');
            sb.append("video=").append(media.videoUrl()).append('\n');
            sb.append("cover=").append(media.cover()).append('\n');
            sb.append("images=").append(media.images().size()).append('\n');
            for (String u : media.images()) sb.append("  ").append(u).append('\n');
            if (url.equals(VIDEO_NOTE)) video = media; else images = media;
        }
        Files.writeString(dump, sb.toString(), StandardCharsets.UTF_8);
        System.out.println("  dump=" + dump.toAbsolutePath());

        Assumptions.assumeTrue(video != null, "视频笔记没有返回媒体");
        Assumptions.assumeFalse(video.videoUrl().isBlank(), "视频笔记没有直链");
        Assumptions.assumeTrue(video.images().isEmpty(), "视频笔记不应混入图片：" + video.images());
        download("xhs-probe-", ".mp4", video.videoUrl(), 100_000);
        System.out.println("  video images=" + video.images().size());

        Assumptions.assumeTrue(images != null, "多图笔记没有返回媒体");
        Assumptions.assumeFalse(images.images().isEmpty(), "多图笔记没有图片");
        for (String u : images.images()) {
            Assumptions.assumeTrue(u.contains("xhscdn.com"), "图片不是小红书 CDN：" + u);
            Assumptions.assumeFalse(u.contains("sns-avatar") || u.contains("picasso-static"),
                    "混入了头像/图标：" + u);
        }
        download("xhs-probe-", ".webp", images.images().get(0), 10_000);
        System.out.println("  image note images=" + images.images().size());
    }

    private static void download(String prefix, String suffix, String url, long minSize) throws Exception {
        Path out = Files.createTempFile(prefix, suffix);
        var r = new FileDownloader().download(List.of(url), out,
                Map.of("Referer", "https://www.xiaohongshu.com/", "User-Agent", HttpClient.UA_CHROME),
                p -> { }, () -> false);
        long size = Files.size(out);
        System.out.println("  download=" + r.success() + " size=" + size + " url=" + url);
        Files.deleteIfExists(out);
        Assumptions.assumeTrue(r.success() && size > minSize, "下载失败或过小：" + url);
    }
}
