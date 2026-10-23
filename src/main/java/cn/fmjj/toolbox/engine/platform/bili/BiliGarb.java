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


    /** 装扮资源（按类别）。 */
    public record Resource(String category, String categoryCn, String name, String url) {}

    /** 类别中文名。 */
    public static String categoryName(String key) {
        return switch (key) {
            case "card" -> "卡片";
            case "card_bg" -> "卡片背景";
            case "emoji_package" -> "表情包";
            case "loading" -> "加载动画";
            case "play_icon" -> "播放图标";
            case "skin" -> "皮肤";
            case "space_bg" -> "空间背景";
            case "thumbup" -> "点赞动画";
            default -> key;
        };
    }

    /**
     * 从 suit_items 抽出全部资源 URL。
     *
     * <p>结构：`suit_items` 是对象，每个键是一类（card/card_bg/emoji_package/loading/
     * play_icon/skin/space_bg/thumbup），值是数组，元素含 name + properties；
     * 资源 URL 藏在 properties 的各种 `*image*` / `*url*` 字段里。
     */
    public static List<Resource> parseResources(JsonNode suitItems) {
        List<Resource> out = new ArrayList<>();
        var cats = suitItems.fieldNames();
        while (cats.hasNext()) {
            String cat = cats.next();
            String cn = categoryName(cat);
            JsonNode arr = suitItems.path(cat);
            if (!arr.isArray()) continue;
            for (JsonNode item : arr) {
                String name = item.path("name").asText("");
                JsonNode props = item.path("properties");
                var pf = props.fieldNames();
                while (pf.hasNext()) {
                    String key = pf.next();
                    String val = props.path(key).asText("");
                    if (!isResourceUrl(val)) continue;
                    out.add(new Resource(cat, cn, name.isEmpty() ? cat : name, norm(val)));
                }
            }
        }
        return out;
    }

    /** 只认图片/视频 URL，排除 md5、颜色等。 */
    private static boolean isResourceUrl(String v) {
        if (v == null || v.isEmpty()) return false;
        String u = v.startsWith("//") ? "https:" + v : v;
        if (!u.startsWith("http")) return false;
        String lower = u.toLowerCase(java.util.Locale.ROOT);
        return lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg")
                || lower.endsWith(".gif") || lower.endsWith(".webp")
                || lower.endsWith(".mp4") || lower.contains(".png?") || lower.contains(".jpg?");
    }

    private static String norm(String u) {
        return u.startsWith("//") ? "https:" + u : u;
    }

    /** 取装扮详情 + 资源清单。 */
    public record SuitDetail(String itemId, String name, List<Resource> resources) {}

    public SuitDetail detail(String itemId, String cookie) throws Exception {
        JsonNode data = suitDetail(itemId, cookie);
        return new SuitDetail(itemId, data.path("name").asText(""),
                parseResources(data.path("suit_items")));
    }


    // ==================== 收藏集 ====================

    /** 收藏集活动资源。 */
    public record ActResource(String label, String url) {}

    /** 收藏集活动信息。 */
    public record ActInfo(String actId, String title, List<ActResource> resources) {}

    private static final String ACT_BASIC_API = "https://api.bilibili.com/x/vas/dlc_act/act/basic";

    /** 取收藏集活动（活动视觉 + 卡池图）。 */
    public ActInfo fetchAct(String actId, String cookie) throws Exception {
        JsonNode d = http.getJson(ACT_BASIC_API + "?act_id=" + HttpClient.urlEncode(actId),
                headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("收藏集加载失败：" + d.path("message").asText());
        }
        return parseAct(actId, d.path("data"));
    }

    /** 纯解析（可离线测试）。 */
    public static ActInfo parseAct(String actId, JsonNode data) {
        List<ActResource> out = new ArrayList<>();
        String title = data.path("act_title").asText("");

        // 活动视觉（字段名自带语义，直接标注）
        addIfUrl(out, "活动主图", data.path("act_y_img").asText(""));
        addIfUrl(out, "横版卡牌(亮)", data.path("horizontal_card_light_url").asText(""));
        addIfUrl(out, "横版卡牌(暗)", data.path("horizontal_card_shadow_url").asText(""));
        addIfUrl(out, "竖版卡牌(亮)", data.path("vertical_card_light_url").asText(""));
        addIfUrl(out, "竖版卡牌(暗)", data.path("vertical_card_shadow_url").asText(""));

        // 动画信息里的图
        JsonNode anim = data.path("animation_info");
        if (anim.isObject()) {
            var f = anim.fieldNames();
            while (f.hasNext()) {
                String k = f.next();
                addIfUrl(out, "动画-" + k, anim.path(k).asText(""));
            }
        }

        // 卡池（每个 lottery 的封面 + 名称）
        JsonNode lotteries = data.path("lottery_list");
        if (lotteries.isArray()) {
            for (JsonNode l : lotteries) {
                String name = l.path("lottery_name").asText("");
                if (name.isEmpty()) name = l.path("goods_name").asText("卡池");
                addIfUrl(out, "卡池-" + name, l.path("lottery_image").asText(""));
            }
        }
        return new ActInfo(actId, title, out);
    }

    private static void addIfUrl(List<ActResource> out, String label, String url) {
        if (isResourceUrl(url)) out.add(new ActResource(label, norm(url)));
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
