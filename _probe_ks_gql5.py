import sys

sys.path.insert(0, "src")
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def call(q, cookie, var, op="p"):
    return httpx.post(
        "https://www.kuaishou.com/graphql",
        json={"operationName": op, "query": q, "variables": var},
        headers={"User-Agent": UA,
                 "Referer": "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy",
                 "Origin": "https://www.kuaishou.com",
                 "Content-Type": "application/json", "Cookie": cookie},
        timeout=20)

Q = """query p($userId: String) {
  visionProfile(userId: $userId) {
    result
    userProfile {
      ownerCount { fan photo follow photo_public __typename }
      profile { gender user_name user_id headurl user_text __typename }
      __typename
    }
    __typename
  }
}"""

# 1) 有没有获赞/liked 字段
Q2 = """query p($userId: String) {
  visionProfile(userId: $userId) {
    result
    userProfile {
      ownerCount { fan photo follow photo_public liked __typename }
      __typename
    }
    __typename
  }
}"""
r = call(Q2, "kpf=PC_WEB; clientid=3; did=web_x", {"userId": "3x2ranvzkq6g6wy"})
print("liked-field probe:", r.status_code, r.text[:300])

# 2) 随机 did 的纯游客 Cookie
r2 = call(Q, "kpf=PC_WEB; clientid=3; did=web_9f3ab7c2d1e4f6a8b0c2d4e6f8a0b1c2", {"userId": "3x2ranvzkq6g6wy"})
print("fresh did guest:", r2.status_code, r2.text[:200])

# 3) 完全无 cookie
r3 = httpx.post(
    "https://www.kuaishou.com/graphql",
    json={"operationName": "p", "query": Q, "variables": {"userId": "3x2ranvzkq6g6wy"}},
    headers={"User-Agent": UA,
             "Referer": "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy",
             "Origin": "https://www.kuaishou.com",
             "Content-Type": "application/json"},
    timeout=20)
print("no cookie:", r3.status_code, r3.text[:200])

# 4) 旧硬编码游客 cookie
r4 = call(Q, "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c", {"userId": "3x2ranvzkq6g6wy"})
print("old guest cookie:", r4.status_code, r4.text[:200])
