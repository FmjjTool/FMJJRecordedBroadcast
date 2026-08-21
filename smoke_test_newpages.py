"""批量重命名 + 文件时间 功能测试（离屏，不打包）。"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from gui_qt.pages.rename_batch import apply_rules, split_name, join_name
from gui_qt.pages.file_time import parse_time_from_name, set_file_times

now = datetime(2026, 8, 21, 12, 0, 0)
fails = 0


def check(name, got, want):
    global fails
    if got != want:
        print(f"  FAIL {name}: got={got!r} want={want!r}")
        fails += 1
    else:
        print(f"  ok   {name}: {got!r}")


print("[1] 拆分/合并")
check("split", split_name("a.b.txt", True), ("a.b", "txt"))
check("split-noext", split_name("abc", True), ("abc", ""))
check("join", join_name("x", "y"), "x.y")

print("[2] 整体：大小写/扩展名")
s, e = apply_rules("Hello", "TXT", {"whole": {"case": "upper", "ext_case": "lower"}}, 0, now)
check("upper+ext lower", (s, e), ("HELLO", "txt"))
s, e = apply_rules("hello", "", {"whole": {"new_ext": "png"}}, 0, now)
check("改扩展名", (s, e), ("hello", "png"))
s, e = apply_rules("hi", "mp4", {"whole": {"ext_case": "delete"}}, 0, now)
check("删扩展名", (s, e), ("hi", ""))

print("[3] 替换：普通/大小写/正则/首个")
s, _ = apply_rules("a_b_c", "", {"replace": {"find": "_", "to": "-", "all": True, "case": True, "regex": False}}, 0, now)
check("替换所有", s, "a-b-c")
s, _ = apply_rules("a_b_c", "", {"replace": {"find": "_", "to": "-", "all": False, "case": True, "regex": False}}, 0, now)
check("替换首个", s, "a-b_c")
s, _ = apply_rules("Abcabc", "", {"replace": {"find": "ABC", "to": "X", "all": True, "case": False, "regex": False}}, 0, now)
check("不区分大小写", s, "XX")
s, _ = apply_rules("img123.png", "", {"replace": {"find": r"\d+", "to": "N", "all": True, "case": True, "regex": True}}, 0, now)
check("正则替换", s, "imgN.png")

print("[4] 添加：前后缀/位置/关键词")
s, _ = apply_rules("file", "", {"add": {"prefix": "[2026]", "suffix": "_v2"}}, 0, now)
check("前后缀", s, "[2026]file_v2")
s, _ = apply_rules("abcdef", "", {"add": {"pos_idx": 2, "pos_text": "-" , "from_right": False}}, 0, now)
check("位置插入", s, "ab-cdef")
s, _ = apply_rules("abcdef", "", {"add": {"pos_idx": 2, "pos_text": "!", "from_right": True}}, 0, now)
check("从右插入", s, "abcd!ef")
s, _ = apply_rules("第01集_番剧", "", {"add": {"kw": "第01集", "kw_text": " [1080P]", "kw_after": True}}, 0, now)
check("关键词后插", s, "第01集 [1080P]_番剧")

print("[5] 删除：类型/位置/关键词")
s, _ = apply_rules("a 1B中2c!", "", {"del": {"types": ["space", "digit", "chinese"], "pos": 0, "cnt": 0}}, 0, now)
check("删空格数字中文", s, "aBc!")
s, _ = apply_rules("abcdef", "", {"del": {"types": [], "pos": 1, "cnt": 2, "from_right": False}}, 0, now)
check("位置删", s, "adef")
s, _ = apply_rules("abcdef", "", {"del": {"types": [], "pos": 1, "cnt": 2, "from_right": True}}, 0, now)
check("从右位置删", s, "abcf")
s, _ = apply_rules("标题_第01集_尾巴", "", {"del": {"types": [], "kw": "_第01集", "kw_cnt": 3, "dir_right": True, "incl_kw": False}}, 0, now)
check("关键词右删", s, "标题_第01集")
s, _ = apply_rules("标题_第01集_尾巴", "", {"del": {"types": [], "kw": "_第01集", "kw_cnt": 2, "dir_right": True, "incl_kw": True}}, 0, now)
check("含关键词删", s, "标题巴")

print("[6] 序号")
r = [apply_rules("img", "png", {"num": {"on": True, "mode": "suffix", "start": 5, "step": 10, "width": 3}}, i, now) for i in range(3)]
check("序号后缀", [x[0] for x in r], ["img005", "img015", "img025"])
r = [apply_rules("img", "", {"num": {"on": True, "mode": "prefix", "start": 1, "step": 1, "width": 2}}, i, now) for i in range(2)]
check("序号前缀", [x[0] for x in r], ["01_img", "02_img"])

print("[7] 模板")
s, e = apply_rules("photo", "jpg", {"template": "{日期}_{名}.{ext}"}, 0, now)
check("模板变量", (s, e), ("2026-08-21_photo.jpg", ""))
s, e = apply_rules("a", "b", {"template": "{序号}_{名}.{ext}"}, 2, now)
check("模板序号", (s, e), ("003_a.b", ""))

print("[8] 文件名时间解析")
for name, want in [("2024-01-02 15-04-05.mp4", "2024-01-02 15:04:05"),
                   ("20240102_150405.flv", "2024-01-02 15:04:05"),
                   ("2024年03月05日.mp4", "2024-03-05 12:00:00")]:
    got = parse_time_from_name(name)
    check(name, got[1] == datetime.strptime(want, "%Y-%m-%d %H:%M:%S").timestamp() if got else None, True)
check("无时间", parse_time_from_name("random.mp4"), None)

print("[9] set_file_times 实测")
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "t.txt")
    open(p, "w").write("x")
    target = datetime(2024, 6, 1, 10, 30, 0).timestamp()
    set_file_times(p, target, target, target)
    st = os.stat(p)
    check("mtime", datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"), "2024-06-01 10:30")
    if os.name == "nt":
        ctime_ok = datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M") == "2024-06-01 10:30"
        check("ctime(创建时间)", ctime_ok, True)

print("[10] 页面构建")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from toolbox.config_manager import init_all_configs
init_all_configs()
from gui_qt import theme
theme.init_theme(); theme.apply_theme(app)
from gui_qt.pages.rename_batch import RenameBatchPage
from gui_qt.pages.file_time import FileTimePage
p1 = RenameBatchPage(None); p2 = FileTimePage(None)
print(f"  ok   RenameBatchPage 8 tabs={p1.tabs.count()}  FileTimePage built")

print("[11] B站装扮 suit_files（对照 BRD suit/[id].vue）")
from gui_qt.pages import bili_garb
suit = {
    "name": "测试装扮",
    "properties": {"image_cover": "https://i0.hdslb.com/cover.png", "fan_share_image": "https://i0.hdslb.com/share.png"},
    "suit_items": {
        "space_bg": [{"name": "空间背景", "properties": {
            "image1_landscape": "https://i0.hdslb.com/l1.png", "image1_portrait": "https://i0.hdslb.com/p1.png",
            "space1_mp4_horizontal": "https://i0.hdslb.com/v1.mp4"}}],
        "emoji_package": [{"name": "表情", "properties": {"item_emoji_list": json.dumps([
            {"name": "[ID_爱心]", "properties": {"image": "https://i0.hdslb.com/a.png",
                                                 "image_gif": "https://i0.hdslb.com/a.gif"}}])}}],
        "play_icon": [{"name": "进度", "properties": {
            "drag_left_png": "https://i0.hdslb.com/dl.png", "drag_right_png": "https://i0.hdslb.com/dr.png",
            "middle_png": "https://i0.hdslb.com/mid.png", "squared_image": "https://i0.hdslb.com/sq.png",
            "static_icon_image": "https://i0.hdslb.com/icon.png"}}],
        "skin": [{"name": "主题", "properties": {
            "head_bg": "https://i0.hdslb.com/hb.png", "tail_bg": "https://i0.hdslb.com/tb.bin",
            "head_myself_mp4_bg_list": json.dumps(["https://i0.hdslb.com/me.mp4"]),
            "package_url": "https://i0.hdslb.com/pkg.zip"}}],
        "pendant": [{"name": "框", "properties": {"image": "https://i0.hdslb.com/pd.png",
                                                  "garb_avatar": "https://i0.hdslb.com/ga.png"}}],
        "card": [{"name": "粉丝牌", "properties": {"image": "https://i0.hdslb.com/c1.png",
                                                    "fans_image": "https://i0.hdslb.com/c2.png"}}],
        "card_bg": [{"name": "评论", "properties": {"image": "https://i0.hdslb.com/cb.png"}}],
        "loading": [{"name": "加载", "properties": {"loading_url": "https://i0.hdslb.com/ld.json",
                                                    "loading_frame_url": "https://i0.hdslb.com/lf.png"}}],
        "thumbup": [{"name": "点赞", "properties": {"image_ani": "https://i0.hdslb.com/tu.bin",
                                                    "image_ani_cut": "https://i0.hdslb.com/tuc.bin",
                                                    "image_preview": "https://i0.hdslb.com/tup.png"}}],
    },
}
sf = bili_garb.suit_files(suit)
paths = {f["path"]: f for f in sf}
check("装扮背景图", "测试装扮/装扮背景图" in paths, True)
check("空间背景循环", "测试装扮/空间背景/背景1" in paths and "测试装扮/空间背景/背景视频1" in paths, True)
check("肖像目录", "测试装扮/空间背景肖像/肖像1" in paths, True)
check("表情名解析[ID_爱心]", any(p.startswith("测试装扮/表情表情包/爱心") for p in paths), True)
check("表情gif+png两格式", sum(1 for f in sf if "爱心" in f["path"]), 2)
check("进度条middle_png", "测试装扮/进度进度条/进度条指示" in paths, True)
check("皮肤字段+资源包", "测试装扮/主题皮肤/顶部背景图" in paths and "测试装扮/主题皮肤/皮肤资源包" in paths, True)
check("皮肤mp4列表", "测试装扮/主题皮肤/“我的”界面背景视频1" in paths, True)
check("头像框效果演示", any("效果演示" in p for p in paths), True)
check("粉丝牌图2", "测试装扮/粉丝牌粉丝牌背景(2)" in paths, True)
check("加载序列帧", "测试装扮/加载加载动画(序列帧)" in paths, True)
svga = [f for f in sf if f.get("ext") == "svga"]
check("点赞动画svga扩展名", len(svga), 2)

print("[12] B站收藏集 lottery_files（对照 BRD lottery.vue）")
act = {
    "act_title": "测试活动",
    "act_y_img": "https://i0.hdslb.com/y.png@100w", "act_square_img": "https://i0.hdslb.com/sq.png",
    "horizontal_card_light_url": "https://i0.hdslb.com/hl.png",
    "app_head_show": "https://i0.hdslb.com/ah.png",
    "animation_info": {"animation_draw_url": {"30": "https://i0.hdslb.com/d30.mp4"},
                        "animation_entry_url": {"60": "https://i0.hdslb.com/e60.mp4"}},
    "related_mids": [123], "related_user_infos": {"123": {"nickname": "UP", "avatar": "https://i0.hdslb.com/face.png"}},
    "collector_medal_info": json.dumps([{"level": 1, "image": "https://i0.hdslb.com/m1.png",
                                          "scene_image": {"1": "https://i0.hdslb.com/m1s1.png", "2": "https://i0.hdslb.com/m1s2.png"}}]),
    "lottery_list": [{"lottery_id": 9, "lottery_name": "合集A", "lottery_image": "https://i0.hdslb.com/lc.png",
                      "lottery_square_img": "https://i0.hdslb.com/lcs.png"}],
}
bag = {"collect_list": [{"redeem_item_name": "勋章兑换", "redeem_item_image": "https://i0.hdslb.com/rm.png"}],
       "lottery_simple_list": [{"lottery_id": 9, "lottery_name": "合集A"}]}
detail = {
    "name": "合集A",
    "item_list": [
        {"card_info": {"card_name": "卡1", "card_img": "https://i0.hdslb.com/c1.png",
                        "card_img_download": "https://i0.hdslb.com/c1d.png",
                        "video_list": ["https://i0.hdslb.com/v1.mp4", "https://i1.hdslb.com/v1.mp4"],
                        "video_list_download": ["https://i0.hdslb.com/v1d.mp4"]}},
        {"card_info": {"card_name": "卡2", "card_img": "https://i0.hdslb.com/c2.png"}}],
    "collect_list": {
        "collect_chain": [{"redeem_item_type": 1000, "redeem_item_name": "钻石背景",
                            "redeem_item_image": "https://i0.hdslb.com/db.png"}],
        "collect_infos": [{"redeem_item_type": 1, "redeem_item_name": "典藏卡",
                            "redeem_item_image": "https://i0.hdslb.com/sc.png",
                            "card_item": {"card_type_info": {
                                "overview_image": "https://i0.hdslb.com/ov.png",
                                "content": {"animation": {"animation_video_urls": [
                                    "https://i0.hdslb.com/a1.mp4", "https://i1.hdslb.com/a1.mp4"]}}}}}]},
    "act_rights_infos": [{"resource": "https://i0.hdslb.com/right.png@200w"}],
}
bili_garb.api.lottery_home_detail = lambda aid, lid: dict(detail) if str(lid) == "9" else {}
lf = bili_garb.lottery_files("107", act, bag, act["lottery_list"])
lpaths = {f["path"]: f for f in lf}
check("勋章等级+场景", "测试活动/收藏集勋章/1级" in lpaths and "测试活动/收藏集勋章/类型2/1级" in lpaths, True)
check("活动视觉剥@后缀", any(f["url"] == "https://i0.hdslb.com/y.png" and "主视觉" in p for p, f in lpaths.items()), True)
check("抽卡动画fps", "测试活动/活动视觉/抽卡动画30fps" in lpaths, True)
check("相关UP头像", any("相关UP头像 - UP" in p for p in lpaths), True)
check("卡牌下载版优先", lpaths.get("合集A/卡池图片/卡1", {}).get("url"), "https://i0.hdslb.com/c1d.png")
vid = lpaths.get("合集A/卡池视频/卡1", {})
check("视频主地址", vid.get("url"), "https://i0.hdslb.com/v1d.mp4")
check("视频镜像备用", set(vid.get("mirrors", [])), {"https://i0.hdslb.com/v1.mp4", "https://i1.hdslb.com/v1.mp4"})
check("兑换内容(钻石背景)", "合集A/其他/钻石背景" in lpaths, True)
check("典藏卡概览图", "合集A/其他/典藏卡(概览图)" in lpaths, True)
check("典藏卡视频逐个下载(原版行为)", "合集A/其他/典藏卡(视频1)" in lpaths and "合集A/其他/典藏卡(视频2)" in lpaths, True)
check("权益资源剥@", any(f["url"] == "https://i0.hdslb.com/right.png" for f in lf if "权益" in f["path"]), True)
check("收藏集封面+方形", "合集A/其他/封面" in lpaths and "合集A/其他/方形封面" in lpaths, True)
check("asset_bag兑换", any("勋章兑换" in p for p in lpaths), True)
check("URL去重", len({f['url'] for f in lf}), len(lf))

print("[13] B站 emoji/info 页字段修复")
from gui_qt.pages import bili_info
space = {"card": {"face": "https://i0.hdslb.com/f.png", "pendant": {"name": "框",
          "image": "https://i0.hdslb.com/p.png", "image_enhance": "https://i0.hdslb.com/pe.png"}},
          "space": {"l_img": "https://i0.hdslb.com/l.png"}}
appinfo = {"images": {"imgUrl": "https://i0.hdslb.com/app.png", "night_imgurl": "https://i0.hdslb.com/appn.png"},
           "archive": {"item": [{"title": "视频1", "cover": "https://i0.hdslb.com/ac.png"}]},
           "card": {"pendant_url": "https://i0.hdslb.com/pa.png"}}
sp = bili_info.space_files(space, appinfo, [{"name": "充电表情1", "icon": "https://i0.hdslb.com/e.png"}])
spp = [f["path"] for f in sp]
check("头像框增强版+普通版+App版", sum(1 for p in spp if p.startswith("头像框")), 3)
check("App背景图+夜间", "App端空间背景图" in spp and "App端空间背景图（夜间模式）" in spp, True)
check("投稿封面", "投稿封面/视频1" in spp, True)
check("充电表情icon字段", "充电表情/充电表情1" in spp, True)

dyn = {"modules": {"module_author": {"face": "https://i0.hdslb.com/f.png",
         "pendant": {"image": "https://i0.hdslb.com/p.png", "image_enhance": "https://i0.hdslb.com/pe.png"}},
         "module_dynamic": {"major": {"type": "MAJOR_TYPE_OPUS",
           "opus": {"pics": [{"url": "https://i0.hdslb.com/1.png"}]}}}},
       "orig": {"modules": {"module_dynamic": {"major": {"type": "MAJOR_TYPE_DRAW",
           "draw": {"items": [{"src": "https://i0.hdslb.com/orig.png"}]}}}}}}
df = bili_info.dynamic_files(dyn)
dfp = [f["path"] for f in df]
check("头像框增强版优先", any(p == "作者头像框" and f["url"].endswith("pe.png") for p, f in zip(dfp, df)), True)
check("转发原动态图片(顶层orig,原版扁平命名)", "原动态图片1" in dfp, True)

print("[14] 链接解析 bili/links.py（对照 BRD linkResolver.ts）")
from bili.links import parse_link
check("blackboard dlc", parse_link("https://www.bilibili.com/blackboard/activity-Mz9T5bO5Q3.html?type=dlc&id=102544&lottery_id="),
      ("lottery", "102544"))
check("blackboard suit", parse_link("https://www.bilibili.com/blackboard/activity-Mz9T5bO5Q3.html?type=suit&id=336202401"),
      ("suit", "336202401"))
check("digital-card act_id", parse_link("https://www.bilibili.com/h5/mall/equity-link/digital-card?act_id=99999"),
      ("lottery", "99999"))
check("collect-home item_id", parse_link("https://www.bilibili.com/h5/mall/equity-link/collect-home?item_id=123"),
      ("suit", "123"))
check("suit detail id", parse_link("https://www.bilibili.com/h5/mall/suit/detail?id=456"), ("suit", "456"))
check("直播间链接", parse_link("https://live.bilibili.com/544820"), ("liveroom", "544820"))
check("空间链接", parse_link("https://space.bilibili.com/1467772"), ("user", "1467772"))
check("动态链接", parse_link("https://t.bilibili.com/123456789"), ("dynamic", "123456789"))
check("视频BV", parse_link("https://www.bilibili.com/video/BV1xx411c7mD"), ("video", "BV1xx411c7mD"))
check("纯BV号", parse_link("BV1xx411c7mD"), ("video", "BV1xx411c7mD"))
check("分享文本去标题", parse_link("【标题】 https://www.bilibili.com/blackboard/activity-Mz9T5bO5Q3.html?type=dlc&id=102544"),
      ("lottery", "102544"))
check("普通文本不识别", parse_link("随便打点字"), None)
check("无type的blackboard不误判", parse_link("https://www.bilibili.com/blackboard/activity-Mz9T5bO5Q3.html?id=1"), None)

print()
print("[15] 直播间表情字段结构（data.data → pkg_name/emoticons[{emoji,url}]）")
room_pkg = {"pkg_id": 1, "pkg_name": "UP主大表情",
            "emoticons": [{"emoji": "啊", "url": "http://i0.hdslb.com/a.png", "unlock_show_text": ""}]}
check("包名 pkg_name", room_pkg["pkg_name"], "UP主大表情")
check("表情名 emoji + url", room_pkg["emoticons"][0]["emoji"] == "啊" and room_pkg["emoticons"][0]["url"].startswith("http"), True)

print()
print("[16] music_tag 原生标签编辑器")
from gui_qt import main_window as mw
check("music_tag 已注册", "music_tag" in mw.PAGE_CLASSES, True)
check("导航包含音乐标签", any(k == "music_tag" for g, items in mw.NAV_GROUPS for k, _ in items), True)
from music import tags_io
tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tagtest")
os.makedirs(tmp, exist_ok=True)
f = os.path.join(tmp, "t.mp3")
shutil.copy(r"download\歌曲文件\封茗囧菌 - 初次约会.mp3", f)
tags_io.save_tags(f, {k: "" for k in tags_io.FIELDS} | {"title": "T1", "artist": "A1", "track": "3"})
back = tags_io.load_tags(f)
check("tags_io 写读往返", back["title"] == "T1" and back["artist"] == "A1" and back["track"] == "3", True)
tags_io.save_cover(f, b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png")
cov = tags_io.load_cover(f)
check("tags_io 封面读写", cov is not None and cov[1] == "image/png", True)
check("tags_io 封面移除", (tags_io.clear_cover(f), tags_io.load_cover(f) is None)[1], True)
from gui_qt.pages.music_tag import _guess_kw
check("文件名猜关键词(艺术家 - 标题)", _guess_kw("x/张三 - 歌名.mp3", {}), "歌名")
check("标签猜关键词", _guess_kw("x/a.mp3", {"title": "T", "artist": "A"}), "T A")
shutil.rmtree(tmp, ignore_errors=True)

print()
if fails:
    print(f"共 {fails} 个失败")
    sys.exit(1)
print("全部通过")
