package cn.fmjj.toolbox.engine.platform.douyin;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 抖音真实网络冒烟（默认跳过）。
 * 手动执行：{@code FMJJ_NET_TEST=1 mvn test -Dtest=DouyinNetworkIT}
 */
class DouyinNetworkIT {

    /** 实测样本（封茗囧菌 现场 桃花庵）。 */
    private static final String SAMPLE = "7415554945589513513";

    @Test
    @DisplayName("浏览器渲染 → 标题/作者/视频直链")
    void liveParse() throws Exception {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")),
                "设置 FMJJ_NET_TEST=1 才跑真实网络测试");
        Assumptions.assumeTrue(DouyinBrowser.available(), "本机没有 Edge/Chrome");

        long t0 = System.currentTimeMillis();
        var v = new DouyinParser().parse(SAMPLE);
        long ms = System.currentTimeMillis() - t0;

        System.out.println("  title=" + v.title());
        System.out.println("  author=" + v.author());
        System.out.println("  awemeId=" + v.awemeId());
        System.out.println("  videoUrl=" + v.videoUrl().substring(0, Math.min(110, v.videoUrl().length())));
        System.out.println("  耗时=" + ms + "ms");

        assertFalse(v.title().isEmpty(), "标题为空");
        assertTrue(v.videoUrl().startsWith("http"), "视频直链无效：" + v.videoUrl());
    }
}
