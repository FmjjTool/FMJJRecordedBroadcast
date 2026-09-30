import sys

sys.path.insert(0, "src")
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
GUEST = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"


def call(q, cookie, op="p", var=None):
    r = httpx.post(
        "https://www.kuaishou.com/graphql",
        json={"operationName": op, "query": q, "variables": var or {}},
        headers={"User-Agent": UA,
                 "Referer": "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy",
                 "Origin": "https://www.kuaishou.com",
                 "Content-Type": "application/json", "Cookie": cookie},
        timeout=20)
    return r

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

r = call(Q, GUEST, var={"userId": "3x2ranvzkq6g6wy"})
print("guest:", r.status_code, r.text[:600])

from auth import get_cookie
ck = get_cookie("kuaishou") or ""
r2 = call(Q, ck, var={"userId": "3x2ranvzkq6g6wy"})
print("saved:", r2.status_code, r2.text[:600])
