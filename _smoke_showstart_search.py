"""秀动关键词搜索冒烟：验证客户端接口通道可用。

背景：秀动演出列表页改成纯客户端渲染后，SSR 的 /event/list?keyword=
不再内嵌卡片，旧 HTTP 解析恒返回 0 条（表现为「秀动无结果」）。
新通道用 Playwright 复用页面 $nuxt.$axios 调 /api/web/activity/list
（拦截器自动补 crpsign 签名），cityCode 空串 = 全国。

跑法：python _smoke_showstart_search.py
"""
import sys

sys.path.insert(0, "src")

import tickets


def log(m):
    print("  LOG:", m)


print("== 1. 接口通道：已知有演出的关键词（痛仰）==")
evs, total = tickets._showstart_api_search("痛仰", log=log)
print(f"  → {len(evs)} 场 / total={total}")
for e in evs[:3]:
    print(f"    {e.event_id} | {e.title[:30]} | {e.city} | {e.venue[:18]} | "
          f"{e.show_time} | {e.price_range}")
assert evs, "痛仰 应该有在售演出，接口通道疑似失效"

print("== 2. 旧 SSR 通道（预期 0，仅作对照）==")
ssr = tickets._showstart_search_keyword_ssr("痛仰")
print(f"  → SSR 解析 {len(ssr)} 场（预期 0：页面已改客户端渲染）")

print("== 3. 统一入口：封茗囧菌（秀动全国当前无在售）==")
res, diag = tickets.search_tickets_by_keyword("封茗囧菌", platform="showstart")
print(f"  → {len(res)} 场，diag={diag}")

print("== 4. 统一入口：痛仰（all 平台，看 diag 文案）==")
res2, diag2 = tickets.search_tickets_by_keyword("痛仰", platform="showstart")
print(f"  → {len(res2)} 场，diag={diag2}")
assert len(res2) > 0, "统一入口应能拿到秀动结果"

print("\n✅ 秀动关键词搜索冒烟通过")
