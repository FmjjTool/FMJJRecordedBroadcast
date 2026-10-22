package cn.fmjj.toolbox.engine.platform.kuaishou;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.net.HttpClient;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 快手接口探测（默认跳过）：{@code FMJJ_NET_TEST=1 mvn test -Dtest=KuaishouProbeIT}
 *
 * 目的：确认 CookieStore 解出的明文 Cookie 能否让快手网页版返回 SSR 数据。
 */
class KuaishouProbeIT {

    @Test
    @DisplayName("解密 Cookie → 请求 short-video 页 → 检查 __APOLLO_STATE__")
    void probe() throws Exception {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")),
                "设置 FMJJ_NET_TEST=1 才跑");

        String cookie = CookieStore.get("kuaishou");
        System.out.println("  cookieLen=" + (cookie == null ? 0 : cookie.length()));
        if (cookie != null && !cookie.isEmpty()) {
            System.out.println("  cookieHead=" + cookie.substring(0, Math.min(60, cookie.length())));
            System.out.println("  looksEncrypted=" + cookie.startsWith("enc:"));
        }

        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", HttpClient.UA_CHROME);
        headers.put("Referer", "https://www.kuaishou.com/");
        headers.put("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
        headers.put("Accept-Language", "zh-CN,zh;q=0.9");
        if (cookie != null && !cookie.isEmpty()) headers.put("Cookie", cookie);

        String html = new HttpClient().getText("https://www.kuaishou.com/short-video/3xabcdefg", headers);
        System.out.println("  htmlLen=" + html.length());
        System.out.println("  hasApollo=" + html.contains("__APOLLO_STATE__"));
        System.out.println("  head=" + html.substring(0, Math.min(200, html.length())).replace('\n', ' '));
    }
}
