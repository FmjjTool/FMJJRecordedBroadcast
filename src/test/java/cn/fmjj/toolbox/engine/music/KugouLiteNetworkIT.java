package cn.fmjj.toolbox.engine.music;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 酷狗概念版网络探测（默认跳过）：{@code FMJJ_NET_TEST=1 mvn test -Dtest=KugouLiteNetworkIT}
 *
 * <p>只验证免登录部分：二维码生成 + 首次轮询。搜索/直链需要真实扫码登录。
 */
class KugouLiteNetworkIT {

    @Test
    @DisplayName("生成二维码 → 轮询等待扫码")
    void qrCreateAndPoll() throws Exception {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")),
                "设置 FMJJ_NET_TEST=1 才跑");

        KugouLiteScraper client = new KugouLiteScraper();
        KugouLiteScraper.QrCode qr = client.qrCreate();
        System.out.println("  keyLen=" + qr.key().length());
        System.out.println("  imageBytes=" + (qr.image() == null ? 0 : qr.image().length));
        System.out.println("  content=" + qr.content());
        Assumptions.assumeFalse(qr.key().isBlank(), "没有拿到 qrcode key");

        KugouLiteScraper.Poll poll = client.qrPoll(qr.key());
        System.out.println("  pollStatus=" + poll.status() + " msg=" + poll.message());
        Assumptions.assumeTrue(poll.status() == 1 || poll.status() == 2,
                "首次轮询状态异常：" + poll.status() + " " + poll.message());
    }

    @Test
    @DisplayName("导入会话 → 校验 → 搜索 → 取直链（需 FMJJ_LITE_SESSION）")
    void sessionSearchAndUrl() {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")),
                "设置 FMJJ_NET_TEST=1 才跑");
        String file = System.getenv("FMJJ_LITE_SESSION");
        Assumptions.assumeTrue(file != null && !file.isBlank(),
                "设置 FMJJ_LITE_SESSION=<会话 json 路径> 才跑");

        KugouLiteScraper.Session session = KugouLiteScraper.readSession(java.nio.file.Path.of(file));
        Assumptions.assumeTrue(session.loggedIn(), "会话文件没有 token");
        KugouLiteScraper client = new KugouLiteScraper(session);

        var verify = client.verifySession();
        System.out.println("  verify=" + verify.ok() + " " + verify.message());
        Assumptions.assumeTrue(verify.ok(), "会话已失效：" + verify.message());

        var songs = client.searchSong("霜雪千年", 1, 5);
        System.out.println("  songs=" + songs.size());
        Assumptions.assumeFalse(songs.isEmpty(), "搜索无结果");
        var first = songs.get(0);
        System.out.println("  first=" + first.songName() + " / " + first.singer() + " / " + first.songId());

        var info = client.playInfo(first.songId(), "128");
        System.out.println("  urlOk=" + (info.url() != null && !info.url().isBlank())
                + " ext=" + info.ext() + " err=" + info.error());
        Assumptions.assumeTrue(info.error().isEmpty() && !info.url().isBlank(), "直链失败：" + info.error());
    }
}
