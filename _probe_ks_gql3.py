import sys

sys.path.insert(0, "src")
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
GUEST = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"


def call(q, op="p"):
    r = httpx.post(
        "https://www.kuaishou.com/graphql",
        json={"operationName": op, "query": q, "variables": {}},
        headers={"User-Agent": UA,
                 "Referer": "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy",
                 "Origin": "https://www.kuaishou.com",
                 "Content-Type": "application/json", "Cookie": GUEST},
        timeout=20)
    return r

# 猜1：visionProfile 用 userId 参数
q1 = 'query p { visionProfile(userId: "3x2ranvzkq6g6wy") { result } }'
r = call(q1)
print("q1:", r.status_code, r.text[:400])

# 猜2：错误里说的字段结构
q2 = 'query p { visionProfile { result } }'
r = call(q2)
print("q2:", r.status_code, r.text[:400])

# 猜3：visionProfilePhotoList
q3 = 'query p { visionProfilePhotoList(userId: "3x2ranvzkq6g6wy", pcursor: "") { result } }'
r = call(q3)
print("q3:", r.status_code, r.text[:400])
