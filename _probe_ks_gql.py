import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
GUEST = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"
Q = """query visionProfile($pcursor: String, $principalId: String) {
  visionProfile(pcursor: $pcursor, principalId: $principalId) {
    result
    userProfile { ownerCount { fan photo follow photo_public __typename } profile { gender user_name user_id headurl user_text __typename } __typename }
    __typename
  }
}"""

import sys
sys.path.insert(0, "src")

def call(cookie):
    return httpx.post(
        "https://www.kuaishou.com/graphql",
        json={"operationName": "visionProfile", "query": Q,
              "variables": {"pcursor": "", "principalId": "3x2ranvzkq6g6wy"}},
        headers={"User-Agent": UA,
                 "Referer": "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy",
                 "Origin": "https://www.kuaishou.com",
                 "Content-Type": "application/json", "Cookie": cookie},
        timeout=20)

r = call(GUEST)
print("guest:", r.status_code, r.text[:300])

from auth import get_cookie
ck = get_cookie("kuaishou") or ""
r2 = call(ck)
print("saved:", r2.status_code, r2.text[:300])
