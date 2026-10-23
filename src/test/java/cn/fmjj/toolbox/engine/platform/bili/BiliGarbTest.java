package cn.fmjj.toolbox.engine.platform.bili;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 装扮搜索解析测试（结构取自真实接口：条目在 data.list，不是 data.items）。 */
class BiliGarbTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    @DisplayName("parseSearch：从 data.list 取条目，dlc_act_id 优先作为跳转键")
    void parseSearch() throws Exception {
        String json = """
                {"list":[
                  {"name":"装扮收藏集-同人-某UP主","item_id":"113100","group_name":"装扮收藏集-同人",
                   "properties":{"dlc_act_id":"113100"}},
                  {"name":"某装扮","item_id":"336202401","group_name":"装扮",
                   "properties":{}},
                  {"name":"","item_id":""}
                ],"pn":1,"ps":20,"total":2}
                """;
        var items = BiliGarb.parseSearch(JSON.readTree(json));
        assertEquals(2, items.size(), "空条目应被跳过");
        assertEquals("装扮收藏集-同人-某UP主", items.get(0).name());
        assertEquals("113100", items.get(0).itemId());
        assertEquals("113100", items.get(0).jumpKey(), "有 dlc_act_id 时用它跳转");

        assertEquals("336202401", items.get(1).itemId());
        assertEquals("336202401", items.get(1).jumpKey(), "无 dlc_act_id 时回退 item_id");
    }

    @Test
    @DisplayName("parseSearch：空数据不崩")
    void empty() throws Exception {
        assertTrue(BiliGarb.parseSearch(JSON.readTree("{}")).isEmpty());
        assertTrue(BiliGarb.parseSearch(JSON.readTree("{\"list\":[]}")).isEmpty());
    }

    @Test
    @DisplayName("parseResources：按类别抽资源，只认图片/视频 URL")
    void parseResources() throws Exception {
        String json = """
                {"card":[{"name":"桃味白菜","properties":{
                    "image":"//i0.hdslb.com/bfs/garb/a.png",
                    "fans_image":"https://i0.hdslb.com/bfs/garb/b.png",
                    "fan_no_color":"#FF0000",
                    "package_md5":"abc123"}}],
                 "space_bg":[{"name":"背景","properties":{
                    "image1_landscape":"//i0.hdslb.com/bfs/garb/c.jpg",
                    "space_1_mp4_horizontal":"//i0.hdslb.com/bfs/garb/d.mp4"}}],
                 "thumbup":[{"name":"点赞","properties":{"image_ani":"//i0.hdslb.com/x.gif"}}]}
                """;
        var list = BiliGarb.parseResources(JSON.readTree(json));
        // card 2 张图 + space_bg 1 图 1 视频 + thumbup 1 gif = 5
        assertEquals(5, list.size(), "颜色和 md5 应被排除：" + list);
        assertTrue(list.stream().allMatch(r -> r.url().startsWith("https://")), "// 应补 https");
        assertTrue(list.stream().anyMatch(r -> r.categoryCn().equals("空间背景")));
        assertTrue(list.stream().anyMatch(r -> r.url().endsWith(".mp4")));
    }

    @Test
    @DisplayName("categoryName：8 类中文名")
    void categoryName() {
        assertEquals("卡片", BiliGarb.categoryName("card"));
        assertEquals("表情包", BiliGarb.categoryName("emoji_package"));
        assertEquals("点赞动画", BiliGarb.categoryName("thumbup"));
        assertEquals("unknown", BiliGarb.categoryName("unknown"));
    }

    @Test
    @DisplayName("parseAct：活动主图/横竖卡牌/卡池，非 URL 字段被排除")
    void parseAct() throws Exception {
        String json = """
                {"act_title":"封茗囧菌·不同的你",
                 "act_y_img":"https://i0.hdslb.com/bfs/garb/main.png",
                 "horizontal_card_light_url":"https://i0.hdslb.com/bfs/b/hl.png",
                 "horizontal_card_shadow_url":"https://i0.hdslb.com/bfs/garb/hs.png",
                 "vertical_card_light_url":"https://i0.hdslb.com/bfs/b/vl.png",
                 "vertical_card_shadow_url":"https://i0.hdslb.com/bfs/garb/vs.png",
                 "total_buy_cnt":123,
                 "animation_info":{"bg":"https://i0.hdslb.com/bfs/garb/anim.mp4"},
                 "lottery_list":[{"lottery_name":"不同的你",
                    "lottery_image":"https://i0.hdslb.com/bfs/garb/pool.png"}]}
                """;
        var info = BiliGarb.parseAct("113100", JSON.readTree(json));
        assertEquals("封茗囧菌·不同的你", info.title());
        assertEquals(7, info.resources().size(), "5 张卡牌/主图 + 1 动画 + 1 卡池：" + info.resources());
        assertTrue(info.resources().stream().anyMatch(r -> r.label().equals("活动主图")));
        assertTrue(info.resources().stream().anyMatch(r -> r.label().startsWith("卡池-")));
        assertTrue(info.resources().stream().anyMatch(r -> r.url().endsWith(".mp4")));
        assertTrue(info.resources().stream().allMatch(r -> r.url().startsWith("https://")));
    }

    @Test
    @DisplayName("parseAct：空数据不崩")
    void parseActEmpty() throws Exception {
        var info = BiliGarb.parseAct("1", JSON.readTree("{}"));
        assertEquals("", info.title());
        assertTrue(info.resources().isEmpty());
    }
}
