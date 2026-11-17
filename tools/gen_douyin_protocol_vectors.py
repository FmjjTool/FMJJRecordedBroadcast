"""生成抖音弹幕协议的跨语言测试向量（期望值来自 Python 原版运行时）。

用法：
    py -3.12 tools/gen_douyin_protocol_vectors.py

仅 **读取** Python 原版目录（默认 D:\\PythonApps\\FMJJRecordedBroadcast），
不写入、不修改任何文件。输出的 base64/hex 直接对应
src/test/java/.../DouyinLiveProtocolTest.java 中的字面量。
"""
import base64
import json
import sys

PY_SRC = r"D:\PythonApps\FMJJRecordedBroadcast\src"
sys.path.insert(0, PY_SRC)

from toolbox import douyin_live as d  # noqa: E402


def tag(field, wire):
    return d._enc_varint((field << 3) | wire)


def ld(field, value):
    if isinstance(value, str):
        value = value.encode()
    return tag(field, 2) + d._enc_varint(len(value)) + value


def vi(field, value):
    return tag(field, 0) + d._enc_varint(value)


def main():
    user = ld(3, "测试用户")
    chat_msg = ld(1, "WebcastChatMessage") + ld(2, ld(2, user) + ld(3, "你好世界"))

    gift_struct = vi(12, 5) + ld(16, "小心心") + ld(
        1, ld(1, "https://p3-webcastcdn.douyin.com/a.webp"))
    gift_msg = ld(1, "WebcastGiftMessage") + ld(2,
        ld(7, user) + vi(5, 3) + vi(6, 3) + vi(2, 3)
        + ld(15, gift_struct) + ld(19, ld(2, "送出了小心心 x3")))

    member_msg = ld(1, "WebcastMemberMessage") + ld(2, ld(2, user) + ld(11, "来了"))
    like_msg = ld(1, "WebcastLikeMessage") + ld(2, ld(5, user) + vi(2, 3) + vi(3, 100))
    social_msg = ld(1, "WebcastSocialMessage") + ld(2, ld(2, user) + vi(6, 1))
    roomseq_msg = ld(1, "WebcastRoomUserSeqMessage") + ld(2, vi(3, 1234) + vi(7, 9999) + ld(8, "10万+"))
    roomstats_msg = ld(1, "WebcastRoomStatsMessage") + ld(2, vi(5, 4321) + ld(3, "在线观众：4321"))

    banner_json = json.dumps({
        "25v_7_activity": {"title": "活动标题", "subtitle": "副标题",
                           "status": {"Name": "进行中"},
                           "_extra": {"rule": "规则文本"}},
        "9_1_gift_task": {"component_name": "gift_task", "task_items": [
            {"status": 1, "phases": [{"conditions": [{"current": 2, "target": 5}]}]}]},
        "3_2_fans": {"3_2_fans": {"foot": {"extra": {"selectors": ["百万粉团"]}}}},
    }, ensure_ascii=False)
    banner_msg = ld(1, "WebcastInRoomBannerMessage") + ld(2, ld(2, banner_json))

    frame = vi(2, 123) + ld(5, ld(1, "compress_type") + ld(2, "gzip")) \
        + ld(5, ld(1, "im-internal_ext") + ld(2, "EXT")) + ld(7, "msg") + ld(8, b"\x01\x02\x03")
    resp = ld(1, chat_msg) + ld(2, "CUR") + ld(5, "EXT2") + vi(9, 1)

    vectors = {
        "heartbeat_hex": d._heartbeat().hex(),
        "ack_hex": d._ack_msg("EXT2", 123).hex(),
        "frame_b64": base64.b64encode(frame).decode(),
        "resp_b64": base64.b64encode(resp).decode(),
        "chat_b64": base64.b64encode(chat_msg).decode(),
        "gift_b64": base64.b64encode(gift_msg).decode(),
        "member_b64": base64.b64encode(member_msg).decode(),
        "like_b64": base64.b64encode(like_msg).decode(),
        "social_b64": base64.b64encode(social_msg).decode(),
        "roomseq_b64": base64.b64encode(roomseq_msg).decode(),
        "roomstats_b64": base64.b64encode(roomstats_msg).decode(),
        "banner_b64": base64.b64encode(banner_msg).decode(),
    }
    print(json.dumps(vectors, ensure_ascii=False, indent=1))
    print("banner_content=" + repr(d.extract_msg(banner_msg)["content"]))


if __name__ == "__main__":
    main()
