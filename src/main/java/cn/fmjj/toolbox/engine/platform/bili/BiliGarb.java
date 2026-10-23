package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * B站装扮 / 收藏集（对应 Python 版 bili_garb.py 的搜索与详情）。
 *
 * <p>接口（免签名）：
 * <ul>
 *   <li>搜索：{@code x/garb/v2/mall/home/search?key_word=&pn=} → {@code data.list}（扁平条目）</li>
 *   <li>详情：{@code x/garb/v2/user/suit/benefit?item_id=&part=cards}</li>
 *   <li>收藏集：{@code x/vas/dlc_act/act/basic?act_id=} / {@code asset_bag}</li>
 * </ul>
 */
public final class BiliGarb {

    private static final String SEARCH_API = "https://api.bilibili.com/x/garb/v2/mall/home/search";
    private static final String SUIT_API = "https://api.bilibili.com/x/garb/v2/user/suit/benefit";

    /** 搜索结果条目。 */
    public record Item(String name, String itemId, String groupName, String actId) {
        /** 收藏集要用 dlc_act_id 跳转（item_id 无效）。 */
        public String jumpKey() {
            return actId == null || actId.isEmpty() ? itemId : actId;
        }
    }

    private final HttpClient http = new HttpClient();

    private Map<String, String> headers(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", "https://www.bilibili.com/");
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    /** 装扮商城搜索（关键词 + 页码）。 */
    public List<Item> search(String keyword, int page, String cookie) throws Exception {
        String url = SEARCH_API + "?key_word=" + HttpClient.urlEncode(keyword) + "&pn=" + page;
        JsonNode d = http.getJson(url, headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("装扮搜索失败：" + d.path("message").asText());
        }
        return parseSearch(d.path("data"));
    }

    /** 纯解析（可离线测试）：注意条目在 data.list，不是 data.items。 */
    public static List<Item> parseSearch(JsonNode data) {
        List<Item> out = new ArrayList<>();
        JsonNode list = data.path("list");
        if (!list.isArray()) return out;
        for (JsonNode n : list) {
            String name = n.path("name").asText("");
            String itemId = n.path("item_id").asText("");
            if (name.isEmpty() && itemId.isEmpty()) continue;
            out.add(new Item(name, itemId,
                    n.path("group_name").asText(""),
                    n.path("properties").path("dlc_act_id").asText("")));
        }
        return out;
    }

    /** 装扮详情（suit_items 里含表情/头像框/粉丝牌等资源）。 */
    public JsonNode suitDetail(String itemId, String cookie) throws Exception {
        String url = SUIT_API + "?item_id=" + HttpClient.urlEncode(itemId) + "&part=cards";
        JsonNode d = http.getJson(url, headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("装扮详情失败：" + d.path("message").asText());
        }
        return d.path("data");
    }
}
